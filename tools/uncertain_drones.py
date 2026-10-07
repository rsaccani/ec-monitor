#!/usr/bin/env python3
"""What are the declared drones nightly.py leaves uncertain? (7 October 2026)

Runs nightly.Nightly over one day, read only, for its classes, encounters,
thermals and systems, then reads the day again keeping the fixes of the
uncertain addresses only, and profiles each: systems, address type (callsign
prefix), OGN device database type, share of packets declaring 13, whether
it sat still (a test bench), ground speed, height above the terrain model,
extent, take-off roll, hovering, 1-degree region, encounters, and the
multirotor / fixed-wing shape of tools/drone_check.py. Groups them into
plausible kinds with counts and air hours.

Prints aggregates and salted hashes only; positions to 1 degree.

Usage: (cd ~/ads-l-map && nice -n 19 python3 tools/uncertain_drones.py 2026-10-06)
"""
import collections
import datetime
import hashlib
import math
import os
import secrets
import statistics
import sys

sys.path.insert(0, ".")
import nightly  # noqa: E402
import sources  # noqa: E402

SALT = secrets.token_hex(8)
BENCH_M, BENCH_S = 300, 3600
DDB_NAMES = {1: "glider", 2: "plane", 3: "ultralight", 4: "helicopter", 5: "drone", 6: "other"}


def h(a):
    return hashlib.sha1((SALT + a).encode()).hexdigest()[:8]


def pct(v, q):
    v = sorted(v)
    return v[int(q * (len(v) - 1))] if v else None


def main():
    day = datetime.date.fromisoformat(sys.argv[1])
    raw = os.path.expanduser(os.getenv("EC_RAW_DIR", "~/ec-raw"))
    n = nightly.Nightly(day, nightly.load_ddb())
    n.run(raw)
    tables = n.rows()
    ev = n.evidence()
    unc = {a for a, e in ev.items() if e == nightly.UNCERTAIN}
    print(f"classes: {dict(collections.Counter(nightly.EVIDENCE_NAMES[e] for e in ev.values()))}")
    air = collections.Counter()
    for (_, _, _, _, a), s in n.drone_air.items():
        air[a] += s
    enc = collections.Counter(d for d, _, _, _ in n.encounters)
    thermals = collections.Counter()
    for (a, _), tr in n.circles.items():
        if a in unc:
            thermals[a] += len(tr.thermals)

    # second pass: the uncertain addresses' fixes
    fixes = collections.defaultdict(list)
    prefixes = collections.defaultdict(set)
    d0, d1 = n.d0, n.d1
    for h_ in range(25):
        p = nightly.hour_path(raw, day + datetime.timedelta(days=h_ // 24), h_ % 24)
        if p is None:
            continue
        for epoch, line in nightly.read_lines(p, d1 + 301 if h_ == 24 else None):
            src = line[:line.find(">")]
            a = src[-6:]
            if a not in unc:
                continue
            try:
                _, rest = line.split(">", 1)
                head, body = rest.split(":", 1)
            except ValueError:
                continue
            m = sources._position.match(body)
            if not m:
                continue
            g = m.groups()
            sod = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2])
            now = int(epoch)
            t = now - now % 86400 + sod
            if t - epoch > 300:
                t -= 86400
            if epoch - t > 300 or not d0 <= t < d1:
                continue
            lat = int(g[3]) + float(g[4]) / 60
            lon = int(g[7]) + float(g[8]) / 60
            if g[5] == "S":
                lat = -lat
            if g[9] == "W":
                lon = -lon
            alt = sources._alt.search(body)
            prefixes[a].add(src[:3])
            fixes[a].append((t, lat, lon, int(alt.group(1)) * 0.3048 if alt else None,
                             int(g[12]) * 1.852 if g[12] else 0.0))

    rows = []
    for a in unc:
        f = sorted(set(fixes.get(a, [])))
        own = n.addr_systems.get(a, set())
        relayed = n.addr_platforms.get(a, set())
        c = n.addr_cats.get(a, collections.Counter())
        share13 = c[13] / sum(c.values()) if c else None
        moving = [x[4] for x in f if x[4] >= 10]
        v50, v90 = (statistics.median(moving) if moving else 0.0), (pct(moving, 0.9) or 0.0)
        agl = []
        for x in f[::max(1, len(f) // 300)]:
            if x[3] is not None:
                gr = n.terrain.elevation(x[1], x[2])
                if gr is not None:
                    agl.append(x[3] - gr)
        a50, a90 = (statistics.median(agl) if agl else None), pct(agl, 0.9)
        if f:
            la, lo = [x[1] for x in f], [x[2] for x in f]
            spread = nightly.dist(min(la), min(lo), max(la), max(lo))
            dur = f[-1][0] - f[0][0]
        else:
            spread, dur = 0, 0
        roll = None
        for j, x in enumerate(f):
            if x[3] is None or j == 0:
                continue
            gr = n.terrain.elevation(x[1], x[2])
            if gr is not None and x[3] - gr > 30:
                before = [q[4] for q in f[max(0, j - 5):j]]
                roll = max(before) if before else None
                break
        high = [(x, x[3] - n.terrain.elevation(x[1], x[2])) for x in f[::max(1, len(f) // 300)]
                if x[3] is not None and n.terrain.elevation(x[1], x[2]) is not None]
        high = [x for x, g_ in high if g_ > 20]
        hover = sum(1 for x in high if x[4] < 5) / len(high) if high else None
        if v90 <= 70 and (a90 is None or a90 <= 200) and (roll is None or roll <= 30):
            shape = "multirotor-like"
        elif v50 >= 60 or (roll is not None and roll > 50):
            shape = "fixed-wing-like"
        else:
            shape = "unclear"
        if not own and relayed:
            group = "platform-relayed"
        elif f and spread < BENCH_M and dur >= BENCH_S:
            group = "bench (still for an hour or more)"
        elif thermals[a]:
            group = "circles in thermals"
        else:
            group = shape
        ddb = n.ddb_types.get(a)
        rows.append({"hash": h(a), "group": group, "shape": shape, "air_h": air[a] / 3600,
                     "systems": " + ".join(sorted(own)) or ("(" + " + ".join(sorted(relayed)) + ")"),
                     "prefix": "+".join(sorted(prefixes.get(a, ()))) or "-",
                     "ddb": DDB_NAMES.get(ddb, "none") if ddb is not None else "none",
                     "share13": share13, "fixes": len(f), "v50": v50, "v90": v90, "a50": a50, "a90": a90,
                     "extent_km": n.drone_extent.get(a, 0) / 1000, "roll": roll, "hover": hover,
                     "spread_m": spread, "dur_h": dur / 3600, "thermals": thermals[a], "enc": enc[a],
                     "region": f"{math.floor(f[0][1])},{math.floor(f[0][2])}" if f else "-"})

    def fmt(v, spec):
        return "-" if v is None else format(v, spec)
    print(f"\nuncertain addresses: {len(rows)}, air {sum(r['air_h'] for r in rows):.1f} h, "
          f"in encounters: {sum(1 for r in rows if r['enc'])} ({sum(r['enc'] for r in rows)} encounters)")
    print("hash     group                             systems                     pfx      ddb        13%  fixes "
          "air_h  v50  v90  agl50 agl90  ext_km roll hover therm enc region")
    for r in sorted(rows, key=lambda r: (r["group"], -r["air_h"])):
        print(f"{r['hash']} {r['group'][:33]:33s} {r['systems'][:27]:27s} {r['prefix'][:8]:8s} {r['ddb']:10s} "
              f"{fmt(r['share13'] and 100 * r['share13'], '3.0f'):>4} {r['fixes']:6d} {r['air_h']:5.2f} "
              f"{r['v50']:4.0f} {r['v90']:4.0f} {fmt(r['a50'], '5.0f'):>6} {fmt(r['a90'], '5.0f'):>5} "
              f"{r['extent_km']:7.1f} {fmt(r['roll'], '4.0f'):>4} {fmt(r['hover'] and 100 * r['hover'], '3.0f'):>5} "
              f"{r['thermals']:5d} {r['enc']:3d} {r['region']}")
    print("\nby group: addresses, air hours, encounters; systems; prefixes; DDB")
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    for g, rs in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"  {g:33s} {len(rs):3d} {sum(r['air_h'] for r in rs):6.1f} h  enc {sum(r['enc'] for r in rs)}  "
              f"systems {dict(collections.Counter(r['systems'] for r in rs).most_common(4))}  "
              f"prefix {dict(collections.Counter(r['prefix'] for r in rs))}  "
              f"ddb {dict(collections.Counter(r['ddb'] for r in rs))}")


if __name__ == "__main__":
    main()
