# One-off tools

`schema_doc.py` is the exception: it is run after every migration, and regenerates `schema.sql` and
`SCHEMA.md` from the production database (README.md, Running it). The rest are described below.

Scripts that changed stored figures after the fact, or checked them, kept
here because METHOD.md cites what they did and the code behind every figure
is meant to be public. Until 7 October 2026 they lived in a private
operations repository and in the home directory of the host. They ran once
each, on the server, from the service's checkout (`cd ~/ads-l-map`), and
print aggregates only. None is meant to run again: the backfills stop at a
marker file in `~/ec-replay/` if they already wrote.

| Script | What it did | When |
|---|---|---|
| `ec_weather_cleanup.py` | Collected FANET weather-station addresses from the live feed and deleted them from October's `monthly_sources`, with the unknown-category row of `monthly_hours` they had inflated | 5 October 2026, after `41a073c` |
| `ec_fanet_nocat_cleanup.sh` | Deleted October's FANET rows with no category (weather stations, ground-station beacons, devices without a fix), backup first | 5 October 2026, after `9bd6f89` |
| `ec_replay_airborne.py`, `ec_replay_versions.py` | Harness: run a given version of `sources.py` over the raw recording with a fake clock fed by the recorded arrival times | 6 October 2026 |
| `ec_backfill_20261006.py` | Recomputed 06:07:56–11:00:05 UTC of 6 October under the new airborne rules and added new minus old to the detail, grid and hours tables | 6 October 2026 |
| `ec_backfill_sig_20261006.py` | Filled the `sig_*` columns from the recording, per system to 12:16:02 and per aircraft to 13:17:13 UTC | 6 October 2026 |
| `ec_backfill_fixes_20261006.py` | Brought `sig_*` from 06:07:56 to 17:45:24 UTC under the review's fixes (`3c1347c`) | 6 October 2026 |
| `indep_check.py`, `svc_replay.py` | Independent recomputation from METHOD.md alone, and the service's own code over the same hours, to compare the two | 6 October 2026 |
| `ec_radio_silence_check.py` | Where free flight's radio time without signal comes from: by device, length of silence and height | 6 October 2026 |
| `ec_fanet_neighbours.py` | FANET intervals in flight by number of FANET neighbours, behind the 15-second rule | 6 October 2026 |
| `ec_circling_direction.py` | First look at circling direction by kind and by pilot, behind section 10.2 | 7 October 2026 |

The backfills take the `sources.py` versions to compare as arguments; the
copies used were saved on the host in `~/ec-replay/`. The new side of each was
the code deployed at the end of its window: `39a72b6` (11:00:05),
`f659df0` and `5414708` (12:16:02 and 13:17:13), `3c1347c` (17:45:24).
