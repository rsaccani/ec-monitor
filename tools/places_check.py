"""Checks of the /places endpoint (README.md) against the real data/ files.

Run from the checkout: python3 tools/places_check.py. It needs no database: the
checks call sources.place_request, which the route wraps (importing app.py
starts the live listener and wants a database), and the last lines print what the index costs in memory and how long a query takes.
"""
import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sources  # noqa: E402


class Reply:
    headers = {"Cache-Control": "public"}


def get(**params):
    body, status = sources.place_request({k: str(v) for k, v in params.items()})
    return status, body, Reply


def names(rows, kind=None):
    return [r["name"] for r in rows if kind is None or r["kind"] == kind]


# Base memory first: the three lists the thermal cells already hold.
tracemalloc.start()
sources._load_places(); sources._load_landmarks(); sources._load_sites()
base = tracemalloc.get_traced_memory()[0]
t0 = time.time()
sources._place_get()
build_s = time.time() - t0
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

code, rows, resp = get(q="grappa")
assert code == 200 and resp.headers["Cache-Control"].startswith("public"), (code, resp.headers)
peaks = names(rows, "peak")
assert peaks and peaks[0] == "Monte Grappa", peaks

code, rows, _ = get(q="cucco")
assert "Monte Cucco" in names(rows), names(rows)

for a, b in (("dolomiti", "dolomiti"), ("säntis", "santis"), ("SANTIS", "säntis")):
    ra, rb = get(q=a)[1], get(q=b)[1]
    assert ra and ra == rb, (a, b, names(ra), names(rb))
assert "Säntis" in names(get(q="santis")[1]), names(get(q="santis")[1])
assert get(q="dolomiti")[1], "dolomiti"
# punctuation and a word inside a name
assert any("Aquila" in n for n in names(get(q="l'aquila")[1])), names(get(q="l'aquila")[1])
assert any("Grappa" in n for n in names(get(q="del grappa")[1])), names(get(q="del grappa")[1])

for q in ("", "a", " g ", "-"):
    assert get(q=q)[1] == [], q
assert len(get(q="mo")[1]) == 10

code, rows, _ = get(s=45.7, w=11.6, n=46.0, e=11.95)
assert code == 200 and "Monte Grappa" in names(rows, "peak"), names(rows)[:10]
assert any(r["kind"] in ("takeoff", "fivl") for r in rows), "no take-offs"
assert len(rows) <= 200
mg = next(r for r in rows if r["name"] == "Monte Grappa")
assert mg["ele"] > 1700 and mg["sitelinks"] >= 3, mg

for bad in (dict(s=45, w=11, n=47, e=12), dict(s=45, w=11, n=46, e=13), dict(s="x", w=11, n=46, e=12),
            dict(s=46, w=11, n=45, e=12), dict(s=45, w=11, n=46), dict(s="nan", w=11, n=46, e=12)):
    code, body, _ = get(**bad)
    assert code == 400 and "error" in body, (bad, code)
# exactly one degree is allowed
assert get(s=45, w=11, n=46, e=12)[0] == 200

print("ok")
print("sample search:", get(q="grappa")[1][:2])
print("sample box:", get(s=45.7, w=11.6, n=46.0, e=11.95)[1][:2])
print(f"index: {sources._place_index['n']} keys, build {build_s:.1f} s, "
      f"resident {(cur - base) / 1e6:.1f} MB, transient peak {(peak - base) / 1e6:.1f} MB")
t0 = time.time()
for q in ("mo", "monte", "gr", "san"):
    get(q=q)
print(f"4 broad queries: {(time.time() - t0) * 1000:.0f} ms")
