#!/usr/bin/env python3
"""Fill sig_air and sig_late, and radio per aircraft, from 06:07:56 UTC on 6 October 2026.

One-off, written on 2026-10-06 (ec-monitor.md). The columns hold the time
without signal under the rules that took effect at 11:00 UTC that day; the
service fills them from the moment the worker that knows them boots. This
replays the raw recording from its start (06:07:56) to that boot through the
same sources.py as one uninterrupted worker, and adds only those two columns
to monthly_visibility_detail and monthly_visibility_grid (every other column
gets zero, so the upsert leaves it as it is). Positions are those recorded,
Europe only. Run with --dry-run first; --write refuses to run twice.

The per-system columns went live with the worker booted at the first time
given (12:16:02); radio followed per aircraft (pseudo-source AIRCRAFT, grid
group "aircraft") with the one booted at the second (13:17:13). So per-system
rows are taken up to the first time and per-aircraft rows up to the second.

Usage: python3 ec_backfill_sig_20261006.py sources.py "sig boot" "aircraft boot" (--dry-run | --write)
"""
import datetime
import glob
import os
import sys

import pymysql

import ec_backfill_20261006 as bf
import ec_replay_airborne as base

START = "2026-10-06 06:07:56"
MARKER = os.path.expanduser("~/ec-replay/backfill-sig-20261006.done")


def run_until(mod, start, end, paths, tr):
    """Feed recorded lines with start <= t < end; flush at end. Reuses tr if given."""
    out = {"daily": {}, bf.TABLES[0]: {}, bf.TABLES[1]: {}, bf.TABLES[2]: {}}
    sink = []
    if tr is None:
        tr = mod.SourceTracker(lambda line: None, lambda: bf.FakeConn(sink), lambda s: False)
    else:
        tr.connect_db = lambda: bf.FakeConn(sink)
    next_flush = start + 900
    for line in base.lines(paths):
        stamp, _, raw = line.partition(" ")
        try:
            t = float(stamp)
        except ValueError:
            continue
        if t < start:
            continue
        if t >= end:
            break
        while t >= next_flush:
            base.clock[0] = next_flush
            tr.visibility_loop()
            bf.collect(sink, out)
            next_flush += 900
        base.clock[0] = t
        tr.handle(raw)
    base.clock[0] = end
    tr.visibility_loop()
    bf.collect(sink, out)
    return out, tr, sink


def main():
    path, boot, boot2, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    if mode == "--write" and os.path.exists(MARKER):
        sys.exit("already written: " + MARKER)
    paths = sorted(glob.glob(os.path.expanduser("~/ec-raw/20261006-*.aprs.zst")) +
                   glob.glob(os.path.expanduser("~/ec-raw/20261006-*.aprs")))
    mod = bf.load("sig", path)
    first, tr, sink = run_until(mod, bf.epoch(START), bf.epoch(boot), paths, None)
    second, tr, sink = run_until(mod, bf.epoch(boot), bf.epoch(boot2), paths, tr)
    rows = {}
    for t, cols in ((bf.TABLES[0], mod.DETAIL_COLUMNS), (bf.TABLES[1], mod.GRID_COLUMNS)):
        ia, il = cols.index("sig_air"), cols.index("sig_late")
        rows[t] = []
        merged = {}
        for part, upto in ((first, "both"), (second, "aircraft")):
            for k, v in part[t].items():
                per_aircraft = mod.AIRCRAFT in k or "aircraft" in k
                if upto == "aircraft" and not per_aircraft:
                    continue
                acc = merged.setdefault(k, [0.0, 0.0])
                acc[0] += v[ia]
                acc[1] += v[il]
        for k, (a, l) in merged.items():
            v = [0.0] * len(cols)
            v[ia], v[il] = a, l
            if v[ia] <= 0:
                continue
            vals = [0.0] * len(cols)
            vals[ia], vals[il] = round(v[ia], 1), round(v[il], 1)
            rows[t].append(k + tuple(vals))
    det, grid = rows[bf.TABLES[0]], rows[bf.TABLES[1]]
    da, dl = 6 + mod.DETAIL_COLUMNS.index("sig_air"), 6 + mod.DETAIL_COLUMNS.index("sig_late")
    air, late = sum(r[da] for r in det), sum(r[dl] for r in det)
    gi = 5 + mod.GRID_COLUMNS.index("sig_air")
    pga = sum(r[gi] for r in grid if r[3] == "pga")
    ad = sum(r[da] for r in det if r[1] == mod.AIRCRAFT)
    al = sum(r[dl] for r in det if r[1] == mod.AIRCRAFT)
    print("per aircraft, %s to %s UTC: %.0f h judged, %.1f%% without signal" % (START, boot2, ad / 3600, 100 * al / ad if ad else 0))
    print("%s to %s UTC: detail %d rows, %.0f h judged, %.1f%% without signal; grid %d rows, pga %.0f h" % (
        START, boot, len(det), air / 3600, 100 * late / air if air else 0, len(grid), pga / 3600))
    if mode != "--write":
        print("dry run: nothing written")
        return
    import dotenv
    env = dotenv.dotenv_values(os.path.expanduser("~/ads-l-map/.env"))
    conn = pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                           database="ads_l", autocommit=False)
    with conn.cursor() as cur:
        cur.executemany(mod.DETAIL_SQL, det)
        cur.executemany(mod.GRID_SQL, grid)
    conn.commit()
    open(MARKER, "w").write("written %s\n" % datetime.datetime.utcnow().isoformat())
    print("written")


if __name__ == "__main__":
    main()
