# Coverage analysis tools

The scripts behind METHOD.md, coverage in detail: how well the ground stations of the Open Glider Network hear an
aircraft at a given distance, how that turns into a prediction of the share of flying time without a signal for
every 0.25-degree cell, and how the prediction compares with what the service measured. They run on a copy of the
recording and print aggregates only: no output names an aircraft, and no data lives in this repository. Every
input and output is a command-line argument, and none of the scripts knows where anything is kept.

Python 3, `numpy`, `scipy` and `pymysql` (importing `sources.py` needs it, even where no database is used), and
`zstdcat` on the path. One way to get them without touching the system: `uv run --with numpy --with scipy --with
pymysql python reach.py ...`. Run the scripts from this directory.

## Inputs

| Input | What it is | Where it comes from |
|---|---|---|
| feed | a directory of hourly files `YYYYMMDD-HH.aprs.zst`, each line `<epoch> <APRS line>` | the recording of `recorder.py` |
| terrain | a flat little-endian int16 grid, 15 arc-second, north-up (the file `terrain/build_tiles.py` starts from); an optional `.json` beside it gives `rows`, `cols`, `step`, `north`, `west`, else the Europe grid (72 N, 25 W, 8880 x 16800) is assumed | ETOPO 2022, as in `terrain/` |
| coverage-gaps snapshot | the JSON of `/conspicuity-monitor/api/coverage-gaps`, saved on the day | the public API |
| `stations.json`, daily gaps TSV | stations with their sensitivity and reception rose; the daily rows of `daily_coverage_gaps` | `stations_snapshot.py`, which needs the service's database |

## The scripts, in the order to run them

**`stations_snapshot.py`** exports `stations.json` (OGN stations heard in the last 7 days, twins collapsed, median
signal at 10 km over 30 days, 36-sector reception rose) and, with `--daily-gaps`, the daily coverage-gaps rows. It
is the only script that reads the database, and it only reads. It is the one step that needs the service's host.

**`reach.py`** measures the reception curve: the probability that a station at distance d, with or without line
of sight, hears an aircraft in the air, per family, system and height above ground. `run` reads the feed and writes
one pickle of counts per day; `report` adds the days up, prints the curves and writes them as JSON, with intervals
from resampling whole days (the pairs of one minute are not independent, so the plain binomial interval is far too
narrow). The variants:

- `--stations ogn --los`: OGN stations only, with line of sight. These are the curves the coverage model uses.
- `--stations all --los`: FANET ground stations count too.
- `--any-system --los`: the probe analysis. Paragliders that carry two radios are probes; a hit is a reception on
  any system. `report --any-system --system-curves curves.json` compares it with two independent radios, and also
  prints the dip in reception at 0-2 km split by system, station kind, speed and height.
- without `--los` the line-of-sight counts stay zero and the run is several times faster.
- `--sample N` keeps one address in N of gliders and powered aircraft, for a quick look.

**`los_cells.py`** computes, for every counting cell of the snapshot, the line of sight from its 40 nearest stations
to 8 x 8 points at four heights: the geometry of the model, saved as `los.pkl`. `--extra-station` adds a station
that does not exist to one cell, for the counterfactual "what would a station here do".

**`validate.py`** predicts the share without signal of every cell and band from the curves and `los.pkl`, and
compares it with the measured share of the snapshot: rank correlation, mean absolute error (plain and weighted by
probe hours), bias, for the variants A (one curve), B (per-station sensitivity), C (per-sector sensitivity) and
the no-line-of-sight ones, and with three calibrations fitted on half of the cells and scored on the other half.
`--experiment family` (both families, per-family curves), `ogn` (free flight, OGN-only curves, the combined
`1-(1-pF)(1-pN)` of FLARM and FANET), `any` (the probe curve against the per-system ones).

**`reliability.py`** gives the ceiling for that comparison: how well the measured cells agree with themselves when
the days are split at random into two halves. A model cannot correlate better with the measurement than the
measurement does with itself, and the Spearman-Brown figure says what that limit is.

**`diagnose.py`** tests three suspects for the model's bias, alone and together: points weighted by where probes
really fly instead of spread over the cell, station uptime, and the probes' real height above ground. `weights`
reads the feed (one worker per day), `run` recomputes the model.

**`volume.py`** measures the coverage volume itself: airborne seconds and seconds without signal in voxels of 0.02
degree and 100 m (fine) and 0.05 degree and 250 m (coarse), per channel (radio, phone) and family (free flight,
crewed), with aircraft-days per voxel. `run` replays the feed with the service's own rules, `merge` writes
`summary.txt` and JSON tiles per degree square for the box given by `--box`.

`covlib.py` is the shared code: feed reading (through `sources.py`, so that a change of the flight rules reaches
the analysis too), terrain, the line-of-sight test, the curves and the model helpers.

## Order and time

On 24 days of feed (6-29 October 2026), on an Apple M3 Pro with 10 cores used:

1. `stations_snapshot.py` (seconds) on the host, and the snapshot from the API.
2. `reach.py run --stations ogn --los`: about 4 minutes, one worker per day, 1 GB per worker; then `report`
   (seconds). The same again with `--any-system --los` (about half the time: it parses paragliders only).
   A day of feed takes about a minute on one core, half of it parsing.
3. `los_cells.py`: once per snapshot, on one core (the figure was not timed); `--only` tests one cell in
   a second.
4. `validate.py`: seconds to a minute for `ogn` and `any`; `family` is the longest because of the calibration grid search.
5. `reliability.py`: seconds. `diagnose.py weights`: 20 s a day, in parallel; `run`: not timed.
6. `volume.py run`: about a minute per day, in parallel; `merge`: seconds.

Line of sight dominates `reach.py` once the parsing is done. A GPU does not help: on the Apple M3 Pro, the 10
CPU cores beat the Metal GPU (PyTorch MPS) at the line-of-sight kernel, and parsing the feed, which is half
the time, stays on the CPU in any case.

## How this maps to METHOD.md

The section "METHOD.md, coverage in detail" describes the rules; these scripts are the code behind its figures.

| METHOD.md, coverage in detail | Script |
|---|---|
| the reception curve and its line-of-sight form | `reach.py` |
| the probe curve, several radios on one aircraft, the dip near the station | `reach.py --any-system` |
| the model (stations in line of sight, product of misses) | `los_cells.py`, `validate.py` |
| station sensitivity and reception rose | `stations_snapshot.py`, variants B and C of `validate.py` |
| how far the model can agree with the measurement | `reliability.py` |
| what else explains the bias | `diagnose.py` |
| the measured volume | `volume.py` |

## Reproducing and checking

The counts of `reach.py` are the same as those of the first versions of these scripts, bin for bin (the
per-day pickles compare equal for the per-system and the probe runs); `report` differs only in that the first
day-resampled interval block now also prints the point estimate. The terrain is sampled every 250 m in the first
and last 5 km of a ray and every 500 m between, the earth radius is 4/3, the antenna 10 m above the terrain, the
bins 1 km up to 100 km, and the summary bins are 0-2, 2-5, 5-10, 10-15, 15-20, 20-30, 30-40, 40-50, 50-75 and
75-100 km. A change to any of these is a change to what is measured: it belongs in METHOD.md, committed on its own
with its reason.
