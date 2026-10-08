"""Fetch named peaks, volcanoes, saddles and mountain passes for 34-72 N, 25 W-45 E
from Overpass in 5-degree tiles, serially and slowly. Resumable: a tile whose
tile_<s>_<w>.csv exists is done; a tile that keeps failing is split into four
2.5-degree sub-tiles (tile_<s>_<w>.csv named by their own corner). Failed tiles
are listed in failed.txt at the end."""
import urllib.request, urllib.parse, urllib.error, time, os, sys, json
UA = "ec-monitor landmarks/1.0 (www.saccani.net/conspicuity-monitor; yearly refresh, serial, tiled)"
BASE = os.environ.get("OVERPASS", "https://overpass-api.de/api")
Q = '''[out:csv(::type,::id,::lat,::lon,name,ele,natural,mountain_pass,wikidata,wikipedia,prominence,"name:en";true;"\\t")][timeout:180][maxsize:67108864][bbox:{s},{w},{n},{e}];
(node["natural"="peak"]["name"]; node["natural"="volcano"]["name"]; node["natural"="saddle"]["name"]; nwr["mountain_pass"="yes"]["name"];);
out center;'''
PAUSE = 20          # seconds between successful requests
MAX_TRIES = 6       # per tile before splitting (or giving up on a 1.25-degree tile)

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def wait_slot():
    """Ask /status and sleep until a slot is free (at most 10 min)."""
    for _ in range(20):
        try:
            st = urllib.request.urlopen(urllib.request.Request(BASE + "/status", headers={"User-Agent": UA}), timeout=30).read().decode()
            if "slots available now" in st:
                return
            waits = [int(x.split(",")[-1].split()[1]) for x in st.splitlines() if "Slot available after" in x]
            time.sleep(min(max(waits or [30]) + 2, 120))
        except Exception:
            time.sleep(30)

def fetch(s, w, size):
    fn = f"tile_{s:g}_{w:g}.csv" if size == 5 else f"tile_{s:g}_{w:g}_{size:g}.csv"
    if os.path.exists(fn) and os.path.getsize(fn) > 0:
        return True
    n, e = min(s + size, 72), min(w + size, 45)
    delay = 60
    marker = fn[:-4] + ".split"
    for attempt in range(0 if os.path.exists(marker) else MAX_TRIES):
        wait_slot()
        t = time.time()
        try:
            req = urllib.request.Request(BASE + "/interpreter",
                data=urllib.parse.urlencode({"data": Q.format(s=s, w=w, n=n, e=e)}).encode(),
                headers={"User-Agent": UA})
            body = urllib.request.urlopen(req, timeout=400).read()
            if b"runtime error" in body[:4000] or body[:200].lstrip().startswith(b"<"):
                raise RuntimeError(body[:200].decode("utf-8", "replace").replace("\n", " "))
            open(fn + ".part", "wb").write(body)
            os.replace(fn + ".part", fn)
            log(fn, body.count(b"\n") - 1, "rows", f"{time.time()-t:.0f}s")
            time.sleep(PAUSE)
            return True
        except Exception as ex:
            log(fn, "attempt", attempt, type(ex).__name__, str(ex)[:120], f"sleep {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, 900)
    if size > 1.25:
        log(fn, "splitting")
        open(marker, "w").close()
        h = size / 2
        return all([fetch(s + i * h, w + j * h, h) for i in (0, 1) for j in (0, 1)])
    log(fn, "GAVE UP")
    GAVE_UP.append(f"{s:g}\t{w:g}\t{size:g}")
    return False

def done_already(s, w):
    return os.path.exists(f"tile_{s:g}_{w:g}.csv")

GAVE_UP = []
t0 = time.time()
tiles = sorted(((s, w) for s in range(34, 72, 5) for w in range(-25, 45, 5)),
               key=lambda t: (t[0] + 2.5 - 47) ** 2 + ((t[1] + 2.5 - 10) * 0.7) ** 2)
failed = []
for s, w in tiles:
    # a tile split earlier counts as done when all four quarter files exist
    if not fetch(s, w, 5):
        failed.append((s, w))
with open("failed.txt", "w") as f:
    for x in GAVE_UP:
        f.write(x + "\n")
log("finished", len(tiles), "tiles,", len(failed), "with gaps,", f"{(time.time()-t0)/60:.0f} min")
