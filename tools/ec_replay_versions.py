#!/usr/bin/env python3
"""Replay the recorded feed through two versions of the EC monitor's sources.py.

Written on 2026-10-06 to show a change to the method before it goes live
(ec-monitor.md). Uses the clock, reader and summary of ec_replay_airborne.py;
only aggregates are printed.

Usage: python3 ec_replay_versions.py old.py new.py <file.aprs[.zst]> ...
"""
import importlib.util
import os
import sys
import time as real_time
import types

import ec_replay_airborne as base


def load(name, path):
    spec = importlib.util.spec_from_loader("sources_" + name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = os.path.join(base.CHECKOUT, "sources.py")      # the DEM path is relative to it
    exec(compile(open(path).read(), mod.__file__, "exec"), mod.__dict__)
    mod.time = base.FakeTime()
    mod.datetime = types.SimpleNamespace(datetime=base.FakeDateTime, date=base.real_datetime.date,
                                         timedelta=base.real_datetime.timedelta, timezone=base.real_datetime.timezone)
    return mod


def main():
    trackers = {}
    for name, path in (("old", sys.argv[1]), ("new", sys.argv[2])):
        mod = load(name, path)
        trackers[name] = (mod, mod.SourceTracker(lambda line: None, lambda: None, lambda s: False))
    base.RULES = trackers                   # the summary prints one column per entry
    n, t0, first, last = 0, real_time.time(), None, None
    for line in base.lines(sys.argv[3:]):
        stamp, _, raw = line.partition(" ")
        try:
            base.clock[0] = float(stamp)
        except ValueError:
            continue
        first = first or base.clock[0]
        last = base.clock[0]
        for mod, tr in trackers.values():
            tr.handle(raw)
        n += 1
    fmt = lambda s: real_time.strftime("%Y-%m-%d %H:%M", real_time.gmtime(s))
    print("replayed %d lines, %s to %s UTC, in %.0f s" % (n, fmt(first), fmt(last), real_time.time() - t0))
    for name, (mod, tr) in trackers.items():
        print("%s: implausible segments or positions %d" % (name, sum(v[4] for v in tr.totals.values())))
    base.report({name: base.summary(mod, tr) for name, (mod, tr) in trackers.items()})


if __name__ == "__main__":
    main()
