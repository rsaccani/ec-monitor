#!/usr/bin/env python3
"""How often do crewed aircraft of different kinds come close? (7 October 2026)

For the "Sharing the air" section planned beside the drones of Question 7:
encounters between free flight (6, 7), gliders (1), powered aircraft
(2, 8, 9) and helicopters (3), every source including ADS-B, one aircraft
one 24-bit address. An address's category is the majority of its packets
that day over all its systems, ADS-B's "0" (not yet known) left out of the
vote, so a stray packet does not change the kind.

An encounter is a fix of each, both airborne (the speed of their kind,
sources.FLYING_KT), within 10 s, and within 1 km and 150 m (wide) or 300 m
and 100 m (close); one per pair per 10 minutes. Same-kind pairs are left
out, and so is any pair that stays within 300 m for more than 5 minutes
over more than 3 km: one pilot carrying two devices under two addresses
(a crossing does not last).

Prints aggregates only: kind pairs, systems as combinations, counts.

Usage: (cd ~/ads-l-map && nice -n 19 python3 tools/crewed_encounters.py ~/ec-raw/*.aprs.zst)
"""
import collections
import math
import statistics
import subprocess
import sys

sys.path.insert(0, ".")
import sources  # noqa: E402

KIND = {6: "free flight", 7: "free flight", 1: "glider", 2: "powered", 8: "powered", 9: "powered",
        3: "helicopter"}
THRESHOLDS = {"wide": (1000, 150), "close": (300, 100)}
BANDS = {"wide": (300, 600), "close": (100, 200)}
DT = 10
EVERY = 600
COMPANION_M, COMPANION_S, COMPANION_KM, COMPANION_GAP = 300, 300, 3.0, 60
CELL_LAT, CELL_LON = 0.02, 0.04
WINDOW = 340
RADIO_KINDS = {"adsl", "flarm", "fanet", "adsb", "radio"}


def lines(paths):
    for p in paths:
        with subprocess.Popen(["zstdcat", "-q", p], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace")
                sp = line.find(" ")
                try:
                    yield float(line[:sp]), line[sp + 1:].rstrip("\n")
                except ValueError:
                    continue


def packets(paths):
    for epoch, line in lines(paths):
        try:
            src, rest = line.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        if not body.startswith("/"):
            continue
        raw = head.split(",", 1)[0]
        if raw in sources.EXCLUDED or raw in ("OGNSDR", "OGNSXR") or body[26:27] in ("&", "_"):
            continue
        cat, no_track = sources.id_info(body)
        if no_track:
            continue
        system = sources.same_system(raw, src)
        if sources.rebroadcast(system, src, cat):
            continue
        yield epoch, src, system, body, cat


def main():
    paths = sys.argv[1:]
    votes = collections.defaultdict(collections.Counter)
    for epoch, src, system, body, cat in packets(paths):
        if cat is None or (cat == 0 and system == "OGADSB"):
            continue
        votes[(src[-6:], int(epoch // 86400))][cat] += 1
    major = {k: c.most_common(1)[0][0] for k, c in votes.items()}
    del votes
    print(f"address-days with a category: {len(major):,}")

    systems = collections.defaultdict(set)         # (address, day) -> labels
    last = {}                                       # address -> (t, lat, lon)
    index = collections.defaultdict(list)           # (cell lat, cell lon, t // DT) -> fixes
    index_order = collections.deque()
    max_t = -1e18
    contacts = {}                                   # pair -> [first t, first lat, lon, last t, lat, lon]
    companions = set()                              # (pair, day)
    open_enc = {}                                   # (pair, threshold) -> [t0, min d, closing, day, kinds]
    done = []

    def close_enc(key, e):
        done.append((key[0], key[1]) + tuple(e))

    for epoch, src, system, body, cat in packets(paths):
        addr = src[-6:]
        day = int(epoch // 86400)
        label, kind = sources.source_info(system)
        if kind != "platform":
            systems[(addr, day)].add((label, kind in RADIO_KINDS))
        m = major.get((addr, day))
        k = KIND.get(m)
        if k is None:
            continue
        p = sources._position.match(body)
        if not p:
            continue
        g = p.groups()
        a = sources._alt.search(body)
        if not a or g[12] is None:
            continue
        kt = int(g[12])
        if kt < sources.FLYING_KT.get(m, sources.AIRBORNE_KT):
            continue
        alt = int(a.group(1)) * 0.3048
        if sources.implausible(m, kt, alt):
            continue
        lat = int(g[3]) + float(g[4]) / 60
        lon = int(g[7]) + float(g[8]) / 60
        if g[5] == "S":
            lat = -lat
        if g[9] == "W":
            lon = -lon
        if abs(lat) < 1 and abs(lon) < 1:
            continue
        sod = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2])
        now = int(epoch)
        t = now - now % 86400 + sod
        if t - epoch > 300:
            t -= 86400
        if epoch - t > sources.STALE_SECONDS:
            continue
        prev = last.get(addr)
        if prev is not None:
            if t <= prev[0]:
                continue                            # one fix per address and second, whatever the system
            if sources._distance(prev[1], prev[2], lat, lon) > sources.IMPLAUSIBLE_MS * max(t - prev[0], 1):
                last[addr] = (t, lat, lon)
                continue                            # a jump: shared address or corrupt fix
        last[addr] = (t, lat, lon)
        course = int(g[11]) if g[11] and g[11] != "000" else None
        cl, co = int(lat // CELL_LAT), int(lon // CELL_LON)
        tb = int(t // DT)
        me = (t, lat, lon, alt, addr, k, course, kt, day)
        for dl in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for db in (-1, 0, 1):
                    for o in index.get((cl + dl, co + dc, tb + db), ()):
                        if o[4] == addr or abs(o[0] - t) > DT or abs(o[3] - alt) > THRESHOLDS["wide"][1]:
                            continue
                        d = sources._distance(lat, lon, o[1], o[2])
                        if d > THRESHOLDS["wide"][0]:
                            continue
                        pair = (addr, o[4]) if addr < o[4] else (o[4], addr)
                        if d <= COMPANION_M:
                            c = contacts.get(pair)
                            if c is None or t - c[3] > COMPANION_GAP:
                                contacts[pair] = [t, lat, lon, t, lat, lon]
                            else:
                                c[3], c[4], c[5] = max(c[3], t), lat, lon
                                if (c[3] - c[0] > COMPANION_S and
                                        sources._distance(c[1], c[2], lat, lon) > COMPANION_KM * 1000):
                                    companions.add((pair, day))
                        if o[5] == k:
                            continue
                        # closing speed from the two velocity vectors
                        closing = None
                        if course is not None and o[6] is not None:
                            v1 = (kt * math.sin(math.radians(course)), kt * math.cos(math.radians(course)))
                            v2 = (o[7] * math.sin(math.radians(o[6])), o[7] * math.cos(math.radians(o[6])))
                            closing = math.hypot(v1[0] - v2[0], v1[1] - v2[1]) * 1.852
                        kinds = tuple(sorted((k, o[5])))
                        for name, (hm, vm) in THRESHOLDS.items():
                            if d > hm or abs(o[3] - alt) > vm:
                                continue
                            key = (pair, name)
                            e = open_enc.get(key)
                            start = min(t, o[0])
                            if e is not None and start - e[0] <= EVERY:
                                if d < e[1]:
                                    e[1], e[2] = d, closing
                                continue
                            if e is not None:
                                close_enc(key, e)
                            open_enc[key] = [start, d, closing, day, kinds]
        key = (cl, co, tb)
        if key not in index:
            index_order.append(key)
        index[key].append(me)
        max_t = max(max_t, t)
        while index_order and index_order[0][2] * DT < max_t - WINDOW:
            index.pop(index_order.popleft(), None)
    for key, e in open_enc.items():
        close_enc(key, e)

    def combo(addr, day):
        s = systems.get((addr, day), set())
        return " + ".join(sorted(x[0] for x in s)) or "relayed only", {x[0] for x in s if x[1]}, {x[0] for x in s}

    days = collections.Counter()
    out = collections.defaultdict(lambda: {"n": 0, "pairs": set(), "bands": collections.Counter(),
                                           "closing": [], "combos": collections.Counter(),
                                           "radio": 0, "any": 0})
    dropped = 0
    for pair, name, t0, d, closing, day, kinds in done:
        if (pair, day) in companions:
            dropped += 1
            continue
        r = out[(kinds, name)]
        r["n"] += 1
        r["pairs"].add(pair)
        lo, hi = BANDS[name]
        r["bands"]["<%d" % lo if d < lo else "<%d" % hi if d < hi else "<%d" % THRESHOLDS[name][0]] += 1
        if closing is not None:
            r["closing"].append(closing)
        (ca, ra, aa), (cb, rb, ab) = combo(pair[0], day), combo(pair[1], day)
        ka = KIND[major[(pair[0], day)]]
        a_side, b_side = (ca, cb) if ka == kinds[0] else (cb, ca)
        r["combos"][f"{a_side} | {b_side}"] += 1
        r["radio"] += bool(ra & rb)
        r["any"] += bool(aa & ab)
        if name == "wide":
            days[day] += 1
    print(f"pairs set aside as one pilot with two devices: {len({p for p, _ in companions}):,} "
          f"({dropped:,} encounters dropped)")
    import datetime
    print("wide encounters per UTC day (fix time): " + ", ".join(
        f"{datetime.date(1970, 1, 1) + datetime.timedelta(days=d)} {n:,}" for d, n in sorted(days.items())))
    for name in THRESHOLDS:
        hm, vm = THRESHOLDS[name]
        print(f"\n== {name}: within {DT} s, {hm} m and {vm} m")
        for (kinds, nm), r in sorted(out.items(), key=lambda x: -x[1]["n"]):
            if nm != name:
                continue
            med = statistics.median(r["closing"]) if r["closing"] else None
            print(f"  {kinds[0]} x {kinds[1]}: {r['n']:,} encounters, {len(r['pairs']):,} pairs, "
                  f"closest {dict(sorted(r['bands'].items()))}, median closing "
                  f"{'-' if med is None else f'{med:.0f} km/h'}, share a radio system {r['radio']:,}, "
                  f"share any system {r['any']:,}")
            for c, n in r["combos"].most_common(8):
                print(f"      {n:5,}  {c}")
            rest = sum(r["combos"].values()) - sum(n for _, n in r["combos"].most_common(8))
            if rest:
                print(f"      {rest:5,}  other combinations")


if __name__ == "__main__":
    main()
