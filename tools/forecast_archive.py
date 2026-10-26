#!/usr/bin/env python3
"""Archive this morning's numerical weather forecasts, as issued, for later comparison with the feed.

Run once a day, after the morning model runs are on Open-Meteo (04:30 UTC is a good time:
the 00 UTC runs of ICON-EU, ICON-D2, ICON-2I, AROME and the 18 UTC run of IFS are all in by then).
For each model it downloads the hourly forecast of the current UTC day, 06-20 UTC, at a fixed set
of points, and saves one gzipped JSON per day and model:

    $EC_WEATHER_DIR/forecasts/YYYY-MM-DD/<model>.json.gz

EC_WEATHER_DIR defaults to ~/ec-weather. The files are written atomically and a model already
saved for the day is skipped, so a second run only fills the gaps.

Why the forecast API and not the historical-forecast API: the second one stitches the freshest
available runs together, so it answers "what did the model know in hindsight". This script keeps
"what the model said in the morning", which is what a pilot or a forecaster could have read.

Points: a regular grid of GRID_STEP = 0.75 degrees (about 83 km north-south, 55 km east-west) over
41-49.25 N, 4-16.75 E, 216 points, plus the 14 radiosonde stations of sondes.py. The step comes from
the API budget. Open-Meteo counts a request as locations x max(1, variables / 10) calls (measured:
56 variables cost 5.6 a location, and a 0.5 degree grid of 500 points exhausted the free hour after
two models). With 32-56 variables per model the five models cost about 23 calls a point, so a
230-point day is about 4,600 of the 10,000 allowed, and a 500-point day would be 10,500. 0.75
degrees still resolves a front, the Po valley / Alps contrast and the main valley wind regimes;
use --step 0.5 only with a paid key or spread over two days. Points outside a model's domain
(ICON-2I stops at 48.9 N, AROME at 16 E) are skipped, and a point the API refuses is skipped
alone and remembered in $EC_WEATHER_DIR/outside.json, so later runs leave it out for OUTSIDE_DAYS.

Open-Meteo free tier: non-commercial use, CC BY 4.0 (attribution required), 600 calls a minute,
5,000 an hour, 10,000 a day. One model per request, GRID_BATCH locations per request. A sliding
window (Limiter) keeps the weight under MAX_PER_MINUTE and MAX_PER_HOUR, so a full run takes a
little over an hour; 429 and 5xx answers are retried with exponential backoff (an hourly-limit
answer waits ten minutes), and a batch that the API refuses is resolved by probing its points one by one.

Usage:  forecast_archive.py [--date YYYY-MM-DD] [--models m1,m2] [--step 0.5] [--dry-run]
Exit status: 0 all models saved or already present, 1 at least one model failed.
"""
import argparse, datetime as dt, gzip, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sondes import ACTIVE, STATIONS  # noqa: E402

API = "https://api.open-meteo.com/v1/forecast"
META = "https://api.open-meteo.com/data/{dir}/static/meta.json"
LAT0, LAT1, LON0, LON1 = 41.0, 49.5, 4.0, 17.0
GRID_STEP = 0.75
GRID_BATCH = 20          # locations per request
SLEEP = 1.0              # minimum pause between requests; the weight limiter below does the real pacing
MAX_PER_MINUTE = 450     # API call weight we allow ourselves, against 600/minute
MAX_PER_HOUR = 4000      # against 5,000/hour; a full run is about 4,600, so it takes a little over an hour
DOMAIN_MARGIN = 0.3      # degrees kept clear of a model's published domain edge
OUTSIDE_DAYS = 30        # a point a model refused is left out for this long, then asked again
OUTSIDE_FILE = None      # $EC_WEATHER_DIR/outside.json, set in main()
HOUR0, HOUR1 = 6, 20     # UTC, inclusive

# Open-Meteo model id -> (name of its data directory for run metadata, pressure levels with data)
MODELS = {
    "icon_eu": ("dwd_icon_eu", [1000, 975, 950, 925, 900, 850, 800, 750, 700, 600, 500]),
    "ecmwf_ifs025": ("ecmwf_ifs025", [1000, 925, 850, 700, 600, 500]),
    "icon_d2": ("dwd_icon_d2", [1000, 975, 950, 925, 900, 850, 800, 750, 700, 600, 500]),
    "italia_meteo_arpae_icon_2i": ("italia_meteo_arpae_icon_2i", [1000, 925, 850, 700, 500]),
    "meteofrance_arome_france": ("meteofrance_arome_france0025", [1000, 950, 925, 900, 850, 800, 750, 700, 600, 500]),
}
SURFACE = ["temperature_2m", "pressure_msl", "surface_pressure", "boundary_layer_height",
           "wind_speed_10m", "wind_direction_10m", "wind_speed_80m", "wind_direction_80m",
           "wind_speed_120m", "wind_direction_120m", "wind_speed_180m", "wind_direction_180m"]


def variables(levels):
    return SURFACE + [f"{v}_{p}hPa" for p in levels
                      for v in ("temperature", "wind_speed", "wind_direction", "geopotential_height")]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


class Limiter:
    """Sliding-window pacing on the API's call weight. A request for n locations and v variables
    costs n * max(1, v / 10) (measured 25 October 2026: 56 variables cost 5.6 a location)."""
    events = []

    @classmethod
    def wait(cls, weight):
        t0 = time.time()
        while True:
            now = time.time()
            cls.events = [(t, w) for t, w in cls.events if now - t < 3600]
            minute = sum(w for t, w in cls.events if now - t < 60)
            hour = sum(w for t, w in cls.events)
            if minute + weight <= MAX_PER_MINUTE and hour + weight <= MAX_PER_HOUR:
                cls.events.append((now, weight))
                # a long silent gap in the log otherwise reads as a hung run (26 October: AROME, 56 minutes)
                if now - t0 >= 30:
                    log("limiter waited", round(now - t0), "s, weight in the last hour", round(hour), "of", MAX_PER_HOUR)
                return
            time.sleep(5)


class Counter:
    requests = 0
    bytes = 0
    weight = 0.0


def http_json(url, tries=5, weight=0):
    """GET and decode JSON. 400 raises ValueError (caller may split the batch); 429 and 5xx back off."""
    delay = 10
    for attempt in range(tries):
        if weight:
            Limiter.wait(weight)
            Counter.weight += weight
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ec-monitor-forecast-archive"}), timeout=120) as r:
                raw = r.read()
            Counter.requests += 1
            Counter.bytes += len(raw)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code == 400:
                raise ValueError(body)
            if e.code == 429 and "aily" in body:
                raise RuntimeError("daily limit reached: " + body)
            if e.code == 429 and "ourly" in body:
                delay = 600
            log("HTTP", e.code, body[:120], f"retry in {delay}s")
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            log("ERR", e, f"retry in {delay}s")
        time.sleep(delay)
        delay = min(delay * 3, 600)
    raise RuntimeError("giving up after %d tries: %s" % (tries, url[:200]))


def meta(directory):
    try:
        m = http_json(META.format(dir=directory), tries=2)
    except Exception as e:  # metadata is a nicety, never a reason to fail
        log("meta", directory, e)
        return {}
    wkt = m.pop("crs_wkt", "")
    b = re.search(r"BBOX\[([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\]", wkt)
    if b:
        m["bbox"] = [float(x) for x in b.groups()]  # lat0, lon0, lat1, lon1
    iso = lambda k: (dt.datetime.fromtimestamp(m[k], dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if k in m else None)
    m["run_init_utc"] = iso("last_run_initialisation_time")
    m["run_available_utc"] = iso("last_run_availability_time")
    return m


def build_points(step):
    pts, seen = [], set()
    for sid in ACTIVE:
        name, lat, lon = STATIONS[sid][:3]
        pts.append({"id": "sonde-" + sid, "lat": round(lat, 4), "lon": round(lon, 4), "name": name})
    n_lat, n_lon = int(round((LAT1 - LAT0) / step)), int(round((LON1 - LON0) / step))
    for i in range(n_lat + 1):
        for j in range(n_lon + 1):
            lat, lon = round(LAT0 + i * step, 4), round(LON0 + j * step, 4)
            pts.append({"id": f"g{lat:.2f}_{lon:.2f}", "lat": lat, "lon": lon})
    return pts


def probe(base, p):
    """Does the model have this point? One cheap call (one variable, one hour): a refused point is found
    for 1 call of weight instead of by halving a 56-variable batch."""
    model = re.search(r"models=([^&]+)", base).group(1)
    day = re.search(r"start_hour=([^&]+)", base).group(1)
    q = f"{API}?hourly=temperature_2m&models={model}&timezone=GMT&start_hour={day}&end_hour={day}&latitude={p['lat']:.4f}&longitude={p['lon']:.4f}"
    try:
        http_json(q, weight=1)
        return True
    except ValueError:
        return False


def load_outside():
    try:
        with open(OUTSIDE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_outside(known):
    tmp = OUTSIDE_FILE + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(known, f, indent=1, sort_keys=True)
    os.replace(tmp, OUTSIDE_FILE)


def fetch_batch(base, batch, nvars, refused):
    """Return one response per point (None for a point the model lacks). When the API refuses a batch
    because of a point outside the model's real domain, probe the points one by one, then fetch the good ones."""
    q = f"{base}&latitude={','.join('%.4f' % p['lat'] for p in batch)}&longitude={','.join('%.4f' % p['lon'] for p in batch)}"
    try:
        d = http_json(q, weight=len(batch) * max(1.0, nvars / 10))
    except ValueError as e:
        if len(batch) == 1:
            log("skip", batch[0]["id"], str(e)[:100])
            if "No data" in str(e):
                refused.append(batch[0]["id"])
            return [None]
        good = [p for p in batch if probe(base, p)]
        for p in batch:
            if p not in good:
                log("skip", p["id"], "outside the model's domain")
                refused.append(p["id"])
            time.sleep(0.2)
        res = iter(fetch_batch(base, good, nvars, refused) if good else [])
        return [next(res) if p in good else None for p in batch]
    return [d] if isinstance(d, dict) else d


def archive_model(model, day, points, outdir, dry):
    path = os.path.join(outdir, model + ".json.gz")
    if os.path.exists(path):
        log(model, "already saved, skipped")
        return True
    directory, levels = MODELS[model]
    m0 = meta(directory)
    bb = m0.get("bbox")
    # a margin, because some domains (ICON-D2's southern edge) refuse points just inside the published box
    # and every refused point costs a bisection of its batch, that is several wasted calls
    mg = DOMAIN_MARGIN
    pts = [p for p in points if not bb or (bb[0] + mg <= p["lat"] <= bb[2] - mg and bb[1] + mg <= p["lon"] <= bb[3] - mg)]
    # The published box is a rectangle and the real domain is not (AROME's eastern edge slants from
    # 12.6 E at 41 N to 15 E at 49 N, 33 points on 26 October), and every refused point costs its
    # batch a probe of each point: points refused on an earlier day stay out for OUTSIDE_DAYS.
    known = load_outside()
    cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=OUTSIDE_DAYS)).isoformat()
    mine = {k: v for k, v in known.get(model, {}).items() if v > cutoff}
    n_box = len(pts)
    pts = [p for p in pts if p["id"] not in mine]
    hv = variables(levels)
    base = (f"{API}?hourly={','.join(hv)}&models={model}&wind_speed_unit=kmh&timezone=GMT"
            f"&start_hour={day}T{HOUR0:02d}:00&end_hour={day}T{HOUR1:02d}:00")
    log(model, "points", len(pts), "of", len(points), "(%d known outside)" % (n_box - len(pts)), "variables", len(hv), "run", m0.get("run_init_utc"))
    if dry:
        return True
    out, units, hours, t0, nreq0, refused = [], {}, None, time.time(), Counter.requests, []
    for i in range(0, len(pts), GRID_BATCH):
        batch = pts[i:i + GRID_BATCH]
        for p, d in zip(batch, fetch_batch(base, batch, len(hv), refused)):
            if not d or "hourly" not in d:
                continue
            h = d["hourly"]
            hours = hours or h["time"]
            series = {k: v for k, v in h.items() if k != "time" and any(x is not None for x in v)}
            for k in series:
                units.setdefault(k, d.get("hourly_units", {}).get(k))
            out.append({**p, "elev": d.get("elevation"), "series": series})
        time.sleep(SLEEP)
    if refused or len(mine) != len(known.get(model, {})):
        known[model] = {**mine, **{pid: day for pid in refused}}
        save_outside(known)
    if not out:
        log(model, "no data at all")
        return False
    m1 = meta(directory)
    doc = {
        "model": model, "date": day, "api": "forecast", "fetched_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "request": base + "&latitude=<lat,...>&longitude=<lon,...>",
        "batch": GRID_BATCH, "grid_step_deg": GRID_STEP,
        "run_init_utc": m0.get("run_init_utc"), "run_init_utc_after": m1.get("run_init_utc"),
        "run_available_utc": m0.get("run_available_utc"), "update_interval_s": m0.get("update_interval_seconds"),
        "time_resolution_s": m0.get("temporal_resolution_seconds"),
        "units": units, "hours": hours, "points": out,
    }
    os.makedirs(outdir, exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)
    log(model, "saved", len(out), "points", os.path.getsize(path), "bytes,", Counter.requests - nreq0, "requests,", round(time.time() - t0), "s")
    return True


def main():
    global GRID_STEP, OUTSIDE_FILE
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"), help="UTC day (default today)")
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--step", type=float, default=GRID_STEP, help="grid step in degrees")
    ap.add_argument("--dry-run", action="store_true", help="list what would be fetched")
    a = ap.parse_args()
    GRID_STEP = a.step
    root = os.path.expanduser(os.environ.get("EC_WEATHER_DIR", "~/ec-weather"))
    outdir = os.path.join(root, "forecasts", a.date)
    OUTSIDE_FILE = os.path.join(root, "outside.json")
    points = build_points(a.step)
    ok = True
    for model in a.models.split(","):
        if model not in MODELS:
            sys.exit("unknown model " + model)
        try:
            ok = archive_model(model, a.date, points, outdir, a.dry_run) and ok
        except Exception as e:
            log(model, "FAILED:", e)
            ok = False
    log("total requests", Counter.requests, "call weight", round(Counter.weight), "bytes received", Counter.bytes)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
