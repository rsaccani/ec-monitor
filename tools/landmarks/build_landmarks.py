"""tile_*.csv + sitelinks.json -> landmarks-europe.tsv (filter F: Wikidata sitelinks >= 3)."""
import glob, json, re, collections
MIN_SL = 3
SL = json.load(open("sitelinks.json"))
def ele(s):
    m = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", s or "")
    return str(round(float(m.group(1).replace(",", ".")))) if m else ""
seen, out, total = set(), [], collections.Counter()
for fn in sorted(glob.glob("tile_*.csv")):
    with open(fn, encoding="utf-8") as f:
        h = f.readline().rstrip("\n").split("\t")
        for line in f:
            r = dict(zip(h, line.rstrip("\n").split("\t")))
            key = (r["@type"], r["@id"])
            if key in seen or not r.get("@lat"):
                continue
            seen.add(key)
            kind = "pass" if r.get("mountain_pass") == "yes" or r.get("natural") == "saddle" else "peak"
            total[kind] += 1
            q = r.get("wikidata", "").strip()
            n = SL.get(q, 0) if re.fullmatch(r"Q\d+", q) else 0
            if n >= MIN_SL:
                out.append((r["name"].strip(), float(r["@lat"]), float(r["@lon"]), ele(r.get("ele")), kind, n, q))
out.sort(key=lambda x: (round(x[1], 2), x[2]))
with open("landmarks-europe.tsv", "w", encoding="utf-8") as f:
    f.write("# name\tlat\tlon\tele\tkind\tsitelinks\twikidata\n")
    for x in out:
        f.write(f"{x[0]}\t{x[1]:.5f}\t{x[2]:.5f}\t{x[3]}\t{x[4]}\t{x[5]}\t{x[6]}\n")
print("named features", sum(total.values()), dict(total))
print("kept", len(out), dict(collections.Counter(x[4] for x in out)))
