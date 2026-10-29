#!/usr/bin/env python3
"""Measured coverage volume: airborne time and time without signal per cell of space, per radio channel and family.

The measured side of "coverage in detail" (METHOD.md): where in three dimensions aircraft fly, and how much of that
time had no signal. The feed is replayed with the service's own rules (sources.py: parsing, cadences, the flight and
jump rules, the feed-break gate), and every segment between two consecutive fixes of an aircraft is split across the
voxels it crosses, with its seconds and the seconds late (the segment time beyond the expected interval of the
radio plus a 10 s tolerance, METHOD.md section 2). Four series: radio (FLARM, ADS-L, OGN tracker, PilotAware,
FANET, also when an internet gateway forwards them) and phone (OGNSKY, OGNAVI, OGNVVO, OGNMTK, FXCAPP), each for
free flight (paragliders, hang gliders) and for crewed aircraft. Two resolutions: fine (0.02 degree, 100 m) and
coarse (0.05 degree, 250 m). Outputs hold aggregates only; addresses live in memory as set members, to count
aircraft-days per voxel.

  python3 volume.py run   --feed FEED --work WORK       all days in parallel, one npz per day in WORK
  python3 volume.py day   --feed FEED --work WORK --day YYYYMMDD
  python3 volume.py merge --feed FEED --work WORK --out OUT    summary.txt and tiles/ (JSON per degree square)

Needs zstdcat and numpy. --workers sets how many days run at once; each holds its voxel tables in memory.
"""
import sys, os, glob, math, time, json, resource, subprocess, collections
import argparse
import multiprocessing as mp

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import sources  # noqa: E402

STALE = sources.STALE_SECONDS
SESSION_BREAK = sources.SESSION_BREAK
TOL = sources.CADENCE_TOLERANCE

# Phone channel: apps with a known cadence (sources.APP_CADENCE) plus two with a fixed one.
PHONE_CADENCE = {k: v for k, v in sources.APP_CADENCE.items()}
PHONE_CADENCE["OGNMTK"] = (None, 20, 20)
PHONE_CADENCE["FXCAPP"] = (None, 60, 60)
RADIO_CADENCE = sources.RADIO_CADENCE          # radio systems, whatever gateway (via) forwards them

SERIES = ("radio_ff", "radio_ac", "phone_ff", "phone_ac")
FF = (6, 7)
AIRCRAFT = frozenset({1, 2, 3, 4, 5, 8, 9, 11, 12})   # crewed, not free flight; 10 unknown, 13 drone, 14/15 ground left out
LEVELS = {"fine": (50, 100.0), "coarse": (20, 250.0)}  # (cells per degree, metres per band)
LEVEL_ORDER = ("fine", "coarse")
ALT_MAX = 6000.0
NLON = {"fine": 360 * 50, "coarse": 360 * 20}
NK = {"fine": 60, "coarse": 24}


def read_lines(path):
    proc = subprocess.Popen(["zstdcat", "-q", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace")
            sp = line.find(" ")
            try:
                epoch = float(line[:sp])
            except ValueError:
                continue
            line = line[sp + 1:].rstrip("\n")
            if line[:1] == "#" and line != sources.FEED_BREAK:
                continue
            yield epoch, line
    finally:
        proc.kill(); proc.wait()


def gated(lines, gate):
    for epoch, line in lines:
        skipped, edges = gate.step(epoch)
        for edge in edges:
            yield edge, sources.FEED_BREAK
        if not skipped:
            yield epoch, line


class Acc:
    def __init__(self):
        self.air = {lv: [dict() for _ in SERIES] for lv in LEVEL_ORDER}   # key -> [air, late]
        self.acd = {lv: [dict() for _ in SERIES] for lv in LEVEL_ORDER}   # key -> set of address hashes
        self.stats = collections.Counter()

    def deposit(self, s, addr, lat0, lon0, a0, lat1, lon1, a1, secs, late):
        for lv in LEVEL_ORDER:
            n, dz = LEVELS[lv]
            u0, v0, w0 = (lat0 + 90) * n, (lon0 + 180) * n, a0 / dz
            u1, v1, w1 = (lat1 + 90) * n, (lon1 + 180) * n, a1 / dz
            f0 = (math.floor(u0), math.floor(v0), math.floor(w0))
            f1 = (math.floor(u1), math.floor(v1), math.floor(w1))
            nl, nk = NLON[lv], NK[lv]
            air, acd = self.air[lv][s], self.acd[lv][s]
            if f0 == f1:
                parts = ((((f0[0] * nl + f0[1]) * nk + f0[2]), 1.0),)
            else:
                ts = {0.0, 1.0}
                for x0, x1 in ((u0, u1), (v0, v1), (w0, w1)):
                    if x0 != x1:
                        lo, hi = math.floor(min(x0, x1)), math.floor(max(x0, x1))
                        for g in range(lo + 1, hi + 1):
                            ts.add((g - x0) / (x1 - x0))
                ts = sorted(t for t in ts if 0.0 <= t <= 1.0)
                parts = []
                for p, q in zip(ts, ts[1:]):
                    if q <= p:
                        continue
                    m = (p + q) / 2
                    key = (math.floor(u0 + (u1 - u0) * m) * nl + math.floor(v0 + (v1 - v0) * m)) * nk + \
                        math.floor(w0 + (w1 - w0) * m)
                    parts.append((key, q - p))
            for key, frac in parts:
                r = air.get(key)
                if r is None:
                    r = air[key] = [0.0, 0.0]
                    acd[key] = {addr}
                else:
                    acd[key].add(addr)
                r[0] += secs * frac
                r[1] += late * frac


def process_day(job):
    day, feed, work = job
    files = sorted(glob.glob(os.path.join(feed, day + "-*.aprs.zst")))
    acc = Acc()
    st = acc.stats
    gate = sources.FeedGate(sources.load_exclusions())
    zero = sources.ZeroSpeed()
    last = {}            # (src, tocall, via) -> last packet time of the stream, copy dedupe
    rstate = {}          # address -> (t, lat, lon, kt, alt, category)   radio timeline
    heard = {}           # address -> {tocall: t}
    pstate = {}          # (src, tocall) -> (t, lat, lon, kt, alt, raw_kt)  phone stream
    SYMCAT = sources.SYMBOL_CATEGORY
    id_info, packet_system, rebroadcast = sources.id_info, sources.packet_system, sources.rebroadcast
    EXC = sources.EXCLUDED
    position, extra, alt_re = sources._position, sources._extra_precision, sources._alt
    for path in files:
        for epoch, line in gated(read_lines(path), gate):
            if line == sources.FEED_BREAK:
                last.clear(); rstate.clear(); heard.clear(); pstate.clear(); zero.feed_break()
                continue
            try:
                src, rest = line.split(">", 1)
                head, body = rest.split(":", 1)
            except ValueError:
                continue
            if body[:1] != "/" or body[26:27] in ("&", "_"):
                continue
            hp = head.split(",")
            if hp[0] in EXC:
                continue
            tocall = packet_system(hp, src)
            is_radio = tocall in RADIO_CADENCE
            if not is_radio and tocall not in PHONE_CADENCE:
                continue
            if is_radio and src.startswith(sources.RANDOM_PREFIX) and tocall in sources.RANDOM_ROTATING:
                continue
            idcat, no_track = id_info(body)
            if no_track:
                continue
            if rebroadcast(tocall, src, idcat):
                continue
            m = position.match(body)
            if not m or body[1:7] == "______" or "h" not in body[:8] or (body[8:10] == "00" and body[17:20] == "000"):
                continue
            hh, mi, ss, latd, latm, ns, _t, lond, lonm, ew, symbol, course, speed = m.groups()
            category = idcat if idcat is not None else SYMCAT.get(symbol, 255)
            fam = 0 if category in FF else 1 if category in AIRCRAFT else None
            if fam is None:
                continue
            latm, lonm = float(latm), float(lonm)
            w = extra.search(body)
            if w:
                latm += int(w.group(1)) / 1000
                lonm += int(w.group(2)) / 1000
            lat = (int(latd) + latm / 60) * (-1 if ns == "S" else 1)
            lon = (int(lond) + lonm / 60) * (-1 if ew == "W" else 1)
            a = alt_re.search(body)
            alt = int(a.group(1)) * 0.3048 if a else None
            kt = int(speed) if speed is not None else None
            sod = int(hh) * 3600 + int(mi) * 60 + int(ss)
            now = int(epoch)
            t = now - now % 86400 + sod
            if t - epoch > STALE:
                t -= 86400
            if epoch - t > STALE:
                continue
            via = "radio" if sources._radio_meta.search(body) else "net"
            skey = (src, tocall, via)
            old = sources.implausible(category, kt, alt)
            if old or sources.impossible_fix(category, kt, alt):
                last.pop(skey, None)
                if is_radio:
                    rstate.pop(src[-6:], None)
                else:
                    pstate.pop((src, tocall), None)
                continue
            pl = last.get(skey)
            if pl is not None and t <= pl:
                continue
            last[skey] = t
            fly = zero.speed(skey, tocall, category, t, lat, lon, kt)
            if alt is None or alt < -200 or alt >= ALT_MAX:
                st["fix_no_alt_or_out_of_range_" + ("radio" if is_radio else "phone")] += 1
                if is_radio:
                    rstate.pop(src[-6:], None)
                else:
                    pstate.pop((src, tocall), None)
                continue
            if alt < 0:
                alt = 0.0
            ahash = hash(src[-6:])
            if is_radio:
                addr = src[-6:]
                h = heard.setdefault(addr, {})
                h[tocall] = t
                prev = rstate.get(addr)
                if prev is not None and t <= prev[0]:
                    continue
                rstate[addr] = (t, lat, lon, fly, alt, category)
                if prev is None:
                    continue
                secs = t - prev[0]
                if secs > SESSION_BREAK:
                    continue
                d = _dist(prev[1], prev[2], lat, lon)
                if sources.impossible_jump(category, d, secs) or not sources.flying(category, prev[3], fly):
                    continue
                interval = max(RADIO_CADENCE[s][2] for s, ts in h.items() if t - ts <= SESSION_BREAK)
                late = max(0.0, secs - interval - TOL)
                acc.deposit(fam, ahash, prev[1], prev[2], prev[4], lat, lon, alt, secs, late)
            else:
                pk = (src, tocall)
                prev = pstate.get(pk)
                pstate[pk] = (t, lat, lon, fly, alt, kt)
                if prev is None:
                    continue
                secs = t - prev[0]
                if secs > SESSION_BREAK:
                    continue
                d = _dist(prev[1], prev[2], lat, lon)
                if sources.impossible_jump(category, d, secs) or not sources.flying(category, prev[3], fly):
                    continue
                slow = min(prev[5] or 0, kt or 0) * 0.514444
                late = max(0.0, secs - sources.expected_interval(PHONE_CADENCE[tocall], slow) - TOL)
                acc.deposit(2 + fam, ahash, prev[1], prev[2], prev[4], lat, lon, alt, secs, late)
    # to arrays
    out = {}
    for lv in LEVEL_ORDER:
        for s, name in enumerate(SERIES):
            air, acd = acc.air[lv][s], acc.acd[lv][s]
            keys = np.fromiter(air.keys(), dtype=np.int64, count=len(air))
            vals = np.array(list(air.values()), dtype=np.float64).reshape(-1, 2)
            cnt = np.fromiter((len(acd[k]) for k in air.keys()), dtype=np.int32, count=len(air))
            out[f"{lv}_{name}_k"], out[f"{lv}_{name}_air"] = keys, vals[:, 0].astype(np.float32)
            out[f"{lv}_{name}_late"], out[f"{lv}_{name}_acd"] = vals[:, 1].astype(np.float32), cnt
    np.savez(os.path.join(work, day + ".npz"), **out)
    return day, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**30 if sys.platform == "darwin" else 2**20), dict(st)


_R = 6371000.0


def _dist(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _R * math.asin(min(1.0, math.sqrt(x)))


def run_all(feed, work, workers=10):
    os.makedirs(work, exist_ok=True)
    days = sorted({os.path.basename(p)[:8] for p in glob.glob(os.path.join(feed, "*.aprs.zst"))})
    days = [d for d in days if not os.path.exists(os.path.join(work, d + ".npz"))]
    t0 = time.time()
    with mp.Pool(min(workers, len(days) or 1)) as pool:
        for day, rss, st in pool.imap_unordered(process_day, [(d, feed, work) for d in days]):
            print(f"{day} done at {time.time() - t0:.0f}s, worker peak RSS {rss:.2f} GB, {st}", flush=True)
    print(f"all days {time.time() - t0:.0f}s", flush=True)


# ---------------------------------------------------------------- merge, summary, tiles
BOX = (43.0, 48.5, 5.0, 17.0)      # lat_lo, lat_hi, lon_lo, lon_hi of the summary and of the tiles: the Alps by default; --box changes it
FIELDS = [f"{s}_{f}" for s in SERIES for f in ("air_s", "late_s", "acd")]


def load_level(lv, work):
    days = sorted(glob.glob(os.path.join(work, "2*.npz")))
    per = {}
    for s in SERIES:
        ks, air, late, acd = [], [], [], []
        for p in days:
            z = np.load(p)
            ks.append(z[f"{lv}_{s}_k"]); air.append(z[f"{lv}_{s}_air"].astype(np.float64))
            late.append(z[f"{lv}_{s}_late"].astype(np.float64)); acd.append(z[f"{lv}_{s}_acd"].astype(np.float64))
        ks = np.concatenate(ks)
        u, inv = np.unique(ks, return_inverse=True)
        per[s] = (u, np.bincount(inv, np.concatenate(air)), np.bincount(inv, np.concatenate(late)),
                  np.bincount(inv, np.concatenate(acd)))
    allk = np.unique(np.concatenate([per[s][0] for s in SERIES]))
    T = np.zeros((len(allk), 12))
    for si, s in enumerate(SERIES):
        idx = np.searchsorted(allk, per[s][0])
        for j in range(3):
            T[idx, si * 3 + j] = per[s][1 + j]
    n, dz = LEVELS[lv]
    nl, nk = NLON[lv], NK[lv]
    k = allk % nk
    r = allk // nk
    ilon = r % nl
    ilat = r // nl
    return dict(T=T, k=k, ilat=ilat, ilon=ilon, n=n, dz=dz)


def merge(feed, work, out, box):
    import shutil
    t0 = time.time()
    lines = []
    P = lines.append
    tiles_root = os.path.join(out, "tiles")
    shutil.rmtree(tiles_root, ignore_errors=True)
    files = sorted(glob.glob(os.path.join(feed, "*.aprs.zst")))
    first, last = (os.path.basename(files[0])[:8], os.path.basename(files[-1])[:8]) if files else ("", "")
    index = {"generated": time.strftime("%Y-%m-%d"), "feed": f"{first} to {last}, {len(files)} hourly files",
             "box": {"lat": box[:2], "lon": box[2:]},
             "series": list(SERIES), "fields_after_ijk": FIELDS,
             "array": "[i, j, k] + for each series in order radio_ff, radio_ac, phone_ff, phone_ac: air_s, late_s, acd",
             "notes": ["air_s: airborne seconds in the voxel; late_s: seconds without signal (METHOD.md section 2: segment time minus "
                       "cadence interval minus 10 s tolerance); acd: aircraft-days (distinct aircraft per day, summed over days)",
                       "ff = paraglider + hang glider (packet category 6, 7); ac = categories 1-5, 8, 9, 11, 12; drones, unknown and ground left out",
                       "radio = FLARM, ADS-L, OGN tracker, PilotAware, FANET, also when an internet gateway forwards them; phone = OGNSKY, OGNAVI, OGNVVO, OGNMTK (20 s), FXCAPP (60 s)",
                       "altitude is MSL; k = floor(alt / dz); voxels with under 1 s of airborne time in every series are omitted"],
             "levels": {}, "tiles": {}}
    # day stats come from the run log; summary of data here
    P(f"Measured coverage volume, summary (feed {first} to {last}, packet categories, no per-day vote)")
    P("Series: " + ", ".join(SERIES))
    P("")
    shares = {}
    for lv in LEVEL_ORDER:
        D = load_level(lv, work)
        T, n, dz = D["T"], D["n"], D["dz"]
        lat_lo = D["ilat"] / n - 90
        lon_lo = D["ilon"] / n - 180
        inbox = (lat_lo >= box[0]) & (lat_lo < box[1]) & (lon_lo >= box[2]) & (lon_lo < box[3])
        P(f"== level {lv}: {n} cells/deg ({1 / n:g} deg), {dz:g} m, {len(T):,} voxels with data in Europe, {int(inbox.sum()):,} in the box")
        for name, sel in (("Europe (whole feed)", np.ones(len(T), bool)), (f"box {box[0]:g}-{box[1]:g}N {box[2]:g}-{box[3]:g}E", inbox)):
            P(f"  {name}: series, air hours, late hours, late share, share of air time in voxels with acd>=3 and air>=600 s")
            for si, s in enumerate(SERIES):
                air, late, acd = T[sel, si * 3], T[sel, si * 3 + 1], T[sel, si * 3 + 2]
                ok = (acd >= 3) & (air >= 600)
                tot = air.sum()
                P(f"    {s:9s} {tot / 3600:10.0f} h {late.sum() / 3600:9.0f} h  late {100 * late.sum() / max(tot, 1):5.1f}%  "
                  f"dense {100 * air[ok].sum() / max(tot, 1):5.1f}%  ({int(ok.sum()):,} dense voxels of {int((air > 0).sum()):,})")
        if lv == "coarse":
            P("")
            P("  box, per 250 m MSL band (coarse voxels), per series: air hours / late share")
            hdr = "    band m      " + "".join(f"{s:>22s}" for s in SERIES)
            P(hdr)
            for b in range(NK[lv]):
                sel = inbox & (D["k"] == b)
                cells = []
                for si in range(4):
                    a = T[sel, si * 3].sum(); l = T[sel, si * 3 + 1].sum()
                    cells.append(f"{a / 3600:9.0f} h {100 * l / a if a >= 3600 else float('nan'):6.1f}%")
                P(f"    {b * 250:4d}-{(b + 1) * 250:<4d}  " + "".join(f"{c:>22s}" for c in cells))
        P("")
        # tiles
        index["levels"][lv] = {"deg": 1 / n, "cells_per_deg": n, "dz_m": dz, "bands": NK[lv], "alt_range_m": [0, ALT_MAX],
                               "i": "latitude cell inside the tile, 0 = south edge", "j": "longitude cell inside the tile, 0 = west edge",
                               "k": "altitude band index, MSL"}
        index["tiles"][lv] = []
        sel = np.nonzero(inbox & (np.round(T[:, [0, 3, 6, 9]]).max(axis=1) >= 1))[0]
        tl = D["ilat"][sel] // n - 90
        tn = D["ilon"][sel] // n - 180
        order = np.lexsort((tn, tl))
        sel, tl, tn = sel[order], tl[order], tn[order]
        os.makedirs(os.path.join(tiles_root, lv), exist_ok=True)
        cuts = np.nonzero((np.diff(tl) != 0) | (np.diff(tn) != 0))[0] + 1
        for grp in np.split(np.arange(len(sel)), cuts):
            if not len(grp):
                continue
            g = sel[grp]
            rows = np.column_stack([D["ilat"][g] % n, D["ilon"][g] % n, D["k"][g], np.round(T[g])]).astype(np.int64)
            name = f"{int(tl[grp[0]])}_{int(tn[grp[0]])}"
            with open(os.path.join(tiles_root, lv, name + ".json"), "w") as f:
                json.dump(rows.tolist(), f, separators=(",", ":"))
            index["tiles"][lv].append({"lat": int(tl[grp[0]]), "lon": int(tn[grp[0]]), "voxels": int(len(g)), "file": f"{lv}/{name}.json"})
    with open(os.path.join(tiles_root, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    size = subprocess.run(["du", "-sk", tiles_root], capture_output=True, text=True).stdout.split()[0]
    P(f"Output: tiles/ {int(size) / 1024:.1f} MB on disk; " + ", ".join(
        f"{lv} {len(index['tiles'][lv])} tiles" for lv in LEVEL_ORDER))
    open(os.path.join(out, "summary.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"merge {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("day", "run", "merge"))
    ap.add_argument("--feed", required=True, help="directory of hourly files YYYYMMDD-HH.aprs.zst")
    ap.add_argument("--work", required=True, help="directory for the per-day npz files")
    ap.add_argument("--out", help="merge: where summary.txt and tiles/ go")
    ap.add_argument("--day", help="day: YYYYMMDD")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--box", default=",".join(str(x) for x in BOX), help="merge: LAT_LO,LAT_HI,LON_LO,LON_HI")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    if a.cmd == "day":
        print(process_day((a.day, a.feed, a.work)))
    elif a.cmd == "run":
        run_all(a.feed, a.work, a.workers)
    else:
        if not a.out:
            ap.error("merge needs --out")
        os.makedirs(a.out, exist_ok=True)
        merge(a.feed, a.work, a.out, tuple(float(x) for x in a.box.split(",")))


if __name__ == "__main__":
    main()
