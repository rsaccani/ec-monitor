#!/usr/bin/env python3
"""Fill daily_station_rose from the raw recording (METHOD.md 10.7, 25 October 2026).

    python3 tools/station_rose_backfill.py --from 2026-10-19 --to 2026-10-25 [--dry-run]

Run from the checkout, with EC_RAW_DIR set as for nightly.py. One day at a time it reads the
24 hourly files (and the first minutes of the next day's, as nightly.py does), keeps the direct
airborne radio receptions with the nightly's own tests, and accumulates them in rose.Rose.
It does not run the whole nightly: the pass has no vote of categories, no chaining of random
addresses and no per-stream drop, none of which changes a reception's distance or bearing, and
it does not look at the DDB's no-track list (a rose holds no address). Station positions are
read from `&` beacons as nightly.station_position does, after seeding them from daily_stations
of the same day when the table has it, so that a fix heard before the station's first beacon
of the day still counts.

A day is replaced in one transaction (delete, then insert), and rows older than 30 days before
the last day written are deleted. A dry run reads, prints the figures and writes nothing.
"""
import argparse
import datetime
import logging
import os
import resource
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import nightly                      # noqa: E402
import recorder                     # noqa: E402
import rose                         # noqa: E402
import sources                      # noqa: E402

logger = logging.getLogger("station_rose")
KEEP_DAYS = 30
# Garbage streams (ec-monitor notes, item 0i): an address whose positions jump hundreds of km every
# few seconds at a flat signal. FLR18008D, heard only by Stallikon, alone stretches its reach to 380 km.
# A list until the generic rule exists.
GARBAGE = {"FLR18008D"}


class Positions:
    """What Nightly.station_position needs of a Nightly, and nothing else."""

    def __init__(self, d0):
        self.d0, self.stations, self.s, self.failed = d0, {}, True, {}

    def seed(self, rows):
        for name, lat, lon in rows:
            st = self.stations[name] = nightly.Station(self.d0)
            st.lat, st.lon = float(lat), float(lon)


def seed_rows(day):
    try:
        conn = nightly.connect_db()
    except Exception as e:
        logger.warning("No database for the station positions of the day: %s", e)
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, lat, lon FROM daily_stations WHERE day = %s AND lat IS NOT NULL",
                        (day.isoformat(),))
            return cur.fetchall()
    except Exception as e:
        logger.warning("daily_stations not read: %s", e)
        return []
    finally:
        conn.close()


def one_day(raw_dir, day):
    d0 = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp())
    d1 = d0 + 86400
    pos = Positions(d0)
    pos.seed(seed_rows(day))
    r = rose.Rose()
    files = [(nightly.hour_path(raw_dir, day, h), None) for h in range(24)]
    nxt = nightly.hour_path(raw_dir, day + datetime.timedelta(days=1), 0)
    if nxt is not None:
        files.append((nxt, d1 + nightly.STALE + 1))
    gate = sources.FeedGate(sources.load_exclusions())
    missing = [h for h, (p, _) in enumerate(files[:24]) if p is None]
    fed = lines = 0
    for path, stop in files:
        if path is None:
            continue
        for epoch, line in nightly.gated(nightly.read_lines(path, stop), gate):
            lines += 1
            fed += handle(pos, r, d0, d1, epoch, line)
        logger.info("%s: %s lines, %s receptions, %.0f MB", os.path.basename(path), f"{lines:,}", f"{fed:,}",
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    return r, pos, fed, missing


def handle(pos, r, d0, d1, epoch, line):
    """Nightly.handle, reduced to what a reception of the rose needs; 1 when it fed the rose."""
    if line == sources.FEED_BREAK:
        return 0
    if line.split(">", 1)[0] in GARBAGE:
        return 0
    try:
        src, rest = line.split(">", 1)
        head, body = rest.split(":", 1)
    except ValueError:
        return 0
    if nightly.EUROPE_ONLY and recorder.outside_europe(body):
        return 0
    if not body.startswith("/"):
        return 0
    path = head.split(",")
    raw_tc = path[0]
    in_day = d0 <= epoch < d1
    if raw_tc == "OGNSDR" or raw_tc == "OGNSXR":
        if in_day:
            nightly.Nightly.station_position(pos, epoch, src, body)
        return 0
    if body[26:27] == "&" and raw_tc in nightly.STATION_TOCALLS and in_day and " id" not in body:
        nightly.Nightly.station_position(pos, epoch, src, body)
    if body[26:27] == "&" or raw_tc in sources.EXCLUDED or body[26:27] == "_":
        return 0
    tocall = sources.packet_system(path, src)
    id_category, no_track = sources.id_info(body)
    if no_track:
        return 0
    if id_category is None and tocall in sources.AIRCRAFT_ONLY:
        return 0
    if sources.rebroadcast(tocall, src, id_category):
        return 0
    label, kind = sources.source_info(tocall)
    if body[1:7] == "______" or "h" not in body[:8]:
        return 0
    if body[8:10] == "00" and body[17:20] == "000":
        return 0
    m = sources._position.match(body)
    if not m:
        return 0
    hh, mi, ss, latd, latm, ns, _table, lond, lonm, ew, symbol, course, speed = m.groups()
    via = "radio" if kind == "adsb" or sources._radio_meta.search(body) else "net"
    if via != "radio":
        return 0
    receiver = path[-1]
    st = pos.stations.get(receiver)
    if st is None or st.lat is None:
        return 0
    latm, lonm = float(latm), float(lonm)
    w = sources._extra_precision.search(body)
    if w:
        latm += int(w.group(1)) / 1000
        lonm += int(w.group(2)) / 1000
    lat = int(latd) + latm / 60
    lon = int(lond) + lonm / 60
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon
    a = sources._alt.search(body)
    alt_m = int(a.group(1)) * 0.3048 if a else None
    kt = int(speed) if speed is not None else None
    if tocall == nightly.REMOTE_ID:
        category = nightly.DRONE
    elif id_category is not None:
        category = id_category
    else:
        category = sources.SYMBOL_CATEGORY.get(symbol, sources.UNKNOWN_CATEGORY)
    sod = int(hh) * 3600 + int(mi) * 60 + int(ss)
    now = int(epoch)
    t = now - now % 86400 + sod
    if t - epoch > nightly.STALE:
        t -= 86400
    if epoch - t > nightly.STALE or not (d0 <= t < d1):
        return 0
    if sources.implausible(category, kt, alt_m) or sources.impossible_fix(category, kt, alt_m):
        return 0
    if (kt or 0) < sources.FLYING_KT.get(category, sources.AIRBORNE_KT):
        return 0                        # not airborne, by the packet's own category and speed
    snr = nightly._FIX_SNR.search(body)
    r.feed(receiver, tocall, st.lat, st.lon, lat, lon, float(snr.group(1)) if snr else None)
    return 1


def write(day, rows):
    conn = nightly.connect_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM daily_station_rose WHERE day = %s", (day.isoformat(),))
            if rows:
                cur.executemany("INSERT INTO daily_station_rose (day, station, source, n_total, payload) "
                                "VALUES (%s, %s, %s, %s, %s)", rows)
            cur.execute("DELETE FROM daily_station_rose WHERE day < %s",
                        ((day - datetime.timedelta(days=KEEP_DAYS)).isoformat(),))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="first", required=True)
    ap.add_argument("--to", dest="last", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--top", type=int, default=0, help="also print the sectors of the N stations with most receptions")
    ap.add_argument("--show", default="", help="comma-separated station names whose sectors are printed")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    raw_dir = os.path.expanduser(os.getenv("EC_RAW_DIR") or "")
    if not os.path.isdir(raw_dir):
        logger.error("EC_RAW_DIR is not set or not a directory")
        return 1
    day, last = datetime.date.fromisoformat(args.first), datetime.date.fromisoformat(args.last)
    while day <= last:
        t0 = time.time()
        r, pos, fed, missing = one_day(raw_dir, day)
        rows = r.rows(day.isoformat())
        pairs = len(r.blocks)
        logger.info("%s: %s receptions, %d station-source pairs, %d rows, missing hours %s, %.0f s, peak %.0f MB",
                    day, f"{fed:,}", pairs, len(rows), missing or "none", time.time() - t0,
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        names = list(filter(None, args.show.split(",")))
        names += [row[1] for row in sorted(rows, key=lambda x: -x[3])[:args.top]]
        for name in names:
            for row in rows:
                if row[1] == name:
                    print(f"== {day} {name} {row[2]} n={row[3]}")
                    for sec in rose.combine([row[4]]):
                        if sec["n"]:
                            print(f"   {sec['sector'] * 10:3d} deg  n={sec['n']:7d}  p95={sec['p95_km']}  snr10={sec['snr10']}")
        if missing:
            logger.warning("%s has hours missing from the recording; the rose of that day is partial", day)
        if not args.dry_run:
            write(day, rows)
            logger.info("%s: written", day)
        day += datetime.timedelta(days=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
