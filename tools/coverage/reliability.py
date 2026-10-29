#!/usr/bin/env python3
"""How reliable are the measured cells? The ceiling for any model that is compared with them.

A model cannot agree with the measurement better than the measurement agrees with itself. Per (cell, band, family)
the daily rows of the coverage-gaps table are split at random into two halves of days, the share without signal
is computed on each half, and the two halves are correlated across cells; repeated 100 times. The Spearman-Brown
formula then gives the correlation of the whole window with a hypothetical second window of the same size:
2r / (1 + r), the usual way to turn half-vs-half into full-vs-full. A model whose rank correlation with the
measurement (validate.py) approaches that figure has nothing left to learn from the measurement.

Floors, as in the snapshot: a cell counts with at least 7200 probe seconds and 5 probe aircraft-days over the
window, and a half needs at least 3600 probe seconds to enter a split.

Input: the daily rows of the coverage-gaps table as a TSV with the header
day lat_idx lon_idx family band probe_s probe_late_s probe_aircraft air_s (stations_snapshot.py --daily-gaps
writes it). With --coverage-gaps the script also checks that the whole-window shares it recomputes from the daily
rows match the snapshot's. Aggregates only.
"""
import argparse
import collections

import numpy as np
from scipy.stats import rankdata

import covlib as cl


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--daily", required=True, help="TSV of the daily coverage-gaps rows")
    ap.add_argument("--coverage-gaps", default="", help="snapshot JSON, to check the recomputed shares against")
    ap.add_argument("--splits", type=int, default=100)
    ap.add_argument("--text", default="", help="also write the report here")
    a = ap.parse_args()
    D = collections.defaultdict(list)
    days = set()
    with open(a.daily) as f:
        next(f)
        for l in f:
            d, la, lo, fam, b, ps, pl, pa, air = l.rstrip("\n").split("\t")
            days.add(d)
            if int(ps) > 0:
                D[(int(la), int(lo), fam, int(b))].append((d, int(ps), int(pl), int(pa)))
    print("days", len(days), min(days), max(days), "keys with probe", len(D))
    full = {}
    for k, v in D.items():
        ps = sum(x[1] for x in v)
        pl = sum(x[2] for x in v)
        pa = sum(x[3] for x in v)
        if ps >= 7200 and pa >= 5:
            full[k] = pl / ps
    print("keys passing the floors", len(full), "free_flight", sum(1 for k in full if k[2] == "free_flight"))
    if a.coverage_gaps:
        cg = cl.load_json(a.coverage_gaps)
        ref = {}
        for c in cg["cells"]:
            for b, bd in enumerate(c["bands"]):
                for fam, fd in bd["families"].items():
                    if fd["counts"]:
                        ref[(c["lat_idx"], c["lon_idx"], fam, b)] = fd["share_without_signal"]
        both = set(full) & set(ref)
        print("snapshot counting rows", len(ref), "| in both", len(both), "max |diff| vs snapshot",
              max((abs(full[k] - ref[k]) for k in both), default=None))
    rng = np.random.default_rng(11)

    def run(fam):
        keys = [k for k in full if k[2] == fam]
        sp, pe, nn = [], [], []
        for _ in range(a.splits):
            x, y = [], []
            for k in keys:
                v = D[k]
                perm = rng.permutation(len(v))
                h = len(v) // 2
                A_ = [v[i] for i in perm[:h]]
                B_ = [v[i] for i in perm[h:]]
                pa_ = sum(t[1] for t in A_)
                pb_ = sum(t[1] for t in B_)
                if pa_ >= 3600 and pb_ >= 3600:
                    x.append(sum(t[2] for t in A_) / pa_)
                    y.append(sum(t[2] for t in B_) / pb_)
            x, y = np.array(x), np.array(y)
            nn.append(len(x))
            pe.append(np.corrcoef(x, y)[0, 1])
            sp.append(np.corrcoef(rankdata(x), rankdata(y))[0, 1])
        sp, pe = np.mean(sp), np.mean(pe)
        return "%s: n/split %.0f, half-vs-half Spearman %.3f Pearson %.3f | Spearman-Brown full: Spearman %.3f Pearson %.3f" % (
            fam, np.mean(nn), sp, pe, 2 * sp / (1 + sp), 2 * pe / (1 + pe))
    out = [run("free_flight"), run("glider")]
    print("\n".join(out))
    if a.text:
        with open(a.text, "w") as f:
            f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
