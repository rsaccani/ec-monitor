"""Shared code of the coverage analysis tools (README.md in this directory).

What lives here, and why in one place: the feed reading that every tool repeats (it goes through sources.py so that
a change of the flight or plausibility rules reaches the analysis too), the terrain model and the line-of-sight test
(the same geometry in the reception curves, the coverage model and the diagnosis, so that they cannot drift apart),
the reception curves and the coverage-model helpers. Nothing here reads a fixed path: every file is an argument.
"""
import json
import math
import os
import re
import resource
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import sources  # noqa: E402

R = 6371000.0
TWO_REFF = 2 * R * 4 / 3           # effective earth radius 4/3 R: standard atmospheric refraction
ANT = 10.0                         # station antenna above the terrain, m
NBIN = 100                         # reception curves: 1 km bins up to 100 km
EDGES = [0, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100]     # km, the bins the curves are summarised in
CEN = np.array([1.0, 3.5, 7.5, 12.5, 17.5, 25, 35, 45, 62.5, 87.5])   # km, where each summary bin is placed
LC = np.log(CEN)
# Packet category -> aircraft family, and tocall -> radio system.
FAM = {6: "PG", 7: "PG", 1: "GL", 2: "PW", 8: "PW", 9: "PW"}
SYS = {"OGFLR": "FLARM", "OGNFLR": "FLARM", "OGFLR6": "FLARM", "OGFLR7": "FLARM", "OGNFNT": "FANET", "OGNTRK": "TRK"}
SYSL = ("FLARM", "FANET", "TRK")
pos_re = re.compile(r"[/@]\d{6}h(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW])(.)")


# ---------------------------------------------------------------- terrain
class Terrain:
    """A flat little-endian int16 grid (tools/terrain/build_tiles.py documents the format), north-up, row 0 at the
    northern edge. The geometry comes from the .json beside the file when there is one (keys rows, cols, step,
    north, west); without it the 15 arc-second Europe model (72 N, 25 W, 8880 x 16800 cells) is assumed."""

    def __init__(self, path):
        meta = {"rows": 8880, "cols": 16800, "step": 1 / 240, "north": 72.0, "west": -25.0}
        side = os.path.splitext(path)[0] + ".json"
        if os.path.exists(side):
            with open(side) as f:
                j = json.load(f)
            for k in meta:
                if k in j:
                    meta[k] = j[k]
        self.path = path
        self.rows, self.cols, self.step = int(meta["rows"]), int(meta["cols"]), float(meta["step"])
        self.north, self.west = float(meta["north"]), float(meta["west"])
        self._a = None

    @property
    def grid(self):
        if self._a is None:
            self._a = np.memmap(self.path, dtype="<i2", mode="r", shape=(self.rows, self.cols))
        return self._a

    def elev(self, lat, lon):
        """Bilinear between cell centres, sea floor clamped to 0 (as sources.Terrain). NaN outside the model."""
        a = self.grid
        rows, cols = self.rows, self.cols
        y = (self.north - lat) / self.step
        x = (lon - self.west) / self.step
        ok = (y >= 0) & (y < rows) & (x >= 0) & (x < cols)
        y = np.clip(y - 0.5, 0, rows - 1.0001)
        x = np.clip(x - 0.5, 0, cols - 1.0001)
        iy = y.astype(np.int64)
        ix = x.astype(np.int64)
        fy = (y - iy).astype(np.float32)
        fx = (x - ix).astype(np.float32)

        def g(r, c):
            return np.maximum(a[r, c], 0).astype(np.float32)
        h = (g(iy, ix) * (1 - fx) + g(iy, ix + 1) * fx) * (1 - fy) + (g(iy + 1, ix) * (1 - fx) + g(iy + 1, ix + 1) * fx) * fy
        return np.where(ok, h, np.nan)


_terrain = None


def set_terrain(path):
    """Select the terrain file of this process. Worker processes call it again themselves: with the spawn start
    method (the default on macOS) a module global set by the parent does not reach them."""
    global _terrain
    if _terrain is None or _terrain.path != path:
        _terrain = Terrain(path)
    return _terrain


def elev(lat, lon):
    return _terrain.elev(lat, lon)


def hav(la1, lo1, la2, lo2):
    p1, p2 = np.radians(la1), np.radians(la2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lo2 - lo1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def bearing(la1, lo1, la2, lo2):
    p1, p2 = np.radians(la1), np.radians(la2)
    dl = np.radians(lo2 - lo1)
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def xyz(lat, lon):
    """Unit vectors, for the k-d tree: chord distance there is a monotone function of the great-circle one."""
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]


def los_pairs(la_s, lo_s, z_s, la_p, lo_p, z_p, D):
    """Line of sight for N station-aircraft pairs (arrays), heights in m MSL, D in m (all under 100 km).

    Samples the terrain every 250 m in the first and last 5 km of the ray and every 500 m between: near the ends a
    ridge a few hundred metres in front of an antenna matters most and the ray is lowest, in the middle a 500 m step
    cannot miss a ridge that is high enough to block. 4/3 earth, bilinear terrain, a ray that grazes counts as
    blocked, a pair beyond the radio horizon of both ends is blocked whatever the terrain. Outside the model the
    terrain never blocks."""
    n = len(D)
    kb = max(0, int(math.ceil((D.max() - 10000) / 500)))
    A = 250.0 * np.arange(1, 21)
    B = 5000 + 500.0 * np.arange(1, kb + 1)
    C = 250.0 * np.arange(1, 21)
    dd = np.concatenate([np.broadcast_to(A, (n, 20)), np.broadcast_to(B, (n, kb)), D[:, None] - C[None, :]], axis=1)
    valid = np.concatenate([A[None, :] < D[:, None], B[None, :] < D[:, None] - 5000, np.ones((n, 20), bool)], axis=1) \
        & (dd > 0) & (dd < D[:, None])
    dd = np.where(valid, dd, 1.0)
    t = (dd / D[:, None])
    th = elev(la_s[:, None] + (la_p - la_s)[:, None] * t, lo_s[:, None] + (lo_p - lo_s)[:, None] * t)
    th = np.where(np.isnan(th), -1e9, th)
    rel = th - dd * dd / TWO_REFF
    sl = (z_p - D * D / TWO_REFF - z_s) / D
    blocked = (valid & (rel > z_s[:, None] + sl[:, None] * dd)).any(axis=1)
    hor = D > np.sqrt(TWO_REFF * np.maximum(z_s, 0)) + np.sqrt(TWO_REFF * np.maximum(z_p, 0))
    return ~(blocked | hor)


# ---------------------------------------------------------------- feed
def feed_path(feed, day, hour):
    return os.path.join(feed, "%s-%02d.aprs.zst" % (day, hour))


def feed_days(feed):
    """The days that have at least one hourly file in the feed directory, as YYYYMMDD strings."""
    return sorted({f[:8] for f in os.listdir(feed) if f.endswith(".aprs.zst")})


def parse_days(arg, feed):
    return [d for d in arg.split(",") if d] if arg else feed_days(feed)


def stream(path):
    """The position-bearing lines of one hourly file: decompress and let grep drop the rest first, which is
    most of the time saved on a day of feed."""
    cmd = "zstdcat %s | /usr/bin/grep -aE '^[0-9.]+ [^ >]+>(OGFLR[67]?|OGNFLR|OGNFNT|OGNTRK|OGNSDR),'" % path
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, text=True, errors="replace", bufsize=1 << 20)


def split_line(line):
    """'<epoch> <src>><tocall>,<path>:<body>' -> (ts, src, tocall, path, body), or None."""
    try:
        ts, rest = line.split(" ", 1)
        src, _, r = rest.partition(">")
        head, _, body = r.partition(":")
        tocall, _, path_ = head.partition(",")
    except ValueError:
        return None
    return ts, src, tocall, path_, body


def _station_fix(body):
    m = pos_re.match(body)
    if m and m.group(7) == "&":
        la = int(m.group(1)) + float(m.group(2)) / 60
        lo = int(m.group(4)) + float(m.group(5)) / 60
        if la or lo:
            return -la if m.group(3) == "S" else la, -lo if m.group(6) == "W" else lo
    return None


def station_positions(args):
    """Station beacons (OGNSDR, and FANET stations as OGNFNT with '&') from one hour of a day: the first of a list
    of hours around midday that exists, since every station beacons every few minutes and one hour finds nearly
    all of them. Returns {name: (lat, lon, 'OGN' | 'FANET')}. args = (feed, day)."""
    feed, day = args
    out = {}
    for h in (12, 13, 11, 10, 14, 6, 7):
        p = feed_path(feed, day, h)
        if not os.path.exists(p):
            continue
        pr = subprocess.Popen("zstdcat %s | /usr/bin/grep -aE '>(OGNSDR|OGNFNT),.*&'" % p, shell=True,
                              stdout=subprocess.PIPE, text=True, errors="replace")
        for line in pr.stdout:
            sp = split_line(line)
            if sp is None:
                continue
            _, src, tocall, _, body = sp
            fix = _station_fix(body)
            if fix:
                out[src] = (fix[0], fix[1], "FANET" if tocall == "OGNFNT" else "OGN")
        pr.wait()
        break
    return out


def receptions(feed, day, stations):
    """Every forwarded reception of a day, in feed order: yields (ts, src, tocall, rx, body).

    Station beacons met on the way update `stations` (so a station that moved or appeared since the prepass is
    still placed) and are not yielded. Packets that did not come through a ground station of the OGN (qAS), and
    stations that are in fact internet gateways of apps (names with 'mqtt', SKT and SKX prefixes), are dropped:
    this analysis is about radio reception."""
    for h in range(24):
        path = feed_path(feed, day, h)
        if not os.path.exists(path):
            continue
        p = stream(path)
        for line in p.stdout:
            sp = split_line(line)
            if sp is None:
                continue
            ts, src, tocall, path_, body = sp
            if tocall == "OGNSDR" or (tocall == "OGNFNT" and body[:1] != ">" and "&" in body[:30]):
                fix = _station_fix(body)
                if fix:
                    stations[src] = (fix[0], fix[1], "FANET" if tocall == "OGNFNT" else "OGN")
                    continue
                if tocall == "OGNSDR":
                    continue
            if ",qAS," not in "," + path_:
                continue
            rx = path_.rsplit(",", 1)[-1]
            if "mqtt" in rx.lower() or rx.startswith(("SKT", "SKX")):
                continue
            yield ts, src, tocall, rx, body
        p.wait()


def flying_fix(body):
    """The airborne, plausible fix of a packet body, or None. Returns (match, category, speed_kt, alt_m): the
    flight floor per category and the plausibility rules are those of sources.py, so that the analysis counts
    the same aircraft the service does."""
    m = sources._position.match(body)
    if not m or m.group(13) is None:
        return None
    a = sources._alt.search(body)
    if not a:
        return None
    cat, _ = sources.id_info(body)
    speed = int(m.group(13))
    alt = int(a.group(1)) * 0.3048
    if speed < sources.FLYING_KT.get(cat, sources.AIRBORNE_KT) or sources.implausible(cat, speed, alt) \
            or sources.impossible_fix(cat, speed, alt):
        return None
    return m, cat, speed, alt


def latlon(m, body):
    """Degrees from a position match, with the extra precision digits of the APRS '!Wnn!' extension."""
    latm, lonm = float(m.group(5)), float(m.group(9))
    w = sources._extra_precision.search(body)
    if w:
        latm += int(w.group(1)) / 1000
        lonm += int(w.group(2)) / 1000
    lat = int(m.group(4)) + latm / 60
    lon = int(m.group(8)) + lonm / 60
    if m.group(6) == "S":
        lat = -lat
    if m.group(10) == "W":
        lon = -lon
    return lat, lon


# ---------------------------------------------------------------- curves
def wilson(k, n, z=1.96):
    """Wilson score interval of a proportion."""
    if n == 0:
        return (float("nan"),) * 2
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def pbins(v, rt, rh):
    """Reception probability per summary bin from a (4, 100) count array (rows: total, hit, total with LOS, hit
    with LOS): rt, rh pick the total and hit rows."""
    v = np.asarray(v)
    return np.array([v[rh, lo:hi].sum() / max(1, v[rt, lo:hi].sum()) for lo, hi in zip(EDGES[:-1], EDGES[1:])])


def curve_fn(p):
    """A distance function (km) from the ten bin probabilities: forced not to rise with distance (reception
    cannot improve further away; a rise is noise from a thin bin), then interpolated linearly in log distance."""
    p = np.minimum.accumulate(p)
    return lambda d: np.interp(np.log(np.maximum(d, 1e-3)), LC, p)


# ---------------------------------------------------------------- coverage model
def ranks(v):
    """Ranks with ties averaged, for the Spearman correlation."""
    v = np.asarray(v)
    o = np.argsort(v, kind="mergesort")
    r = np.empty(len(v))
    r[o] = np.arange(len(v))
    for u in np.unique(v):
        k = v == u
        if k.sum() > 1:
            r[k] = r[k].mean()
    return r


def rho(a, b):
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def load_json(path):
    with open(path) as f:
        return json.load(f)


def station_offsets(sig_json):
    """Per-station sensitivity offsets from stations.json (stations_snapshot.py): dB is one clipped offset per station
    (variant B), dC one per station and 10-degree sector (variant C), both relative to the median station and
    limited to +-12 dB; a sector with fewer than 20 samples borrows its two neighbours, then falls back to dB."""
    S = sig_json["stations"]
    med = sig_json["all_median"]
    sig = np.array([np.nan if s["sig"] is None else s["sig"] for s in S])
    dB = np.where(np.isnan(sig), 0.0, np.clip(sig - med, -12, 12))
    dC = np.repeat(dB[:, None], 36, 1)
    for i, s in enumerate(S):
        n = np.array(s["n"], float)
        sm = np.array(s["s"], float)
        for k in range(36):
            if n[k] >= 20:
                dC[i, k] = np.clip(sm[k] / n[k] - med, -12, 12)
                continue
            idx = [(k - 1) % 36, k, (k + 1) % 36]
            if n[idx].sum() >= 20:
                dC[i, k] = np.clip(sm[idx].sum() / n[idx].sum() - med, -12, 12)
    return dB, dC


def model_rows(cg, los, families=("free_flight", "glider")):
    """The measured rows to validate against: one per (cell, band, family) the coverage-gaps snapshot counts and
    that has a precomputed line of sight. Each is (cell key, family, band, measured share without signal, probe
    hours)."""
    rows = []
    for c in cg["cells"]:
        key = (c["lat_idx"], c["lon_idx"])
        if key not in los:
            continue
        for b, bd in enumerate(c["bands"]):
            for fam, fd in bd["families"].items():
                if fam in families and fd["counts"]:
                    rows.append((key, fam, b, fd["share_without_signal"], fd["probe_hours"]))
    return rows


def qpred(los, cell, b, fn, dB=None, dC=None, variant="A", filt=True):
    """Predicted share without signal of one cell and band: the product over the cell's stations of (1 - p(d)),
    for the points that each station sees, averaged over the 64 points of the cell. Variant A uses one curve for
    all stations; B stretches each station's distances by its sensitivity offset; C by its offset in the sector
    the point lies in."""
    r = los[cell]
    ns = len(r["sidx"])
    L = r["L"][:ns, :, b].astype(bool) if filt else np.ones((ns, 64), bool)
    D = r["D"][:ns] / 1000.0
    si = r["sidx"]
    if variant == "B":
        D = D * 10 ** (-dB[si][:, None] / 20)
    elif variant == "C":
        sec = (r["B"][:ns] // 10).astype(int) % 36
        D = D * 10 ** (-dC[si[:, None], sec] / 20)
    return float(np.prod(1 - L * fn(D), axis=0).mean())


def maxrss_mb():
    """Peak memory of this process in MB (macOS reports bytes, Linux kilobytes)."""
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r // (1 << 20) if sys.platform == "darwin" else r // 1024
