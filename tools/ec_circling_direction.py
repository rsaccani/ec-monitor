#!/usr/bin/env python3
"""Do paragliders, hang gliders and gliders circle left or right, and do pilots prefer one side?

Written on 2026-10-07 (ec-monitor.md). The direction comes from the course in
successive fixes of one device, so it works for every source that sends a
course often enough, whether or not it sends a turn rate. Only gaps up to
5 s are used: at 15 s a paraglider circling in 20 s turns 270 degrees, which
reads as 90 the other way.

A climb ("thermal") is a run of same-sign turning at 5 to 45 degrees a second
totalling at least two full turns; runs of the same sign within 60 s and 1 km
are merged, so a dropout does not split one thermal into two. One aircraft is
one 24-bit address; when it is heard on several systems only the system with
most thermals is kept, so a FLARM+FANET+ADS-L instrument counts once.

Per-pilot preference is tested against what chance alone would give: if every
pilot chose each side with the population's probability p, a pilot's count of
right-hand thermals out of n would be binomial, and the spread of the shares
across pilots would match it. A wider spread means some pilots prefer a side.
Thermals of one pilot are not fully independent (a gaggle imposes the
direction of whoever was first), which would also widen it; the report gives
both the spread and the number of strongly one-sided pilots.

Also checks the sign convention of the transmitted turn rate against the
course change. Aggregates and counts only.

Usage: (cd ~/ads-l-map && nice -n 19 python3 ~/ec-explore/ec_circling_direction.py OUTDIR <files>)
"""
import collections
import math
import os
import re
import statistics
import subprocess
import sys

POS = re.compile(r"^/(\d{2})(\d{2})(\d{2})h(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW]).(\d{3})/(\d{3})")
ID = re.compile(r"\bid([0-9A-F]{8}|[0-9A-F]{10})\b")
ROT = re.compile(r" ([+-]?\d+\.\d)rot")
KINDS = {1: "glider", 6: "hang glider", 7: "paraglider"}
FLOOR_KMH = {1: 60, 6: 15, 7: 15}      # circling only counts at airborne speed
MAX_GAP = 5
MIN_RATE, MAX_RATE = 5.0, 45.0          # deg/s: 72 s to 8 s per turn
MIN_TURN = 720
MERGE_S, MERGE_M = 60, 1000
SKIP = {"OGADSB", "OGNSDR", "OGNSXR", "OGNDVS", "OGMLAT", "OGNDELAY"}


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")


def dist(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    return 6371000 * math.hypot((lon2 - lon1) * p * math.cos((lat1 + lat2) * p / 2), (lat2 - lat1) * p)


class Track:
    __slots__ = ("last", "run", "run_start", "run_pos", "prev_end", "prev_sign", "prev_pos", "cw", "ccw", "kind",
                 "deg_cw", "deg_ccw")

    def __init__(self, kind):
        self.kind = kind
        self.last = None
        self.run = 0.0
        self.run_start = None
        self.run_pos = None
        self.prev_end = -1e9
        self.prev_sign = 0
        self.prev_pos = None
        self.cw = self.ccw = 0
        self.deg_cw = self.deg_ccw = 0.0


def close_run(tr, t, lat, lon):
    if abs(tr.run) >= MIN_TURN:
        sign = 1 if tr.run > 0 else -1
        merged = (sign == tr.prev_sign and tr.run_start - tr.prev_end <= MERGE_S and tr.prev_pos is not None
                  and dist(tr.prev_pos[0], tr.prev_pos[1], tr.run_pos[0], tr.run_pos[1]) <= MERGE_M)
        if not merged:
            if sign > 0:
                tr.cw += 1
            else:
                tr.ccw += 1
        if sign > 0:
            tr.deg_cw += tr.run
        else:
            tr.deg_ccw -= tr.run
        tr.prev_sign, tr.prev_end, tr.prev_pos = sign, t, (lat, lon)
    tr.run = 0.0
    tr.run_start = None


def main():
    out, paths = sys.argv[1], sys.argv[2:]
    tracks = {}
    rot_agree = collections.Counter()
    for line in lines(paths):
        try:
            _, rest = line.split(" ", 1)
            src, rest = rest.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        tc = head.split(",")[0]
        if tc in SKIP:
            continue
        m = POS.match(body)
        if not m:
            continue
        i = ID.search(body)
        if not i:
            continue
        x = i.group(1)
        if len(x) == 8:
            b = int(x[:2], 16)
            cat, notrack = (b >> 2) & 15, b & 0x40
        else:
            w = int(x[:4], 16)
            cat, notrack = (w >> 10) & 15, w & 0x4000
        if cat not in KINDS or notrack:
            continue
        hh, mi, ss, latd, latm, ns, lond, lonm, ew, course, speed = m.groups()
        t = int(hh) * 3600 + int(mi) * 60 + int(ss)
        lat = int(latd) + float(latm) / 60
        lon = int(lond) + float(lonm) / 60
        if ns == "S":
            lat = -lat
        if ew == "W":
            lon = -lon
        crs, kmh = int(course), int(speed) * 1.852
        key = (src[-6:], tc)
        tr = tracks.get(key)
        if tr is None:
            tr = tracks[key] = Track(cat)
        p = tr.last
        if p is not None and t <= p[0] and p[0] - t < 43200:
            continue                      # duplicate from another receiver, or out of order
        tr.last = (t, crs, kmh, lat, lon)
        if p is None or crs == 0 or p[1] == 0:
            continue
        gap = (t - p[0]) % 86400
        if gap > MAX_GAP or min(kmh, p[2]) < FLOOR_KMH[cat] or kmh > 200:
            close_run(tr, p[0], p[3], p[4])
            continue
        d = (crs - p[1] + 540) % 360 - 180        # positive = heading increasing = clockwise seen from above
        rate = d / gap
        r = ROT.search(body)
        if r and abs(rate) >= MIN_RATE:
            rv = float(r.group(1))
            if abs(rv) >= 1:
                rot_agree[(tc, (rv > 0) == (rate > 0))] += 1
        if MIN_RATE <= abs(rate) <= MAX_RATE and (tr.run == 0 or (tr.run > 0) == (d > 0)):
            if tr.run_start is None:
                tr.run_start, tr.run_pos = p[0], (p[3], p[4])
            tr.run += d
        else:
            close_run(tr, p[0], p[3], p[4])
            if MIN_RATE <= abs(rate) <= MAX_RATE:
                tr.run_start, tr.run_pos, tr.run = p[0], (p[3], p[4]), d

    # one system per aircraft: the one with most thermals
    best = {}
    for (addr, tc), tr in tracks.items():
        n = tr.cw + tr.ccw
        if n and (addr not in best or n > best[addr][1].cw + best[addr][1].ccw):
            best[addr] = (tc, tr)

    o = open(os.path.join(out, "circling.txt"), "w")

    def P(*a):
        print(*a, file=o)

    P(f"Aircraft with at least one thermal: {len(best)}")
    P("Turn-rate sign against course change (agree = positive rot is clockwise):")
    for tc in sorted({k[0] for k in rot_agree}):
        a, d = rot_agree[(tc, True)], rot_agree[(tc, False)]
        P(f"  {tc:8s} agree {a:>8,} disagree {d:>8,}")
    P()
    for cat, name in KINDS.items():
        trs = [(tc, tr) for tc, tr in best.values() if tr.kind == cat]
        cw = sum(tr.cw for _, tr in trs)
        ccw = sum(tr.ccw for _, tr in trs)
        dcw = sum(tr.deg_cw for _, tr in trs)
        dccw = sum(tr.deg_ccw for _, tr in trs)
        n = cw + ccw
        if not n:
            continue
        p = cw / n
        P(f"== {name}: {len(trs)} aircraft, {n} thermals; right (clockwise) {cw} = {100*p:.1f}%, left {ccw}; "
          f"by degrees turned right {100*dcw/(dcw+dccw):.1f}%")
        P("   by system: " + ", ".join(f"{k} {v}" for k, v in collections.Counter(tc for tc, _ in trs).most_common()))
        for nmin in (5, 10, 20):
            sel = [tr for _, tr in trs if tr.cw + tr.ccw >= nmin]
            if len(sel) < 5:
                continue
            shares = [tr.cw / (tr.cw + tr.ccw) for tr in sel]
            # expected variance of a share under no preference: p(1-p)/n_i, averaged
            exp_var = statistics.mean(p * (1 - p) / (tr.cw + tr.ccw) for tr in sel)
            obs_var = statistics.pvariance(shares)
            strong_r = sum(1 for s in shares if s >= 0.8)
            strong_l = sum(1 for s in shares if s <= 0.2)
            # expected number of pilots this one-sided by chance alone
            exp_strong = 0.0
            for tr in sel:
                k = tr.cw + tr.ccw
                for j in range(k + 1):
                    if j / k >= 0.8 or j / k <= 0.2:
                        exp_strong += math.comb(k, j) * p ** j * (1 - p) ** (k - j)
            hist = collections.Counter(min(9, int(s * 10)) for s in shares)
            P(f"   pilots with >= {nmin:2d} thermals: {len(sel):4d}; spread of right-hand share: observed var "
              f"{obs_var:.4f} vs {exp_var:.4f} by chance (ratio {obs_var/exp_var:.2f}); >=80% right {strong_r}, "
              f">=80% left {strong_l}, chance alone {exp_strong:.1f}")
            P("     share right 0-10%..90-100%: " + " ".join(f"{hist[b]}" for b in range(10)))
    o.close()


if __name__ == "__main__":
    main()
