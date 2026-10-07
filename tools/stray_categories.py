#!/usr/bin/env python3
"""How many packets declare a category their device does not usually declare, and why? (7 October 2026)

The drone check of 6 October found 55 addresses that sent category 13 in a
single packet among hundreds declaring another kind. This measures such
"stray" categories over the whole raw recording. Pass 1 finds, per address,
system (tocall after same_system) and UTC day of reception, the category
declared most often. Pass 2 classes every position packet as normal or
stray (a category other than that majority) and counts, by system and by
receiving station, how often strays carry other anomalies, and whether the
same fix heard by other receivers carried the majority category. It then
estimates what strays do to the live measures (a segment is filed under
the category of the packet that ends it, as in sources.count_hours) and to
monthly_sources (a device's month category is that of its first packet),
the latter also against the database, read only.

FANET's switch between paraglider or hang glider and static object is its
ground-tracking mode and is counted apart, not as a stray. PilotAware
rebroadcasts (sources.rebroadcast) are left out, as everywhere.

Prints aggregates only: systems, receiver names (public), counts. No
address, flight id or position leaves the server.

Usage: (cd ~/ads-l-map && nice -n 19 python3 tools/stray_categories.py [--db-only] ~/ec-raw/*.aprs*)
--db-only skips pass 2 and only compares monthly_sources with the majorities.
"""
import collections
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, ".")
import sources  # noqa: E402

GROUP_S = 20                 # seconds to wait for other receivers of one fix
SPEED_MS = 500 / 3.6
SPEED_MIN_M = 1000           # below this a "speed" is position rounding over a second
JUMP_M = 50000
SENTINEL_M = 60000
FANET_GROUND = {6, 7, 15}
RECEIVERS = {"OGNSDR", "OGNSXR"}


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", "-q", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace")
                sp = line.find(" ")
                try:
                    epoch = float(line[:sp])
                except ValueError:
                    continue
                yield epoch, line[sp + 1:].rstrip("\n")


def packets(paths):
    """(epoch, src, system, path, body, category) of every position packet declaring a category."""
    for epoch, line in lines(paths):
        try:
            src, rest = line.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        if not body.startswith("/"):
            continue
        path = head.split(",")
        raw = path[0]
        if raw in sources.EXCLUDED or raw in RECEIVERS or body[26:27] in ("&", "_"):
            continue
        cat, no_track = sources.id_info(body)
        if cat is None or no_track:
            continue
        system = sources.same_system(raw, src)
        if sources.rebroadcast(system, src, cat):
            continue
        yield epoch, src, system, path, body, cat


def majority(counter):
    return counter.most_common(1)[0][0]


def kind_of(system, maj, cat):
    if cat == maj:
        return "normal"
    if system == "OGNFNT" and {maj, cat} <= FANET_GROUND and 15 in (maj, cat):
        return "fanet_ground"
    return "stray"


def main():
    db_only = "--db-only" in sys.argv
    paths = [a for a in sys.argv[1:] if a != "--db-only"]
    # --- pass 1: majority per address, system and day ---------------------------
    counts = collections.defaultdict(collections.Counter)
    for epoch, src, system, path, body, cat in packets(paths):
        counts[(src[-6:], system, int(epoch // 86400))][cat] += 1
    maj = {k: majority(c) for k, c in counts.items()}
    over_days = collections.defaultdict(collections.Counter)
    for (addr, system, _), c in counts.items():
        over_days[(addr, system)].update(c)
    maj_all = {k: majority(c) for k, c in over_days.items()}
    del counts
    print(f"addresses x systems x days: {len(maj):,}")
    if db_only:
        compare_db(maj_all)
        return

    # --- pass 2 -----------------------------------------------------------------
    by_sys = collections.defaultdict(collections.Counter)     # system -> kind -> packets
    by_rx = collections.defaultdict(collections.Counter)      # receiver -> kind -> radio packets
    pair = collections.defaultdict(collections.Counter)       # system -> (majority, stray cat) -> packets
    anomalies = collections.defaultdict(collections.Counter)  # kind -> anomaly -> packets
    last = {}                                                 # address -> (t, lat, lon)
    groups, order = {}, collections.deque()
    others = collections.Counter()                            # what other receivers carried for a stray fix
    timeline = {}                                             # address -> (t, lat, lon, kt)
    filed = collections.Counter()                             # (majority, filed category) -> seconds
    air_by_maj = collections.Counter()
    first = {}                                                # (address, system) -> first category this month

    def flush(g):
        entries = g[1]
        if len(entries) < 2:
            return
        for rx, cat, kind, m in entries:
            if kind != "stray":
                continue
            rest = [c for r, c, _, _ in entries if r != rx]
            if not rest:
                continue
            if m in rest:
                others["another receiver had the majority category"] += 1
            elif cat in rest:
                others["another receiver had the same stray category"] += 1
            else:
                others["other receivers had a third category"] += 1

    for epoch, src, system, path, body, cat in packets(paths):
        addr = src[-6:]
        m = maj[(addr, system, int(epoch // 86400))]
        kind = kind_of(system, m, cat)
        by_sys[system][kind] += 1
        if kind == "stray":
            pair[system][(m, cat)] += 1
        if (addr, system) not in first and epoch >= 0:
            first[(addr, system)] = cat
        radio = system == "OGADSB" or sources._radio_meta.search(body)
        rx = path[-1] if radio and system != "OGADSB" else None
        if rx is not None:
            by_rx[rx][kind] += 1
        p = sources._position.match(body)
        if not p:
            continue
        g = p.groups()
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
        a = sources._alt.search(body)
        alt = int(a.group(1)) * 0.3048 if a else None
        kt = int(g[12]) if g[12] else None
        an = anomalies[kind]
        an["packets"] += 1
        if alt is not None and alt >= SENTINEL_M:
            an["altitude >= 60,000 m"] += 1
        prev = last.get(addr)
        if prev is not None and t > prev[0]:
            d = sources._distance(prev[1], prev[2], lat, lon)
            if d > SPEED_MIN_M and d / (t - prev[0]) > SPEED_MS:
                an["implied speed > 500 km/h"] += 1
            if d > JUMP_M:
                an["jump > 50 km"] += 1
        if prev is None or t > prev[0]:
            last[addr] = (t, lat, lon)
        # the same fix through other receivers
        if rx is not None:
            k = (addr, system, sod)
            grp = groups.get(k)
            if grp is None:
                groups[k] = (epoch, [(rx, cat, kind, m)])
                order.append(k)
            elif all(r != rx for r, _, _, _ in grp[1]):
                grp[1].append((rx, cat, kind, m))
            while order and epoch - groups[order[0]][0] > GROUP_S:
                flush(groups.pop(order.popleft()))
        # what the live service files (count_hours): ADS-B is not measured
        if system == "OGADSB" or epoch - t > sources.STALE_SECONDS:
            continue
        tp = timeline.get(addr)
        if tp is not None and t <= tp[0]:
            continue
        timeline[addr] = (t, lat, lon, kt)
        if tp is None:
            continue
        sec = t - tp[0]
        if sec > sources.SESSION_BREAK or sources._distance(tp[1], tp[2], lat, lon) > sources.IMPLAUSIBLE_MS * max(sec, 1):
            continue
        if not sources.flying(cat, tp[3], kt):
            continue
        air_by_maj[m] += sec
        if kind == "stray":
            filed[(m, cat)] += sec
    while order:
        flush(groups.pop(order.popleft()))

    # --- report -----------------------------------------------------------------
    name = sources.source_info
    print("\n== Packets by system: normal, stray (non-majority), FANET ground mode")
    tot_stray = sum(c["stray"] for c in by_sys.values())
    for system, c in sorted(by_sys.items(), key=lambda x: -sum(x[1].values())):
        n = sum(c.values())
        if n < 1000:
            continue
        top = ", ".join(f"{a}>{b} {k:,}" for (a, b), k in pair[system].most_common(4))
        print(f"  {name(system)[0]:16s} {n:>11,}  stray {c['stray']:>8,} ({100 * c['stray'] / n:5.2f}%)  "
              f"FANET ground {c['fanet_ground']:>7,}   commonest majority>stray: {top}")
    print(f"  all stray packets: {tot_stray:,}")

    print("\n== Receivers (radio packets)")
    rx_stray = sorted(((c["stray"], r) for r, c in by_rx.items()), reverse=True)
    all_rx = sum(c["stray"] for c in by_rx.values())
    for n in (10, 50):
        share = sum(x for x, _ in rx_stray[:n]) / all_rx if all_rx else 0
        print(f"  top {n} receivers hold {100 * share:.1f}% of {all_rx:,} stray radio packets "
              f"(of {len(by_rx):,} receivers)")
    print("  top by count: " + ", ".join(
        f"{r} {n:,} ({100 * n / sum(by_rx[r].values()):.1f}%)" for n, r in rx_stray[:15]))
    big = [(c["stray"] / sum(c.values()), r, sum(c.values())) for r, c in by_rx.items() if sum(c.values()) >= 5000]
    big.sort(reverse=True)
    print("  top by share (receivers with 5,000+ packets): " + ", ".join(
        f"{r} {100 * s:.1f}% of {n:,}" for s, r, n in big[:15]))
    shares = sorted(s for s, _, _ in big)
    if shares:
        print(f"  median share among those {len(shares):,} receivers: {100 * shares[len(shares) // 2]:.2f}%")

    print("\n== Other anomalies, per 10,000 packets")
    for kind in ("normal", "stray", "fanet_ground"):
        a = anomalies[kind]
        if a["packets"]:
            print(f"  {kind:13s} {a['packets']:>11,} packets: " + ", ".join(
                f"{k} {10000 * a[k] / a['packets']:.1f}" for k in
                ("altitude >= 60,000 m", "implied speed > 500 km/h", "jump > 50 km")))

    print("\n== The same fix through other receivers, for stray radio packets heard twice or more")
    for k, n in others.most_common():
        print(f"  {k}: {n:,}")

    print("\n== (a) Live measures: airborne seconds filed under a stray category (count_hours rules)")
    tot = sum(filed.values())
    print(f"  {tot / 3600:.1f} h of {sum(air_by_maj.values()) / 3600:.0f} h airborne")
    for (m, c), sec in filed.most_common(12):
        print(f"  majority {m} filed as {c}: {sec / 3600:.2f} h ({100 * sec / air_by_maj[m]:.3f}% of category {m})")

    print("\n== (b) First packet against majority, per (address, system) over the recording")
    wrong = collections.Counter()
    by_maj = collections.Counter()
    for k, c in first.items():
        m = maj_all[k]
        by_maj[m] += 1
        if c != m and kind_of(k[1], m, c) == "stray":
            wrong[(m, c)] += 1
    print(f"  devices whose first recorded packet is a stray: {sum(wrong.values()):,} of {len(first):,}")
    for (m, c), n in wrong.most_common(10):
        print(f"  majority {m}, first packet {c}: {n:,} (of {by_maj[m]:,} with majority {m})")

    compare_db(maj_all)


def compare_db(maj_all):
    """monthly_sources' October category against the recording's majority, by system (read only)."""
    try:
        import pymysql
        db = pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"), database="ads_l")
        with db.cursor() as cur:
            cur.execute(f"SELECT source, device_id, category FROM monthly_sources "
                        f"WHERE month = '2026-10' AND {sources.COUNTED_SQL}")
            rows = cur.fetchall()
        db.close()
    except Exception as e:
        print(f"  database not read: {e}")
        return
    cmp = collections.Counter()
    seen = 0
    for source, device_id, cat in rows:
        k = (device_id[-6:], sources.same_system(source, device_id))
        m = maj_all.get(k)
        if m is None or cat is None:
            continue
        seen += 1
        if cat != m:
            cmp[(sources.source_info(k[1])[0], "FANET ground" if kind_of(k[1], m, cat) == "fanet_ground" else "differs",
                 m, cat)] += 1
    print(f"\n== (b) monthly_sources October rows also in the recording: {seen:,}; "
          f"stored category differs from the recording's majority: {sum(cmp.values()):,}")
    for (system, why, m, c), n in cmp.most_common(20):
        print(f"  {system:14s} {why:12s} majority {m:>3}, stored {c:>3}: {n:,}")
    by = collections.Counter()
    for (system, why, m, c), n in cmp.items():
        by[system] += n
    print("  by system: " + ", ".join(f"{k} {v:,}" for k, v in by.most_common()))


if __name__ == "__main__":
    main()
