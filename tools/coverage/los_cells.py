#!/usr/bin/env python3
"""Line of sight from the stations to the points of every counting cell: the geometry of the coverage model.

For every 0.25-degree cell of the coverage-gaps snapshot that counts (and lies inside the terrain model, less a
margin), take the 40 stations nearest to the cell centre within 100 km and test the line of sight from each to an
8x8 grid of points spread over the cell, at 150, 450, 900 and 1500 m above the ground (the middles of the height
bands of the snapshot). The terrain is the grid given with --terrain, 4/3 earth, antenna 10 m above the terrain,
samples every 250 m in the first and last 5 km of a ray and every 500 m between (the same geometry as
covlib.los_pairs, with the ground under each point supplied instead of the aircraft's height). Writes a pickle
{(lat_idx, lon_idx): {station index, distance, bearing, visibility, point coordinates, ground height}} that
validate.py and diagnose.py read. It is the slow step of the model; the terrain is read one band of latitude at a time to keep the memory down.

--extra-station LAT_IDX:LON_IDX=LAT,LON adds one station that does not exist to that cell, to ask what a new
station would do there (the counterfactual of METHOD.md, coverage in detail); it is appended after the real ones.

  python3 los_cells.py --coverage-gaps cg.json --stations stations.json --terrain TERRAIN.i16 --out los.pkl
"""
import argparse
import pickle
import time

import numpy as np

import covlib as cl

HEIGHTS = np.array([150., 450., 900., 1500.])
NP = 8                      # points per side of a cell
KB = 260                    # most 500 m samples in the middle of a ray (100 km would need 180: room to spare)
NEAREST = 40
MARGIN_LAT, MARGIN_LON = 0.9, 1.2     # cells closer than this to the terrain edge are skipped, rays would leave it
A = 250.0 * np.arange(1, 21)
Bd = 5000 + 500.0 * np.arange(1, KB + 1)
Cj = 250.0 * np.arange(1, 21)


class Band:
    """The terrain rows around one latitude, in memory as float32 (sea floor and no-data as 0, like
    sources.Terrain), so that the 36 cells of a row of cells read the file once."""

    def __init__(self, t, f, lat_lo, lat_hi):
        self.t = t
        self.r0 = max(0, int((t.north - lat_hi) / t.step) - 2)
        r1 = min(t.rows, int((t.north - lat_lo) / t.step) + 3)
        f.seek(self.r0 * t.cols * 2)
        a = np.fromfile(f, dtype="<i2", count=(r1 - self.r0) * t.cols).reshape(r1 - self.r0, t.cols)
        self.h = np.maximum(a, 0).astype(np.float32)
        self.nr = r1 - self.r0

    def at(self, lat, lon):
        t = self.t
        y = np.clip((t.north - lat) / t.step - 0.5 - self.r0, 0, self.nr - 1.0001)
        x = np.clip((lon - t.west) / t.step - 0.5, 0, t.cols - 1.0001)
        iy = y.astype(int)
        ix = x.astype(int)
        fy = y - iy
        fx = x - ix
        h = self.h
        return (h[iy, ix] * (1 - fx) + h[iy, ix + 1] * fx) * (1 - fy) + (h[iy + 1, ix] * (1 - fx) + h[iy + 1, ix + 1] * fx) * fy


def los_from(band, la_s, lo_s, agl_ant, plat, plon, ground_p):
    """Visibility [points, heights] of the points from one antenna, and the distances."""
    D = cl.hav(la_s, lo_s, plat, plon)
    zA = band.at(np.array(la_s), np.array(lo_s)) + agl_ant
    dd = np.concatenate([np.broadcast_to(A, (len(D), 20)), np.broadcast_to(Bd, (len(D), KB)), D[:, None] - Cj[None, :]], axis=1)
    valid = np.concatenate([A[None, :] < D[:, None], Bd[None, :] < D[:, None] - 5000, np.ones((len(D), 20), bool)], axis=1) \
        & (dd > 0) & (dd < D[:, None])
    dd = np.where(valid, dd, 1.0)
    t = dd / D[:, None]
    th = band.at(la_s + (plat[:, None] - la_s) * t, lo_s + (plon[:, None] - lo_s) * t)
    rel = th - dd * dd / cl.TWO_REFF
    out = np.zeros((len(D), len(HEIGHTS)), np.uint8)
    for k, h in enumerate(HEIGHTS):
        tz = ground_p + h
        sl = (tz - D * D / cl.TWO_REFF - zA) / D
        blocked = (valid & (rel > zA + sl[:, None] * dd)).any(axis=1)
        hor = D > np.sqrt(cl.TWO_REFF * max(zA, 0)) + np.sqrt(cl.TWO_REFF * np.maximum(tz, 0))
        out[:, k] = ~(blocked | hor)
    return out, D


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coverage-gaps", required=True, help="coverage-gaps snapshot, JSON")
    ap.add_argument("--stations", required=True, help="stations.json from stations_snapshot.py")
    ap.add_argument("--terrain", required=True)
    ap.add_argument("--out", required=True, help="los.pkl")
    ap.add_argument("--only", default="", help="LAT_IDX:LON_IDX, one cell, for a test")
    ap.add_argument("--extra-station", action="append", default=[], help="LAT_IDX:LON_IDX=LAT,LON")
    a = ap.parse_args()
    t = cl.set_terrain(a.terrain)
    cg = cl.load_json(a.coverage_gaps)
    S = cl.load_json(a.stations)["stations"]
    slat = np.array([s["lat"] for s in S])
    slon = np.array([s["lon"] for s in S])
    EXTRA = {}
    for e in a.extra_station:
        k, v = e.split("=")
        EXTRA[tuple(int(x) for x in k.split(":"))] = tuple(float(x) for x in v.split(","))
    only = tuple(int(x) for x in a.only.split(":")) if a.only else None
    south = t.north - t.rows * t.step
    east = t.west + t.cols * t.step
    cells = {}
    for c in cg["cells"]:
        if not any(f["counts"] for b in c["bands"] for f in b["families"].values()):
            continue
        if not (south + MARGIN_LAT <= c["lat"] <= t.north - MARGIN_LAT and t.west + MARGIN_LON <= c["lon"] <= east - MARGIN_LON):
            continue
        cells[(c["lat_idx"], c["lon_idx"])] = c
    if only:
        cells = {only: cells[only]}
    print("cells in the terrain extent:", len(cells), flush=True)
    res = {}
    t0 = time.time()
    f = open(a.terrain, "rb")
    by_lat = {}
    for key in cells:
        by_lat.setdefault(key[0], []).append(key)
    for li in sorted(by_lat):
        clat = (li + 0.5) * 0.25
        band = Band(t, f, clat - 1.2, clat + 1.2)
        for key in by_lat[li]:
            c = cells[key]
            clon = c["lon"]
            sub = (np.arange(NP) + 0.5) / NP * 0.25 - 0.125
            plat = (clat + sub)[:, None].repeat(NP, 1).ravel()
            plon = (clon + sub)[None, :].repeat(NP, 0).ravel()
            gp = band.at(plat, plon)
            d0 = cl.hav(clat, clon, slat, slon)
            order = np.argsort(d0)
            order = order[d0[order] <= 100000][:NEAREST]
            n = len(order) + (key in EXTRA)
            L = np.zeros((n, 64, 4), np.uint8)
            Dm = np.zeros((n, 64), np.float32)
            Bm = np.zeros_like(Dm)
            for i, si in enumerate(order):
                L[i], D = los_from(band, slat[si], slon[si], cl.ANT, plat, plon, gp)
                Dm[i] = D
                Bm[i] = cl.bearing(slat[si], slon[si], plat, plon)
            if key in EXTRA:
                la, lo = EXTRA[key]
                L[-1], D = los_from(band, la, lo, cl.ANT, plat, plon, gp)
                Dm[-1] = D
                Bm[-1] = cl.bearing(la, lo, plat, plon)
            res[key] = dict(sidx=order, D=Dm, B=Bm, L=L, nextra=int(key in EXTRA), plat=plat.astype(np.float32),
                            plon=plon.astype(np.float32), ground=gp.astype(np.float32))
        print("lat row", li, len(res), "cells", round(time.time() - t0), "s, maxrss MB",
              cl.maxrss_mb(), flush=True)
        del band
    with open(a.out, "wb") as o:
        pickle.dump(res, o)
    print("done", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
