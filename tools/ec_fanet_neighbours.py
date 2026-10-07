#!/usr/bin/env python3
"""Does FANET's interval follow its neighbours, as its specification says?

Written on 2026-10-06 (ec-monitor.md). FANET.protocol.txt recommends a
tracking interval of floor((neighbours/10 + 1) * 5 s): 5 s with fewer than
ten FANET devices in range, 10 s with ten to nineteen, 15 s with twenty to
twenty-nine. Reads the raw recording in ~/ec-raw; prints distributions only.

For every pair of consecutive radio packets of an airborne paraglider or hang
glider on FANET (both ends at 10 km/h or more, at most 60 s apart), counts the
FANET devices of any kind, ground stations and weather stations included,
heard in the previous 60 s within R km, and groups the intervals by that
count. If the specification holds, the most common interval should step from
5 s to 10 s to 15 s.

Usage: python3 ec_fanet_neighbours.py <file.aprs[.zst]> ...
"""
import collections
import math
import re
import subprocess
import sys

POS = re.compile(r"^[/@](\d{6})h(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW])(.)(\d{3})/(\d{3})")
ID = re.compile(r" id([0-9A-F]{2})")
DB = re.compile(r" -?\d+\.\d+dB ")
RADII = (5, 10, 20)
WINDOW = 60
BUCKETS = ((0, 9), (10, 19), (20, 29), (30, 999))


def km(a, b):
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return 6371 * math.hypot(dlat, dlon)


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")


def main():
    seen = {}                       # FANET device -> (t, lat, lon), any kind
    last = {}                       # airborne free-flight FANET device -> (t, kmh)
    stats = {r: collections.defaultdict(collections.Counter) for r in RADII}
    for line in lines(sys.argv[1:]):
        try:
            stamp, rest = line.split(" ", 1)
            src, rest = rest.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        if head.split(",")[0] != "OGNFNT":
            continue
        m = POS.match(body)
        if not m:
            continue
        t = float(stamp)
        lat = int(m.group(2)) + float(m.group(3)) / 60
        lon = int(m.group(5)) + float(m.group(6)) / 60
        if m.group(4) == "S":
            lat = -lat
        if m.group(7) == "W":
            lon = -lon
        here = (lat, lon)
        seen[src] = (t, lat, lon)
        i = ID.search(body)
        if not i or ((int(i.group(1), 16) >> 2) & 15) not in (6, 7) or not DB.search(body):
            continue
        kmh = int(m.group(10)) * 1.852
        prev = last.get(src)
        last[src] = (t, kmh)
        if prev is None or not (0 < t - prev[0] <= 60) or min(kmh, prev[1]) < 10:
            continue
        gap = round(t - prev[0])
        near = [(x[1], x[2]) for d, x in seen.items() if d != src and t - x[0] <= WINDOW]
        for r in RADII:
            n = sum(1 for p in near if km(here, p) <= r)
            b = next(i for i, (lo, hi) in enumerate(BUCKETS) if lo <= n <= hi)
            stats[r][b][gap] += 1
    for r in RADII:
        print("Within %d km:" % r)
        for b, (lo, hi) in enumerate(BUCKETS):
            c = stats[r][b]
            n = sum(c.values())
            if not n:
                continue
            gaps = sorted(c.elements())
            mode = c.most_common(1)[0][0]
            share = lambda a, z: 100 * sum(v for k, v in c.items() if a <= k <= z) / n
            print("  %3d-%-3d neighbours: %6d intervals, mode %2d s, median %2d s; 4-7 s %3.0f%%, 8-12 s %3.0f%%, 13-17 s %3.0f%%, 18+ s %3.0f%%" % (
                lo, hi if hi < 999 else 999, n, mode, gaps[n // 2], share(4, 7), share(8, 12), share(13, 17), share(18, 60)))


if __name__ == "__main__":
    main()
