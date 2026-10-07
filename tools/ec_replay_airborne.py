#!/usr/bin/env python3
"""Replay the recorded OGN feed through the EC monitor under three flight rules.

Written on 2026-10-06 to test a change to what counts as flight before it
goes live (ec-monitor.md). Runs on the host, next to the recording in
~/ec-raw and the checkout in ~/ads-l-map; only aggregates are printed.

The production sources.py is loaded once per rule, with the two airborne
tests (the visibility measure and the flying hours) replaced by a call to the
rule, and its clock replaced by the time of reception recorded on each line.
Nothing is written to the database: no thread is started and no flush runs.

  current   10 km/h at either end of the stretch (as deployed)
  speed     both ends above a speed for the kind of aircraft: 15 km/h for
            paragliders and hang gliders, 25 kt for gliders, 40 kt for
            powered aircraft; 10 km/h at either end for anything else
  height    the current rule, and at least 100 m above the ground at the
            start of the stretch
  speed10   as speed, with 10 km/h at both ends for free flight, and FANET
            judged by a fixed 15-second interval (its specification's
            interval up to 29 neighbours) instead of 5

Usage: python3 ec_replay_airborne.py <file.aprs[.zst]> ...
"""
import collections
import datetime as real_datetime
import importlib.util
import os
import subprocess
import sys
import time as real_time
import types

CHECKOUT = os.path.expanduser("~/ads-l-map")
KT = 1 / 1.852
FREE, GLIDER, POWERED = (6, 7), (1,), (2, 8, 9)
TYPE = {6: "free flight", 7: "free flight", 1: "glider", 2: "powered", 8: "powered", 9: "powered"}

clock = [0.0]


class FakeDateTime(real_datetime.datetime):
    @classmethod
    def utcnow(cls):
        return real_datetime.datetime.utcfromtimestamp(clock[0])


class FakeTime:
    def __getattr__(self, name):
        return getattr(real_time, name)

    def time(self):
        return clock[0]

    def monotonic(self):
        return clock[0]


def rule_current(cat, pspeed, speed, palt, plat, plon, terrain):
    return (pspeed or 0) >= 10 * KT or (speed or 0) >= 10 * KT


def rule_speed(cat, pspeed, speed, palt, plat, plon, terrain):
    lo = min(pspeed or 0, speed or 0)
    if cat in FREE:
        return lo >= 15 * KT
    if cat in GLIDER:
        return lo >= 25
    if cat in POWERED:
        return lo >= 40
    return rule_current(cat, pspeed, speed, palt, plat, plon, terrain)


def rule_speed10(cat, pspeed, speed, palt, plat, plon, terrain):
    if cat in FREE:
        return min(pspeed or 0, speed or 0) >= 10 * KT
    return rule_speed(cat, pspeed, speed, palt, plat, plon, terrain)


def rule_height(cat, pspeed, speed, palt, plat, plon, terrain):
    if not rule_current(cat, pspeed, speed, palt, plat, plon, terrain):
        return False
    if palt is None or plat is None:
        return True                     # the hours test has no height: unchanged
    ground = terrain.elevation(plat, plon)
    return ground is None or palt - ground >= 100


RULES = {"current": rule_current, "speed": rule_speed, "speed10": rule_speed10}
FANET_15 = {"speed10"}


def load(name, rule):
    src = open(os.path.join(CHECKOUT, "sources.py")).read()
    a = "        if not ((pspeed or 0) >= AIRBORNE_KT or (speed or 0) >= AIRBORNE_KT):\n            return\n"
    b = "        if not ((prev[3] or 0) >= AIRBORNE_KT or (speed or 0) >= AIRBORNE_KT):\n            return\n"
    assert src.count(a) == 1 and src.count(b) == 1, "sources.py changed: update the replay"
    src = src.replace(a, "        if not AIRBORNE_RULE(category, pspeed, speed, palt, plat, plon, self.terrain):\n            return\n")
    src = src.replace(b, "        if not AIRBORNE_RULE(category, prev[3], speed, None, prev[1], prev[2], self.terrain):\n            return\n")
    spec = importlib.util.spec_from_loader("sources_" + name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = os.path.join(CHECKOUT, "sources.py")
    sys.path.insert(0, CHECKOUT)
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    mod.AIRBORNE_RULE = rule
    if name in FANET_15:
        mod.RADIO_CADENCE["OGNFNT"] = (None, 15, 15)
    mod.time = FakeTime()
    mod.datetime = types.SimpleNamespace(datetime=FakeDateTime, date=real_datetime.date,
                                         timedelta=real_datetime.timedelta, timezone=real_datetime.timezone)
    return mod


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace").rstrip("\n")


def summary(mod, tr):
    cols = mod.DETAIL_COLUMNS
    ia, il, ic = cols.index("air_seconds"), cols.index("cad_air"), cols.index("cad_late")
    out = {}
    by = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    for (month, tocall, via, cat, msl, agl), v in tr.detail.items():
        label, kind = mod.source_info(tocall)
        chan = "app" if kind == "app" else "radio" if via == "radio" and kind != "adsb" else None
        if chan is None:
            continue
        t = TYPE.get(cat, "other")
        keys = [(chan, t, "all"), (chan, t, agl), (chan, "all", "all")]
        if t == "free flight":
            keys.append((chan, t, label))
        for key in keys:
            x = by[key]
            x[0] += v[ia]; x[1] += v[il]; x[2] += v[ic]
    out["late"] = by
    hours = collections.Counter()
    for (month, cat), v in tr.hours.items():
        hours[TYPE.get(cat, "other")] += v[1]
    out["hours"] = hours
    # Question 5 as the page computes it, from the in-memory grid.
    gc = mod.GRID_COLUMNS
    ga, gl = gc.index("cad_air"), gc.index("cad_late")
    apps, pg = collections.defaultdict(lambda: [0.0, 0.0]), collections.Counter()
    for (month, la, lo, grp, via), v in tr.grid.items():
        if grp == "apps":
            apps[(la, lo)][0] += v[ga]; apps[(la, lo)][1] += v[gl]
        elif grp in ("pg", "pga"):
            pg[(la, lo)] += v[ga]
    total = sum(pg.values())
    judged = sum(s for k, s in pg.items() if apps[k][0] >= 7200)
    steps = [0, 0, 0, 0]
    covered = 0
    for k, s in pg.items():
        a = apps[k]
        if a[0] < 7200:
            continue
        fresh = 1 - a[1] / a[0]
        steps[0 if fresh >= .95 else 1 if fresh >= .90 else 2 if fresh >= .75 else 3] += s
    for k, a in apps.items():
        if a[0] >= 7200 and a[1] / a[0] <= .05:
            covered += 1
    out["q5"] = (total, judged, steps, covered, sum(1 for a in apps.values() if a[0] >= 7200))
    return out


def main():
    trackers = {}
    for name, rule in RULES.items():
        mod = load(name, rule)
        trackers[name] = (mod, mod.SourceTracker(lambda line: None, lambda: None, lambda s: False))
    n, t0 = 0, real_time.time()
    first = last = None
    for line in lines(sys.argv[1:]):
        stamp, _, raw = line.partition(" ")
        try:
            clock[0] = float(stamp)
        except ValueError:
            continue
        first = first or clock[0]
        last = clock[0]
        for mod, tr in trackers.values():
            tr.handle(raw)
        n += 1
    fmt = lambda s: real_time.strftime("%Y-%m-%d %H:%M", real_time.gmtime(s))
    print("replayed %d lines, %s to %s UTC, in %.0f s" % (n, fmt(first), fmt(last), real_time.time() - t0))
    report({name: summary(mod, tr) for name, (mod, tr) in trackers.items()})


def report(res):
    print("\nTime without signal, share of the time judged (hours judged in brackets)")
    keys = sorted({k for r in res.values() for k in r["late"]}, key=lambda k: (k[0], k[1], str(k[2])))
    print("%-34s" % "channel / aircraft / height band" + "".join("%22s" % n for n in res))
    for k in keys:
        cells = []
        for r in res.values():
            v = r["late"].get(k, [0, 0, 0])
            cells.append("%5.1f%% (%6.0f h)" % (100 * v[2] / v[1], v[1] / 3600) if v[1] >= 1800 else "%22s" % "–")
        if any(c.strip() != "–" for c in cells):
            print("%-34s" % " / ".join(str(x) for x in k) + "".join("%22s" % c for c in cells))
    print("\nFlying hours per aircraft (address)")
    for t in ("free flight", "glider", "powered", "other"):
        print("%-34s" % t + "".join("%22.0f" % (r["hours"][t] / 3600) for r in res.values()))
    print("\nQuestion 5: free-flight hours (sum over sources), judged, split >=95/90-95/75-90/<75, squares covered of judged")
    for name, r in res.items():
        total, judged, steps, cov, nj = r["q5"]
        print("%-10s %6.0f h, judged %5.0f h (%s), %s, %d of %d squares" % (
            name, total / 3600, judged / 3600, "%.0f%%" % (100 * judged / total) if total else "–",
            " / ".join("%.0f%%" % (100 * s / judged) if judged else "–" for s in steps), cov, nj))


if __name__ == "__main__":
    main()
