#!/usr/bin/env python3
"""Bring sig_* since 06:07:56 UTC on 6 October 2026 under the evening's fixes.

One-off, written on 2026-10-06 (ec-monitor.md). The deploy of that evening
changed three things that sig_* depend on: radio per aircraft judged by the
longest interval among the systems heard (it was the shortest), the
free-flight airborne threshold at 8 kt (15/1.852 kt let only 9 kt pass), and
an implausible fix ending the per-aircraft radio track. This replays the raw
recording from 06:07:56 to that deploy through the old and the new sources.py,
each as one uninterrupted worker, and adds new minus old to sig_air and
sig_late only (every other column gets zero), in monthly_visibility_detail
and monthly_visibility_grid. Europe only, as recorded. Run with --dry-run
first; --write refuses to run twice.

Usage: python3 ec_backfill_fixes_20261006.py old.py new.py "deploy time" (--dry-run | --write)
"""
import datetime
import glob
import os
import sys

import pymysql

import ec_backfill_20261006 as bf
import ec_backfill_sig_20261006 as sig

START = "2026-10-06 06:07:56"
MARKER = os.path.expanduser("~/ec-replay/backfill-fixes-20261006.done")


def sig_rows(out, mod):
    """{table: {key: [sig_air, sig_late]}} from a run's captured rows."""
    res = {}
    for t, cols in ((bf.TABLES[0], mod.DETAIL_COLUMNS), (bf.TABLES[1], mod.GRID_COLUMNS)):
        ia, il = cols.index("sig_air"), cols.index("sig_late")
        res[t] = {k: [v[ia], v[il]] for k, v in out[t].items() if v[ia] or v[il]}
    return res


def main():
    old_path, new_path, deploy, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    if mode == "--write" and os.path.exists(MARKER):
        sys.exit("already written: " + MARKER)
    paths = sorted(glob.glob(os.path.expanduser("~/ec-raw/20261006-*.aprs.zst")) +
                   glob.glob(os.path.expanduser("~/ec-raw/20261006-*.aprs")))
    runs = {}
    for name, path in (("old", old_path), ("new", new_path)):
        mod = bf.load(name, path)
        out, _, _ = sig.run_until(mod, bf.epoch(START), bf.epoch(deploy), paths, None)
        runs[name] = (mod, sig_rows(out, mod))
    mod = runs["new"][0]
    rows = {}
    summary = []
    for t, cols, nkeys in ((bf.TABLES[0], mod.DETAIL_COLUMNS, 6), (bf.TABLES[1], mod.GRID_COLUMNS, 5)):
        ia, il = cols.index("sig_air"), cols.index("sig_late")
        o, n = runs["old"][1][t], runs["new"][1][t]
        rows[t] = []
        for k in set(o) | set(n):
            a = n.get(k, [0, 0])[0] - o.get(k, [0, 0])[0]
            l = n.get(k, [0, 0])[1] - o.get(k, [0, 0])[1]
            if abs(a) < 0.05 and abs(l) < 0.05:
                continue
            vals = [0.0] * len(cols)
            vals[ia], vals[il] = round(a, 1), round(l, 1)
            rows[t].append(k + tuple(vals))
        if t == bf.TABLES[0]:
            for label, test in (("radio per aircraft, free flight", lambda k: k[1] == mod.AIRCRAFT and k[3] in (6, 7)),
                                ("radio per aircraft, all", lambda k: k[1] == mod.AIRCRAFT),
                                ("apps and per-system radio, free flight", lambda k: k[1] != mod.AIRCRAFT and k[3] in (6, 7))):
                for name in ("old", "new"):
                    d = runs[name][1][t]
                    air = sum(v[0] for k, v in d.items() if test(k))
                    late = sum(v[1] for k, v in d.items() if test(k))
                    summary.append("%-40s %s: %7.0f h judged, %5.1f%% without signal" % (
                        label, name, air / 3600, 100 * late / air if air else 0))
    print("%s to %s UTC" % (START, deploy))
    print("\n".join(summary))
    print("rows to write: detail %d, grid %d" % (len(rows[bf.TABLES[0]]), len(rows[bf.TABLES[1]])))
    if mode != "--write":
        print("dry run: nothing written")
        return
    import dotenv
    env = dotenv.dotenv_values(os.path.expanduser("~/ads-l-map/.env"))
    conn = pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                           database="ads_l", autocommit=False)
    with conn.cursor() as cur:
        cur.executemany(mod.DETAIL_SQL, rows[bf.TABLES[0]])
        cur.executemany(mod.GRID_SQL, rows[bf.TABLES[1]])
    conn.commit()
    open(MARKER, "w").write("written %s\n" % datetime.datetime.utcnow().isoformat())
    print("written")


if __name__ == "__main__":
    main()
