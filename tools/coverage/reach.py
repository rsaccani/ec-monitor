#!/usr/bin/env python3
"""Reception by distance: how often a ground station within reach hears an aircraft that is in the air.

Two steps. `run` reads a feed (hourly OGN/APRS files, one worker per day) and writes one small pickle of counts per
day. `report` adds the days up, prints the curves and writes them as JSON, with intervals from resampling whole days.
Aggregates only: nothing in the outputs identifies an aircraft.

  python3 reach.py run --feed FEED --terrain TERRAIN.i16 --out DIR --stations ogn --los
  python3 reach.py report --out DIR --json curves_ogn.json

What is counted. The feed is cut into aircraft-minutes: the first fix of an address in a minute, with the set of
stations that received any packet of it that minute. Every station that was active that day (it forwarded at least
one packet of the same aircraft family and system) and lies within 100 km of the fix is a pair. A pair is a hit
when the station is in that minute's set, a miss otherwise. Counts go per 1 km bin of distance, per family
(PG paraglider and hang glider, GL glider, PW powered), per system (FLARM, FANET, TRK) and per height above ground
(0: under 300 m, 1: 300-1000 m, 2: above 1000 m). Each cell holds four counts: pairs, hits, pairs in line of
sight, hits in line of sight. The line of sight (covlib.los_pairs) is what separates a station that is too far from
one that is simply behind a hill; without --los the two line-of-sight counts stay zero.

--stations ogn counts only the stations of the Open Glider Network (the curves behind METHOD.md, coverage in
detail), `all` adds FANET ground stations. --any-system switches to the probe analysis: paragliders and hang
gliders that sent on two or more systems that day are probes, a station is a candidate when it was active for one
of the probe's systems, and a hit is a reception on any system in that minute; it also splits the dip at 0-2 km by
system, station kind, speed and height. The probe curves answer what a station hears of an aircraft that carries
several radios; the per-system ones answer how good each radio is.
"""
import argparse
import collections
import glob
import json
import math
import multiprocessing as mp
import os
import pickle
import sys
import time
import zlib

import numpy as np
from scipy.spatial import cKDTree

import covlib as cl

sources = cl.sources


def _keep_station(stations, name, kind):
    s = stations.get(name)
    return s is not None and (kind == "all" or s[2] == "OGN")


def _pair_chunks(sla, slo, alat, alon, in_model, agr, chunk=3000):
    """Station-aircraft pairs within 100 km, a chunk of aircraft at a time (a k-d tree on unit vectors finds the
    candidates, the haversine distance decides). Yields (station index, aircraft index, distance m)."""
    tree_s = cKDTree(cl.xyz(sla, slo))
    chord = 2 * math.sin(100000.0 / cl.R / 2)
    order = np.argsort(alat)
    for c0 in range(0, len(alat), chunk):
        ai = order[c0:c0 + chunk]
        tree_a = cKDTree(cl.xyz(alat[ai], alon[ai]))
        sm = tree_s.sparse_distance_matrix(tree_a, chord, output_type="ndarray")
        si = sm["i"].astype(np.int64)
        aj = ai[sm["j"].astype(np.int64)]
        D = cl.hav(sla[si], slo[si], alat[aj], alon[aj])
        keep = (D < 100000) & in_model[si] & ~np.isnan(agr[aj])
        if keep.any():
            yield si[keep], aj[keep], D[keep]


def run_day(job):
    """One day, one process: parse, then pair up, test the line of sight and count."""
    day, stations, opts = job
    feed, terrain, out, kind, sample, do_los = (opts[k] for k in ("feed", "terrain", "out", "stations", "sample", "los"))
    cl.set_terrain(terrain)
    t0 = time.time()
    stations = dict(stations)
    ams = {}                       # (addr, minute, system) -> [family, lat, lon, alt, set(receiving stations)]
    act = collections.defaultdict(set)
    nlines = 0
    nfiles = sum(os.path.exists(cl.feed_path(feed, day, h)) for h in range(24))
    for ts, src, tocall, rx, body in cl.receptions(feed, day, stations):
        fx = cl.flying_fix(body)
        if fx is None:
            continue
        m, cat, speed, alt = fx
        fam = cl.FAM.get(cat)
        sysn = cl.SYS.get(tocall)
        if fam is None or sysn is None:
            continue
        nlines += 1
        act[(sysn, fam)].add(rx)
        # --sample N: keep 1 address in N of gliders and powered (a stable hash, so a day is reproducible);
        # paragliders are always all, they are the thin population the curves are about.
        if fam != "PG" and sample > 1 and zlib.crc32(src.encode()) % sample:
            continue
        key = (src, int(float(ts)) // 60, sysn)
        e = ams.get(key)
        if e is None:
            lat, lon = cl.latlon(m, body)
            ams[key] = [fam, lat, lon, alt, {rx}]
        else:
            e[4].add(rx)
    t_parse = time.time() - t0
    agg = {}
    npairs = 0
    for (sysn, fam), names in act.items():
        names = sorted(n for n in names if _keep_station(stations, n, kind))
        keys = [k for k, v in ams.items() if v[0] == fam and k[2] == sysn]
        if not names or not keys:
            continue
        sidx = {n: i for i, n in enumerate(names)}
        M = len(names)
        sla = np.array([stations[n][0] for n in names])
        slo = np.array([stations[n][1] for n in names])
        sgr = cl.elev(sla, slo)
        in_model = ~np.isnan(sgr)
        alat = np.array([ams[k][1] for k in keys])
        alon = np.array([ams[k][2] for k in keys])
        aalt = np.array([ams[k][3] for k in keys])
        agr = cl.elev(alat, alon)
        hk = np.unique(np.array([i * M + sidx[rx] for i, k in enumerate(keys) for rx in ams[k][4] if rx in sidx],
                                dtype=np.int64))
        for si, aj, D in _pair_chunks(sla, slo, alat, alon, in_model, agr):
            hit = np.isin(aj * M + si, hk, assume_unique=False)
            agl = aalt[aj] - agr[aj]
            ab = np.where(agl < 300, 0, np.where(agl < 1000, 1, 2))
            zs = sgr[si] + cl.ANT
            zp = np.maximum(aalt[aj], agr[aj] + 2)
            # sorted by distance, so that the sample matrix of a piece is only as wide as its longest ray needs
            o = np.argsort(D)
            si, aj, D, hit, ab, zs, zp = si[o], aj[o], D[o], hit[o], ab[o], zs[o], zp[o]
            los = np.zeros(len(D), bool)
            if do_los:
                for q in range(0, len(D), 8000):
                    s = slice(q, q + 8000)
                    los[s] = cl.los_pairs(sla[si[s]], slo[si[s]], zs[s], alat[aj[s]], alon[aj[s]], zp[s],
                                          np.maximum(D[s], 1.0))
            npairs += len(D)
            bi = np.minimum((D / 1000).astype(int), cl.NBIN - 1)
            for b_ab in range(3):
                mk = ab == b_ab
                if not mk.any():
                    continue
                e = agg.setdefault((fam, sysn, b_ab), np.zeros((4, cl.NBIN), np.int64))
                e[0] += np.bincount(bi[mk], minlength=cl.NBIN)
                e[1] += np.bincount(bi[mk & hit], minlength=cl.NBIN)
                e[2] += np.bincount(bi[mk & los], minlength=cl.NBIN)
                e[3] += np.bincount(bi[mk & los & hit], minlength=cl.NBIN)
    res = dict(day=day, agg=agg, nfiles=nfiles, receptions=nlines, aircraft_minutes=len(ams), pairs=npairs,
               stations_active={k: len(v) for k, v in act.items()}, wall=time.time() - t0, wall_parse=t_parse,
               maxrss_mb=cl.maxrss_mb())
    with open(os.path.join(out, "agg-%s.pkl" % day), "wb") as f:
        pickle.dump(res, f)
    print(day, "done wall %.0fs parse %.0fs pairs %d aircraft-minutes %d" % (res["wall"], t_parse, npairs, len(ams)), flush=True)
    return day


def run_day_any(job):
    """The probe analysis of one day (see the module text)."""
    day, stations, opts = job
    feed, terrain, out, kind = (opts[k] for k in ("feed", "terrain", "out", "stations"))
    cl.set_terrain(terrain)
    idf = sources._id_field
    t0 = time.time()
    stations = dict(stations)
    ams = {}       # (address, minute) -> [lat, lon, alt, speed_kt, {system: set(receiving stations)}]
    act = collections.defaultdict(set)
    daysys = collections.defaultdict(set)
    for ts, src, tocall, rx, body in cl.receptions(feed, day, stations):
        im = idf.search(body)
        if not im:
            continue
        fx = cl.flying_fix(body)
        if fx is None:
            continue
        m, cat, speed, alt = fx
        if cl.FAM.get(cat) != "PG":
            continue
        sysn = cl.SYS.get(tocall)
        if sysn is None:
            continue
        # the address is the last six hex digits of the id field: the same device on its different radios
        ad = im.group(1)[-6:]
        act[sysn].add(rx)
        daysys[ad].add(sysn)
        key = (ad, int(float(ts)) // 60)
        e = ams.get(key)
        if e is None:
            lat, lon = cl.latlon(m, body)
            e = ams[key] = [lat, lon, alt, speed, {}]
        e[4].setdefault(sysn, set()).add(rx)
    t_parse = time.time() - t0
    names = sorted(set().union(*act.values()) & set(stations)) if act else []
    names = [n for n in names if _keep_station(stations, n, kind)]
    res = dict(day=day, nam=len(ams), probes=sum(1 for v in daysys.values() if len(v) >= 2), addresses=len(daysys),
               cnt={}, dip={})
    if not names or not ams:
        with open(os.path.join(out, "agg-%s.pkl" % day), "wb") as f:
            pickle.dump(res, f)
        return day
    sidx = {n: i for i, n in enumerate(names)}
    M = len(names)
    sla = np.array([stations[n][0] for n in names])
    slo = np.array([stations[n][1] for n in names])
    skind = np.array([stations[n][2] == "FANET" for n in names])
    sgr = cl.elev(sla, slo)
    in_model = ~np.isnan(sgr)
    actf = {s: np.array([n in act[s] for n in names]) for s in cl.SYSL}
    keys = list(ams.keys())
    alat = np.array([ams[k][0] for k in keys])
    alon = np.array([ams[k][1] for k in keys])
    aalt = np.array([ams[k][2] for k in keys])
    aspd = np.array([ams[k][3] for k in keys])
    agr = cl.elev(alat, alon)
    present = {s: np.array([s in ams[k][4] for k in keys]) for s in cl.SYSL}
    dsys = {s: np.array([s in daysys[k[0]] for k in keys]) for s in cl.SYSL}
    probe = np.array([len(daysys[k[0]]) >= 2 for k in keys])
    hk = {}
    for s in cl.SYSL:
        l = [i * M + sidx[rx] for i, k in enumerate(keys) for rx in ams[k][4].get(s, ()) if rx in sidx]
        hk[s] = np.unique(np.array(l, dtype=np.int64))
    cnt = collections.defaultdict(lambda: np.zeros((4, cl.NBIN), np.int64))
    dip = collections.defaultdict(lambda: np.zeros(2, np.int64))
    for si, aj, D in _pair_chunks(sla, slo, alat, alon, in_model, agr):
        pk = aj * M + si
        hit = {s: np.isin(pk, hk[s]) for s in cl.SYSL}
        zs = sgr[si] + cl.ANT
        zp = np.maximum(aalt[aj], agr[aj] + 2)
        o = np.argsort(D)
        los = np.empty(len(D), bool)
        for q in range(0, len(D), 8000):
            ix = o[q:q + 8000]
            los[ix] = cl.los_pairs(sla[si[ix]], slo[si[ix]], zs[ix], alat[aj[ix]], alon[aj[ix]], zp[ix],
                                   np.maximum(D[ix], 1.0))
        bi = np.minimum((D / 1000).astype(int), cl.NBIN - 1)
        # eligible: the aircraft sent on that system that day and the station was active on it
        elig = {s: dsys[s][aj] & actf[s][si] for s in cl.SYSL}
        anyelig = elig["FLARM"] | elig["FANET"] | elig["TRK"]
        hany = hit["FLARM"] | hit["FANET"] | hit["TRK"]
        pr = probe[aj]

        def add(name, mk, h):
            e = cnt[name]
            e[0] += np.bincount(bi[mk], minlength=cl.NBIN)
            e[1] += np.bincount(bi[mk & h], minlength=cl.NBIN)
            e[2] += np.bincount(bi[mk & los], minlength=cl.NBIN)
            e[3] += np.bincount(bi[mk & los & h], minlength=cl.NBIN)
        add("any_probe", pr & anyelig, hany)
        add("any_single", ~pr & anyelig, hany)
        for s in cl.SYSL:
            add(s + "_probe", pr & elig[s], hit[s])
        # The dip at 0-2 km: per system present in the minute, line-of-sight pairs only, 0-2 and 2-5 km, split by
        # station kind, the aircraft's speed (under 15, 15-25, 25+ kt) and its height above ground (50, 300 m).
        agl = aalt[aj] - agr[aj]
        spb = np.where(aspd[aj] < 15, 0, np.where(aspd[aj] < 25, 1, 2))
        ab = np.where(agl < 50, 0, np.where(agl < 300, 1, 2))
        dbk = np.where(D < 2000, 0, np.where(D < 5000, 1, -1))
        for s in cl.SYSL:
            mk = present[s][aj] & actf[s][si] & los & (dbk >= 0)
            if not mk.any():
                continue
            for kd in (0, 1):
                mm = mk & (skind[si] == bool(kd))
                for db_, spb_, ab_ in set(zip(dbk[mm].tolist(), spb[mm].tolist(), ab[mm].tolist())):
                    z = mm & (dbk == db_) & (spb == spb_) & (ab == ab_)
                    dip[(s, kd, db_, spb_, ab_)] += np.array([z.sum(), (z & hit[s]).sum()])
    res["cnt"] = dict(cnt)
    res["dip"] = dict(dip)
    res["wall"] = time.time() - t0
    res["parse"] = t_parse
    with open(os.path.join(out, "agg-%s.pkl" % day), "wb") as f:
        pickle.dump(res, f)
    print(day, "done %.0fs (parse %.0fs) aircraft-minutes %d probes %d/%d" % (
        res["wall"], t_parse, len(ams), res["probes"], res["addresses"]), flush=True)
    return day


def cmd_run(a):
    days = cl.parse_days(a.days, a.feed)
    os.makedirs(a.out, exist_ok=True)
    kind = a.stations or ("all" if a.any_system else "ogn")
    opts = dict(feed=a.feed, terrain=a.terrain, out=a.out, stations=kind, sample=a.sample, los=a.los)
    t0 = time.time()
    workers = max(1, min(a.workers, len(days)))
    # Station positions first, from all the days together: a station that beacons on some days only is still placed
    # on the days when it carried traffic and did not beacon in the sampled hour.
    with mp.Pool(workers) as pool:
        parts = pool.map(cl.station_positions, [(a.feed, d) for d in days])
    stations = {}
    for d in parts:
        stations.update(d)
    print("station positions", len(stations), "prepass %.0fs" % (time.time() - t0), flush=True)
    fn = run_day_any if a.any_system else run_day
    with mp.Pool(workers, maxtasksperchild=1) as pool:
        for _ in pool.imap_unordered(fn, [(d, stations, opts) for d in days]):
            pass
    print("all done %.0fs" % (time.time() - t0))


# ---------------------------------------------------------------- report
def _cross(v, row_t, row_h, level):
    """First distance where the 1 km bin probability, smoothed over five bins, falls under `level`."""
    t = v[row_t].astype(float)
    h = v[row_h].astype(float)
    ts = np.convolve(t, np.ones(5), "same")
    hs = np.convolve(h, np.ones(5), "same")
    with np.errstate(all="ignore"):
        p = np.where(ts > 30, hs / ts, np.nan)
    for i in range(1, 100):
        if not np.isnan(p[i]) and p[i] < level and p[i - 1] >= level:
            return i + 0.5
    for i in range(0, 100):
        if not np.isnan(p[i]) and p[i] < level:
            return i + 0.5
    return None


def report_system(As, a):
    T = collections.defaultdict(lambda: np.zeros((4, 100), np.int64))   # (family, system|ALL, height|ALL)
    for ag in As:
        for (fam, sysn, ab), v in ag["agg"].items():
            for s in (sysn, "ALL"):
                for b in (ab, "ALL"):
                    T[(fam, s, b)] += v
    out = []
    P = lambda *x: out.append(" ".join(str(i) for i in x))
    P("days", len(As), "files", sum(g["nfiles"] for g in As), "aircraft-minutes", sum(g["aircraft_minutes"] for g in As),
      "pairs", sum(g["pairs"] for g in As))

    def table(title, keys):
        P("\n==", title)
        P("bins km:", ["%d-%d" % (x, y) for x, y in zip(cl.EDGES[:-1], cl.EDGES[1:])])
        for key in keys:
            v = T.get(key)
            if v is None:
                continue
            name = "/".join(map(str, key))
            for mode, (rt, rh) in (("LOS", (2, 3)), ("all", (0, 1))):
                bs = [(int(v[rt, lo:hi].sum()), int(v[rh, lo:hi].sum())) for lo, hi in zip(cl.EDGES[:-1], cl.EDGES[1:])]
                cells = ["  -  " if n < 30 else "%.2f" % (k / n) for n, k in bs]
                P("%-12s %-4s p=" % (name, mode), cells, "n_total=%d" % sum(n for n, _ in bs),
                  "p<50%% at %s km, <20%% at %s km" % (_cross(v, rt, rh, .5), _cross(v, rt, rh, .2)))
            bs = [(int(v[2, lo:hi].sum()), int(v[3, lo:hi].sum())) for lo, hi in zip(cl.EDGES[:-1], cl.EDGES[1:])]
            P("%-12s LOS n=" % name, [n for n, _ in bs])
            P("%-12s LOS 95%% CI=" % name, ["%.2f-%.2f" % cl.wilson(k, n) if n >= 30 else "-" for n, k in bs])
    fams = ["PG", "GL", "PW"]
    table("by family (all systems, all heights)", [(f, "ALL", "ALL") for f in fams])
    table("by family and system", [(f, s, "ALL") for f in fams for s in cl.SYSL])
    table("paraglider+hang glider by height above ground (0 <300, 1 300-1000, 2 >1000 m)", [("PG", "ALL", b) for b in (0, 1, 2)])
    # The Wilson intervals above treat every pair as independent, but the pairs of one minute are not: they are far
    # too narrow. Resampling whole days (days are the independent unit) gives the honest interval.
    rng = np.random.default_rng(3)

    def resampled(select, draws, title):
        out.append(title)
        for label, sel in select:
            D = np.zeros((len(As), 4, 100), np.int64)
            for i, ag in enumerate(As):
                for (f, s, ab), v in ag["agg"].items():
                    if sel(f, s):
                        D[i] += v
            est = []
            for _ in range(draws):
                w = np.bincount(rng.integers(0, len(As), len(As)), minlength=len(As))
                v = np.tensordot(w, D, 1)
                est.append([v[3, lo:hi].sum() / max(1, v[2, lo:hi].sum()) for lo, hi in zip(cl.EDGES[:-1], cl.EDGES[1:])])
            est = np.array(est)
            pt = [D[:, 3, lo:hi].sum() / max(1, D[:, 2, lo:hi].sum()) for lo, hi in zip(cl.EDGES[:-1], cl.EDGES[1:])]
            out.append("%s: " % label + " ".join("%.2f(%.2f-%.2f)" % (pt[j], *np.percentile(est[:, j], [2.5, 97.5])) for j in range(10)))
    resampled([(f, lambda ff, s, f=f: ff == f) for f in fams], 2000,
              "\n== 95% interval from resampling days (2000 draws), LOS, by family, all systems")
    resampled([("%s/%s" % (f, sy), lambda ff, s, f=f, sy=sy: ff == f and s == sy) for f in fams for sy in cl.SYSL], 1000,
              "\n== day-resampled 95% intervals, LOS, by family/system")
    with open(a.json, "w") as f:
        json.dump({"|".join(map(str, k)): v.tolist() for k, v in T.items()}, f)
    return out


def report_any(As, a):
    T = collections.defaultdict(lambda: np.zeros((4, 100), np.int64))
    DIP = collections.defaultdict(lambda: np.zeros(2, np.int64))
    for g in As:
        for k, v in g["cnt"].items():
            T[k] += v
        for k, v in g["dip"].items():
            DIP[k] += v
    with open(a.json, "w") as f:
        json.dump({k: v.tolist() for k, v in T.items()}, f)
    nb = lambda v, rt: [int(v[rt, lo:hi].sum()) for lo, hi in zip(cl.EDGES[:-1], cl.EDGES[1:])]
    out = []
    P = lambda *x: out.append(" ".join(str(i) for i in x))
    P("days", len(As), "addresses", sum(g["addresses"] for g in As), "probe address-days", sum(g["probes"] for g in As))
    P("bins", cl.EDGES)
    for name in ("any_probe", "FLARM_probe", "FANET_probe", "TRK_probe", "any_single"):
        v = T[name]
        P("%-12s LOS" % name, np.round(cl.pbins(v, 2, 3), 2).tolist(), "n", nb(v, 2))
        P("%-12s all" % name, np.round(cl.pbins(v, 0, 1), 2).tolist())
    pf, pn = cl.pbins(T["FLARM_probe"], 2, 3), cl.pbins(T["FANET_probe"], 2, 3)
    P("1-(1-pF)(1-pN) probe-subset per-system LOS", np.round(1 - (1 - pf) * (1 - pn), 2).tolist())
    if a.system_curves:
        # What two independent radios would give: the per-system curves of the same paragliders, combined. A probe
        # curve above it means the radios of one aircraft are heard together more than chance.
        C = cl.load_json(a.system_curves)
        gf, gn = cl.pbins(C["PG|FLARM|ALL"], 2, 3), cl.pbins(C["PG|FANET|ALL"], 2, 3)
        P("global per-system PG LOS FLARM", np.round(gf, 2).tolist(), "FANET", np.round(gn, 2).tolist())
        P("1-(1-pF)(1-pN) global per-system", np.round(1 - (1 - gf) * (1 - gn), 2).tolist())
    P("\n== dip, LOS pairs, PG, d 0-2 km vs 2-5 km: p (n)")

    def agg(f):
        r = {}
        for db in (0, 1):
            t = h = 0
            for k, v in DIP.items():
                if k[2] == db and f(k):
                    t += v[0]
                    h += v[1]
            r[db] = (round(h / max(1, t), 2), int(t))
        return r
    for s in cl.SYSL:
        P("system", s, agg(lambda k: k[0] == s))
    for s in ("FLARM", "FANET"):
        for kd, kn in ((0, "OGN station"), (1, "FANET ground station")):
            P(s, kn, agg(lambda k: k[0] == s and k[1] == kd))
    for sp, sn in ((0, "<15 kt"), (1, "15-25 kt"), (2, ">=25 kt")):
        P("speed", sn, agg(lambda k: k[3] == sp))
    for ab, an in ((0, "<50 m agl"), (1, "50-300 m"), (2, ">300 m")):
        P("height", an, agg(lambda k: k[4] == ab))
    P("all", agg(lambda k: True))
    return out


def cmd_report(a):
    As = [pickle.load(open(f, "rb")) for f in sorted(glob.glob(os.path.join(a.out, "agg-*.pkl")))]
    if not As:
        sys.exit("no agg-*.pkl in %s" % a.out)
    out = report_any(As, a) if a.any_system else report_system(As, a)
    text = "\n".join(out) + "\n"
    if a.text:
        with open(a.text, "w") as f:
            f.write(text)
    print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="count hits and misses per day")
    r.add_argument("--feed", required=True, help="directory of hourly files YYYYMMDD-HH.aprs.zst")
    r.add_argument("--terrain", required=True, help="flat int16 terrain grid (covlib.Terrain)")
    r.add_argument("--out", required=True, help="directory for the per-day pickles")
    r.add_argument("--days", default="", help="comma-separated YYYYMMDD; default: every day of the feed")
    r.add_argument("--workers", type=int, default=10, help="one day per worker; 10 cores did 24 days in about 4 minutes")
    r.add_argument("--stations", choices=("ogn", "all"), default=None,
                   help="which ground stations count; default ogn, or all with --any-system")
    r.add_argument("--los", action="store_true",
                   help="test the line of sight (the slow part); without it the LOS counts stay zero")
    r.add_argument("--any-system", action="store_true", help="the probe analysis, hits on any system")
    r.add_argument("--sample", type=int, default=1,
                   help="keep 1 address in N of gliders and powered aircraft (paragliders always all)")
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="add the days up")
    p.add_argument("--out", required=True, help="the directory `run` wrote")
    p.add_argument("--json", required=True, help="where to write the summed counts (the curves other tools read)")
    p.add_argument("--text", default="", help="also write the printed report here")
    p.add_argument("--any-system", action="store_true", help="report a run made with --any-system")
    p.add_argument("--system-curves", default="",
                   help="--any-system only: the JSON of a per-system report, to compare with")
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
