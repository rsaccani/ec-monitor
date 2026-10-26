#!/usr/bin/env python3
"""Replay the recorded feed through the live tracker before and after the chaining of
FLARM's random addresses (to-do 0j, 26 October 2026) and compare /api/live's answers.

Two versions of sources.py are loaded (the one before, the one after), each tracker is
fed the same lines under the recorded clock, and at chosen times the live window is
applied as prune_loop does and live_snapshot is asked for every layer. Only counts are
printed, plus the time one snapshot takes. Nothing is written anywhere.

Usage: python3 ec_replay_live_rnd.py old_sources.py new_sources.py HH:MM[,HH:MM...] <file.aprs[.zst]> ...
The times are UTC of the first file's day; BOX (lat0,lat1,lon0,lon1) is the area whose
helicopters are listed (default: Yeovil).
"""
import collections
import datetime
import os
import sys
import time as real_time

import ec_replay_airborne as base
import ec_replay_versions as ver

BOX = tuple(float(x) for x in os.environ.get("BOX", "50.7,51.3,-3.2,-2.0").split(","))
LAYERS = {"flarm", "fanet", "radio", "apps", "network", "trackers", "relays", "ogn", "other"}


def stub_parse(mod):
    pos = mod._position

    def parse(line):
        try:
            head, body = line.split(":", 1)
        except ValueError:
            return None
        m = pos.match(body)
        if not m:
            return None
        g = m.groups()
        lat = int(g[3]) + float(g[4]) / 60
        lon = int(g[7]) + float(g[8]) / 60
        cat = mod.id_info(body)[0]
        alt = None
        i = body.find("A=")
        if i >= 0:
            try:
                alt = float(body[i + 2:i + 8])
            except ValueError:
                pass
        return {"lat": -lat if g[5] == "S" else lat, "lon": -lon if g[9] == "W" else lon,
                "altitude": alt, "heading": None, "speed": None, "vspeed": None, "category": cat,
                "station": head.split(",")[-1], "signal": None, "gps_fix": None, "gps_sats": None,
                "timestamp": datetime.datetime.utcfromtimestamp(base.clock[0])}
    return parse


class NoTerrain:
    def elevation(self, lat, lon):
        return None


def window(tr, mod, now):
    """What prune_loop does to the live state."""
    cutoff = now - mod.LIVE_WINDOW
    tr.live = {k: v for k, v in tr.live.items() if v["_mono"] > cutoff}
    tr.live_parsed_at = {k: v for k, v in tr.live_parsed_at.items() if v > cutoff}


def report(name, mod, tr, at):
    window(tr, mod, base.clock[0])
    t0 = real_time.perf_counter()
    snap = tr.live_snapshot(set(mod.LAYER_OF_KIND.values()))
    ms = (real_time.perf_counter() - t0) * 1000
    in_box = lambda d: BOX[0] <= d["lat"] <= BOX[1] and BOX[2] <= d["lon"] <= BOX[3]
    rnd = [d for d in snap if d["id"].startswith("RND")]
    heli = [d for d in snap if d.get("category") == 3]
    box = [d for d in heli if in_box(d) and d["id"].startswith("RND")]
    chained = [d for d in rnd if d.get("chain")]
    print("%s %s: entries %d, RND entries %d (of which chains of 2+: %d, holding %d addresses), "
          "helicopters %d, RND helicopters in the box %d, snapshot %.1f ms" % (
              name, at, len(snap), len(rnd), len(chained), sum(d["chain"] for d in chained),
              len(heli), len(box), ms))
    if hasattr(tr, "rnd_prev"):
        gaps = []
        for b, a in tr.rnd_prev.items():
            pa, pb = tr.live.get("RND%06X" % a), tr.live.get("RND%06X" % b)
            if pa and pb and a in tr.rnd_last and b in tr.rnd_first:
                gap = tr.rnd_first[b] - tr.rnd_last[a]
                gaps.append((mod._distance(pa["lat"], pa["lon"], pb["lat"], pb["lon"]), gap))
                if os.environ.get("DEBUG") and gaps[-1][0] > 5000:
                    print("   far link %06X -> %06X: a %.5f,%.5f first %.0f last %.0f; b %.5f,%.5f first %.0f last %.0f; live t %s %s" % (
                        a, b, pa["lat"], pa["lon"], tr.rnd_first[a] % 3600, tr.rnd_last[a] % 3600,
                        pb["lat"], pb["lon"], tr.rnd_first[b] % 3600, tr.rnd_last[b] % 3600, pa["t"][11:19], pb["t"][11:19]))
        if gaps:
            print("   links with both fixes held: %d; gap to the next address up to %.0f s, longest distance %.0f m, "
                  "implied speed over 60 m/s: %d" % (len(gaps), max(g for _, g in gaps), max(d for d, _ in gaps),
                                                     sum(1 for d, g in gaps if d / max(g, 1) > 60)))
    if os.environ.get("DEBUG"):
        for d in rnd:
            print("   RND entry %s x%s at %.3f,%.3f heard %s" % (d["id"], d.get("chain", 1), d["lat"], d["lon"], d["t"][11:19]))
    if os.environ.get("JUMPS"):
        for d in chained:
            ms = sorted((m for m in tr.live.values() if tr.rnd_chain(m) == d["id"]), key=lambda m: m["_mono"])
            for m1, m2 in zip(ms, ms[1:]):
                dist = mod._distance(m1["lat"], m1["lon"], m2["lat"], m2["lon"])
                if dist > 5000:
                    a, b = int(m1["id"][3:], 16), int(m2["id"][3:], 16)
                    print("   jump in %s: %s (%s, heard %s, span %.0f s) -> %s (%s, heard %s, span %.0f s) %.0f m; linked %s" % (
                        d["id"], m1["id"], "%.3f,%.3f" % (m1["lat"], m1["lon"]), m1["t"][11:19], tr.rnd_last[a] - tr.rnd_first[a],
                        m2["id"], "%.3f,%.3f" % (m2["lat"], m2["lon"]), m2["t"][11:19], tr.rnd_last[b] - tr.rnd_first[b], dist,
                        tr.rnd_prev.get(b) == a))
    for d in chained:
        t = d["trail"]
        step = max(mod._distance(a[0], a[1], b[0], b[1]) for a, b in zip(t, t[1:]))
        print("   chain %s: %d addresses, %d in the trail, longest step between two fixes %.0f m" % (d["id"], d["chain"], len(t), step))
    if box:
        print("   box: " + "; ".join("%s x%s" % (d["id"], d.get("chain", 1)) for d in sorted(box, key=lambda d: d["t"])[:12]))
    return snap


def main():
    old_path, new_path, times = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
    files = sys.argv[4:]
    trackers = {}
    for name, path in (("old", old_path), ("new", new_path)):
        mod = ver.load(name, path)
        tr = mod.SourceTracker(stub_parse(mod), lambda: None, lambda s: False)
        tr.terrain = NoTerrain()
        trackers[name] = (mod, tr)
    day = None
    marks = []
    n, t0 = 0, real_time.time()
    mem = []
    polled = 0.0
    peak = 0
    for line in base.lines(files):
        stamp, _, raw = line.partition(" ")
        try:
            base.clock[0] = float(stamp)
        except ValueError:
            continue
        if day is None:
            day = datetime.datetime.utcfromtimestamp(base.clock[0]).replace(hour=0, minute=0, second=0, microsecond=0)
            marks = sorted(day.replace(tzinfo=datetime.timezone.utc).timestamp() + int(t[:2]) * 3600 + int(t[3:]) * 60
                           for t in times)
        while marks and base.clock[0] >= marks[0]:
            at = datetime.datetime.utcfromtimestamp(marks.pop(0)).strftime("%H:%M")
            for name, (mod, tr) in trackers.items():
                report(name, mod, tr, at)
            mem.append(len(trackers["new"][1].rnd_first))
        if base.clock[0] >= polled + 5:      # the map asks every 5 s
            polled = base.clock[0]
            for mod, tr in trackers.values():
                tr.live_snapshot(set(mod.LAYER_OF_KIND.values()))
        for mod, tr in trackers.values():
            if raw == base.FEED_BREAK:
                base.feed_break(tr, base.clock[0])
            else:
                tr.handle(raw)
        n += 1
        peak = max(peak, len(trackers["new"][1].rnd_first))
    at = datetime.datetime.utcfromtimestamp(base.clock[0]).strftime("%H:%M:%S") + " (end)"
    for name, (mod, tr) in trackers.items():
        report(name, mod, tr, at)
    tr = trackers["new"][1]
    print("links taken back %d" % tr.rnd_unlinked)
    print("new state: most addresses held at once %d; at the end rnd_first %d, rnd_prev %d, rnd_root %d, live %d; replayed %d lines in %.0f s" % (
        peak, len(tr.rnd_first), len(tr.rnd_prev), len(tr.rnd_root), len(tr.live), n, real_time.time() - t0))


if __name__ == "__main__":
    main()
