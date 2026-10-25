# What each part costs

Measured on the production host (2 vCPU, 4 GB RAM, 80 GB disk) on 25 October 2026, the
morning the monitor switched to the whole world, unless a line says otherwise. The commands at
the end repeat every figure. Update the date and the figures when a part changes size; a
figure older than a few months is a guess.

## Memory

The service is one gunicorn worker with two threads, which holds the live tracker, the raw
recorder and every page's cache. It held 276 MB of anonymous memory (302 MB RSS) shortly after
a restart. Its `/internal/memory` endpoint attributes 178 MB of it:

| Part | MB |
|---|---|
| Reference data: admin-1 regions 40, places 24, landmarks 14, site names 13, sites 10 | 101 |
| Device check (`tracker.check`) | 30 |
| Check caches (grid 9.6, encounters 3.7, patterns 3.2, prediction 2.9, detail 2.5, coverage gaps 1.4, others) | 27 |
| OGN device database tables (cards, type map, registrations) | 17 |
| Live tracker (live map, history, stations, seen and heard sets) | 1.5 |
| Not attributed: interpreter, libraries, allocator (inference) | about 98 |

The terrain model is a memory-mapped file: the worker reads pages of it as aircraft fly over
them, and those pages count in RSS as file cache, which the kernel takes back under pressure.
They are not in the anonymous figure.

The nightly batch peaked at 297–366 MB RSS from 15 to 23 October and at 435 MB on 24 October.
In a dry run of a synthetic world day (25 October; the real 24 October plus the outside-Europe
traffic of a US afternoon in every hour) its anonymous memory reached 183 MB against 168 MB for
the same day measured in Europe only; RSS was 919 against 488 MB, the difference being the
world terrain file read through the mapping.

MariaDB held 582 MB RSS (buffer pool 300 MB) for the whole host, the EC database included.

## CPU

- Service: 20.5 CPU-minutes over 8 h 39 min of uptime, about 4% of one core, with the feed
  parsing, the recorder and the pages in the same worker; per route or per thread it cannot be
  split (all threads are named `gunicorn`).
- Nightly: 567–1,689 s a day from 15 to 24 October for 9–18 million lines; the time follows the
  busy hours, when pairs of stations hearing the same aircraft are compared (relay and
  sensitivity), more than the line count. The synthetic world day took 25 min 8 s against
  21 min 20 s in Europe only. There is no timing per family (conspicuity, stations, patterns):
  they run interleaved, line by line.

## Database

`ads_l` held 82 MB on 25 October.

| Part | Tables | MB | Growth |
|---|---|---|---|
| Visibility, per day and source | `daily_visibility` | 21.6 | about 0.9 MB a day |
| Devices per source and month | `monthly_sources`, `monthly_devices` | 17.6 | about 16 MB a month, addresses kept two months, then counts |
| Visibility grid, detail, prediction | `monthly_visibility_*`, `monthly_prediction` | 13.2 | about 8 MB a month |
| Ground stations | `daily_stations` | 6.0 | about 0.4 MB a day |
| Air cells | `daily_air_cells` | 6.5 | about 0.3 MB a day |
| Flights | `daily_flights` | 2.5 | |
| About 25 smaller nightly tables | | about 20 | each under 0.1 MB a day |

Growth was measured in Europe; the world adds roughly a tenth of the stations and more of the
nightly rows.

## Disk

| Part | Size |
|---|---|
| Raw recording, `~/ec-raw`, 30 days | 200–250 MB a day compressed, about 7 GB when full (1.4 GB on 25 October, kept 7 days until 24 October) |
| World terrain, `data/world_15s_tiles.i16` (land tiles only, from 25 October 2026) | 2.26 GB; the flat `world_15s.i16` it was built from was 6.2 GB |
| European terrain, `data/europe_15s.i16` | 285 MB, no longer read since 25 October |
| Reference data in `data/` (tsv, admin-1) | about 12 MB |
| Nightly logs | under 1 MB |

## Commands

```sh
# memory: worker anon and RSS, and what the service attributes
ssh fivl 'grep -E "^(Rss|Pss|Anonymous):" /proc/$(pgrep -n -f "[g]unicorn -w")/smaps_rollup'   # -n: the worker, newer than the master
ssh fivl 'curl -s localhost:5000/internal/memory'
# nightly: seconds and lines per day, peak memory
ssh fivl 'mysql ads_l -e "SELECT day, seconds, line_count FROM nightly_runs ORDER BY day DESC LIMIT 10"; grep "Peak memory" ~/ec-nightly/nightly.log | tail -10'
# database: size per table
ssh fivl 'mysql -e "SELECT table_name, table_rows, ROUND((data_length+index_length)/1048576,1) mb FROM information_schema.tables WHERE table_schema=\"ads_l\" ORDER BY mb DESC"'
# disk
ssh fivl 'du -sh ~/ec-raw ~/ads-l-map/data; du -ch ~/ec-raw/$(date -u -d yesterday +%Y%m%d)-*.zst | tail -1'
```
