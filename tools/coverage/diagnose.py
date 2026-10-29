#!/usr/bin/env python3
"""Why does the coverage model miss? Three suspects, tested one by one and together.

`weights` reads the feed and writes, per day, two things: the probes' airborne seconds on a 0.01-degree grid per
height band (so that the model's points can be weighted by where probes really fly instead of spread evenly over the
cell), and the uptime of every station between 08:00 and 18:00 UTC (a station that is alive 60 % of the day does not
cover 100 % of it). Probes are paragliders and hang gliders that sent on two or more systems that day; their fixes
are joined by interpolation across gaps up to 5 minutes, in steps of 10 seconds.

`run` recomputes the model of validate.py (variant A, free flight, uncalibrated) with the suspects switched on:

  1  flight-weighted points: the 200 busiest 0.01-degree squares of the cell and band, weighted by probe seconds,
     instead of the 64 even points (the probes fly where the lift is, not over the whole cell).
  2  station uptime: each station's p(d) multiplied by its share of the 08-18 UTC day that it was alive (any beacon
     or forwarded packet within the previous 10 minutes).
  3  the probes' mean height above ground in the cell and band, instead of the middle of the band.

and every combination, for the combined curve 1-(1-pF)(1-pN) (a probe carries both radios) and for FLARM alone,
both from an OGN-only reach.py report. Per configuration: Spearman correlation, MAE, MAEw (weighted by probe
hours), bias, and the focus cell's three bands. A suspect that does not move these figures is not what is wrong.

  python3 diagnose.py weights --feed FEED --terrain T.i16 --out W --days 20261011,20261012
  python3 diagnose.py run --coverage-gaps cg.json --los los.pkl --stations stations.json --curves curves_ogn.json \\
      --weights W --terrain T.i16
Aggregates only.
"""
import argparse
import collections
import glob
import multiprocessing as mp
import os
import pickle
import time

import numpy as np

import covlib as cl

sources = cl.sources
GAP = 300                  # s: a gap in a probe's fixes up to this is flown through, longer ones are a landing
STEP_S = 10                # s: the interpolation step
UP_FROM, UP_TO = 470, 1080 # minutes of the UTC day in which events count for the uptime (08:00 minus the 10 minute look-back, to 18:00)
CAP = 200                  # squares per cell and band that carry the flight weights
MID = [150., 450., 900., 1500.]


def weights_day(job):
    day, feed, terrain, out = job
    cl.set_terrain(terrain)
    t0 = time.time()
    fixes = {}                          # (address, second) -> (lat, lon, alt)
    daysys = collections.defaultdict(set)
    ev = collections.defaultdict(set)   # station -> minutes of the window with an event
    for h in range(24):
        path = cl.feed_path(feed, day, h)
        if not os.path.exists(path):
            continue
        p = cl.stream(path)
        for line in p.stdout:
            sp = cl.split_line(line)
            if sp is None:
                continue
            ts, src, tocall, path_, body = sp
            md = (int(float(ts)) // 60) % 1440
            if UP_FROM <= md < UP_TO:
                if tocall == "OGNSDR":
                    ev[src].add(md)
                    continue
                if ",qAS," in "," + path_:
                    ev[path_.rsplit(",", 1)[-1]].add(md)
            elif tocall == "OGNSDR":
                continue
            if tocall == "OGNSDR" or ",qAS," not in "," + path_:
                continue
            rx = path_.rsplit(",", 1)[-1]
            if "mqtt" in rx.lower() or rx.startswith(("SKT", "SKX")):
                continue
            im = sources._id_field.search(body)
            fx = cl.flying_fix(body)
            if not im or fx is None:
                continue
            m, cat, speed, alt = fx
            sysn = cl.SYS.get(tocall)
            if cl.FAM.get(cat) != "PG" or sysn is None:
                continue
            ad = im.group(1)[-6:]
            daysys[ad].add(sysn)
            k = (ad, int(float(ts)))
            if k in fixes:
                continue
            lat, lon = cl.latlon(m, body)
            fixes[k] = (lat, lon, alt)
        p.wait()
    up = {}
    for st, mins in ev.items():
        arr = np.zeros(1100, np.int32)
        for mm in mins:
            arr[mm - UP_FROM] = 1
        c = np.concatenate([[0], np.cumsum(arr)])
        t = np.arange(10, 610)                      # minutes 08:00 + i
        alive = (c[t + 1] - c[t - 10]) > 0          # an event in minutes (t-10 .. t]
        up[st] = float(alive.mean())
    keep = [(k[0], k[1], v[0], v[1], v[2]) for k, v in fixes.items() if len(daysys[k[0]]) >= 2]
    wres = (np.zeros(0, np.int64), np.zeros(0), np.zeros(0))
    nprobe_s = 0.0
    if keep:
        ad = np.array([x[0] for x in keep])
        _, aid = np.unique(ad, return_inverse=True)
        t = np.array([x[1] for x in keep], float)
        la = np.array([x[2] for x in keep])
        lo = np.array([x[3] for x in keep])
        al = np.array([x[4] for x in keep])
        o = np.lexsort((t, aid))
        aid, t, la, lo, al = aid[o], t[o], la[o], lo[o], al[o]
        i = np.where((aid[1:] == aid[:-1]) & (t[1:] - t[:-1] > 0) & (t[1:] - t[:-1] <= GAP))[0]
        dt = t[i + 1] - t[i]
        n = np.ceil(dt / STEP_S).astype(int)
        rep = np.repeat(np.arange(len(i)), n)
        k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
        f = (k + 0.5) / n[rep]
        a0 = i[rep]
        sla = la[a0] + (la[a0 + 1] - la[a0]) * f
        slo = lo[a0] + (lo[a0 + 1] - lo[a0]) * f
        sal = al[a0] + (al[a0 + 1] - al[a0]) * f
        sw = dt[rep] / n[rep]
        nprobe_s = float(sw.sum())
        agl = sal - np.nan_to_num(cl.elev(sla, slo), nan=0.0)
        band = np.where(agl < 300, 0, np.where(agl < 600, 1, np.where(agl < 1200, 2, 3)))
        ila = np.floor(sla / 0.01).astype(np.int64)
        ilo = np.floor(slo / 0.01).astype(np.int64)
        key = ((ila + 20000) * 40000 + (ilo + 20000)) * 4 + band
        u, inv = np.unique(key, return_inverse=True)
        wres = (u, np.bincount(inv, sw), np.bincount(inv, sw * np.maximum(agl, 0)))
    res = dict(day=day, w=wres, up=up, probe_s=nprobe_s, wall=time.time() - t0)
    with open(os.path.join(out, "agg-%s.pkl" % day), "wb") as f:
        pickle.dump(res, f)
    print(day, "done %.0fs probe hours %.0f stations %d" % (res["wall"], nprobe_s / 3600, len(up)), flush=True)
    return day


def cmd_weights(a):
    days = cl.parse_days(a.days, a.feed)
    os.makedirs(a.out, exist_ok=True)
    with mp.Pool(max(1, min(a.workers, len(days))), maxtasksperchild=1) as pool:
        list(pool.imap_unordered(weights_day, [(d, a.feed, a.terrain, a.out) for d in days]))


def cmd_run(a):
    cl.set_terrain(a.terrain)
    cg = cl.load_json(a.coverage_gaps)
    with open(a.los, "rb") as f:
        los = pickle.load(f)
    S = cl.load_json(a.stations)["stations"]
    C = cl.load_json(a.curves)
    pF = np.minimum.accumulate(cl.pbins(C["PG|FLARM|ALL"], 2, 3))
    pN = np.minimum.accumulate(cl.pbins(C["PG|FANET|ALL"], 2, 3))
    pC = np.minimum.accumulate(1 - (1 - pF) * (1 - pN))
    CURVES = {"combined": lambda d: np.interp(np.log(np.maximum(d, 1e-3)), cl.LC, pC),
              "FLARM-only": lambda d: np.interp(np.log(np.maximum(d, 1e-3)), cl.LC, pF)}
    W = [pickle.load(open(f, "rb")) for f in sorted(glob.glob(os.path.join(a.weights, "agg-*.pkl")))]
    upm = {}
    for r in W:
        for st, v in r["up"].items():
            upm[st] = upm.get(st, 0.0) + v / len(W)
    UP = np.array([max([upm.get(n, 0.0) for n in s["name"].split(" + ")]) for s in S])
    print("days", len(W), "uptime of stations in the model set: mean %.2f, quartiles %s" % (UP.mean(), np.round(np.percentile(UP, [25, 50, 75]), 2).tolist()))
    K = np.concatenate([r["w"][0] for r in W])
    WS = np.concatenate([r["w"][1] for r in W])
    WA = np.concatenate([r["w"][2] for r in W])
    u, inv = np.unique(K, return_inverse=True)
    WS = np.bincount(inv, WS)
    WA = np.bincount(inv, WA)
    K = u
    band = K % 4
    rest = K // 4
    ilo = rest % 40000 - 20000
    ila = rest // 40000 - 20000
    cell_la = ila // 25                       # 0.01 degree squares, 25 to a 0.25 degree cell
    cell_lo = np.floor_divide(ilo, 25)
    print("probe airborne hours total %.0f" % (WS.sum() / 3600))
    order = np.lexsort((band, cell_lo, cell_la))
    K, WS, WA, band, ila, ilo, cell_la, cell_lo = [x[order] for x in (K, WS, WA, band, ila, ilo, cell_la, cell_lo)]
    idx = {}
    for i in range(len(K)):
        idx.setdefault((int(cell_la[i]), int(cell_lo[i]), int(band[i])), []).append(i)
    rows = cl.model_rows(cg, los, ("free_flight",))
    rows = [(r[0], r[2], r[3], r[4]) for r in rows]
    m = np.array([r[2] for r in rows])
    w = np.array([r[3] for r in rows])
    print("rows", len(rows), "snapshot probe hours in rows %.0f" % w.sum())
    sub = (np.arange(8) + 0.5) / 8 * 0.25 - 0.125

    def pts_uniform(k):
        clat = (k[0] + 0.5) * 0.25
        clon = (k[1] + 0.5) * 0.25
        return (clat + sub)[:, None].repeat(8, 1).ravel(), (clon + sub)[None, :].repeat(8, 0).ravel(), np.ones(64) / 64

    def pts_weighted(k, b):
        ii = idx.get((k[0], k[1], b))
        if not ii:
            return None
        ii = np.array(ii)
        top = np.argsort(-WS[ii])[:CAP]
        ii = ii[top]
        ww = WS[ii]
        return (ila[ii] + 0.5) * 0.01, (ilo[ii] + 0.5) * 0.01, ww / ww.sum()

    def mean_agl(k, b):
        ii = idx.get((k[0], k[1], b))
        return None if not ii else float(WA[ii].sum() / WS[ii].sum())

    def los_set(k, plat, plon, h):
        r = los[k]
        si = r["sidx"]
        ns = len(si)
        npt = len(plat)
        sla = np.array([S[j]["lat"] for j in si])
        slo = np.array([S[j]["lon"] for j in si])
        gs = cl.elev(sla, slo) + 10
        gp = np.nan_to_num(cl.elev(plat, plon), nan=0.0) + h
        I = np.repeat(np.arange(ns), npt)
        J = np.tile(np.arange(npt), ns)
        D = cl.hav(sla[I], slo[I], plat[J], plon[J])
        L = cl.los_pairs(sla[I], slo[I], gs[I], plat[J], plon[J], gp[J], np.maximum(D, 1.0))
        return L.reshape(ns, npt), D.reshape(ns, npt) / 1000.0, si

    def pred(L, D, si, wt, f, up):
        pe = f(D) * (UP[si][:, None] if up else 1.0)
        return float((np.prod(1 - L * pe, axis=0) * wt).sum())

    geo = {}                      # (row, points U|W, height mid|agl) -> line of sight of that configuration
    for ri, (k, b, _, _) in enumerate(rows):
        uu = pts_uniform(k)
        wp = pts_weighted(k, b)
        ma = mean_agl(k, b)
        hm = MID[b]
        hv = ma if ma is not None else hm
        for pn, pts in (("U", uu), ("W", wp if wp is not None else uu)):
            for hn, h in (("mid", hm), ("agl", hv)):
                L, D, si = los_set(k, pts[0], pts[1], h)
                geo[(ri, pn, hn)] = (L, D, si, pts[2])
    nw = sum(1 for (k, b, _, _) in rows if pts_weighted(k, b) is not None)
    print("rows with probe weights in the window: %d of %d; mean AGL of probes per band:" % (nw, len(rows)),
          {b: round(float(np.mean([mean_agl(k, bb) for (k, bb, _, _) in rows if bb == b and mean_agl(k, bb) is not None])), 0) for b in (0, 1, 2)},
          "vs mid", MID[:3])
    foc = []
    if a.focus_cell:
        fk = tuple(int(x) for x in a.focus_cell.split(":"))
        foc = [next(i for i, r in enumerate(rows) if r[0] == fk and r[1] == b) for b in (0, 1, 2)]
    cfgs = [("base", "U", "mid", 0), ("1 flight-weighted points", "W", "mid", 0), ("2 uptime", "U", "mid", 1),
            ("3 probes' mean AGL", "U", "agl", 0), ("1+2", "W", "mid", 1), ("1+3", "W", "agl", 0),
            ("2+3", "U", "agl", 1), ("1+2+3", "W", "agl", 1)]
    out = []
    for cn, f in CURVES.items():
        out.append("== curve: " + cn)
        for name, pn, hn, up in cfgs:
            Q = np.array([pred(*geo[(ri, pn, hn)][:3], geo[(ri, pn, hn)][3], f, up) for ri in range(len(rows))])
            e = np.abs(m - Q)
            out.append("%-26s Spearman %.3f MAE %.3f MAEw %.3f bias %+.3f | focus cell %s" % (
                name, cl.rho(m, Q), e.mean(), (e * w).sum() / w.sum(), (Q - m).mean(), [round(float(Q[i]), 2) for i in foc]))
    if foc:
        out.append("focus cell measured %s" % [round(float(m[i]), 2) for i in foc])
    print("\n".join(out))
    if a.text:
        with open(a.text, "w") as f:
            f.write("\n".join(out) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weights", help="probe flight weights and station uptime, per day")
    w.add_argument("--feed", required=True)
    w.add_argument("--terrain", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--days", default="", help="comma-separated YYYYMMDD; default: every day of the feed")
    w.add_argument("--workers", type=int, default=10)
    w.set_defaults(fn=cmd_weights)
    r = sub.add_parser("run", help="the model with the suspects switched on")
    r.add_argument("--coverage-gaps", required=True)
    r.add_argument("--los", required=True)
    r.add_argument("--stations", required=True)
    r.add_argument("--curves", required=True, help="OGN-only per-system report of reach.py (JSON)")
    r.add_argument("--weights", required=True, help="the directory `weights` wrote")
    r.add_argument("--terrain", required=True)
    r.add_argument("--focus-cell", default="", help="LAT_IDX:LON_IDX, printed by band")
    r.add_argument("--text", default="")
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
