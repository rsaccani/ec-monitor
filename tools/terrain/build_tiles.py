"""A flat int16 terrain grid -> the tiled layout that sources.Terrain also reads.

Run once per terrain model, with numpy:

    python3 build_tiles.py world_15s.i16 world_15s_tiles.i16

The flat grid (with its .json beside it) is read one band of TILE rows at a
time. Each TILE x TILE square that has some cell above 0 m is written, row by
row, to the output; the others are left out, since sources.Terrain returns 0
for every cell below sea level or without data, so a square with none above
0 m reads as sea either way. An index of one little-endian uint32 per square,
row-major, gives the position of each square among those written, or
0xFFFFFFFF for sea. Writes <out>, <out minus .i16>.idx and <out minus .i16>.json.

On the world model of ETOPO 2022 at 15 arc-seconds (60 S to 90 N), squares of
one degree keep 19,581 of 54,000 and the file drops from 6.2 to 2.3 GB
(25 October 2026).
"""
import json
import os
import sys

import numpy as np

TILE = 240          # cells: one degree at 15 arc-seconds
SEA = 0xFFFFFFFF


def main(src, out):
    base = os.path.splitext(out)[0]
    with open(os.path.splitext(src)[0] + ".json") as f:
        meta = json.load(f)
    rows, cols = meta["rows"], meta["cols"]
    if rows % TILE or cols % TILE:
        sys.exit(f"{rows} x {cols} cells is not a whole number of {TILE}-cell tiles")
    grid = np.memmap(src, dtype="<i2", mode="r", shape=(rows, cols))
    tile_rows, tile_cols = rows // TILE, cols // TILE
    index = np.full(tile_rows * tile_cols, SEA, dtype="<u4")
    kept = 0
    with open(out, "wb") as f:
        for tr in range(tile_rows):
            band = np.asarray(grid[tr * TILE:(tr + 1) * TILE]).reshape(TILE, tile_cols, TILE)
            land = band.max(axis=(0, 2)) > 0
            for tc in np.flatnonzero(land):
                f.write(np.ascontiguousarray(band[:, tc, :]).astype("<i2").tobytes())
                index[tr * tile_cols + tc] = kept
                kept += 1
    index.tofile(base + ".idx")
    meta.update({
        "tile": TILE, "tile_rows": tile_rows, "tile_cols": tile_cols, "tiles_kept": kept,
        "index": os.path.basename(base) + ".idx",
        "method": meta.get("method", "") + f" Tiled by tools/terrain/build_tiles.py from {os.path.basename(src)}: "
                  f"{TILE}x{TILE}-cell squares with some cell above 0 m kept, row-major, the others (sea "
                  f"for the reader) left out; {kept} of {tile_rows * tile_cols} kept.",
    })
    with open(base + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    print(f"{kept} of {tile_rows * tile_cols} tiles kept, {kept * TILE * TILE * 2 / 1e9:.2f} GB")


if __name__ == "__main__":
    main(*sys.argv[1:])
