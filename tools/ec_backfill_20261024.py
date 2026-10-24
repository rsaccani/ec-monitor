#!/usr/bin/env python3
"""Take the feed outage of 24 October 2026 out of the stored totals.

Written on 2026-10-24. Between about 05:30 and 08:20 UTC the service read the
feed too slowly and the OGN server dropped the connection 137 times. The
tracker had no notion of a feed interruption, so the time the feed was cut
counted as aircraft losing signal (METHOD.md, "When the feed is cut"). The new
sources.py ends every track at a feed break and ignores the window listed in
data/feed-exclusions.txt; the totals the old code stored for that day still
include the outage. This script, on the pattern of ec_backfill_20261006.py:

1. replays the day's recording through sources.py as a worker that knows no
   feed break (no exclusion, markers ignored): what the live service stored;
2. replays the same lines through sources.py as it is now, with the exclusion
   and the markers;
3. takes new minus old, per key and column, of monthly_visibility_detail,
   monthly_visibility_grid and monthly_hours, whose columns are doubles summed
   on duplicate keys, so that adding the difference leaves the month as if the
   new rules had run all day.

daily_visibility has unsigned counters and is append-only, so the difference
of its columns is only reported. The reception pattern and the predictions are
reported, not changed, as in the backfill of 6 October. Both replays start at
the same instant with no track open, so what they cannot know about the time
before cancels out of the difference. Sessions are pruned every 15 minutes of
recording time, as the service's flush does, so that disappearances are counted
when the service would have counted them.

Default mode reads the recording and prints aggregates only (row counts and
summed differences per table and column, never an address) and writes nothing.
--apply adds the differences to the database in one transaction and refuses to
run twice (a marker file beside the recording).

--to is the instant the new code started running live (UTC, on 24 October):
from then on the live totals are already right and must not be corrected. The
recording must hold the hours up to it.

Usage: (cd <checkout> && nice -n 19 python3 tools/ec_backfill_20261024.py --to HH:MM [--from HH:MM] [--apply])
"""
import argparse
import calendar
import collections
import datetime
import glob
import os
import sys

import ec_replay_airborne as base
import ec_replay_versions as versions

DAY = "2026-10-24"
RAW_DIR = os.path.expanduser(os.getenv("EC_RAW_DIR", "~/ec-raw"))
MARKER = os.path.join(RAW_DIR, "backfill-20261024.done")
PRUNE_EVERY = 900                   # sources.VISIBILITY_FLUSH
TABLES = ("monthly_visibility_detail", "monthly_visibility_grid", "monthly_hours")
TOTALS_COLUMNS = ("packets", "rot_packets", "segments", "sessions", "implausible", "air_seconds",
                  "p0_300", "p0_1000", "p0_3000", "p1_300", "p1_1000", "p1_3000")


def epoch(hhmm):
    h, m = hhmm.split(":")
    return calendar.timegm(datetime.date.fromisoformat(DAY).timetuple()) + int(h) * 3600 + int(m) * 60


def day_files(start, stop):
    """The hourly files from the hour of `start` to the hour before `stop`, the
    uncompressed one while it exists, else the compressed one."""
    paths = []
    for hour in range(int(start // 3600 % 24), int((stop - 1) // 3600 % 24) + 1):
        base_path = os.path.join(RAW_DIR, DAY.replace("-", "") + "-%02d.aprs" % hour)
        for p in (base_path, base_path + ".zst"):
            if os.path.exists(p):
                paths.append(p)
                break
        else:
            sys.exit("the recording has no file for hour %02d" % hour)
    return paths


def replay(mod, paths, start, stop, markers):
    """Feed the lines of [start, stop) to a new tracker of `mod`; return it."""
    tr = mod.SourceTracker(lambda line: None, lambda: None, lambda s: False)
    if not markers:
        tr.gate = mod.FeedGate([])  # the worker the service ran until this day: no exclusion
    next_prune = None
    for line in base.lines(paths):
        stamp, _, raw = line.partition(" ")
        try:
            t = float(stamp)
        except ValueError:
            continue
        if t < start:
            continue
        if t >= stop:
            break
        if next_prune is None:
            next_prune = start + PRUNE_EVERY
        while t >= next_prune:
            base.clock[0] = next_prune
            tr.prune_sessions()
            next_prune += PRUNE_EVERY
        base.clock[0] = t
        if raw == base.FEED_BREAK:
            if markers:
                tr.feed_break(t)
            continue
        tr.handle(raw)
    return tr


def tables_of(tr):
    return {"daily_visibility": tr.totals, TABLES[0]: tr.detail, TABLES[1]: tr.grid, TABLES[2]: tr.hours,
            "monthly_reception_pattern": tr.pattern, "monthly_prediction": tr.prediction}


def difference(old, new):
    """key -> new minus old, for the keys where some column moved by more than 0.05."""
    out = {}
    for k in set(old) | set(new):
        a, b = old.get(k), new.get(k)
        width = len(a or b)
        v = [(b[i] if b else 0.0) - (a[i] if a else 0.0) for i in range(width)]
        if any(abs(x) > 0.05 for x in v):
            out[k] = [round(x, 1) for x in v]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--to", required=True, help="HH:MM UTC: when the new code went live; the replay stops there")
    ap.add_argument("--from", dest="start", default="00:00", help="HH:MM UTC to start replaying (default 00:00)")
    ap.add_argument("--apply", action="store_true", help="write the differences to the database")
    args = ap.parse_args()
    start, stop = epoch(args.start), epoch(args.to)
    if stop <= start:
        sys.exit("--to must be after --from")
    if args.apply and os.path.exists(MARKER):
        sys.exit("already applied: " + MARKER)
    paths = day_files(start, stop)
    here = os.path.join(base.CHECKOUT, "sources.py")

    old_mod = versions.load("old", here)
    old = tables_of(replay(old_mod, paths, start, stop, markers=False))
    new_mod = versions.load("new", here)
    new_tr = replay(new_mod, paths, start, stop, markers=True)
    new = tables_of(new_tr)
    if not new_mod.load_exclusions():
        print("warning: data/feed-exclusions.txt lists no window; only the feed breaks in the recording count")

    delta = {name: difference(old[name], new[name]) for name in old}
    columns = {"daily_visibility": TOTALS_COLUMNS, TABLES[0]: new_mod.DETAIL_COLUMNS, TABLES[1]: new_mod.GRID_COLUMNS,
               TABLES[2]: ("segments", "air_seconds"), "monthly_reception_pattern": ("packets", "snr_sum", "snr_n"),
               "monthly_prediction": new_mod.PREDICTION_COLUMNS}
    print("replayed %s %s to %s UTC from %d files" % (DAY, args.start, args.to, len(paths)))
    for name in ("daily_visibility",) + TABLES + ("monthly_reception_pattern", "monthly_prediction"):
        rows = delta[name]
        sums = collections.Counter()
        for v in rows.values():
            for c, x in zip(columns[name], v):
                sums[c] += x
        note = "" if name in TABLES else "  (reported, not written)"
        print("%s: %d rows differ%s" % (name, len(rows), note))
        for c in columns[name]:
            if abs(sums[c]) > 0.05:
                print("    %-14s %+.1f" % (c, sums[c]))
    if not args.apply:
        print("dry run: nothing written")
        return

    import dotenv
    import pymysql
    env = dotenv.dotenv_values(os.path.join(base.CHECKOUT, ".env"))
    conn = pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                           database="ads_l", autocommit=False)
    sqls = {TABLES[0]: new_mod.DETAIL_SQL, TABLES[1]: new_mod.GRID_SQL, TABLES[2]: new_mod.HOURS_SQL}
    try:
        with conn.cursor() as cur:
            for name in TABLES:
                cur.executemany(sqls[name], [k + tuple(v) for k, v in delta[name].items()])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    with open(MARKER, "w") as f:
        f.write("applied %s\n" % datetime.datetime.now(datetime.timezone.utc).isoformat())
    print("applied")


if __name__ == "__main__":
    main()
