#!/usr/bin/env python3
"""Where does free flight's radio "time without signal" come from?

Written on 2026-10-06 (ec-monitor.md) after the page showed 45% for
paragliders and hang gliders by radio. Applies the rules of METHOD.md as of
that day (both ends at 15 km/h or more, gaps up to 20 minutes, interval plus
10 s of tolerance: FLARM and ADS-L 1 s, OGN tracker 2 s, FANET 15 s) to the raw
recording, and splits the late time by length of the silence, by device and
by height above the ground at the start of the silence. Aggregates only.

Usage: (cd ~/ads-l-map && python3 ~/ec-replay/ec_radio_silence_check.py <files>)
"""
import collections
import re
import subprocess
import sys

sys.path.insert(0, ".")
import sources  # noqa: E402  (run from ~/ads-l-map for the terrain model)

POS = re.compile(r"^/(\d{6})h(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW]).(\d{3})/(\d{3})/A=(-?\d{6})")
ID = re.compile(r" id([0-9A-F]{2})")
DB = re.compile(r" -?\d+\.\d+dB ")
CAD = {"OGFLR": 1, "OGFLR7": 1, "OGNFLR": 1, "OGADSL": 1, "OGNTRK": 2, "OGNFNT": 15}
SAME = {"OGFLR7": "OGFLR", "OGNFLR": "OGFLR"}


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")


def main():
    terr = sources.Terrain(sources.DEM_PATH)
    last = {}
    judged = collections.Counter()
    late_by_len = collections.Counter()
    late_dev = collections.Counter()
    judged_dev = collections.Counter()
    by_agl = collections.defaultdict(lambda: [0.0, 0.0])
    for line in lines(sys.argv[1:]):
        try:
            stamp, rest = line.split(" ", 1)
            src, rest = rest.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        tc = head.split(",")[0]
        if tc not in CAD or not DB.search(body):
            continue
        i = ID.search(body)
        if not i or ((int(i.group(1), 16) >> 2) & 15) not in (6, 7):
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
        kmh = int(m.group(9)) * 1.852
        alt = int(m.group(10)) * 0.3048
        sysname = SAME.get(tc, tc)
        key = (src, sysname)
        p = last.get(key)
        last[key] = (t, kmh, lat, lon, alt)
        if p is None or not (0 < t - p[0] <= 1200) or min(kmh, p[1]) < 15:
            continue
        gap = t - p[0]
        late = max(0.0, gap - CAD[tc] - 10)
        g = terr.elevation(p[2], p[3])
        agl = p[4] - g if g is not None else None
        band = "unknown" if agl is None else "<100 m" if agl < 100 else "100-300 m" if agl < 300 else "300-1000 m" if agl < 1000 else ">1000 m"
        judged[sysname] += gap
        judged_dev[key] += gap
        by_agl[band][0] += gap
        by_agl[band][1] += late
        if late:
            late_dev[key] += late
            b = "11-30 s" if gap <= 30 else "30-60 s" if gap <= 60 else "1-5 min" if gap <= 300 else "5-20 min"
            late_by_len[b] += late
    tj, tl = sum(judged.values()), sum(late_dev.values())
    print("free flight by radio: %.0f h judged, %.1f%% without signal" % (tj / 3600, 100 * tl / tj))
    for s in judged:
        sl = sum(v for k, v in late_dev.items() if k[1] == s)
        print("  %-7s %6.0f h judged, %5.1f%% without signal" % (s, judged[s] / 3600, 100 * sl / judged[s]))
    print("late time by length of the silence:", ", ".join("%s %.0f%%" % (b, 100 * late_by_len[b] / tl)
                                                         for b in ("11-30 s", "30-60 s", "1-5 min", "5-20 min")))
    ranked = sorted(late_dev.values(), reverse=True)
    n = len(judged_dev)
    for share in (0.05, 0.10, 0.25):
        k = max(1, int(n * share))
        print("the %d%% of devices with most silence (%d of %d) hold %.0f%% of it" % (100 * share, k, n, 100 * sum(ranked[:k]) / tl))
    shares = sorted((late_dev[k] / judged_dev[k] for k in judged_dev if judged_dev[k] >= 600), reverse=True)
    if shares:
        print("devices with 10+ min judged: %d; median share without signal %.0f%%; share over 50%%: %.0f%% of devices" % (
            len(shares), 100 * shares[len(shares) // 2], 100 * sum(1 for x in shares if x > 0.5) / len(shares)))
    print("by height above ground at the start of the silence:")
    for b in ("<100 m", "100-300 m", "300-1000 m", ">1000 m", "unknown"):
        v = by_agl.get(b)
        if v and v[0]:
            print("  %-10s %6.0f h judged, %5.1f%% without signal" % (b, v[0] / 3600, 100 * v[1] / v[0]))


if __name__ == "__main__":
    main()
