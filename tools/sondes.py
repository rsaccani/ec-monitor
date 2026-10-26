#!/usr/bin/env python3
"""Radiosonde profiles for the Alps, northern Italy and southern France, daily or historical.

Two sources, both anonymous HTTP:

* UWyo: University of Wyoming's sounding service, fed from the stations' BUFR messages. Full
  vertical resolution (1 s samples, a row every 4-8 m, 3,000-6,000 rows to 25-30 km) and a lag of
  a few hours after the launch. Only some stations are present (see STATIONS, column 'uwyo').
  Its text listing is fixed width, 7 characters a column, and some rows have a blank field (an
  interpolated level with no height), so splitting on whitespace shifts the fields: parse_uwyo_list
  cuts by position. The CSV variant of the same page has none of this trouble and is the default.
* IGRA: NOAA's Integrated Global Radiosonde Archive v2, one text file per station. The current
  year ("y2d") file is rebuilt about once a day and trails the launches by 1-2 days; the full
  record ("por") goes back decades. Reported levels only (about 60-140 a sounding: mandatory,
  significant and wind levels), not the 1 s series. Covers the stations that UWyo lacks:
  Novara/Cameri, Cuneo, San Pietro Capofiume, Pratica di Mare, Nimes and, for the past, Milano
  Linate (to 2021) and Udine/Campoformido (to 2016).

Usage:
  sondes.py uwyo 16045 2026-10-24 12          # one sounding, JSON on stdout
  sondes.py igra 16064 2026-10-23 12
  sondes.py day 2026-10-24 12 [--out DIR]     # every station, best source, saved under DIR

Output records: {"station", "source", "time" (nominal, UTC), "launch" (first fix, UTC if known),
"levels": [{"p": hPa, "z": m, "T": C, "Td": C, "rh": %, "dir": deg, "spd": m/s, "t": s since
launch (IGRA: elapsed time), "lat", "lon"}]}. A missing value is null. Heights are geopotential
metres above sea level in both sources.

DIR defaults to $EC_WEATHER_DIR/sondes (EC_WEATHER_DIR defaults to ~/ec-weather); files are
<DIR>/YYYY-MM-DD/<station>-HH.json.gz, written atomically, and an existing one is skipped.
Data licences: UWyo asks for acknowledgement of the service; IGRA is public domain (NOAA NCEI).
"""
import csv, datetime as dt, gzip, io, json, os, re, sys, tempfile, time, urllib.error, urllib.request, zipfile

# WMO id: (name, lat, lon, IGRA id, on UWyo BUFR, launches (UTC nominal hours), last year of data or None)
STATIONS = {
    "06610": ("Payerne", 46.81, 6.94, "SZM00006610", True, (0, 12), None),
    "10868": ("Munich-Oberschleissheim", 48.24, 11.55, "GMM00010868", True, (0, 12), None),
    "10739": ("Stuttgart", 48.83, 9.20, "GMM00010739", True, (0, 12), None),
    "11035": ("Vienna", 48.25, 16.36, "AUM00011035", True, (0, 12), None),
    "11120": ("Innsbruck", 47.26, 11.34, "AUM00011120", True, (0,), None),   # launches at 02 UTC; UWyo files it under 00
    "16045": ("Udine-Rivolto", 45.98, 13.05, "ITM00016045", True, (0, 12), None),
    "16064": ("Novara-Cameri", 45.53, 8.67, "ITM00016064", False, (0, 12), None),  # replaces Linate since 2021
    "16113": ("Cuneo-Levaldigi", 44.54, 7.61, "ITM00016113", False, (0,), None),
    "16144": ("San Pietro Capofiume", 44.65, 11.62, "ITM00016144", False, (0, 12), None),
    "16245": ("Pratica di Mare", 41.66, 12.45, "ITM00016245", False, (0, 12), None),
    "07645": ("Nimes-Courbessac", 43.86, 4.41, "FRM00007645", False, (0, 12), None),
    "07761": ("Ajaccio", 41.92, 8.79, "FRM00007761", True, (0, 12), None),
    "11240": ("Graz", 46.98, 15.44, "AUM00011240", True, (0,), None),
    "14240": ("Zagreb", 45.82, 16.03, "HRM00014240", True, (0, 12), None),
    # history only: no longer launching
    "16080": ("Milano-Linate", 45.45, 9.28, "ITM00016080", False, (0, 12), 2021),
    "16044": ("Udine-Campoformido", 46.03, 13.18, "ITM00016044", False, (0, 12), 2016),
}
ACTIVE = [s for s, v in STATIONS.items() if v[6] is None]
UA = {"User-Agent": "ec-monitor-sondes"}


def get(url, tries=3, timeout=120):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):   # UWyo answers 400 when it has no sounding near that time
                return None
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        time.sleep(5 * 3 ** a)
    raise RuntimeError(f"{url}: {err}")


def num(s):
    s = s.strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


# ---------------------------------------------------------------- UWyo
def uwyo_url(station, when, kind="CSV"):
    return ("https://weather.uwyo.edu/wsgi/sounding?datetime=%s&id=%s&src=BUFR&type=TEXT:%s"
            % (when.strftime("%Y-%m-%d%%20%H:00:00"), station, kind))


def parse_uwyo_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 3 or not rows[0] or rows[0][0] != "time":
        return None
    out, t0 = [], None
    for r in rows[1:]:
        if len(r) < 13:
            continue
        t = dt.datetime.strptime(r[0].strip(), "%Y-%m-%d %H:%M:%S")
        t0 = t0 or t
        out.append({"p": num(r[3]), "z": num(r[4]), "T": num(r[5]), "Td": num(r[6]), "rh": num(r[8]),
                    "dir": num(r[11]), "spd": num(r[12]), "t": (t - t0).seconds, "lat": num(r[2]), "lon": num(r[1])})
    return t0, out


def parse_uwyo_list(html):
    """The same profile from the fixed-width <pre> table: 7 characters per column, columns named in the header."""
    pre = re.findall(r"<pre>(.*?)</pre>", html, re.S | re.I)
    if not pre:
        return None
    names, out = None, []
    for line in pre[0].split("\n"):
        if names is None:
            if line.strip().startswith("PRES"):
                names = [line[i:i + 7].strip() for i in range(0, len(line), 7) if line[i:i + 7].strip()]
            continue
        if not re.match(r"\s*\d", line):
            continue
        c = {n: num(line[i * 7:(i + 1) * 7]) for i, n in enumerate(names)}
        spd = c.get("SPED")
        if spd is None and c.get("SKNT") is not None:
            spd = round(c["SKNT"] * 0.514444, 1)
        out.append({"p": c.get("PRES"), "z": c.get("HGHT"), "T": c.get("TEMP"), "Td": c.get("DWPT"), "rh": c.get("RELH"),
                    "dir": c.get("DRCT"), "spd": spd})
    return None, out


def fetch_uwyo(station, when):
    """when: naive UTC datetime on the nominal hour. Returns a record or None. UWyo accepts a nominal
    time up to a few hours off the real launch (Innsbruck 00 finds the 02 UTC launch)."""
    raw = get(uwyo_url(station, when, "CSV"))
    parsed = parse_uwyo_csv(raw.decode("utf-8", "replace")) if raw else None
    if not parsed or not parsed[1]:
        return None
    launch, levels = parsed
    return {"station": station, "source": "uwyo-bufr", "time": when.strftime("%Y-%m-%dT%H:00Z"),
            "launch": launch.strftime("%Y-%m-%dT%H:%M:%SZ"), "levels": levels}


# ---------------------------------------------------------------- IGRA
IGRA = "https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive/access"


def igra_zip_url(igra_id, year):
    if year == dt.datetime.now(dt.timezone.utc).year:
        return f"{IGRA}/data-y2d/{igra_id}-data-beg{year}.txt.zip"
    return f"{IGRA}/data-por/{igra_id}-data.txt.zip"


def parse_igra(text, when):
    """IGRA2 data format: a '#' header (ID, date, nominal hour, release time hhmm, number of levels,
    lat, lon) followed by fixed-width level records. Values -9999 and -8888 are missing. The sounding
    whose nominal time is closest to `when` within 3 h is taken (Innsbruck's 02 UTC counts as 00)."""
    best, best_d, cur, blocks = None, 4 * 3600, None, []
    for line in text.splitlines():
        if line.startswith("#"):
            try:
                t = dt.datetime.strptime(line[13:26], "%Y %m %d %H")
            except ValueError:
                cur = None
                continue
            d = abs((t - when).total_seconds())
            cur = [] if d < best_d else None
            if cur is not None:
                best, best_d = line, d
                blocks = cur
        elif cur is not None:
            cur.append(line)
    if not best:
        return None
    out = []
    for line in blocks:
        def f(a, b, scale=1.0):
            v = line[a:b].strip()
            return None if v in ("", "-9999", "-8888") else int(v) / scale
        p = f(9, 15)
        if p is None:
            continue
        r = {"p": p / 100, "z": f(16, 21), "T": f(22, 27, 10), "Td": None, "rh": f(28, 33, 10),
             "dir": f(40, 45), "spd": f(46, 51, 10), "t": f(3, 8)}
        dpdp = f(34, 39, 10)   # dew point depression
        if dpdp is not None and r["T"] is not None:
            r["Td"] = round(r["T"] - dpdp, 1)
        if r["dir"] is not None and r["dir"] > 360:
            r["dir"] = None
        out.append(r)
    return best[27:31], out


def fetch_igra(station, when):
    igra_id = STATIONS[station][3]
    raw = get(igra_zip_url(igra_id, when.year), timeout=600)
    if raw is None:
        return None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode("ascii", "replace")
    parsed = parse_igra(text, when)
    if not parsed:
        return None
    rel, levels = parsed
    return {"station": station, "source": "igra2", "time": when.strftime("%Y-%m-%dT%H:00Z"),
            "launch": rel if rel.strip("0123456789") == "" and int(rel) < 2500 else None, "levels": levels}


# ---------------------------------------------------------------- day driver
def fetch_best(station, when):
    """UWyo first (full resolution, hours of lag); IGRA when the station is not on UWyo or the day is too recent for it."""
    if STATIONS[station][4]:
        rec = fetch_uwyo(station, when)
        if rec:
            return rec
    return fetch_igra(station, when)


def save(rec, root):
    day = rec["time"][:10]
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s-%s.json.gz" % (rec["station"], rec["time"][11:13]))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9) as z:
        z.write(json.dumps(rec, separators=(",", ":")).encode())
    os.replace(tmp, path)
    return path


def main(argv):
    if len(argv) >= 5 and argv[1] in ("uwyo", "igra"):
        when = dt.datetime.strptime(argv[3], "%Y-%m-%d").replace(hour=int(argv[4]))
        rec = (fetch_uwyo if argv[1] == "uwyo" else fetch_igra)(argv[2], when)
        json.dump(rec, sys.stdout, separators=(",", ":"))
        return 0 if rec else 1
    if len(argv) >= 4 and argv[1] == "day":
        when = dt.datetime.strptime(argv[2], "%Y-%m-%d").replace(hour=int(argv[3]))
        root = (argv[argv.index("--out") + 1] if "--out" in argv
                else os.path.join(os.path.expanduser(os.environ.get("EC_WEATHER_DIR", "~/ec-weather")), "sondes"))
        for st in ACTIVE:
            if when.hour not in STATIONS[st][5]:
                continue
            path = os.path.join(root, when.strftime("%Y-%m-%d"), "%s-%02d.json.gz" % (st, when.hour))
            if os.path.exists(path):
                continue
            try:
                rec = fetch_best(st, when)
            except Exception as e:
                print(st, "FAILED:", e, file=sys.stderr)
                continue
            print(st, STATIONS[st][0], "-", ("%s %d levels -> %s" % (rec["source"], len(rec["levels"]), save(rec, root))) if rec else "none",
                  file=sys.stderr, flush=True)
            time.sleep(1)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
