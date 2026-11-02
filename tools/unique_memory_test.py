#!/usr/bin/env python3
"""Memory of station_unique.Unique on a synthetic feed (METHOD.md 10.5).

    python3 tools/unique_memory_test.py [--aircraft 10000] [--minutes 10] [--receivers 2500] [--every 3]

10,000 aircraft airborne at once is above a summer Saturday's peak (about 2,100 per five minutes on
31 October 2026, 6,000 to 10,000 expected in summer). Each sends a packet every --every seconds, each packet
reaches 2 to 6 receivers (4 on average) out of --receivers, copies arrive up to 4 s late, and the
five minutes the structure holds a key are over after the first five minutes of feed, so ten
minutes show the steady state. Prints the peak of tracemalloc over the structure alone, the keys open at that
moment, the bytes per open key, and the resident set of the process. Exits 1 above 200 bytes per key.
"""
import argparse
import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import station_unique  # noqa: E402


class Flat:
    def elevation(self, lat, lon):
        return 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--aircraft", type=int, default=10000)
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--receivers", type=int, default=2500)
    ap.add_argument("--every", type=int, default=3, help="seconds between the packets of one aircraft")
    args = ap.parse_args()
    rnd = random.Random(1)
    names = [f"STN{i:04d}" for i in range(args.receivers)]
    # Each aircraft: its address, its phase, its altitude, and the receivers around it.
    fleet = [("%06X" % rnd.randrange(1 << 24), rnd.randrange(args.every), rnd.choice((150.0, 600.0, 1800.0)),
              rnd.sample(names, rnd.randint(2, 6))) for _ in range(args.aircraft)]
    u = station_unique.Unique(Flat())
    t0 = 1_800_000_000
    start = time.time()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    peak_keys = peak_bytes = packets = 0
    for sec in range(args.minutes * 60):
        now = t0 + sec
        for addr, phase, alt, rxs in fleet:
            if (sec + phase) % args.every:
                continue
            for rx in rxs:
                late = rnd.random() * 4
                u.packet(now + late, now, "OGFLR", 1, 40, 10, alt, addr, rx, 45.0, 11.0)   # as the nightly calls it, a fresh int per packet
                packets += 1
        if sec % 10 == 9:
            cur = tracemalloc.get_traced_memory()[0] - base
            if cur > peak_bytes:
                peak_bytes, peak_keys = cur, u.open_keys()
    peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    rows = u.rows("2026-11-02")
    per = peak_bytes / max(peak_keys, 1)
    print(f"aircraft {args.aircraft}, {args.minutes} min, {packets:,} packets, {u.keys:,} keys counted, "
          f"{len(rows)} stations, {time.time() - start:.0f} s")
    print(f"tracemalloc: structure at peak {peak_bytes / 1e6:.1f} MB with {peak_keys:,} open keys = {per:.0f} bytes per "
          f"open key (largest instant {peak / 1e6:.1f} MB)")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
    print(f"process maxrss {rss:.0f} MB "
          f"(includes the {args.aircraft:,}-aircraft fleet table and the tracemalloc bookkeeping)")
    return 0 if per <= 200 else 1


if __name__ == "__main__":
    sys.exit(main())
