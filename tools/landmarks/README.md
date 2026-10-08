# Peaks and passes: data/landmarks-europe.tsv

`data/landmarks-europe.tsv` names thermal places and probable wave in PATTERNS.md, sections 3 and 8. It
holds named peaks, volcanoes, saddles and mountain passes from OpenStreetMap within 34–72° N and 25° W–45° E
that have at least 3 Wikidata sitelinks, the number of Wikipedia and sister-project pages about them. A
summit known in three languages names a place, and an unnamed hump does not. Columns: name, lat, lon,
ele (m, when tagged), kind (`peak` or `pass`), sitelinks, wikidata. Built on 8 October 2026: 17,901 rows.

Refresh it once a year. Run the three scripts in an empty working directory, in this order:

```sh
mkdir -p /tmp/lm && cd /tmp/lm
python3 ~/git/ec-monitor/tools/landmarks/fetch_europe.py > fetch_europe.log   # Overpass, about 5 hours
python3 ~/git/ec-monitor/tools/landmarks/sitelinks.py                         # Wikidata SPARQL, minutes
python3 ~/git/ec-monitor/tools/landmarks/build_landmarks.py                   # writes landmarks-europe.tsv
cp landmarks-europe.tsv ~/git/ec-monitor/data/
```

`fetch_europe.py` asks Overpass for 5-degree tiles, nearest the Alps first. It saves one `tile_<s>_<w>.csv`
per tile and is resumable: a tile whose file exists is skipped, so after an interruption run it again. The
limits that work are `[timeout:180][maxsize:64MB]` (written as 67108864 bytes), **one request at a time**,
and a check of `/status` before each request, waiting for a free slot. A tile that keeps failing is split
into four 2.5-degree tiles, then into 1.25 degrees, and the ones given up on are listed in `failed.txt`.
`sitelinks.py` keeps its answers in `sitelinks.json` and only asks for Wikidata ids it has not seen.
`build_landmarks.py` joins the two and applies the 3-sitelink filter.

Known gaps in the file of 8 October 2026, where overpass-api.de was refusing connections when the run
stopped: Arctic Scandinavia above 69° N, Iceland, Finland and Karelia around 64° N, and the 40–45° E band
(the Caucasus, eastern Turkey and western Russia). Running `fetch_europe.py` again in the same directory
fetches only those tiles.

Sources: OpenStreetMap contributors, under the Open Database License (names and positions); Wikidata,
CC0 (sitelink counts).
