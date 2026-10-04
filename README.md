# Electronic Conspicuity Monitor

The backend of the [Electronic Conspicuity Monitor](https://www.saccani.net/conspicuity-monitor/), a public
measurement of how well the systems that make light aircraft visible to each other actually work: ADS-L
over SRD860, ADS-L Mobile, FLARM, FANET, ADS-B, the OGN tracker and the phone apps that forward
positions over the internet. It reads the Open Glider Network (OGN) feed, keeps a live map of every source
it carries, and adds up, for every source and kind of aircraft, how long an aircraft stayed visible and how
far from its true position another pilot would have placed it.

Every answer is given for light aviation as a whole and then for powered aircraft, gliders and free flight
(paragliders and hang gliders). Free flight gets the most attention because it is the case that rules
written for powered aircraft tend to miss: some 200,000 active pilots in Europe, slow and circling
aircraft, a radio worn close to the pilot's body.

## Method

The rules behind every figure are in [METHOD.md](METHOD.md), and its
[rendered version](https://www.saccani.net/conspicuity-monitor/method/) shows them together with every
change made since. Version 1 is the tag `method-v1`, in force from 4 October 2026 at 00:00 UTC; every
later change to the method is a commit of its own, with its date and its reason. The rules were
worked out and tested on the live feed on 3 October 2026, and the measurements of that day were
discarded.

In short, the service measures:

- how many distinct devices each source shows per month, and through which channel (heard by a ground
  receiver, or forwarded over the internet);
- for every airborne second, whether the last position received is still within 300 m of the aircraft
  (up to date), between 300 m and 1 km (approximate) or more than 1 km behind (lost);
- the same by height above sea and above ground, by 0.25-degree cell, and how often aircraft go silent
  for 2, 5 or 20 minutes while airborne;
- how far ahead a receiver can predict a turning aircraft with and without the turn rate it transmits,
  including after emulated packet losses of 2 to 32 seconds;
- the reception pattern of radios while their aircraft circles, as a measure of how the installation,
  and in free flight the pilot's body, shields the signal.

## Data and privacy

Positions come from the OGN APRS feed (`aprs.glidernet.org:14580`), which carries what volunteer ground
receivers hear and what apps and platforms forward. Everything the service publishes is either a live
position of the last few minutes or a count; what it keeps, and for how long, is below. The rules are
in [METHOD.md](METHOD.md), sections 1 and 9.

**Owners' choices.** The OGN device database records, for each device, whether its owner allows it to
be tracked and identified, and the OGN data usage terms require every service to follow those
choices. A device with `TRACKED=N` is dropped as soon as its packet is read: it appears nowhere, on the
map, in the counts or in the measures. A device with `IDENTIFIED=N` is shown and counted without its
aircraft model or registration. Devices are matched by their 24-bit address, whatever prefix the feed
gives them.

**What is shown live.** The map shows the positions of the last 15 minutes (60 for ADS-L), with the
last ten positions of each ADS-L device.

**What is stored, and for how long.**

| What | Kept |
|---|---|
| Positions and tracks | never written to the database; held in memory only while the device is on the live map or being measured |
| Device addresses (`monthly_devices`, `monthly_sources`), with the first and last time each was heard in the month | the current and the previous month; then reduced to counts and deleted |
| Counts of devices per month, address type, category, source and channel (`*_summary`) | indefinitely |
| Totals of the measures per day, month, source, category, height band and 0.25-degree cell | indefinitely |

A device address is personal data in the sense of the GDPR, since it can be traced to an aircraft and
its pilot. That is why addresses are kept only as long as the counts need them: every six hours the
service reduces each month older than the previous one to `monthly_devices_summary` and
`monthly_sources_summary`, in one transaction per table, and deletes its addresses. The previous month
is kept whole to measure how many devices are heard again from one month to the next. No address and
no position is published by the statistics endpoints, which return counts and sums only.

**Data licence and terms.** The OGN data are under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/), and what the endpoints
serve is a database derived from them, offered under the same licence. The
[OGN data usage terms](https://www.glidernet.org/ogn-data-usage/) also say that OGN data older than 24
hours must not be redistributed. The live map stays within that. The published figures are counts
and sums over longer periods, which contain no position of any identifiable aircraft; whether the
rule also covers aggregates of this kind is not settled in the terms.

## How it runs

One gunicorn worker runs a listener thread on the OGN feed (the whole feed, about 1,200 lines a second at a busy hour)
and the Flask endpoints that the page reads. `sources.py` keeps the totals in memory and writes them to
MariaDB every 15 minutes, counted from the worker's start, so a restart loses what accumulated since
the last write. Height above ground comes from a terrain model, a raw int16 grid cut from ETOPO 2022 at
15 arc-seconds (`data/europe_15s.i16`, with its bounds and step in the `.json` beside it, or the path in
`ADSL_DEM`); it is about 300 MB and is not in the repository. Without it, height above ground is recorded
as unknown.

Until 3 October 2026 this project was called `ads-l-map` and only mapped ADS-L devices. The production
service, its directory and the `ads_l` database still carry that name.

## Endpoints

The public endpoints live under `/conspicuity-monitor/api/`, which nginx proxies to the service on the
same host as the page. `/demo` and `/device-map` are for local use and are not proxied.

The statistics endpoints answer JSON, and also `?format=csv` with one flat row per entry (nested fields
become `parent.child` columns), for whoever wants to redo the sums in a spreadsheet. They are cached for
10 minutes.

### `/demo`
**Method:** GET
**Description:** A standalone demo map of ADS-L devices (`templates/map.html`), for whoever runs the service locally. It is not exposed on the public site and is not kept in step with it.

### `/conspicuity-monitor/api/adsl`
**Method:** GET
**Description:** The ADS-L devices heard in the last 60 minutes, with their last ten positions, as JSON.

### `/conspicuity-monitor/api/adsl/monthly`
**Method:** GET
**Description:** Returns one entry per month on record, newest first: `devices` (distinct addresses), `addresses` (the same split by address type: `icao`, `flarm`, `ogn`, `random`, `pilotaware`, `fanet`, `other`), `categories` and `categorised` (aircraft category as declared in the OGN id, recorded only since the `category` column exists), and `partial`, true for the current UTC month. Random addresses change at every power-up or more often, so they overcount devices.

### `/conspicuity-monitor/api/live`
**Method:** GET
**Description:** Live positions from the other OGN sources, for the map's layer selector. `?layers=` takes a comma-separated subset of `flarm`, `fanet`, `radio` (other radio systems), `apps` (phone apps), `trackers` (hardware sending over a cellular or satellite link) and `relays` (platforms forwarding other sources, plus unknown internet sources); `&bbox=west,south,east,north` limits the answer to the visible area. ADS-L stays on `/adsl`, and ADS-B is not mapped. A device leaves the list 15 minutes after it was last heard.

### `/conspicuity-monitor/api/sources`
**Method:** GET
**Description:** Distinct devices per month for every OGN source (the APRS tocall, see `tocalls.txt` in glidernet/ogn-aprs-protocol), split by `via`: `radio` when a ground receiver heard the packet (it carries the receiver's dB and kHz figures, and ADS-B always counts as radio), `net` when an app or platform injected it over the internet. `multi_day` counts devices heard on two different days of the month, which shows whether a source keeps stable ids. Cached for 10 minutes.

### `/conspicuity-monitor/api/visibility`
**Method:** GET
**Description:** Daily visibility totals for the last 62 days, per source, channel and aircraft category, as defined in [METHOD.md](METHOD.md): airborne seconds, and the seconds during which the position a receiver could estimate was more than 300 m, 1 km or 3 km from the true one, for the last-point and the packet-based estimator. Also packets, packets carrying a turn rate, segments, session breaks and implausible segments. Cached for 10 minutes.

### `/conspicuity-monitor/api/hours`
**Method:** GET
**Description:** Flying time per month and aircraft category (`air_seconds`, `segments`), each aircraft counted once: every fix of the same 24-bit address feeds one timeline, whatever source or channel it came by. Starts on 2026-10-04 (METHOD.md).

### `/conspicuity-monitor/api/systems`
**Method:** GET
**Description:** Per month and aircraft category, how many aircraft (24-bit addresses) were heard on one, two, or three or more systems, how many on both a radio system and a phone app (`radio_and_phone`) or on phone apps only (`phone_only`), the combinations of systems shared by at least 5 aircraft with the rest pooled in `other_combinations`, and the ADS-L transmitters among them (`adsl`, `adsl_with_flarm`, `adsl_with_fanet`, `adsl_only`). Only counts; computed from the months whose addresses are still kept (METHOD.md).

### `/conspicuity-monitor/api/visibility/detail`
**Method:** GET
**Description:** Monthly visibility totals per source, channel, aircraft category, height band above sea (`msl_band`, 0 = below 1,000 m … 4 = above 4,000 m, 255 when the packet carries no altitude) and above ground (`agl_band`, 0 = below 300 m, 1 = 300–600, 2 = 600–1,200, 3 = 1,200–2,000, 4 = above 2,000; 255 when outside the terrain model or with no altitude), with a third estimator that ignores the turn rate (`p2_*`) and the same figures restricted to segments that carried a turn rate (`rot_*`) or showed circling (`circ_*`), and the airborne seconds during which the last position was no older than 3, 6, 15 and 30 s (`age_le*`) with the segments no longer than 3 and 6 s (`seg_le*`), and `vanish_2`, `vanish_5`, `vanish_20`, devices silent for more than 2, 5 or 20 minutes after being last seen airborne in that band, and `int_le2` … `int_le64`, airborne segments no longer than 2 … 64 s (the real interval between received packets). See METHOD.md.

### `/conspicuity-monitor/api/visibility/grid`
**Method:** GET
**Description:** Monthly visibility per 0.25-degree cell, group of sources and channel, attributed to where the aircraft was last seen; `?month=YYYY-MM`, default the current month. Groups are the map layers plus `adsl`; the extra group `pg` sums paragliders and hang gliders over every source and channel (its `via` column is always `radio` and means nothing), for the share of free flight in cells where apps work (METHOD.md). Until 2026-10-03 trackers and relays were a single `network` group.

### `/conspicuity-monitor/api/pattern`
**Method:** GET
**Description:** Radio packets received while the aircraft was circling, by source, aircraft category, distance to the receiving station (`dist_band` 0 = under 5 km, 1 = 5–10, 2 = 10–20, 3 = 20–40, 4 = over 40) and angle between the aircraft's course and the bearing to that station (`sector_deg`, 30-degree sectors). `snr_sum`/`snr_n` give the mean signal-to-noise ratio of those packets corrected for distance (free-space loss), so that the pattern can be read in decibels. Read as the radiation pattern of the installation in flight; see METHOD.md, "How the installation shields the signal".

### `/conspicuity-monitor/api/prediction`
**Method:** GET
**Description:** Errors of four predictions of where an aircraft will be 5, 10 and 20 s ahead (straight line, arc from a turn rate the receiver derives from two positions, arc from the transmitted turn rate, last point), by source, category, horizon and circling or straight flight, as counts in error bins (`b0`… with the edges in `bins_m`) plus `n` and `error_sum`. `derived_paired` and `transmitted_paired` hold the same predictions restricted to instants where both exist, which is what the turn-rate comparison reads. See METHOD.md, "Predicting a few seconds ahead".

### `/conspicuity-monitor/api/method`
**Method:** GET
**Description:** `METHOD.md` and the history of its commits, read from the deployed checkout (`text`, `commits` with sha, date, title and body, `deployed` commit). Used by the method page, which cannot fetch GitHub itself because the site's Content-Security-Policy limits connections to its own origin. Cached for 10 minutes.

### `/device-map`
**Method:** GET
**Description:** The device type mapping loaded from the OGN device database.

## Running it

The service needs Python 3.11 or later and MariaDB 10.5 or later (or MySQL 8). Production runs on Debian 12
with the distribution's packages (`python3-flask`, `python3-pymysql`, `python3-dotenv`,
`python3-requests`, `gunicorn`) and MariaDB 10.11. Anywhere else a virtual environment does the same:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Create the database and its tables from [schema.sql](schema.sql), which is taken from the production
tables:

```bash
mysql < schema.sql
```

Then the service user. It needs exactly these grants and nothing more. `monthly_devices` and
`monthly_sources` are updated one column at a time, while `monthly_visibility_detail`,
`monthly_visibility_grid`, `monthly_reception_pattern` and `monthly_prediction` are updated in place
with `INSERT … ON DUPLICATE KEY UPDATE`, which needs `UPDATE` on every column of the table.
`daily_visibility` is append-only. `DELETE` on the two device tables is for the monthly archiving
described under Data and privacy.

```sql
CREATE USER 'ads_user'@'localhost' IDENTIFIED BY 'choose-a-password';
GRANT SELECT, INSERT ON ads_l.* TO 'ads_user'@'localhost';
GRANT UPDATE (category) ON ads_l.monthly_devices TO 'ads_user'@'localhost';
GRANT UPDATE (last_seen) ON ads_l.monthly_sources TO 'ads_user'@'localhost';
GRANT DELETE ON ads_l.monthly_devices TO 'ads_user'@'localhost';
GRANT DELETE ON ads_l.monthly_sources TO 'ads_user'@'localhost';
GRANT UPDATE ON ads_l.monthly_visibility_detail TO 'ads_user'@'localhost';
GRANT UPDATE ON ads_l.monthly_visibility_grid TO 'ads_user'@'localhost';
GRANT UPDATE ON ads_l.monthly_reception_pattern TO 'ads_user'@'localhost';
GRANT UPDATE ON ads_l.monthly_prediction TO 'ads_user'@'localhost';
GRANT UPDATE ON ads_l.monthly_hours TO 'ads_user'@'localhost';
```

The service connects to `localhost` over TCP, port 3306, and reads the user and password from a `.env`
file beside `app.py`, which should be readable only by the user the service runs as:

```
DB_USER=ads_user
DB_PASSWORD=choose-a-password
```

Without a database, set `SKIP_STATS_DATABASE = True` at the top of `app.py`. The live map still works,
and every statistics endpoint answers empty.

To start it:

```bash
gunicorn -w 1 --threads 2 -k gthread --timeout 0 -b 127.0.0.1:5000 app:app
```

(`venv/bin/gunicorn` with a virtual environment). It must stay a single worker, because the listener and
the totals live in that process. Within a few seconds the log shows the connection to the OGN feed, and
`http://127.0.0.1:5000/conspicuity-monitor/api/live?layers=flarm,fanet` starts listing aircraft; the first totals reach the
database 15 minutes after the start. In production the same command runs as a systemd service
(`Restart=always`), behind nginx, which proxies `/conspicuity-monitor/api/` to it.

## Deploying updates

On the production host the service directory is a git checkout of this repository, and `deploy.sh` moves
it to `origin/main`. It needs no root: it sends SIGHUP to the gunicorn master, which starts a new worker
that re-imports the code. If the code does not compile, if the new worker does not answer within a
minute, or if it logs a database write error in its first 30 seconds, the script checks the previous
commit out again and reloads that. Deploy right after a 15-minute write, since a reload loses what
accumulated since the last one. Changes to `requirements.txt`, to the systemd unit or to the schema are
not covered and are applied by hand, a schema change before the code that needs it.

## License

The code is MIT, see [LICENSE](LICENSE). The data come from the
[Open Glider Network](https://www.glidernet.org/) under the ODbL; see Data and privacy.

## Acknowledgements

The Open Glider Network, for the feed, the receivers and the device database, and everyone who keeps
them running; EASA and the people who wrote the ADS-L specification; Europe Air Sports and the European
Hang Gliding and Paragliding Union.
