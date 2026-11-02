#!/usr/bin/env python3
"""Fill daily_station_unique from the raw recording (METHOD.md 10.5, 2 November 2026).

    python3 tools/station_unique_backfill.py --from 2026-10-20 --to 2026-11-01 [--dry-run] [--dump FILE]

Run from the checkout, with EC_RAW_DIR set as for nightly.py. One day at a time it reads the 24
hourly files (and the first minutes of the next day's, as nightly.py does) and feeds the packets
the nightly's own tests let through to station_unique.Unique, the class the nightly uses. It does
not run the whole nightly: this pass has no vote of categories, no chaining of random addresses
(a FLARM device in random-address mode is counted under each address it used), no per-stream drop
and no garbage rule, so a day recomputed here can differ from the nightly's in a few keys. It does
not look at the DDB's no-track list either, which only removes aircraft.

A day is replaced in one transaction (delete, then insert). A dry run reads, prints the figures
and writes nothing; --dump writes the rows of every day to FILE, tab-separated, for comparison.
Memory is bounded by the aircraft airborne in five minutes: about 150 MB on a busy summer day.
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
import sources                      # noqa: E402
import station_unique               # noqa: E402

logger = logging.getLogger("station_unique")


def one_day(raw_dir, day):
    d0 = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp())
    d1 = d0 + 86400
    terrain = sources.Terrain(sources.dem_path())
    if not terrain.ok:
        raise SystemExit("no terrain model: the bands need the height above the ground")
    u = station_unique.Unique(terrain)
    files = [(nightly.hour_path(raw_dir, day, h), None) for h in range(24)]
    nxt = nightly.hour_path(raw_dir, day + datetime.timedelta(days=1), 0)
    if nxt is not None:
        files.append((nxt, d1 + nightly.STALE + 1))
    gate = sources.FeedGate(sources.load_exclusions())
    missing = [h for h, (p, _) in enumerate(files[:24]) if p is None]
    lines = 0
    for path, stop in files:
        if path is None:
            continue
        for epoch, line in nightly.gated(nightly.read_lines(path, stop), gate):
            lines += 1
            handle(u, d0, d1, epoch, line)
        logger.info("%s: %s lines, %s keys counted, %s open, %.0f MB", os.path.basename(path), f"{lines:,}",
                    f"{u.keys:,}", f"{u.open_keys():,}", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    return u, lines, missing


def handle(u, d0, d1, epoch, line):
    """Nightly.handle, reduced to what the measure needs."""
    if line == sources.FEED_BREAK:
        return
    try:
        src, rest = line.split(">", 1)
        head, body = rest.split(":", 1)
    except ValueError:
        return
    if nightly.EUROPE_ONLY and recorder.outside_europe(body):
        return
    if not body.startswith("/"):
        return
    path = head.split(",")
    raw_tc = path[0]
    if raw_tc in sources.EXCLUDED or body[26:27] in ("&", "_"):
        return
    tocall = sources.packet_system(path, src)
    if tocall not in station_unique.SYSTEMS:
        return
    id_category, no_track = sources.id_info(body)
    if no_track or sources.rebroadcast(tocall, src, id_category):
        return
    if body[1:7] == "______" or "h" not in body[:8]:
        return
    if body[8:10] == "00" and body[17:20] == "000":
        return
    m = sources._position.match(body)
    if not m:
        return
    hh, mi, ss, latd, latm, ns, _table, lond, lonm, ew, symbol, course, speed = m.groups()
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
    course = int(course) if course is not None else None
    kt = int(speed) if speed is not None else None
    sod = int(hh) * 3600 + int(mi) * 60 + int(ss)
    now = int(epoch)
    t = now - now % 86400 + sod
    if t - epoch > nightly.STALE:
        t -= 86400
    if epoch - t > nightly.STALE or not (d0 <= t < d1):
        return
    u.packet(epoch, t, tocall, id_category, kt, course, alt_m, src[-6:], nightly.receiver_of(path), lat, lon)


def write(day, rows):
    conn = nightly.connect_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM daily_station_unique WHERE day = %s", (day.isoformat(),))
            if rows:
                cur.executemany("INSERT INTO daily_station_unique (day, station, heard_low, heard_mid, heard_high, "
                                "unique_low, unique_mid, unique_high) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
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
    ap.add_argument("--dump", help="write the rows of every day to this file, tab-separated")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    raw_dir = os.path.expanduser(os.getenv("EC_RAW_DIR") or "")
    if not os.path.isdir(raw_dir):
        logger.error("EC_RAW_DIR is not set or not a directory")
        return 1
    day, last = datetime.date.fromisoformat(args.first), datetime.date.fromisoformat(args.last)
    dump = open(args.dump, "w") if args.dump else None
    while day <= last:
        t0 = time.time()
        u, lines, missing = one_day(raw_dir, day)
        rows = u.rows(day.isoformat())
        logger.info("%s: %s lines, %s keys, %d stations, %s packets without ground, missing hours %s, %.0f s, peak %.0f MB",
                    day, f"{lines:,}", f"{u.keys:,}", len(rows), f"{u.no_ground:,}", missing or "none",
                    time.time() - t0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        if dump:
            for r in rows:
                dump.write("\t".join(str(x) for x in r) + "\n")
            dump.flush()
        if missing:
            logger.warning("%s has hours missing from the recording; its figures are partial", day)
        if not args.dry_run:
            write(day, rows)
            logger.info("%s: written", day)
        day += datetime.timedelta(days=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
