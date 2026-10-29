#!/usr/bin/env python3
"""Measured coverage volume, computed one day at a time (METHOD.md section 10.9).

Where in three dimensions aircraft fly, and how many of those seconds had no signal, per cell of space. The day's raw
feed is replayed once with the service's own rules (sources.py: parsing, cadences, the flight and jump rules, the
feed-break gate), and every segment between two consecutive fixes of an aircraft is split across the voxels it
crosses, with its seconds and the seconds late (the segment time beyond the expected interval of the radio plus a
10 s tolerance, METHOD.md section 2). Four series: radio (FLARM, ADS-L, OGN tracker, PilotAware, FANET, also when an
internet gateway forwards them) and phone (OGNSKY, OGNAVI, OGNVVO, OGNMTK, FXCAPP), each for free flight
(paragliders, hang gliders) and for crewed aircraft. Two levels: fine (0.02 degree, 100 m) and coarse (0.05 degree,
250 m). The files hold aggregates only; addresses live in memory as set members while the day runs, to count
aircraft-days per voxel, and are never written.

The standard library only (the host has no numpy), with the readers of nightly.py.

  python3 volume.py day    [--raw DIR] --store DIR --day YYYY-MM-DD  one day's file: store/volume-YYYYMMDD.bin (--raw defaults to EC_RAW_DIR)
  python3 volume.py month  --store DIR --month YYYY-MM               the month's totals: store/volume-YYYYMM-total.bin
  python3 volume.py prune  --store DIR [--keep-days 396] [--dry-run] delete day files older than 13 months
  python3 volume.py tiles  --store DIR --out DIR [--through YYYY-MM-DD] [--days 365] [--box LAT_LO,LAT_HI,LON_LO,LON_HI]
                                                                     rebuild the site tiles from the last 12 months of day files; OUT is a
                                                                     symlink to OUT.versions/vYYYYMMDDHHMMSS, switched by an atomic rename
  python3 volume.py nightly --store DIR --out DIR [--raw DIR] [--day YYYY-MM-DD]
                                                                     the nightly step: day, month totals on the last day of a month, prune,
                                                                     tiles; one row in volume_runs (seconds, peak memory, status, voxels)

File format: 8 bytes of magic, a 4-byte header length, the header (JSON: day or month, method, levels, the list of
tiles as [level, lat, lon, offset, length]), then one zlib block per degree square and level. Inside a block, for
each of the four series in order: a 4-byte count, then count voxel indices (int32; ((i * n + j) * bands + k) with i
and j the cell inside the square), count seconds airborne (float32), count seconds late (float32), count
aircraft-days (int32). A tile of the site is the same square, so the rebuild reads one square at a time and its
memory does not grow with the number of days kept.
"""
import argparse
import array
import collections
import datetime
import glob
import json
import math
import os
import re
import resource
import shutil
import struct
import sys
import time
import traceback
import zlib

import nightly
import sources

STALE = sources.STALE_SECONDS
SESSION_BREAK = sources.SESSION_BREAK
TOL = sources.CADENCE_TOLERANCE

# Phone channel: apps with a known cadence (sources.APP_CADENCE) plus two with a fixed one.
PHONE_CADENCE = dict(sources.APP_CADENCE)
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
METHOD = 1                                    # bump when the rules above change, so a file says what made it
BOX = (34.0, 72.0, -25.0, 45.0)               # the tiles of the site: all of Europe, as built on 29 October 2026
FIELDS = [f"{s}_{f}" for s in SERIES for f in ("air_s", "late_s", "acd")]

MAGIC = b"ECVOL001"
FILE_GLOB = "volume-2*.bin"
VERSION_RE = re.compile(r"^v\d{14}$")
KEEP_DAYS = 396                               # 13 months

if array.array("i").itemsize != 4 or array.array("f").itemsize != 4 or sys.byteorder != "little":
    raise SystemExit("volume.py needs 4-byte int and float arrays on a little-endian machine")


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


def replay(day, raw_dir):
    """The day's voxels: an Acc filled from the 24 hourly files of `day` (a date)."""
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
    for hour in range(24):
        path = nightly.hour_path(raw_dir, day, hour)
        if path is None:
            st["hours_missing"] += 1
            continue
        for epoch, line in nightly.gated(nightly.read_lines(path), gate):
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
                d = nightly.dist(prev[1], prev[2], lat, lon)
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
                d = nightly.dist(prev[1], prev[2], lat, lon)
                if sources.impossible_jump(category, d, secs) or not sources.flying(category, prev[3], fly):
                    continue
                slow = min(prev[5] or 0, kt or 0) * 0.514444
                late = max(0.0, secs - sources.expected_interval(PHONE_CADENCE[tocall], slow) - TOL)
                acc.deposit(2 + fam, ahash, prev[1], prev[2], prev[4], lat, lon, alt, secs, late)
    return acc


# ---------------------------------------------------------------- the file
def split_squares(acc):
    """{(level, lat, lon): {series index: (keys, air, late, acd)}}, keys local to the square, sorted."""
    out = {}
    for lv in LEVEL_ORDER:
        n = LEVELS[lv][0]
        nl, nk = NLON[lv], NK[lv]
        for s in range(len(SERIES)):
            air, acd = acc.air[lv][s], acc.acd[lv][s]
            for key in sorted(air):
                row, k = divmod(key, nk)
                i, j = divmod(row, nl)
                sq = out.setdefault((lv, i // n - 90, j // n - 180), {})
                cols = sq.get(s)
                if cols is None:
                    cols = sq[s] = (array.array("i"), array.array("f"), array.array("f"), array.array("i"))
                r = air[key]
                cols[0].append(((i % n) * n + (j % n)) * nk + k)
                cols[1].append(r[0]); cols[2].append(r[1]); cols[3].append(len(acd[key]))
    return out


def encode_block(sq):
    parts = []
    for s in range(len(SERIES)):
        cols = sq.get(s)
        count = len(cols[0]) if cols else 0
        parts.append(struct.pack("<i", count))
        if count:
            parts.extend(c.tobytes() for c in cols)
    return zlib.compress(b"".join(parts), 6)


def decode_block(blob):
    """{series index: (keys, air, late, acd)} of one block."""
    raw = zlib.decompress(blob)
    pos, out = 0, {}
    for s in range(len(SERIES)):
        (count,) = struct.unpack_from("<i", raw, pos)
        pos += 4
        if not count:
            continue
        cols = []
        for code in "iffi":
            a = array.array(code)
            a.frombytes(raw[pos:pos + 4 * count])
            pos += 4 * count
            cols.append(a)
        out[s] = tuple(cols)
    return out


def write_file(path, meta, squares):
    """`squares` {(level, lat, lon): block bytes}; written to a temporary name and renamed."""
    index, offset, payload = [], 0, []
    for (lv, lat, lon), blob in sorted(squares.items()):
        index.append([lv, lat, lon, offset, len(blob)])
        offset += len(blob)
        payload.append(blob)
    head = json.dumps(dict(meta, method=METHOD, series=list(SERIES), tiles=index), separators=(",", ":")).encode()
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(MAGIC + struct.pack("<I", len(head)) + head)
        for blob in payload:
            f.write(blob)
    os.replace(tmp, path)
    return os.path.getsize(path)


class Stored:
    """One day or month file, opened for reading its squares."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            if f.read(8) != MAGIC:
                raise ValueError(f"{path} is not a volume file")
            (n,) = struct.unpack("<I", f.read(4))
            self.meta = json.loads(f.read(n))
        self.base = 12 + n
        self.index = {(lv, lat, lon): (off, ln) for lv, lat, lon, off, ln in self.meta["tiles"]}

    def square(self, lv, lat, lon):
        got = self.index.get((lv, lat, lon))
        if got is None:
            return {}
        with open(self.path, "rb") as f:
            f.seek(self.base + got[0])
            return decode_block(f.read(got[1]))


def day_path(store, day):
    return os.path.join(store, "volume-%s.bin" % day.strftime("%Y%m%d"))


def month_path(store, month):
    return os.path.join(store, "volume-%s-total.bin" % month.replace("-", ""))


def run_day(raw_dir, store, day):
    t0 = time.time()
    os.makedirs(store, exist_ok=True)
    acc = replay(day, raw_dir)
    if acc.stats["hours_missing"] >= 24:
        raise SystemExit(f"no hourly file of {day} in {raw_dir}")
    t1 = time.time()
    squares = {key: encode_block(sq) for key, sq in split_squares(acc).items()}
    voxels = {lv: sum(len(acc.air[lv][s]) for s in range(len(SERIES))) for lv in LEVEL_ORDER}
    size = write_file(day_path(store, day), {"day": day.isoformat()}, squares)
    summary = {}
    for lv in LEVEL_ORDER:
        for s, name in enumerate(SERIES):
            air = acc.air[lv][s]
            summary[f"{lv}_{name}"] = [len(air), round(sum(r[0] for r in air.values())), round(sum(r[1] for r in air.values()))]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    out = {"day": day.isoformat(), "replay_s": round(t1 - t0), "total_s": round(time.time() - t0),
           "bytes": size, "squares": len(squares), "voxels": voxels, "peak_rss_mb": round(rss),
           "stats": dict(acc.stats), "voxels_air_late_s": summary}
    print(json.dumps(out, separators=(",", ":")), flush=True)
    return out


# ---------------------------------------------------------------- sums, tiles
def sum_square(files, lv, lat, lon):
    """{key: [12 sums]} of one square over `files` (Stored), in float64 like a sum of the float32 columns."""
    acc = {}
    for fl in files:
        for s, (keys, air, late, acd) in fl.square(lv, lat, lon).items():
            base = s * 3
            for key, a, l, c in zip(keys, air, late, acd):
                r = acc.get(key)
                if r is None:
                    r = acc[key] = [0.0] * 12
                r[base] += a; r[base + 1] += l; r[base + 2] += c
    return acc


def run_month(store, month):
    files = sorted(glob.glob(os.path.join(store, "volume-%s[0-3][0-9].bin" % month.replace("-", ""))))
    if not files:
        raise SystemExit(f"no day files for {month} in {store}")
    stored = [Stored(p) for p in files]
    keys = sorted({k for fl in stored for k in fl.index})
    squares = {}
    for lv, lat, lon in keys:
        acc = sum_square(stored, lv, lat, lon)
        sq = {}
        for s in range(len(SERIES)):
            rows = sorted(k for k, r in acc.items() if r[s * 3] or r[s * 3 + 1] or r[s * 3 + 2])
            if rows:
                sq[s] = (array.array("i", rows), array.array("f", [acc[k][s * 3] for k in rows]),
                         array.array("f", [acc[k][s * 3 + 1] for k in rows]), array.array("i", [int(acc[k][s * 3 + 2]) for k in rows]))
        squares[(lv, lat, lon)] = encode_block(sq)
    size = write_file(month_path(store, month), {"month": month, "days": [fl.meta["day"] for fl in stored]}, squares)
    print(json.dumps({"month": month, "days": len(stored), "bytes": size}), flush=True)


def run_prune(store, keep_days, dry):
    cutoff = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=keep_days)
    gone = 0
    for p in sorted(glob.glob(os.path.join(store, "volume-2???????.bin"))):
        d = datetime.datetime.strptime(os.path.basename(p)[7:15], "%Y%m%d").date()
        if d < cutoff:
            gone += 1
            if not dry:
                os.remove(p)
    print(f"{'would delete' if dry else 'deleted'} {gone} day files older than {cutoff}")


def run_tiles(store, out, through, days, box):
    """Tiles of the site (/data/volume/<level>/<lat>_<lon>.json and index.json) from the last `days` days of day files."""
    t0 = time.time()
    first = through - datetime.timedelta(days=days - 1)
    stored = []
    for p in sorted(glob.glob(os.path.join(store, "volume-2???????.bin"))):
        d = datetime.datetime.strptime(os.path.basename(p)[7:15], "%Y%m%d").date()
        if first <= d <= through:
            stored.append(Stored(p))
    if not stored:
        raise SystemExit("no day files in the window")
    if os.path.lexists(out) and not os.path.islink(out):
        raise SystemExit(f"{out} is a real directory: migrate it first: mkdir OUT.versions; mv OUT OUT.versions/v<14 digits>; ln -s OUT.versions/v<14 digits> OUT")
    versions = out.rstrip("/") + ".versions"
    version = datetime.datetime.now(datetime.timezone.utc).strftime("v%Y%m%d%H%M%S")
    build = os.path.join(versions, version)
    os.makedirs(versions, exist_ok=True)
    shutil.rmtree(build, ignore_errors=True)
    for lv in LEVEL_ORDER:
        os.makedirs(os.path.join(build, lv))
    first_day, last_day = stored[0].meta["day"], stored[-1].meta["day"]
    index = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
             "feed": f"{first_day.replace('-', '')} to {last_day.replace('-', '')}, {len(stored)} days",
             "box": {"lat": list(box[:2]), "lon": list(box[2:])},
             "series": list(SERIES), "fields_after_ijk": FIELDS,
             "array": "[i, j, k] + for each series in order radio_ff, radio_ac, phone_ff, phone_ac: air_s, late_s, acd",
             "notes": ["air_s: airborne seconds in the voxel; late_s: seconds without signal (METHOD.md section 2: segment time minus "
                       "cadence interval minus 10 s tolerance); acd: aircraft-days (distinct aircraft per day, summed over days)",
                       "ff = paraglider + hang glider (packet category 6, 7); ac = categories 1-5, 8, 9, 11, 12; drones, unknown and ground left out",
                       "radio = FLARM, ADS-L, OGN tracker, PilotAware, FANET, also when an internet gateway forwards them; phone = OGNSKY, OGNAVI, OGNVVO, OGNMTK (20 s), FXCAPP (60 s)",
                       "altitude is MSL; k = floor(alt / dz); voxels with under 1 s of airborne time in every series are omitted"],
             "levels": {}, "tiles": {}}
    squares = sorted({k for fl in stored for k in fl.index})
    for lv in LEVEL_ORDER:
        n, dz = LEVELS[lv]
        nk = NK[lv]
        index["levels"][lv] = {"deg": 1 / n, "cells_per_deg": n, "dz_m": dz, "bands": nk, "alt_range_m": [0, ALT_MAX],
                               "i": "latitude cell inside the tile, 0 = south edge", "j": "longitude cell inside the tile, 0 = west edge",
                               "k": "altitude band index, MSL"}
        index["tiles"][lv] = []
    for lv, lat, lon in squares:
        if not (box[0] <= lat < box[1] and box[2] <= lon < box[3]):
            continue
        n = LEVELS[lv][0]
        nk = NK[lv]
        acc = sum_square(stored, lv, lat, lon)
        rows = []
        for key in sorted(acc):
            r = acc[key]
            if max(round(r[0]), round(r[3]), round(r[6]), round(r[9])) < 1:
                continue
            ij, k = divmod(key, nk)
            i, j = divmod(ij, n)
            rows.append([i, j, k] + [int(round(x)) for x in r])
        if not rows:
            continue
        name = f"{lat}_{lon}"
        with open(os.path.join(build, lv, name + ".json"), "w") as f:
            json.dump(rows, f, separators=(",", ":"))
        index["tiles"][lv].append({"lat": lat, "lon": lon, "voxels": len(rows), "file": f"{lv}/{name}.json"})
    with open(os.path.join(build, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    # The site reads OUT/{fine,coarse,index.json}. OUT is a symlink to a complete version directory, and the switch is one
    # rename of a new symlink over it, so a request sees the old tiles or the new ones and never a missing level. The
    # previous version stays until the next run; older ones go.
    previous = os.path.basename(os.readlink(out)) if os.path.islink(out) else None
    tmp = out.rstrip("/") + ".switch"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(os.path.join(os.path.basename(versions), version), tmp)
    os.replace(tmp, out)
    keep = {version, previous}
    for name in os.listdir(versions):
        if VERSION_RE.match(name) and name not in keep:
            shutil.rmtree(os.path.join(versions, name), ignore_errors=True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    print(json.dumps({"tiles": {lv: len(index["tiles"][lv]) for lv in LEVEL_ORDER}, "days": len(stored),
                      "seconds": round(time.time() - t0), "peak_rss_mb": round(rss), "version": version}), flush=True)
    return {"tiles": sum(len(index["tiles"][lv]) for lv in LEVEL_ORDER), "days": len(stored), "version": version}


def record_run(row):
    """One row in volume_runs (schema/2026-10-30-volume-runs.sql). The service user may INSERT but not UPDATE, so the key is
    (day, finished_at) and a rerun adds a row. A database that cannot be reached is reported and does not fail the run."""
    try:
        conn = nightly.connect_db()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO volume_runs (day, finished_at, seconds, peak_rss_mb, status, hours_read, voxels_fine, "
                            "voxels_coarse, day_bytes, tiles, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"volume_runs not written: {type(e).__name__}: {e}", file=sys.stderr)


def run_nightly(raw_dir, store, out, day):
    """The nightly step, after nightly.py has finished its own day. A failed step stops the chain, leaves the tiles on the
    site as they were, and is the status of the row."""
    t0 = time.time()
    status, notes = "ok", []
    d = {"voxels": {"fine": None, "coarse": None}, "bytes": None, "stats": {"hours_missing": None}}
    t = {"tiles": None}
    try:
        d = run_day(raw_dir, store, day)
        if (day + datetime.timedelta(days=1)).day == 1:
            run_month(store, day.strftime("%Y-%m"))
        run_prune(store, KEEP_DAYS, False)
        t = run_tiles(store, out, day, 365, BOX)
    except BaseException as e:                       # SystemExit included: the row must say what stopped
        status = "failed"
        notes.append(f"{type(e).__name__}: {e}"[:200])
        traceback.print_exc()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    missing = d["stats"].get("hours_missing")
    if missing:
        notes.append(f"{missing} hourly files missing")
    record_run((day.isoformat(), datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None), round(time.time() - t0, 1), round(rss),
                status, None if missing is None else 24 - missing, d["voxels"]["fine"], d["voxels"]["coarse"], d["bytes"],
                t["tiles"], "; ".join(notes)[:255]))
    return 0 if status == "ok" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("day", "month", "prune", "tiles", "nightly"))
    ap.add_argument("--raw", help="day, nightly: directory of the hourly files (default: EC_RAW_DIR, from the environment or the .env of the checkout)")
    ap.add_argument("--store", required=True, help="directory of the per-day and monthly files")
    ap.add_argument("--day", help="day, nightly: YYYY-MM-DD (nightly: default yesterday, UTC)")
    ap.add_argument("--month", help="month: YYYY-MM")
    ap.add_argument("--out", help="tiles, nightly: the site path (index.json and the level directories); a symlink into OUT.versions/")
    ap.add_argument("--through", help="tiles: last day of the window, default the newest day file")
    ap.add_argument("--days", type=int, default=365, help="tiles: window length in days, default 365")
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS, help="prune: days of day files kept, default 396 (13 months)")
    ap.add_argument("--dry-run", action="store_true", help="prune: only count")
    ap.add_argument("--box", default=",".join(str(x) for x in BOX), help="tiles: LAT_LO,LAT_HI,LON_LO,LON_HI")
    a = ap.parse_args()
    if a.cmd in ("day", "nightly"):
        nightly.load_dotenv(os.path.join(nightly.HERE, ".env"))
        raw = os.path.expanduser(a.raw or os.getenv("EC_RAW_DIR") or "")
        if not raw or not os.path.isdir(raw):
            ap.error("no --raw and EC_RAW_DIR is not set to a directory")
    if a.cmd == "nightly":
        if not a.out:
            ap.error("nightly needs --out")
        day = (datetime.date.fromisoformat(a.day) if a.day
               else datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1))
        return run_nightly(raw, a.store, a.out, day)
    if a.cmd == "day":
        if not a.day:
            ap.error("day needs --day")
        run_day(raw, a.store, datetime.date.fromisoformat(a.day))
    elif a.cmd == "month":
        if not a.month:
            ap.error("month needs --month")
        run_month(a.store, a.month)
    elif a.cmd == "prune":
        run_prune(a.store, a.keep_days, a.dry_run)
    else:
        if not a.out:
            ap.error("tiles needs --out")
        if a.through:
            through = datetime.date.fromisoformat(a.through)
        else:
            newest = sorted(glob.glob(os.path.join(a.store, "volume-2???????.bin")))
            if not newest:
                raise SystemExit("no day files")
            through = datetime.datetime.strptime(os.path.basename(newest[-1])[7:15], "%Y%m%d").date()
        run_tiles(a.store, a.out, through, a.days, tuple(float(x) for x in a.box.split(",")))


if __name__ == "__main__":
    sys.exit(main())
