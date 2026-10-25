"""Check a tiled terrain file against the flat one it was built from.

    python3 verify_tiles.py world_15s.i16 world_15s_tiles.i16          # numpy: every tile
    python3 verify_tiles.py --lookups world_15s.i16 world_15s_tiles.i16  # sources.Terrain: random points

The first form compares every kept tile byte for byte and checks that every
tile left out has no cell above 0 m. The second runs sources.Terrain.elevation
on both files at 200,000 random points over the model and 40,000 more in the
Alps and along coasts and tile edges, counts any difference, and times the
random lookups on each. Random points touch pages all over both files, so run
it under a memory cap on a production host, e.g.
`systemd-run --user --scope -p MemoryHigh=600M -p MemoryMax=800M nice -n 19 …`:
uncapped, the 1,000,000-point version of 25 October 2026 pushed MariaDB into swap.
"""
import json
import os
import random
import sys
import time


def tiles(flat, tiled):
    import numpy as np
    with open(os.path.splitext(tiled)[0] + ".json") as f:
        meta = json.load(f)
    t, tr_n, tc_n = meta["tile"], meta["tile_rows"], meta["tile_cols"]
    grid = np.memmap(flat, dtype="<i2", mode="r", shape=(meta["rows"], meta["cols"]))
    data = np.memmap(tiled, dtype="<i2", mode="r", shape=(meta["tiles_kept"], t, t))
    index = np.fromfile(os.path.join(os.path.dirname(tiled), meta["index"]), dtype="<u4")
    same = differ = sea_ok = sea_bad = 0
    for tr in range(tr_n):
        band = np.asarray(grid[tr * t:(tr + 1) * t]).reshape(t, tc_n, t)
        for tc in range(tc_n):
            k = index[tr * tc_n + tc]
            if k == 0xFFFFFFFF:
                if band[:, tc, :].max() <= 0:
                    sea_ok += 1
                else:
                    sea_bad += 1
            elif np.array_equal(band[:, tc, :], data[k]):
                same += 1
            else:
                differ += 1
    print(f"kept tiles identical {same}, different {differ}; sea tiles all <= 0 m {sea_ok}, with land {sea_bad}")


def lookups(flat, tiled):
    sys.path.insert(0, os.environ.get("EC_CODE", "."))
    import sources
    a, b = sources.Terrain(flat), sources.Terrain(tiled)
    assert a.ok and b.ok, (a.ok, b.ok)
    rnd = random.Random(25)
    pts = [(rnd.uniform(-60, 90), rnd.uniform(-180, 180)) for _ in range(200_000)]
    alps = [(rnd.uniform(44, 48), rnd.uniform(5, 16)) for _ in range(16_000)]
    # Coasts and tile edges: points within a few cells of a whole degree, on land and sea.
    edges = [(rnd.randint(-59, 89) + rnd.uniform(-0.01, 0.01), rnd.randint(-179, 179) + rnd.uniform(-0.01, 0.01))
             for _ in range(16_000)]
    coasts = [(rnd.uniform(36, 46), rnd.uniform(7, 19)) for _ in range(8_000)]   # Italy and the Adriatic
    extra = alps + edges + coasts
    diff = sum(1 for la, lo in pts + extra if a.elevation(la, lo) != b.elevation(la, lo))
    land = sum(1 for la, lo in pts if (a.elevation(la, lo) or 0) > 0)
    print(f"{len(pts):,} random points ({land:,} on land) and {len(extra):,} in the Alps, on tile edges "
          f"and coasts: {diff} differences")
    for name, t in (("flat", a), ("tiled", b)):
        t0 = time.perf_counter()
        for la, lo in pts:
            t.elevation(la, lo)
        print(f"{name}: {(time.perf_counter() - t0) / len(pts) * 1e6:.2f} us per lookup")


if __name__ == "__main__":
    if sys.argv[1] == "--lookups":
        lookups(*sys.argv[2:])
    else:
        tiles(*sys.argv[1:])
