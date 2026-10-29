#!/usr/bin/env python3
"""Export from the service's database what the coverage model needs from it, as JSON/TSV (read-only).

stations.json: the OGN stations heard in the last 7 days with forwarded packets, with twins (two receivers at one
site) collapsed as sources.collapse_twins does for the service; each station's median signal at 10 km over the
last 30 days (the sensitivity of variants B and C in validate.py) and its pooled reception rose, 36 sectors of
10 degrees, as counts and sums of signal per sector (the rose of the receiving sectors, daily_station_rose).
--daily-gaps FILE also writes the daily rows of daily_coverage_gaps since --since (reliability.py reads them).

Run it from a checkout of the service, where the database credentials are (nightly.connect_db reads DB_USER and
DB_PASSWORD from the environment, else the client's own option file). It only reads.

  python3 stations_snapshot.py --out stations.json [--daily-gaps gaps.tsv --since 2026-10-01]
"""
import argparse
import collections
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import nightly  # noqa: E402
import sources  # noqa: E402

# The sources that feed the rose: radio systems whose signal strength a ground station reports.
ROSE_SOURCES = ("OGFLR", "OGNFNT", "OGADSL", "OGPAW", "OGNTRK")


def stations(cur, out):
    cur.execute("SELECT MAX(day) FROM daily_stations")
    latest = cur.fetchone()[0]
    cur.execute("""SELECT name, day, kind, lat, lon, packets_forwarded, signal_10km_db_median FROM daily_stations
                   WHERE day > %s - INTERVAL 30 DAY ORDER BY day""", (latest,))
    st = {}
    for name, day, kind, lat, lon, pk, sig in cur.fetchall():
        s = st.setdefault(name, dict(kind=None, lat=None, lon=None, pk7=0, sigs=[]))
        s["kind"] = kind
        if lat is not None:
            s["lat"], s["lon"] = float(lat), float(lon)
        if (latest - day).days < 7 and pk:
            s["pk7"] += int(pk)
        if sig is not None:
            s["sigs"].append(float(sig))
    ogn = {n: s for n, s in st.items() if s["kind"] == "OGN"}
    med = {n: statistics.median(s["sigs"]) for n, s in ogn.items() if s["sigs"]}
    allmed = statistics.median(med.values())
    sel = [(n, s["lat"], s["lon"]) for n, s in ogn.items() if s["pk7"] > 0 and s["lat"] is not None]
    col = sources.collapse_twins(sel)
    # the source names are the constants above, so they go into the text; the day stays a parameter
    cur.execute("SELECT station, payload FROM daily_station_rose WHERE source IN (%s) AND day > %%s - INTERVAL 30 DAY" %
                ",".join("'%s'" % x for x in ROSE_SOURCES), (latest,))
    rose = collections.defaultdict(lambda: ([0] * 36, [0.0] * 36))
    for station, payload in cur.fetchall():
        n, sm = rose[station]
        for sec, nsig, ssum, _ in json.loads(payload):
            n[sec] += nsig
            sm[sec] += ssum
    res = []
    for name, lat, lon in col:
        members = name.split(" + ")
        sig = next((med[m] for m in members if m in med), None)
        n = [0] * 36
        sm = [0.0] * 36
        for m in members:
            if m in rose:
                for k in range(36):
                    n[k] += rose[m][0][k]
                    sm[k] += rose[m][1][k]
        res.append(dict(name=name, lat=lat, lon=lon, sig=sig, n=n, s=sm))
    with open(out, "w") as f:
        json.dump(dict(latest=str(latest), all_median=allmed, n_ogn_with_median=len(med), stations=res), f)
    print("latest", latest, "stations", len(res), "with sig", sum(1 for o in res if o["sig"] is not None),
          "with rose", sum(1 for o in res if sum(o["n"]) > 0), "all-median", allmed)


def daily_gaps(cur, out, since):
    cur.execute("""SELECT day, lat_idx, lon_idx, family, band, probe_s, probe_late_s, probe_aircraft, air_s
                   FROM daily_coverage_gaps WHERE day >= %s ORDER BY day, lat_idx, lon_idx, family, band""", (since,))
    n = 0
    with open(out, "w") as f:
        f.write("day\tlat_idx\tlon_idx\tfamily\tband\tprobe_s\tprobe_late_s\tprobe_aircraft\tair_s\n")
        for r in cur:
            f.write("\t".join(str(x) for x in r) + "\n")
            n += 1
    print("daily coverage-gaps rows", n)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="stations.json")
    ap.add_argument("--daily-gaps", default="", help="also write the daily coverage-gaps rows here (TSV)")
    ap.add_argument("--since", default="", help="first day of --daily-gaps, YYYY-MM-DD")
    a = ap.parse_args()
    if a.daily_gaps and not a.since:
        ap.error("--daily-gaps needs --since")
    conn = nightly.connect_db()
    cur = conn.cursor()
    stations(cur, a.out)
    if a.daily_gaps:
        daily_gaps(cur, a.daily_gaps, a.since)


if __name__ == "__main__":
    main()
