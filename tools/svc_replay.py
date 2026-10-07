#!/usr/bin/env python3
"""Run the service's own sources.py once over the recording and print the
sig_* (and cad_*) aggregates by source, channel and kind, including the
AIRCRAFT pseudo-source, plus flying hours. Aggregates only.
Usage: python3 svc_replay.py <sources.py> <files...>"""
import collections, sys, time as real_time
import ec_replay_airborne as base
import ec_replay_versions as ver

TYPE = base.TYPE

def main():
    mod = ver.load("svc", sys.argv[1])
    tr = mod.SourceTracker(lambda line: None, lambda: None, lambda s: False)
    n = 0
    t0 = real_time.time()
    first = last = None
    for line in base.lines(sys.argv[2:]):
        stamp, _, raw = line.partition(" ")
        try:
            base.clock[0] = float(stamp)
        except ValueError:
            continue
        first = first or base.clock[0]; last = base.clock[0]
        tr.handle(raw)
        n += 1
    fmt = lambda s: real_time.strftime("%Y-%m-%d %H:%M:%S", real_time.gmtime(s))
    print("replayed %d lines, %s to %s UTC, in %.0f s" % (n, fmt(first), fmt(last), real_time.time() - t0))
    cols = mod.DETAIL_COLUMNS
    idx = {c: cols.index(c) for c in ("air_seconds", "cad_air", "cad_late", "sig_air", "sig_late", "segments")}
    by = collections.defaultdict(lambda: collections.Counter())
    for (month, tocall, via, cat, msl, agl), v in tr.detail.items():
        label, kind = mod.source_info(tocall)
        t = TYPE.get(cat, "other")
        for key in ((tocall, via, kind, t), ("ALLKIND:" + kind, via, kind, t)):
            for c, i in idx.items():
                by[key][c] += v[i]
    print("%-16s %-6s %-9s %-12s %10s %10s %10s %10s %10s" % ("tocall", "via", "kind", "type", "air_h", "cad_air_h", "cad_late%", "sig_air_h", "sig_late%"))
    for k in sorted(by, key=str):
        c = by[k]
        print("%-16s %-6s %-9s %-12s %10.2f %10.2f %10s %10.2f %10s" % (
            k[0], k[1], k[2], k[3], c["air_seconds"] / 3600, c["cad_air"] / 3600,
            "%.2f" % (100 * c["cad_late"] / c["cad_air"]) if c["cad_air"] else "-",
            c["sig_air"] / 3600, "%.2f" % (100 * c["sig_late"] / c["sig_air"]) if c["sig_air"] else "-"))
    hours = collections.Counter()
    for (month, cat), v in tr.hours.items():
        hours[TYPE.get(cat, "other")] += v[1]
        hours["cat%s" % cat] += v[1]
    print("hours per aircraft:")
    for k in sorted(hours, key=str):
        print("  %-12s %10.2f h" % (k, hours[k] / 3600))
    print("implausible", sum(v[4] for v in tr.totals.values()))

if __name__ == "__main__":
    main()
