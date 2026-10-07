#!/usr/bin/env python3
"""Independent recomputation of the EC monitor's time-without-signal and
flying-hours measures from the raw recording, written from METHOD.md
(version 1 as amended on 6 October 2026) without the service's code.

Prints aggregates only: no ids, addresses, positions or raw lines.

Usage: nice python3 indep_check.py [--hours-adsb] [--fix-drop-only] <file.aprs[.zst]> ...
"""
import collections
import math
import re
import subprocess
import sys

KT = 1.852                          # km/h per knot
SESSION = 20 * 60                   # METHOD 1, "New session"
TOL = 10.0                          # METHOD 2, tolerance in seconds
MAX_AGE = 300                       # METHOD 1, "Time": 5 minutes

# Systems (METHOD 1, "Sources") and their design intervals (METHOD 2 and 8).
SYSTEM = {
    "OGFLR": "FLARM", "OGNFLR": "FLARM", "OGFLR6": "FLARM", "OGFLR7": "FLARM",
    "OGADSL": "ADS-L", "OGNTRK": "OGN tracker", "OGPAW": "PilotAware", "OGNPAW": "PilotAware",
    "OGNFNT": "FANET", "OGNSKY": "SafeSky", "OGNAVI": "Naviter", "OGNVVO": "VarioVoice",
    "OGADSB": "ADS-B",
}
RADIO_INTERVAL = {"FLARM": 1, "ADS-L": 1, "OGN tracker": 2, "PilotAware": 2, "FANET": 15}
APPS = {"SafeSky", "Naviter", "VarioVoice"}
EXCLUDED_TOCALLS = {"OGNSDR", "OGNSXR", "OGNDVS", "OGNDELAY", "OGMLAT"}


def app_interval(system, v_kmh):
    if system == "SafeSky":
        return 2.0
    if system == "Naviter":
        return 60.0
    if system == "VarioVoice":         # 150 m, not sooner than 10 s nor later than 45 s
        v = v_kmh / 3.6
        return 45.0 if v <= 0 else min(45.0, max(10.0, 150.0 / v))
    return None


def kind(cat):
    if cat in (6, 7):
        return "free flight"
    if cat == 1:
        return "glider"
    if cat in (2, 8, 9):
        return "powered"
    return "other"


def airborne(cat, v0, v1):
    """METHOD 1, "Airborne": speeds in km/h at the two ends."""
    if cat in (6, 7):
        return min(v0, v1) >= 15
    if cat == 1:
        return min(v0, v1) >= 25 * KT
    if cat in (2, 8, 9):
        return min(v0, v1) >= 40 * KT
    return max(v0, v1) >= 10


SYMBOL_CAT = {"g": 7, "'": 1, "^": 8, "X": 3, "O": 11}

POS = re.compile(r"^([^>]+)>([^,:]+)[^:]*:[/@](\d\d)(\d\d)(\d\d)h(\d\d)(\d\d\.\d\d)([NS])(.)(\d{3})(\d\d\.\d\d)([EW])(.)"
                 r"(?:(\d{3})/(\d{3}))?(?:/A=(-?\d+))?(.*)$")
IDF = re.compile(r"(?:^|\s)id([0-9A-Fa-f]{8}|[0-9A-Fa-f]{10})(?:\s|$)")
PREC = re.compile(r"!W(\d)(\d)!")


def dist_km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace").rstrip("\n")


class Acc:
    __slots__ = ("judged", "late", "segs")

    def __init__(self):
        self.judged = self.late = 0.0
        self.segs = 0


stats = collections.Counter()


class Stream:
    """One timeline: last accepted fix (t, lat, lon, speed km/h, cat)."""
    __slots__ = ("last",)

    def __init__(self):
        self.last = None

    def step(self, t, lat, lon, v, cat, tag):
        """Returns (T, cat, v0) for an airborne, plausible, in-session segment, else None.
        Packets not newer than the last are dropped."""
        last = self.last
        if last is not None and t <= last[0]:
            stats[tag + ":not newer"] += 1
            return None
        # implausible free-flight fix: dropped and ends the track (METHOD 1)
        if cat in (6, 7) and (v > (100 if cat == 7 else 150)):
            stats[tag + ":implausible fix"] += 1
            if not FIX_DROP_ONLY:
                self.last = None
            return None
        self.last = (t, lat, lon, v, cat)
        if last is None:
            return None
        T = t - last[0]
        if T > SESSION:
            stats[tag + ":new session"] += 1
            return None
        if dist_km(last[1], last[2], lat, lon) / (T / 3600.0) > 500:
            stats[tag + ":implausible segment"] += 1
            return None
        if not airborne(cat, last[3], v):
            return None
        return T, cat, last[3]


def main():
    global FIX_DROP_ONLY
    args = sys.argv[1:]
    hours_adsb = "--hours-adsb" in args
    FIX_DROP_ONLY = "--fix-drop-only" in args
    global FUTURE_MAX
    for a in args:
        if a.startswith("--future-max="):
            FUTURE_MAX = float(a.split("=", 1)[1])
    paths = [a for a in args if not a.startswith("--")]

    sys_streams = {}                    # (system, via, callsign) -> Stream
    air_streams = {}                    # address -> Stream (radio, known systems)
    air_heard = collections.defaultdict(dict)   # address -> {system: last heard}
    hour_streams = {}                   # address -> Stream (all sources and channels)
    hour_streams_adsb = {}
    acc = collections.defaultdict(Acc)  # (channel, kind, system) -> Acc
    per_addr = collections.defaultdict(lambda: [0.0, 0.0, None])   # radio per aircraft: judged, late, kind
    addr_radio_systems = collections.defaultdict(set)
    hours = collections.Counter()
    hours_adsb = collections.Counter()
    first = last_t = None

    for line in lines(paths):
        stamp, _, raw = line.partition(" ")
        try:
            rx = float(stamp)
        except ValueError:
            continue
        first = first or rx
        last_t = rx
        m = POS.match(raw)
        if not m:
            stats["not a position"] += 1
            continue
        call, tocall = m.group(1), m.group(2)
        if tocall in EXCLUDED_TOCALLS or m.group(9) == "I":     # receivers, weather, delayed, synthetic
            stats["excluded tocall/receiver"] += 1
            continue
        system = SYSTEM.get(tocall)
        if tocall == "APRS":
            if not call.startswith("PAW"):
                system = "APRS other"
            else:
                system = "PilotAware"
        if system is None:
            system = "other:" + tocall
        sym = m.group(13)
        if system == "FANET" and sym == "_":
            stats["FANET weather"] += 1
            continue
        rest = m.group(17)
        prec = PREC.search(rest)
        latm = float(m.group(7)) + (int(prec.group(1)) / 1000.0 if prec else 0)
        lonm = float(m.group(11)) + (int(prec.group(2)) / 1000.0 if prec else 0)
        lat = int(m.group(6)) + latm / 60.0
        lon = int(m.group(10)) + lonm / 60.0
        if m.group(8) == "S":
            lat = -lat
        if m.group(12) == "W":
            lon = -lon
        if abs(lat) < 1 and abs(lon) < 1:
            stats["near 0,0"] += 1
            continue
        idm = IDF.search(rest)
        cat = None
        addr = None
        if idm:
            h = idm.group(1)
            b0 = int(h[:2], 16)
            if b0 & 0x40:
                stats["no-track"] += 1
                continue
            cat = (b0 >> 2) & 0x0F
            addr = h[-6:].upper()
        if not cat:
            cat = SYMBOL_CAT.get(sym)
        if addr is None:
            tail = call[-6:]
            addr = tail.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", tail) else call
        if system == "other:OGMSHT" and (cat is None or cat in (0, 15)):
            stats["meshtastic no type"] += 1
            continue
        # time of the fix, on the day closest to reception
        sec = int(m.group(3)) * 3600 + int(m.group(4)) * 60 + int(m.group(5))
        day = math.floor(rx / 86400) * 86400
        t = day + sec
        if t - rx > 43200:
            t -= 86400
        elif rx - t > 43200:
            t += 86400
        if t - rx > FUTURE_MAX:
            t -= 86400                  # --future-max: read as yesterday's fix, as stale
        if t - rx > 300:
            stats["fix more than 5 min in the future"] += 1
        elif t - rx > 0:
            stats["fix 0-5 min in the future"] += 1
        if rx - t > MAX_AGE:
            stats["fix older than 5 min"] += 1
            continue
        v = int(m.group(15)) * KT if m.group(15) else 0.0
        alt = int(m.group(16)) * 0.3048 if m.group(16) else None
        if cat in (6, 7) and alt is not None and alt > 6000:
            stats["free flight above 6000 m"] += 1
            # dropped and ends the track, below
            for d, k in ((sys_streams, None),):
                pass
            radio = "dB" in rest and "kHz" in rest
            via = "radio" if radio else "net"
            if not FIX_DROP_ONLY:
                s = sys_streams.get((system, via, call))
                if s:
                    s.last = None
                for d in (air_streams, hour_streams, hour_streams_adsb):
                    s = d.get(addr)
                    if s:
                        s.last = None
            continue
        radio = ("dB" in rest and "kHz" in rest) or system == "ADS-B"
        via = "radio" if radio else "net"
        k = kind(cat)

        # flying hours per aircraft (METHOD 1, "Flying time")
        for d, h_acc, use, tag in ((hour_streams, hours, system != "ADS-B", "hours"),
                                   (hour_streams_adsb, hours_adsb, True, "hours+adsb")):
            if not use:
                continue
            s = d.get(addr)
            if s is None:
                s = d[addr] = Stream()
            r = s.step(t, lat, lon, v, cat, tag)
            if r:
                h_acc[kind(r[1])] += r[0]
                h_acc["cat%s" % r[1]] += r[0]

        if system == "ADS-B":
            continue

        # per system (stream = system, channel, device)
        key = (system, via, call)
        s = sys_streams.get(key)
        if s is None:
            s = sys_streams[key] = Stream()
        r = s.step(t, lat, lon, v, cat, "system")
        if r:
            T, c, v0 = r
            if via == "radio":
                iv = RADIO_INTERVAL.get(system)
                chan = "radio"
            else:
                iv = app_interval(system, min(v0, v))
                chan = "app" if system in APPS else "net"
                if iv is None and system in RADIO_INTERVAL:
                    iv = RADIO_INTERVAL[system]       # a radio system relayed over the internet
            label = system if iv is not None else system + " (no interval)"
            a = acc[(chan, kind(c), label)]
            a.segs += 1
            a.judged += T
            if iv is not None:
                a.late += max(0.0, T - (iv + TOL))

        # radio per aircraft (METHOD 2, last paragraph but one)
        if via == "radio" and system in RADIO_INTERVAL:
            heard = air_heard[addr]
            heard[system] = t if t > heard.get(system, 0) else heard.get(system, 0)
            addr_radio_systems[addr].add(system)
            s = air_streams.get(addr)
            if s is None:
                s = air_streams[addr] = Stream()
            r = s.step(t, lat, lon, v, cat, "aircraft")
            if r:
                T, c, v0 = r
                iv = min(RADIO_INTERVAL[x] for x, ht in heard.items() if ht >= t - SESSION)
                late = max(0.0, T - (iv + TOL))
                a = acc[("radio per aircraft", kind(c), "AIRCRAFT")]
                a.segs += 1
                a.judged += T
                a.late += late
                pa = per_addr[addr]
                pa[0] += T
                pa[1] += late
                pa[2] = kind(c)

    import time
    fmt = lambda x: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(x))
    print("window %s to %s UTC (reception)%s" % (fmt(first), fmt(last_t), (" [fix-drop-only]" if FIX_DROP_ONLY else "") + " future-max=%g" % FUTURE_MAX))
    print("\n%-20s %-12s %-26s %9s %9s %8s" % ("channel", "kind", "system", "segments", "judged h", "late %"))
    totals = collections.defaultdict(Acc)
    for (chan, k, sysl), a in sorted(acc.items()):
        print("%-20s %-12s %-26s %9d %9.2f %8s" % (chan, k, sysl, a.segs, a.judged / 3600,
                                                   "%.2f" % (100 * a.late / a.judged) if a.judged and "no interval" not in sysl else "-"))
        if "no interval" not in sysl:
            tt = totals[(chan, k)]
            tt.judged += a.judged; tt.late += a.late; tt.segs += a.segs
            tt = totals[(chan, "all")]
            tt.judged += a.judged; tt.late += a.late; tt.segs += a.segs
    print("\nTotals over systems with a known interval")
    for (chan, k), a in sorted(totals.items()):
        print("%-20s %-12s %9.2f h judged %8.2f%% late" % (chan, k, a.judged / 3600, 100 * a.late / a.judged if a.judged else 0))
    print("\nRadio per aircraft by number of radio systems heard in the window")
    grp = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for addr, (j, l, k) in per_addr.items():
        n = len(addr_radio_systems[addr])
        g = grp[(k, "1" if n == 1 else "2+")]
        g[0] += j; g[1] += l; g[2] += 1
    for key, (j, l, n) in sorted(grp.items(), key=str):
        print("  %-12s %-3s aircraft %5d  judged %8.2f h  late %6.2f%%" % (key[0], key[1], n, j / 3600, 100 * l / j if j else 0))
    print("\nFlying hours per aircraft (ADS-B excluded | ADS-B included)")
    for k in sorted(set(hours) | set(hours_adsb), key=str):
        print("  %-12s %10.2f %10.2f" % (k, hours[k] / 3600, hours_adsb[k] / 3600))
    print("\nCounters")
    for k, v in sorted(stats.items()):
        print("  %-40s %d" % (k, v))


FIX_DROP_ONLY = False
FUTURE_MAX = 43200.0

if __name__ == "__main__":
    main()
