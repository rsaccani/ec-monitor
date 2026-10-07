#!/usr/bin/env python3
"""Recompute 6 October 2026, 06:07-11:00 UTC, under the rules deployed at 11:00.

One-off, written on 2026-10-06 (ec-monitor.md). The raw recording began at
06:07:56 UTC; the rules of METHOD.md changed at 11:00:05. In between the
service wrote its 15-minute totals under the old rules, minus what each restart
lost (workers booted at 06:07:56, 06:14:00 and 09:53:00). This script:

1. replays the recording through the old sources.py with those restarts and
   flushes, capturing the rows it would have written (the database is a fake);
2. checks them against what daily_visibility really holds for those flushes,
   source by source, so the replay is known to match the live service;
3. replays the same lines through the new sources.py as one uninterrupted
   worker, flushing every 15 minutes and once at 11:00:05;
4. writes new minus old to monthly_visibility_detail, monthly_visibility_grid
   and monthly_hours, whose columns are doubles summed on duplicate keys, so
   the October figures read as if the new rules had run since 06:07:56. The
   grid group "pga" exists only in the new code, so its rows are added whole.

daily_visibility (unsigned counters, append-only), the reception pattern and
the predictions are left alone. Run with --dry-run first; --write refuses to
run twice (marker file). Only aggregates are printed.

Usage: python3 ec_backfill_20261006.py old_sources.py new_sources.py (--dry-run | --write)
"""
import collections
import datetime
import glob
import os
import sys

import pymysql

import ec_replay_airborne as base
import ec_replay_versions as versions

BOOTS = ["2026-10-06 06:07:56", "2026-10-06 06:14:00", "2026-10-06 09:53:00"]
END = "2026-10-06 11:00:05"
MARKER = os.path.expanduser("~/ec-replay/backfill-20261006.done")
TABLES = ("monthly_visibility_detail", "monthly_visibility_grid", "monthly_hours")


def epoch(s):
    return (datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S") - datetime.datetime(1970, 1, 1)).total_seconds()


class FakeCursor:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def executemany(self, sql, rows):
        self.sink.append((sql, list(rows)))

    def execute(self, sql, args=None):
        self.sink.append((sql, [args]))


class FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def cursor(self):
        return FakeCursor(self.sink)


def load(name, path):
    src = open(path).read()
    a = "        while True:\n            time.sleep(VISIBILITY_FLUSH)\n"
    assert src.count(a) == 1, "visibility_loop changed: update the backfill"
    head, tail = src.split(a)
    body_end = tail.index("\n    def ")
    body = tail[:body_end].replace(
        "            if not totals and not detail and not grid and not pattern and not prediction and not hours:\n"
        "                continue\n",
        "            if not totals and not detail and not grid and not pattern and not prediction and not hours:\n"
        "                return\n")
    src = head + "        if True:\n" + body + tail[body_end:]
    tmp = os.path.expanduser("~/ec-replay/_sources_%s.py" % name)
    open(tmp, "w").write(src)
    return versions.load(name, tmp)


def table_of(sql):
    for t in TABLES + ("daily_visibility", "monthly_reception_pattern", "monthly_prediction"):
        if "INTO " + t in sql or "INTO " + t + " " in sql:
            return t
    return None


def collect(sink, into):
    """Sum captured upsert rows by key; daily_visibility by (source, via, category)."""
    for sql, rows in sink:
        t = table_of(sql)
        for r in rows:
            if t == "daily_visibility":
                k = (r[1], r[2], r[3])
                v = into["daily"].setdefault(k, [0.0, 0.0])
                v[0] += r[5]
                v[1] += r[10]
            elif t in TABLES:
                nkeys = {"monthly_visibility_detail": 6, "monthly_visibility_grid": 5, "monthly_hours": 2}[t]
                k, vals = tuple(r[:nkeys]), r[nkeys:]
                acc = into[t].setdefault(k, [0.0] * len(vals))
                for i, x in enumerate(vals):
                    acc[i] += x
    sink.clear()


def run(mod, boots, end, paths):
    """Feed the lines; a new tracker at each boot, a flush every 15 minutes from it."""
    out = {"daily": {}, TABLES[0]: {}, TABLES[1]: {}, TABLES[2]: {}}
    sink = []
    tr, next_flush, bi = None, None, 0
    for line in base.lines(paths):
        stamp, _, raw = line.partition(" ")
        try:
            t = float(stamp)
        except ValueError:
            continue
        if t >= end:
            break
        while bi < len(boots) and t >= boots[bi]:
            tr = mod.SourceTracker(lambda line: None, lambda: FakeConn(sink), lambda s: False)
            next_flush = boots[bi] + 900
            bi += 1
        if tr is None:
            continue
        while t >= next_flush:
            base.clock[0] = next_flush
            tr.visibility_loop()
            collect(sink, out)
            next_flush += 900
        base.clock[0] = t
        tr.handle(raw)
    return out, tr, sink


def main():
    old_path, new_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode == "--write" and os.path.exists(MARKER):
        sys.exit("already written: " + MARKER)
    paths = sorted(glob.glob(os.path.expanduser("~/ec-raw/20261006-0[6-9].aprs.zst")) +
                   glob.glob(os.path.expanduser("~/ec-raw/20261006-1[01].aprs.zst")))
    boots, end = [epoch(b) for b in BOOTS], epoch(END)

    old = load("old", old_path)
    o, _, _ = run(old, boots, end, paths)
    new = load("new", new_path)
    n, tr, sink = run(new, boots[:1], end, paths)
    base.clock[0] = end                     # the 11:00:05 restart: one last flush
    tr.visibility_loop()
    collect(sink, n)

    # 2. The replayed old service against the database.
    import dotenv
    env = dotenv.dotenv_values(os.path.expanduser("~/ads-l-map/.env"))
    conn = pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                           database="ads_l", autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT source, via, category, SUM(packets), SUM(air_seconds) FROM daily_visibility "
                    "WHERE flushed_at > %s AND flushed_at <= %s GROUP BY 1, 2, 3", (BOOTS[0], END))
        db = {(r[0], r[1], r[2]): (float(r[3]), float(r[4])) for r in cur.fetchall()}
    tot = lambda d, i: sum(v[i] for v in d.values())
    rp, ra = tot(o["daily"], 0), tot(o["daily"], 1)
    dp, da = sum(v[0] for v in db.values()), sum(v[1] for v in db.values())
    print("old replay vs database, 06:07:56-11:00:05 flushes: packets %d vs %d (%+.2f%%), air %.0f h vs %.0f h (%+.2f%%)" % (
        rp, dp, 100 * (rp - dp) / dp, ra / 3600, da / 3600, 100 * (ra - da) / da))
    by = collections.Counter()
    for k in set(db) | set(o["daily"]):
        by[k[0]] += abs(o["daily"].get(k, [0, 0])[1] - db.get(k, (0, 0))[1])
    print("largest differences in air time by source:", ", ".join("%s %.1f h" % (s, v / 3600) for s, v in by.most_common(5)))

    # 4. New minus old.
    delta = {}
    for t in TABLES:
        d = {}
        for k in set(o[t]) | set(n[t]):
            a, b = o[t].get(k), n[t].get(k)
            width = len(a or b)
            v = [(b[i] if b else 0.0) - (a[i] if a else 0.0) for i in range(width)]
            if any(abs(x) > 0.05 for x in v):
                d[k] = [round(x, 1) for x in v]
        delta[t] = d
    pga = sum(v[1] for k, v in delta[TABLES[1]].items() if k[3] == "pga")
    print("rows to write: detail %d, grid %d (pga %.0f h of free flight), hours %d" % (
        len(delta[TABLES[0]]), len(delta[TABLES[1]]), pga / 3600, len(delta[TABLES[2]])))
    for (month, cat), v in sorted(delta[TABLES[2]].items()):
        print("  hours, category %s: %+.1f h" % (cat, v[1] / 3600))
    if mode != "--write":
        print("dry run: nothing written")
        return
    sqls = {TABLES[0]: old.DETAIL_SQL, TABLES[1]: old.GRID_SQL, TABLES[2]: old.HOURS_SQL}
    with conn.cursor() as cur:
        for t in TABLES:
            cur.executemany(sqls[t], [k + tuple(v) for k, v in delta[t].items()])
    conn.commit()
    open(MARKER, "w").write("written %s\n" % datetime.datetime.utcnow().isoformat())
    print("written")


if __name__ == "__main__":
    main()
