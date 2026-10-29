#!/usr/bin/env python3
"""Validate the coverage model against a coverage-gaps snapshot.

The coverage model (METHOD.md, coverage in detail) predicts, for a 0.25-degree cell and a height band, the share of
a free-flight or glider's airborne time without a signal: at each of 64 points of the cell, the chance that no
station in line of sight hears the aircraft is the product of (1 - p(d)) over the 40 nearest stations, p being the
reception curve of reach.py. The measured side is `share_without_signal` of the same cell and band in the
coverage-gaps snapshot (the API endpoint of that name, saved as JSON). This script computes the prediction in
several variants and says how well it agrees:

  A  one curve for every station.
  B  each station's distances stretched by its sensitivity (its median signal at 10 km, +-12 dB around the
     median station): a deaf station reaches less far.
  C  as B, per 10-degree sector of the station (its pooled reception rose): a station behind a mast or a hill
     is deaf in one direction only.
  A_agl  the curve of the aircraft's height above ground band instead of the pooled one (free flight only).
  A_nolos / C_nolos  the curve without the line-of-sight filter and no line-of-sight mask in the model: what
     the model would say if it ignored terrain.

Per variant: Spearman rank correlation with the measured values (does it rank the cells right), the mean absolute
error unweighted and weighted by probe hours (MAE, MAEw) and the bias (prediction minus measurement). Calibrations
(a scale factor, a power q^gamma, a logit-linear map) are fitted on a random half of the cells and scored on the
other, 20 times (seed 7), so the figures are out-of-sample.

Three experiments, each on the curves of a different run of reach.py:

  family  both families, curves from a per-family report (--curves), variants and calibrations compared.
  ogn     free flight, curves of an OGN-only per-system report (--curves): FLARM alone, FANET alone, both
          combined as 1-(1-pF)(1-pN), pooled; uncalibrated.
  any     free flight, the pooled curve against the probe curve (--curves-any, from reach.py --any-system) and the
          combined per-system ones, uncalibrated and with scale and logit calibrations.

Inputs: --coverage-gaps (the snapshot), --los (los.pkl of los_cells.py), --stations (stations.json of
stations_snapshot.py), --curves. Aggregates only.
"""
import argparse
import itertools
import os
import pickle

import numpy as np

import covlib as cl

lg = lambda x: np.log(x / (1 - x))


def make(kind):
    """(parameter grids, function) of a calibration."""
    if kind == "scale":
        return [np.linspace(.1, 1.3, 121)], lambda q, g: np.clip(g[0] * q, 0, 1)
    if kind == "gamma":
        return [np.exp(np.linspace(np.log(.2), np.log(10), 140))], lambda q, g: q ** g[0]
    if kind == "logit":
        return ([np.linspace(-3, 3, 61), np.exp(np.linspace(np.log(.15), np.log(4), 50))],
                lambda q, g: 1 / (1 + np.exp(-(g[0] + g[1] * lg(np.clip(q, .01, .99))))))


def fit(kind, q, m, idx):
    """Grid search of the parameters that minimise the mean absolute error on rows `idx`."""
    axes, f = make(kind)
    best = (9, None)
    for g in itertools.product(*axes):
        e = np.abs(m[idx] - f(q[idx], g)).mean()
        if e < best[0]:
            best = (e, g)
    return best[1]


class Setup:
    def __init__(self, a, families):
        self.cg = cl.load_json(a.coverage_gaps)
        with open(a.los, "rb") as f:
            self.los = pickle.load(f)
        self.sj = cl.load_json(a.stations)
        self.dB, self.dC = cl.station_offsets(self.sj)
        self.rows = cl.model_rows(self.cg, self.los, families)
        self.fam = np.array([r[1] for r in self.rows])
        self.m = np.array([r[3] for r in self.rows])
        self.w = np.array([r[4] for r in self.rows])
        self.cells = sorted({r[0] for r in self.rows})
        self.ci = np.array([self.cells.index(r[0]) for r in self.rows])
        self.out = []
        self.focus = tuple(int(x) for x in a.focus_cell.split(":")) if a.focus_cell else None

    def P(self, *x):
        s = " ".join(str(i) for i in x)
        self.out.append(s)
        print(s, flush=True)

    def q(self, fn_of_row, variant="A", filt=True):
        """Predictions for every row; fn_of_row(row) gives the curve function of that row."""
        return np.array([cl.qpred(self.los, r[0], r[2], fn_of_row(r), self.dB, self.dC, variant, filt) for r in self.rows])

    def splits(self, n=20):
        rng = np.random.default_rng(7)
        return [np.isin(self.ci, rng.permutation(len(self.cells))[:len(self.cells) // 2]) for _ in range(n)]

    def focus_rows(self, fam="free_flight"):
        if self.focus is None:
            return []
        rows = [(i, r[2]) for i, r in enumerate(self.rows) if r[0] == self.focus and r[1] == fam]
        return [i for i, _ in sorted(rows, key=lambda t: t[1])]

    def metrics(self, q, sel=None):
        sel = np.ones(len(q), bool) if sel is None else sel
        m, w = self.m[sel], self.w[sel]
        qq = q[sel]
        e = np.abs(m - qq)
        return cl.rho(m, qq), e.mean(), (e * w).sum() / w.sum(), (qq - m).mean()


def halfsplits(S, q, kinds, label, fams=None):
    """Calibrations fitted on one half of the cells and scored on the other, 20 times. Returns {kind: (mean
    metrics, parameters fitted on all rows)} and prints one line per calibration."""
    res = {}
    splits = S.splits()
    for fam in (fams or [None]):
        fm = np.ones(len(S.m), bool) if fam is None else S.fam == fam
        for kind in kinds:
            met, gs = [], []
            for tr in splits:
                te = ~tr & fm
                trn = np.where(tr & fm)[0]
                if kind == "none":
                    p, g = q[te], ()
                else:
                    g = fit(kind, q, S.m, trn)
                    p = make(kind)[1](q[te], g)
                gs.append(g)
                e = np.abs(S.m[te] - p)
                met.append((e.mean(), (e * S.w[te]).sum() / S.w[te].sum(), (p - S.m[te]).mean(), cl.rho(S.m[te], p)))
            mt = np.mean(met, 0)
            gfull = () if kind == "none" else fit(kind, q, S.m, np.where(fm)[0])
            S.P("%-12s %-8s %-6s MAE %.3f MAEw %.3f bias %+.3f rho %.3f | full-data params %s sd over splits %s" % (
                fam or "all", label, kind, *mt, np.round(gfull, 3).tolist(),
                np.round(np.std(gs, 0), 3).tolist() if gs[0] else "-"))
            res[(fam, kind)] = (mt, gfull)
    return res


def exp_family(a):
    S = Setup(a, ("free_flight", "glider"))
    CJ = cl.load_json(a.curves)

    def curve(key, mode="los"):
        rt, rh = (2, 3) if mode == "los" else (0, 1)
        p = np.minimum.accumulate(cl.pbins(CJ[key], rt, rh))
        return cl.curve_fn(cl.pbins(CJ[key], rt, rh)), p
    CURVES = {mode: {"free_flight": curve("PG|ALL|ALL", mode), "glider": curve("GL|ALL|ALL", mode)} for mode in ("los", "all")}
    AGLC = [curve("PG|ALL|%d" % b)[0] for b in (0, 1, 2)]
    # the snapshot's bands are 0-300, 300-600, 600-1200, above 1200 m; the height-above-ground curves have three
    AGLMAP = {0: 0, 1: 1, 2: 1, 3: 2}
    for fam in ("free_flight", "glider"):
        S.P("curve", fam, "LOS", np.round(CURVES["los"][fam][1], 3).tolist())
    S.P("rows", len(S.rows), "free_flight", int((S.fam == "free_flight").sum()), "glider", int((S.fam == "glider").sum()),
        "cells", len(S.cells))

    def qs(variant, mode="los", agl=False):
        def fn(r):
            return AGLC[AGLMAP[r[2]]] if (agl and r[1] == "free_flight") else CURVES[mode][r[1]][0]
        return S.q(fn, variant, filt=(mode == "los"))
    Q = {"A": qs("A"), "B": qs("B"), "C": qs("C"), "A_agl": qs("A", agl=True), "A_nolos": qs("A", "all"), "C_nolos": qs("C", "all")}
    S.P("\n== uncalibrated, all rows")
    for fam in ("free_flight", "glider"):
        t = S.fam == fam
        for name, q in Q.items():
            r_, mae, maew, bias = S.metrics(q, t)
            S.P("%-11s %-12s n=%d Spearman %.3f MAE %.3f MAEw %.3f bias(pred-meas) %+.3f" % (name, fam, t.sum(), r_, mae, maew, bias))
    S.P("\n== calibrations, 20 random half-splits by cell, fitted per family on the train half, scored on the test half (means)")
    table = {}
    for vname in ("A", "B", "C", "A_agl", "A_nolos"):
        for (fam, kind), v in halfsplits(S, Q[vname], ("none", "scale", "gamma", "logit"), vname, ("free_flight", "glider")).items():
            table[(fam, vname, kind)] = v
    foc = S.focus_rows()
    if foc:
        S.P("\n== focus cell %d:%d, free flight, by band; measured %s" % (*S.focus, [round(float(S.m[i]), 2) for i in foc]))
        for vname in ("A", "B", "C", "A_agl", "A_nolos"):
            for kind in ("none", "scale", "gamma", "logit"):
                g = table[("free_flight", vname, kind)][1]
                f = (lambda x, g: x) if kind == "none" else make(kind)[1]
                S.P("focus %-8s %-6s predicted %s (params %s)" % (vname, kind, [round(float(f(Q[vname][i], g)), 2) for i in foc],
                                                                   np.round(g, 3).tolist()))
    return S


def exp_ogn(a):
    S = Setup(a, ("free_flight",))
    C = cl.load_json(a.curves)
    pF, pN = cl.pbins(C["PG|FLARM|ALL"], 2, 3), cl.pbins(C["PG|FANET|ALL"], 2, 3)
    CUR = {"(a) FLARM OGN-only": cl.curve_fn(pF), "(b) FANET OGN-only": cl.curve_fn(pN),
           "(c) 1-(1-pF)(1-pN)": cl.curve_fn(1 - (1 - pF) * (1 - pN)), "pooled PG OGN-only": cl.curve_fn(cl.pbins(C["PG|ALL|ALL"], 2, 3))}
    S.P("curves", {k: np.round(f(cl.CEN), 2).tolist() for k, f in CUR.items()})
    foc = S.focus_rows()
    for name, f in CUR.items():
        for var in (("A", "B", "C") if name.startswith("(c)") else ("A",)):
            Q = S.q(lambda r: f, var)
            r_, mae, maew, bias = S.metrics(Q)
            S.P("%-22s %s Spearman %.3f MAE %.3f MAEw %.3f bias %+.3f | focus cell %s (measured %s)" % (
                name, var, r_, mae, maew, bias, [round(float(Q[i]), 2) for i in foc], [round(float(S.m[i]), 2) for i in foc]))
    return S


def exp_any(a):
    S = Setup(a, ("free_flight",))
    C = cl.load_json(a.curves)
    CA = cl.load_json(a.curves_any)
    pF, pN = cl.pbins(C["PG|FLARM|ALL"], 2, 3), cl.pbins(C["PG|FANET|ALL"], 2, 3)
    pFp, pNp = cl.pbins(CA["FLARM_probe"], 2, 3), cl.pbins(CA["FANET_probe"], 2, 3)
    CUR = {"base PG LOS": cl.curve_fn(cl.pbins(C["PG|ALL|ALL"], 2, 3)),
           "(a) any-system probe": cl.curve_fn(cl.pbins(CA["any_probe"], 2, 3)),
           "(b) 1-(1-pF)(1-pN) global": cl.curve_fn(1 - (1 - pF) * (1 - pN)),
           "(b') same, probe-subset p": cl.curve_fn(1 - (1 - pFp) * (1 - pNp))}
    S.P("rows", len(S.rows), "curves at bins:")
    for name, f in CUR.items():
        S.P("  %-28s" % name, np.round(f(cl.CEN), 2).tolist())
    foc = S.focus_rows()
    for name, f in CUR.items():
        Q = S.q(lambda r: f)
        r_, mae, maew, bias = S.metrics(Q)
        S.P("\n##", name, "uncalibrated: Spearman %.3f MAE %.3f MAEw %.3f bias %+.3f" % (r_, mae, maew, bias))
        S.P("   focus cell measured", [round(float(S.m[i]), 2) for i in foc], "uncalibrated", [round(float(Q[i]), 2) for i in foc])
        for kind, (mt, g) in halfsplits(S, Q, ("scale", "logit"), name).items():
            S.P("   focus cell calibrated with", kind[1], [round(float(make(kind[1])[1](Q[i], g)), 2) for i in foc])
    return S


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--experiment", choices=("family", "ogn", "any"), required=True)
    ap.add_argument("--coverage-gaps", required=True, help="coverage-gaps snapshot, JSON")
    ap.add_argument("--los", required=True, help="los.pkl from los_cells.py")
    ap.add_argument("--stations", required=True, help="stations.json from stations_snapshot.py")
    ap.add_argument("--curves", required=True, help="summed counts of a per-system reach.py report (JSON)")
    ap.add_argument("--curves-any", default="", help="--experiment any: the JSON of reach.py report --any-system")
    ap.add_argument("--focus-cell", default="", help="LAT_IDX:LON_IDX, a cell to print by band, for a known site")
    ap.add_argument("--text", default="", help="also write the report to this file")
    a = ap.parse_args()
    if a.experiment == "any" and not a.curves_any:
        ap.error("--experiment any needs --curves-any")
    S = {"family": exp_family, "ogn": exp_ogn, "any": exp_any}[a.experiment](a)
    if a.text:
        with open(a.text, "w") as f:
            f.write("\n".join(S.out) + "\n")


if __name__ == "__main__":
    main()
