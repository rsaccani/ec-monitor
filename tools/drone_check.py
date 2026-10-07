#!/usr/bin/env python3
"""Are the devices declared as drones really drones? (7 October 2026)

The dry run of nightly.py for 6 October found FLARM devices declared as
drones (category 13) crossing France above 300 m at over 100 km/h. Before
Question 7 is published, this looks at every address that declares category
13, or is heard by Remote ID, session by session, with the features that
separate a drone from a crewed aircraft set up wrongly: sustained speed,
height above the ground, how it took off (a drone climbs from standstill, an
aeroplane rolls at 60-100 km/h first), session length, and whether the same
24-bit address is also heard by ADS-B, which drones do not carry.

Prints one line per session under a salted hash, with positions rounded to
one degree, and a summary by verdict. No address leaves the server.

Usage: (cd ~/ads-l-map && nice -n 19 python3 tools/drone_check.py <files>)
"""
import collections
import hashlib
import math
import re
import secrets
import statistics
import csv
import io
import subprocess
import sys
import urllib.request

sys.path.insert(0, ".")
import sources  # noqa: E402

POS = re.compile(r"^/(\d{2})(\d{2})(\d{2})h(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW])(.)"
                 r"(?:(\d{3})/(\d{3}))?(?:/A=(-?\d{6}))?")
FPM = re.compile(r" ([+-]?\d+)fpm")
SALT = secrets.token_hex(8)
GAP = 20 * 60


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")


def dist(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    return 6371000 * math.hypot((lon2 - lon1) * p * math.cos((lat1 + lat2) * p / 2), (lat2 - lat1) * p)


def load_ddb():
    """24-bit address -> aircraft model, from the OGN device database, honouring
    TRACKED=N (dropped) and IDENTIFIED=N (no model), as app.py does."""
    text = urllib.request.urlopen("https://ddb.glidernet.org/download/", timeout=60).read().decode("utf-8", "replace")
    models, notrack = {}, set()
    for row in csv.DictReader(io.StringIO(text)):
        dev = row["DEVICE_ID"].strip().strip("'")
        if row.get("TRACKED", "").strip().strip("'") == "N":
            notrack.add(dev)
            continue
        if row.get("IDENTIFIED", "").strip().strip("'") == "N":
            continue
        models[dev] = row["AIRCRAFT_MODEL"].strip().strip("'")
    return models, notrack


def main():
    terr = sources.Terrain(sources.DEM_PATH)
    models, notrack = load_ddb()
    drone_addr = {}                       # address -> set of tocalls declaring 13 / Remote ID
    adsb = set()
    prefixes = {}
    fixes = collections.defaultdict(list)  # address -> [(t, lat, lon, alt, kmh, fpm, tocall)]
    pending = collections.defaultdict(list)
    for line in lines(sys.argv[1:]):
        try:
            stamp, rest = line.split(" ", 1)
            src, rest = rest.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        tc = sources.same_system(head.split(",")[0], src)
        if tc in sources.EXCLUDED:
            continue
        m = POS.match(body)
        if not m:
            continue
        cat, no_track = sources.id_info(body)
        if no_track:
            continue
        addr = src[-6:]
        if addr in notrack:
            continue
        if tc == "OGADSB":
            adsb.add(addr)
        is_drone = cat == 13 or tc == "OGNMAV"
        if is_drone:
            drone_addr.setdefault(addr, set()).add(tc)
            prefixes.setdefault(addr, set()).add(src[:3])
        hh, mi, ss, latd, latm, ns, lond, lonm, ew, sym, course, speed, alt = m.groups()
        lat = int(latd) + float(latm) / 60
        lon = int(lond) + float(lonm) / 60
        if ns == "S":
            lat = -lat
        if ew == "W":
            lon = -lon
        if abs(lat) < 1 and abs(lon) < 1:
            continue
        f = FPM.search(body)
        rec = (float(stamp), lat, lon, int(alt) * 0.3048 if alt else None,
               int(speed) * 1.852 if speed else 0.0, int(f.group(1)) if f else None, tc, cat)
        # keep fixes of any address once it has declared 13; buffer the others briefly
        if addr in drone_addr:
            if pending.get(addr):
                fixes[addr].extend(pending.pop(addr))
            fixes[addr].append(rec)
        elif tc != "OGADSB":
            buf = pending[addr]
            buf.append(rec)
            if len(buf) > 50:
                del buf[:25]

    out = []
    for addr, recs in fixes.items():
        recs.sort()
        sessions, cur = [], []
        for r in recs:
            if cur and r[0] - cur[-1][0] > GAP:
                sessions.append(cur)
                cur = []
            cur.append(r)
        if cur:
            sessions.append(cur)
        cats = collections.Counter(r[7] for r in recs)
        for s in sessions:
            if len(s) < 10:
                continue
            dur = (s[-1][0] - s[0][0]) / 60
            moving = [r[4] for r in s if r[4] >= 10]
            v50 = statistics.median(moving) if moving else 0
            v90 = sorted(moving)[int(0.9 * (len(moving) - 1))] if moving else 0
            agl = []
            for r in s[:: max(1, len(s) // 200)]:
                if r[3] is not None:
                    g = terr.elevation(r[1], r[2])
                    if g is not None:
                        agl.append(r[3] - g)
            agl50 = statistics.median(agl) if agl else float("nan")
            agl90 = sorted(agl)[int(0.9 * (len(agl) - 1))] if agl else float("nan")
            ext = max(dist(s[0][1], s[0][2], r[1], r[2]) for r in s) / 1000
            # take-off: the first fix above 30 m AGL, and the ground speed just before it
            roll = None
            for j, r in enumerate(s):
                if r[3] is None:
                    continue
                g = terr.elevation(r[1], r[2])
                if g is not None and r[3] - g > 30 and j > 0:
                    before = [q[4] for q in s[max(0, j - 5):j]]
                    roll = max(before) if before else None
                    break
            climb = max((r[5] for r in s if r[5] is not None), default=None)
            # Kinematics describe, identity decides: a fixed-wing drone flies
            # like a light aircraft, so speed, height and a take-off roll are
            # shown but never turn a declared drone into a crewed aircraft.
            if v90 <= 70 and (agl90 != agl90 or agl90 <= 200) and (roll is None or roll <= 30):
                shape = "multirotor-like"
            elif v50 >= 60 or (roll is not None and roll > 50):
                shape = "fixed-wing-like"
            else:
                shape = "unclear"
            ids = []
            if addr in adsb:
                ids.append("ADS-B")
            if "ICA" in prefixes.get(addr, ()):
                ids.append("ICAO-address")
            model = models.get(addr)
            out.append((shape, hashlib.sha1((SALT + addr).encode()).hexdigest()[:8],
                        "+".join(sorted(drone_addr[addr])), dict(cats), len(s), dur, v50, v90, agl50, agl90, ext,
                        roll, climb, f"{round(s[0][1])},{round(s[0][2])}", ids, model))
    out.sort(key=lambda x: (x[0], -x[6]))
    model_n = collections.Counter(x[15] or "-" for x in out)
    print(f"addresses declaring 13 or on Remote ID: {len(drone_addr)}; sessions of 10+ fixes: {len(out)}")
    # The model is printed only where three or more sessions share it, so a
    # rare aircraft cannot be picked out by model, place and day together.
    print("shape            hash     systems          fixes   min  v50  v90  agl50  agl90  ext_km roll climb_fpm start   identity / model / categories")
    for v, hsh, systems, cats, n, dur, v50, v90, a50, a90, ext, roll, climb, start, why, model in out:
        shown = model if model and model_n[model] >= 3 else ("(rare model)" if model else "-")
        print(f"{v:16s} {hsh} {systems[:16]:16s} {n:6d} {dur:5.0f} {v50:4.0f} {v90:4.0f} {a50:6.0f} {a90:6.0f} {ext:7.1f} "
              f"{'-' if roll is None else f'{roll:.0f}':>4} {'-' if climb is None else climb:>6} {start:7s} {','.join(why) or '-'} / {shown} / {cats}")
    print("models (sessions):", ", ".join(f"{m} {n}" for m, n in model_n.most_common() if n >= 3 or m == "-"),
          f"+ {sum(1 for m, n in model_n.items() if n < 3 and m != '-')} rare models")
    s = collections.Counter(x[0] for x in out)
    rare = sorted({x[15] for x in out if x[15] and model_n[x[15]] < 3})
    print("rare models, unlinked from sessions:", "; ".join(rare))
    hours = collections.Counter()
    for x in out:
        hours[x[0]] += x[5] / 60
    print("summary:", {k: (n, round(hours[k], 1)) for k, n in s.items()})


if __name__ == "__main__":
    main()
