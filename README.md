# Electronic Conspicuity Monitor

The backend of the [Electronic Conspicuity Monitor](https://conspicuity.eu/), a public
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
[rendered version](https://conspicuity.eu/method/) shows them together with every
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

From 7 October 2026 the same feed and the same nightly code also measure how light aviation flies
(hours of the day, circling, thermals, height through the day, flights, launches, wave). Those are
other questions, and their rules are in [PATTERNS.md](PATTERNS.md), with a log of their own.

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
last ten positions of each ADS-L device. The live count (`/heard`) is held in memory as the last time
each address was heard on each system, for one hour, and leaves the service only as counts.

**What is stored, and for how long.**

| What | Kept |
|---|---|
| Positions and tracks | never written to the database; held in memory only while the device is on the live map or being measured |
| Device addresses (`monthly_devices`, `monthly_sources`), with the first and last time each was heard in the month | the current and the previous month; then reduced to counts and deleted |
| Counts of devices per month, address type, category, source and channel (`*_summary`) | indefinitely |
| Totals of the measures per day, month, source, category, height band and 0.25-degree cell | indefinitely |
| The raw feed (`recorder.py`, METHOD.md section 9), read each night by `nightly.py` | 30 days (`EC_RAW_DAYS`; seven until 24 October 2026) |
| Thermals to each side per device address and day (`daily_circling_pilot`, `nightly.py`) | the current and the previous month; then reduced to counts and deleted |
| Routes between airfields per device address and day (`daily_routes`, `nightly.py`, PATTERNS.md section 6) | the current and the previous month; then reduced to flights and distinct aircraft per route and month, and deleted |
| Far airborne receptions per device address, source, day and side of the aircraft, with their summed signal (`daily_device_pattern`, `nightly.py`, METHOD.md section 9): no position, time or station | 30 days, then deleted by the nightly |
| Countries and regions where each device address flew, per day and source (`daily_device_regions`, `nightly.py`, METHOD.md section 10.6) | the current and the previous month; then reduced to distinct devices per month, country, region, source and category (`monthly_region_summary`), and deleted |
| The nightly totals (`daily_*`: hours, circling, gaggles, parked aircraft, drones, data quality, ground stations by name) | indefinitely |

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

What each part costs in memory, CPU, database and disk is in [RESOURCES.md](RESOURCES.md); `/internal/resources` on the host returns the current figures.

One gunicorn worker runs a listener thread on the OGN feed (the whole feed, about 1,200 lines a second at a busy hour)
and the Flask endpoints that the page reads. `sources.py` keeps the totals in memory and writes them to
MariaDB every 15 minutes, counted from the worker's start, and once more when the worker stops, so a
deploy, a restart or a reboot keeps what accumulated since the last write. The exit write fits in
gunicorn's 30-second graceful timeout together with the requests still in flight; a worker killed
after that, or one that crashes, still loses up to 15 minutes. Height above ground comes from a terrain model, a raw int16 grid cut from ETOPO 2022 at
15 arc-seconds, with its bounds and step in the `.json` beside it: `data/europe_15s.i16` by default (35–72° N,
25° W–45° E, about 300 MB), or the file named by `ADSL_DEM` in `.env`, such as `data/world_15s.i16`
(60° S–90° N, all longitudes, 6.2 GB, cut the same way, so that its European part is the same grid cell for
cell). Since 25 October 2026 the world model is used tiled, `data/world_15s_tiles.i16` (2.3 GB, with
`world_15s_tiles.idx`): squares of one degree with no cell above sea level are left out, since the reader
returns 0 m below sea level anyway, and `tools/terrain/build_tiles.py` makes it from the flat file, which
`sources.Terrain` still reads; `tools/terrain/verify_tiles.py` checks the two give the same elevations.
None of these files is in the repository. Outside the grid, or without one, height above ground is recorded as
unknown.

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
**Description:** The ADS-L devices heard in the last 60 minutes, with their last ten positions, as JSON. Each entry has the same added fields as `/live` (`agl`, `fl`, `khz`, `snr_scale`, `km`, `snr10`, `cn`, `rx`); ADS-L sends no turn rate, so `rot` is null and `rot_sent` false.

### `/conspicuity-monitor/api/heard`
**Method:** GET
**Description:** For the main page's live box (from 7 October 2026): how many distinct aircraft were heard in the last 60 minutes on any system except ADS-B, `{"window_minutes": 60, "aircraft": N, "by_system": {label: n}, "by_kind": {"powered", "glider", "free_flight", "helicopter", "drone", "other"}, "adsl": n, "as_of": ISO time}`. One aircraft is one 24-bit address, systems merged as everywhere (`same_system`): it counts once in `aircraft`, once per system in `by_system`, and in `by_kind` by the last category it declared. The exclusions are those of the measures: receivers and synthetic packets, weather and FANET ground stations, positions near 0,0, no-track devices and those hidden in the OGN device database, Meshtastic nodes without an aircraft category, PilotAware rebroadcasts. Counts only, never an address. Cached for 5 seconds in the service; in nginx it needs a location like `/adsl`'s (a few seconds of `proxy_cache`, the live rate limit), which is outside this repository.

### `/conspicuity-monitor/api/check`
**Method:** GET
**Description:** The device check behind conspicuity.eu/check/ (from 10 October 2026, `check.py`). `?id=` takes a 24-bit address (`DD1234`, also with a prefix such as `FLR` or `0x`) or a registration or competition number the OGN device database shows as identified. Answers `found: false` for anything else, `not_tracked: true` for a device its owner marked so in the database, and otherwise what was heard in the last 24 hours: per system the last fix (with the receiver and its distance for radio, the platform for apps, height above the terrain model), packets, declared category, address type, stealth flag, median interval between positions against the system's nominal one, median arrival delay, frequency offset against the receiver's own median, signal per receiver and distance band next to the median of other devices of the same system there, silences over a minute, impossible jumps, and from 25 October 2026 `pattern` (per sector of 30 degrees from the nose, the median signal of receptions from stations beyond 20 km against the system's own median, `sectors` null until 8 sectors hold 10 receptions) with `pattern_typical`, the usual pattern of the same source and category while circling; plus the nearest receiver active in the last hour. No series of positions. Random addresses (RND) are not kept. The state is in memory, saved every 5 minutes to `data/check-state.pickle`, and each answer is cached 60 s per address here and in nginx, where the location has its own rate limit (`ec_api_check`, 30 a minute per IP with a burst of 30), outside this repository.

### `/conspicuity-monitor/api/adsl/monthly`
**Method:** GET
**Description:** Returns one entry per month on record, newest first: `devices` (distinct addresses), `addresses` (the same split by address type: `icao`, `flarm`, `ogn`, `random`, `pilotaware`, `fanet`, `other`), `categories` and `categorised` (aircraft category as declared in the OGN id, recorded only since the `category` column exists), and `partial`, true for the current UTC month. Random addresses change at every power-up or more often, so they overcount devices.

### `/conspicuity-monitor/api/live`
**Method:** GET
**Description:** Live positions from the other OGN sources, for the map's layer selector. `?layers=` takes a comma-separated subset of `flarm`, `fanet`, `radio` (other radio systems), `apps` (phone apps), `trackers` (hardware sending over a cellular or satellite link) and `relays` (platforms forwarding other sources, plus unknown internet sources); `&bbox=west,south,east,north` limits the answer to the visible area. ADS-L stays on `/adsl`, and ADS-B is not mapped. A device leaves the list 15 minutes after it was last heard. FLARM's rotating random addresses (`RND`) are chained with the nightly's rule (`sources.epra_link`) and listed as one aircraft (from 26 October 2026): `id` is the chain's first address, `chain` the number of addresses heard in the last 15 minutes and `trail` their latest fixes, oldest first, ending where two fixes are more than 2 minutes apart; `rx` lists the receptions of its latest address. Each entry carries, besides the position (from 25 October 2026): `agl` (metres above the terrain model), `fl` (pressure altitude in feet, when sent), `rot` (turn rate in half-turns a minute, only from the systems that send one, `rot_sent` false otherwise), `khz` (frequency offset), `snr_scale` (`fsk` or `lora`), `km` and `snr10` for the last receiving station, `cn` (competition number of an identified device), and `rx`: every station that heard the device by radio in the last 60 seconds, strongest first, as `[station, snr, km, seconds ago, snr at 10 km or null]` (at most 20 stations a device).

### `/conspicuity-monitor/api/sources`
**Method:** GET
**Description:** Distinct devices per month for every OGN source (the APRS tocall, see `tocalls.txt` in glidernet/ogn-aprs-protocol), split by `via`: `radio` when a ground receiver heard the packet (it carries the receiver's dB and kHz figures, and ADS-B always counts as radio), `net` when an app or platform injected it over the internet. `multi_day` counts devices heard on two different days of the month, which shows whether a source keeps stable ids. For months already archived, `return` gives per source how many of the month's devices were heard again the month after (kept from 7 October 2026). Cached for 10 minutes.

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
**Description:** Monthly visibility totals per source, channel, aircraft category, height band above sea (`msl_band`, 0 = below 1,000 m … 4 = above 4,000 m, 255 when the packet carries no altitude) and above ground (`agl_band`, 0 = below 300 m, 1 = 300–600, 2 = 600–1,200, 3 = 1,200–2,000, 4 = above 2,000; 255 when outside the terrain model or with no altitude), with a third estimator that ignores the turn rate (`p2_*`) and the same figures restricted to segments that carried a turn rate (`rot_*`) or showed circling (`circ_*`), and the airborne seconds during which the last position was no older than 3, 6, 15 and 30 s (`age_le*`) with the segments no longer than 3 and 6 s (`seg_le*`), and `vanish_2`, `vanish_5`, `vanish_20`, devices silent for more than 2, 5 or 20 minutes after being last seen airborne in that band, and `int_le2` … `int_le64`, airborne segments no longer than 2 … 64 s (the real interval between received packets). For a source whose cadence is known (phone apps, and from 5 October 2026 FLARM, ADS-L, FANET, OGN trackers and PilotAware on radio), `cadence_m`, `cadence_floor` and `cadence_heartbeat` give the rule applied to it (it sends after that many metres, never sooner than the floor and never later than the heartbeat, in seconds; `cadence_m` is null for a fixed cadence); all three are null otherwise. `cad_air` and `cad_late` are the airborne seconds of such a source and the part of them beyond its expected interval plus 10 s, from 5 October 2026 under whichever rule applied at the time; `sig_air` and `sig_late` are the same under the rules in force since 6 October 2026, from 06:07:56 UTC that day, and are what questions 2, 3, 5 and 6 use. Rows with `source` = `AIRCRAFT` (kind `aircraft`) follow the radio channel per aircraft over all the radio systems of one address and carry `sig_*` only: ⚠️ do not add them to the per-system radio rows, which count the same time again. See METHOD.md.

### `/conspicuity-monitor/api/visibility/grid`
**Method:** GET
**Description:** Monthly visibility per 0.25-degree cell, group of sources and channel, attributed to where the aircraft was last seen; `?month=YYYY-MM`, default the current month. Groups are the map layers plus `adsl`, and three extra groups whose `via` column is always `radio` and means nothing: `pga`, paragliders and hang gliders counted once per aircraft whatever carries them, the denominator of question 5 from 6 October 2026; `aircraft`, the radio channel per aircraft (`sig_*` only); and `pg`, the same free flight summed over every source and channel until 11:00 UTC on 6 October 2026, kept but no longer written. `cad_*` and `sig_*` have the meaning given for the detail above; question 5 and the map of question 6 use `sig_*`. Until 2026-10-03 trackers and relays were a single `network` group.

### `/conspicuity-monitor/api/pattern`
**Method:** GET
**Description:** Radio packets received while the aircraft was circling, by source, aircraft category, distance to the receiving station (`dist_band` 0 = under 5 km, 1 = 5–10, 2 = 10–20, 3 = 20–40, 4 = over 40) and angle between the aircraft's course and the bearing to that station (`sector_deg`, 30-degree sectors). Rows with `centred` true have sectors centred on the heading, so `sector_deg` 0 covers −15 to +15 degrees; those with `centred` false were recorded until 6 October 2026 with sectors beginning at it, so `sector_deg` 0 covers 0 to 30 degrees. The two sets must not be added together. `snr_sum`/`snr_n` give the mean signal-to-noise ratio of those packets corrected for distance (free-space loss), so that the pattern can be read in decibels. Read as the radiation pattern of the installation in flight; see METHOD.md, "How the installation shields the signal".

### `/conspicuity-monitor/api/prediction`
**Method:** GET
**Description:** Errors of four predictions of where an aircraft will be 5, 10 and 20 s ahead (straight line, arc from a turn rate the receiver derives from two positions, arc from the transmitted turn rate, last point), by source, category, horizon and circling or straight flight, as counts in error bins (`b0`… with the edges in `bins_m`) plus `n` and `error_sum`. `derived_paired` and `transmitted_paired` hold the same predictions restricted to instants where both exist, which is what the turn-rate comparison reads. See METHOD.md, "Predicting a few seconds ahead".

### `/conspicuity-monitor/api/patterns`
**Method:** GET
**Description:** The nightly measures of how light aviation flies, described in [PATTERNS.md](PATTERNS.md), and the parked aircraft of METHOD.md section 10.3, computed by `nightly.py`, as several lists (`?format=csv` gives one CSV with a `part` column). `hours`: airborne seconds per month, weekday (0 = Monday) and local solar hour (UTC + longitude/15) of the local solar date, by category, with `aircraft_hours` (distinct aircraft per hour, summed); `dates` says how many local dates each weekday holds. `circling`: thermals, degrees and seconds to each side per month and category (1 glider, 6 hang glider, 7 paraglider). `preference`: the per-pilot test, over the current and the previous month and then for each archived month, for pilots with at least 5 and 10 thermals (observed against chance variance of the right-hand share, pilots at 80% or more on one side against the binomial expectation, a 10-bin histogram). `gaggles`: pairs of thermals flown together, same side or opposite, with the share expected if sides were chosen independently. `parked`: parked aircraft transmitting per month, system and category (`aircraft_days`, seconds, packets). `launches`, `launch_cells`, `tug_tows`: aerotows, winch launches and flights with no tow seen (PATTERNS.md section 7). `thermals`, `thermal_cells` (1°), `thermal_places` (the busiest and strongest places of the current and previous month, per kind: a 0.25° cell, or up to the four cells around the grid corner nearest a site that are named after it, merged, with bounds, the number of `cells` and the name of a place; a place outside its site's block also has `away_km` and `away_dir` from the site, and `label` is the name to show, with that distance and direction appended when present; `name` and `label` carry the site's `country` code in their last bracket), `thermal_hour_places` (per kind, terrain and solar hour over every stored day, `climbs` and `top_place_climbs`, those of the busiest 0.25° cell, which hides an hour of the climb chart above a third), `mixed_thermals`, `agl_hours`, `circling_time`, `flights`, `wave` (probable wave climbs per month and cell: `lat` and `lon` are always the 1° cell; rows counted per 0.25° cell, `cell_deg` 0.25, also carry `south`, `north`, `west`, `east` and a `name`, "near Altdorf (CH)" or "30 km S of Altdorf (CH)"): PATTERNS.md sections 3 to 6 and 8. `cruise`, `helicopters`, `helicopter_cells`, `tug_time`: PATTERNS.md sections 9 to 11. Band edges are in `pattern_bands`. `days`: the days computed and their missing hours.

### `/conspicuity-monitor/api/regions`
**Method:** GET
**Description:** Distinct devices per country, region and source for one month, METHOD.md section 10.6, from `nightly.py` (from 25 October 2026). `?month=YYYY-MM` (default the latest) picks the month; without `?country=` the rows are the countries (`region` ''), with `?country=IT` (ISO code) they are that country's total and its regions (`-` is a position in no region). `source` is the tocall of the system, '' for all sources together, and `sources` gives each tocall's `label` and `kind`. Every row is its own count of distinct devices: a country is never the sum of its regions, since a device flying across a border is in both, and the all-sources row counts a device heard on two systems once. `months` lists the months on record, with `partial` true for those still counted from the kept addresses (the current and the previous month) and, for those, `days`, the UTC days counted so far; archived months come from `monthly_region_summary`. `categories` splits a row's `devices` by aircraft category (names in `category_names`, 255 none declared): a device has one per month and source, the lowest it declared, so the split is exact. Only counts, never an address. `?format=csv` gives one row per category.

### `/conspicuity-monitor/api/drones`
**Method:** GET
**Description:** Drones (category 13 and Remote ID), from `nightly.py`: airborne seconds and drone-days per month and 1-degree cell (`cells`), seconds by height band above ground and speed band (`bands`, edges in `height_bands_m` and `speed_bands_kmh`), by set of systems heard (`systems`), drones by the largest distance from the start of a session (`extent`), and encounters with crewed aircraft in flight by drone systems, other category and systems, closest distance band and whether the two share a system (`encounters`). Every list carries `evidence`, `confirmed` or `uncertain`; devices declaring a drone with crewed evidence (ADS-B emitter category of a crewed aircraft, or a crewed type in the OGN device database and a thermal) and those whose category 13 was a stray packet among others are in none of them, and `classes` counts all the kinds with their airborne seconds. From 9 October 2026 `classes_by_flight`, `bands_by_flight`, `extent_by_flight` and `encounters_by_flight` repeat `classes`, `bands`, `extent` and `encounters` with `flight_class` as well, the class an uncertain drone flies like (`never_above_30m`, `probably_crewed`, `probably_multirotor`, `probably_fixed_wing`, `uncertain`; `not_judged` for the other classes and for days computed before), listed in `flight_classes`; the lists above keep their shape. From the same day `encounter_cells` gives the encounters per month, evidence, flight class, other category and 1-degree cell of the midpoint at the closest approach (`lat`, `lon` as in `cells`); days computed earlier have no cell and appear only in the other lists. `encounters_by_geometry` gives them by closing-speed band (`closing_bands_kmh`) and the angle between the two tracks, as for `/encounters`. METHOD.md section 10.1.

### `/conspicuity-monitor/api/encounters`
**Method:** GET
**Description:** Experimental. Encounters between crewed aircraft (free flight, gliders, powered aircraft, helicopters) from `nightly.py`, per month, kind pair, threshold (`wide` 1 km and 150 m, `close` 300 m and 100 m, both within 10 s), band of the closest distance and of the closing speed, and the systems each side was heard on, with whether they share a radio system or any system. Pairs flying together (tows, formations, one pilot with two devices) are left out. From 9 October 2026 pairs of the same kind count too, except two free-flight aircraft or two gliders (`same_kind_left_out`); they are in `same_kind_encounters`, so that `encounters` keeps different kinds only. From the same day `encounter_cells` gives the encounters per month, kind pair, threshold and 0.25-degree cell of the midpoint at the closest approach (`lat`, `lon` are `floor(degrees × 4)`, `cell_deg` 0.25), and `air_cells` the flying time of each kind per month and cell over the same fixes (`air_seconds`, and `aircraft_days`, the distinct aircraft of each day summed, for leaving out cells flown by too few), the denominator of encounters per hour flown. `by_geometry` counts every pair by closing-speed band and by the angle between the two ground tracks at the closest approach (`same_way` under 45 degrees, overtaking included, `crossing` 45 to 135, `head_on` over 135, null without a velocity for both). `same_aircraft` counts, per month, kind pair and threshold, the pairs (`pair_days`) and encounters left out because the closest approach was the same way, under 150 m and under 20 km/h relative (`same_aircraft_rule`), probably one aircraft under two addresses; drone encounters are counted there too, with `kind_a` `drone`. Days computed earlier have no cell and appear only in `encounters`. Monthly aggregates only, never events. What the measure cannot see is in METHOD.md section 10.2.

### `/conspicuity-monitor/api/quality`
**Method:** GET
**Description:** Data-quality findings of METHOD.md section 10.4 for the last 7 days and the current month: per system, each check's count over its total; per OGN receiver, the receivers flagged, on how many days, with the measured value averaged over those days; and per check the receiver-days flagged among those judged (`receivers_summary`). Individual devices never appear.

### `/conspicuity-monitor/api/stations`
**Method:** GET
**Description:** Ground stations (OGN receivers and FANET ground stations such as GXAirCom), METHOD.md section 10.5, from `nightly.py`. A station has an open maintenance note when one of the checks of 10.5 applies to it that day. `days` has one entry per UTC day computed: the stations judged (`judged`), those with no open note (`without_note`, `share_without_note`), the radio packets they forwarded (`packets`) and those that went through a station with an open note (`packets_through_open_note`, `share_packets_through_open_note`). Each day also has the sensitivity figures of the network: `sensitivity_k`, the slope of the reported signal per tenfold distance fitted that night (20 is free space), and `sensitivity_judged`, the stations compared with their neighbours; the response gives `free_space_k` (20) and `sensitivity_low_share` (the partner share from which a card says a station is low against nearly all its neighbours). `stations_by_note` counts the stations of `latest_day` per note code; `notes` gives each code's title, rule, threshold, unit, meaning, remedy and links to the OGN documentation; `stations_with_note` lists, per note code, the stations that carry it on `latest_day` with the figure that opened it, the packets they forwarded and the days of the window it was open, most packets first; `stations` lists the names seen in the last `window_days` (30) for a search box. `census` counts the stations heard over the last `window_days` (from 25 October 2026): `stations`, `with_traffic` (forwarded at least one radio packet of an aircraft on any day of the window) and `without_traffic`, with `first_day` and `last_day`, the same per kind in `by_kind`, and per country in `by_country` (ISO code, null at sea), each with its `regions` (METHOD.md 10.5). `?format=csv` gives the rows of `days`.

### `/conspicuity-monitor/api/station-map`
**Method:** GET
**Description:** The stations heard in the last 30 days that reported a position, for the map of the stations page (from 25 October 2026): `stations` holds one array per station in the order of `fields`: name, latitude and longitude rounded to 3 decimals (about 100 m), kind, country (ISO code), region, the last day heard and the last day it forwarded an aircraft (null if none in the window). `first_day` and `last_day` bound the days present. `?format=csv` gives one row per station.

### `/conspicuity-monitor/api/stations/<name>`
**Method:** GET
**Description:** One station by its exact name, case-sensitive (`StChamas` and `STCHAMAS` are two stations). Found: `{"found": true, "name", "latest_day", "last_seen", "window_days", "days", "notes"}`, where `days` has one entry per day of the last 30 (availability as a share, the last and median status figures, software version, aircraft heard, packets forwarded, the five receiver checks of 10.4 with their values, `geoid_declared`, the sensitivity against the neighbouring stations (`sensitivity_db` against the network's median station, `sensitivity_partners`, `sensitivity_fixes`, `sensitivity_low_share` for the share of partners it is more than 3 dB below, all null on a day the station was not compared), the codes of the day's open `notes`) and `notes` the open notes of the station's last day with the figure measured, the threshold, (for the sensitivity note the figure is positive, in dB below the network), what it means for the data, the remedy, links to the OGN documentation and on how many of the days counted it was open. Not found: `{"found": false, "similar": [...]}` with the names that differ only in case or contain the name given, status 200.

### `/conspicuity-monitor/api/coverage-gaps`
**Method:** GET
**Description:** Where a new ground station would help most, METHOD.md section 10.5 ("Where a new station would help most"), from `nightly.py`. The last `window_days` (28) days added up per 0.25-degree cell (`lat_idx`, `lon_idx`, centre `lat`, `lon`). `lost_hours` is the flying time of free flight and gliders that a network hearing the cell as well as it hears the probe elsewhere would have made visible (`lost_hours_free_flight`, `lost_hours_glider` split it); `air_hours` is the airborne time of both families in the cell, `probe_hours` and `probe_late_share` the probe's radio time and the share of it without signal. `bands` has four entries (`band_labels`, height above the ground) with the probe's hours, aircraft-days, share without signal, the family airborne hours and `counts`, true where the band has at least `min_probe_hours` (2) of probe time from `min_aircraft_days` (5) probe aircraft-days; only those bands add to `lost_hours`, and cells without one are left out. `lowest_band_heard` is the index of the lowest counting band where the probe is without signal for at most 20% of its time (`heard_share` 0.8), null when there is none. `stations` lists up to six ground stations within `station_km` (30) of the cell's centre, from the positions of `stations_day`, with `stations_within_km` counting all of them. Cells are sorted by `lost_hours`. No address is stored or returned. In nginx it needs a location like `/stations`'s.

### `/conspicuity-monitor/api/snapshots`
**Method:** GET
**Description:** The monthly snapshots on record (METHOD.md section 9): for each month and endpoint, the commit of METHOD.md and of the code in force when it was taken, the date and the size of the JSON. Taken by `nightly.py` the night the last day of a month is computed, from the same functions the endpoints call, so each is the data the page showed for that month under the rules of the time.

### `/conspicuity-monitor/api/snapshot?month=YYYY-MM&endpoint=name`
**Method:** GET
**Description:** One snapshot, as the JSON that endpoint published (`endpoint` is a name from `/snapshots`, e.g. `visibility/grid`). Same licence as the other data. 404 when there is none.

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

Create the database and its tables from [schema.sql](schema.sql):

```bash
mysql < schema.sql
```

`schema.sql` is generated, and complete: `tools/schema_doc.py` writes it from `SHOW CREATE TABLE` on
every table of the production database, together with [SCHEMA.md](SCHEMA.md), which says for each
table what a row is, which section of METHOD.md or PATTERNS.md defines it, which code writes and reads
it, which endpoints publish it, which files of `schema/` created or changed it and what the service user
may do with it. Until 9 October 2026 `schema.sql` was kept by hand and still described the ten tables
of 3 October, while every later table and column was only in `schema/`.

The files in [schema/](schema/) stay the dated record of each change, with the reason for it, and they
are what is applied to a running database: by the owner, as root, before deploying the code that needs
them (`mysql < schema/<file>.sql`). After each one, run on the host

```bash
cd <service checkout> && python3 tools/schema_doc.py --out ~/schemadoc
```

and commit the `schema.sql` and `SCHEMA.md` it writes, in the same commit as the migration where
possible. The script reads only `SHOW` output and `information_schema`, never a row, and refuses to
write into the checkout. It reports on stderr a table with no description in it, a description of a
table that is gone, a table no code names, a table the code names that the database lacks, and any
column whose name or type the migrations (replayed in commit order over the last hand-written
`schema.sql`) do not account for.

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

The tables of the nightly measures, the `DELETE` that lets `nightly.py` replace a day, and
`UPDATE (category)` on `monthly_sources` (a later aircraft category replacing a ground one) are in
[schema/2026-10-07-nightly.sql](schema/2026-10-07-nightly.sql), and the grants on each table added
after it are in the file that creates the table; SCHEMA.md lists the whole set as production has it.
How the nightly runs is in [deploy/README.md](deploy/README.md).

The service connects to `localhost` over TCP, port 3306, and reads the user and password from a `.env`
file beside `app.py`, which should be readable only by the user the service runs as:

```
DB_USER=ads_user
DB_PASSWORD=choose-a-password
```

The same file can switch on the short-lived recording of the raw feed described in METHOD.md,
section 9, which is off unless `EC_RAW_DIR` is set (`recorder.py` documents every option):

```
EC_RAW_DIR=/home/ecmon/ec-raw   # hourly files, compressed with zstd once the hour is over
EC_RAW_DAYS=7                   # files older than this are deleted
EC_RAW_MIN_FREE_GB=10           # below this much free disk the oldest hours go too, down to two days
EC_RAW_KEEP=europe,no-airliners # or world; no-adsb drops every ADS-B packet
EC_SCOPE=europe                 # what nightly.py measures: europe (the default) or world
```

With `EC_SCOPE=europe` the nightly leaves out every position outside the box the recorder's `europe`
keeps, so recording the world changes none of its rows.

It needs `zstd` on the path. Hours are compressed at level 9 (`EC_RAW_ZSTD_LEVEL`; 19 until 7 October 2026, which cost about 9 minutes of CPU per busy hour). A day of Europe without airliners is roughly 0.3 to 0.9 GB once compressed.

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

In production the service directory is a git checkout of this repository, and `deploy.sh` moves
it to `origin/main`. It needs no root: it sends SIGHUP to the gunicorn master, which starts a new worker
that re-imports the code. If the code does not compile, if the new worker does not answer within a
minute, or if it logs a database write error in its first 30 seconds, the script checks the previous
commit out again and reloads that. The old worker writes its totals as it exits, so a deploy needs no
timing; on a rollback the rejected worker writes its own, up to a minute of them. Changes to `requirements.txt`, to the systemd unit or to the schema are
not covered and are applied by hand, a schema change before the code that needs it. After a schema
change, regenerate `schema.sql` and `SCHEMA.md` with `tools/schema_doc.py` (see Running it) and commit
them.

## Data from others

The four files below were first cut to Europe, 34–72° N and 25° W–45° E, and were extended to the world
on 25 October 2026. In each, the rows inside that box are exactly those of the European file, so that no
name in Europe changed; the rows outside it come from the world-wide extraction of that night, built with
the same rules as far as they could be reconstructed.

`data/places-world.tsv` names the thermal places of PATTERNS.md section 3. It is the GeoNames list of
places with 5,000 or more inhabitants (`cities5000`, from https://download.geonames.org/export/dump/),
reduced to name, country, latitude, longitude and population, with districts of cities and historical,
abandoned or destroyed places left out (feature codes PPLX, PPLH, PPLQ, PPLW): 20,263 places in Europe,
downloaded on 7 October 2026, and 44,299 elsewhere, downloaded on 24 October. GeoNames data are licensed
under the [Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/),
© GeoNames (https://www.geonames.org/).

`data/fivl-sites.tsv`: flying sites of the FIVL windgram service, courtesy of FIVL (Federazione Italiana
Volo Libero, https://www.fivl.it/): the 416 sites FIVL gave cumulus.it for its windgram forecasts, with
id, name, latitude and longitude only.

`data/osm-takeoffs.tsv` and `data/osm-airfields.tsv`: free-flight take-offs (`free_flying:site` naming a
take-off or launch, or `sport=free_flying` with a name that is not a landing field's) and aerodromes
(`aeroway=aerodrome`, named, military ones left out, with `icao` where present and `gliding` = 1 for those
tagged for gliding), extracted from OpenStreetMap with Overpass: in Europe 4,602 take-offs and 6,634
aerodromes (228 for gliding) on 7 October 2026, elsewhere 556 take-offs and 35,469 aerodromes on
24 October. © OpenStreetMap contributors, available under the
[Open Database License](https://opendatacommons.org/licenses/odbl/1-0/) (https://www.openstreetmap.org/copyright).

`data/landmarks-world.tsv`: named peaks, volcanoes, saddles and mountain passes with at least 3 Wikidata
sitelinks, which name take-offs and probable wave where no town is close (PATTERNS.md sections 3 and 8).
In Europe they are the 17,901 OpenStreetMap features of 8 October 2026, built with the scripts in
`tools/landmarks/`, whose README gives the yearly refresh and the areas still missing; names and positions
© OpenStreetMap contributors, under the [Open Database License](https://opendatacommons.org/licenses/odbl/1-0/).
Elsewhere they are 24,695 Wikidata items (instances of mountain Q8502, volcano Q8072 or mountain pass
Q133056, or of their subclasses) queried on 24 October 2026, with Wikidata's coordinates and label and no
elevation, since Wikidata stores some in feet. Sitelink counts and these items from
[Wikidata](https://www.wikidata.org/), [CC0](https://creativecommons.org/publicdomain/zero/1.0/).

`data/admin1-world.tsv.gz`: the country and region of a ground station (METHOD.md 10.5), from the Natural
Earth admin-1 states and provinces at 1:10 million (https://www.naturalearthdata.com/, public domain),
merged into one shape per country and region and simplified to 0.002 degrees by
`tools/admin1/build_admin1.py`, which says how regions are grouped; 7,894 rings, 4.8 MB compressed.

Wind for the probable wave of PATTERNS.md section 8: `nightly.py` asks the Open-Meteo historical forecast
API (https://historical-forecast-api.open-meteo.com/v1/forecast, no key) once a night for the wind and
geopotential height at pressure levels from 900 to 400 hPa, at the day's candidate wave climbs, rounded to
0.1 degree. Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/). The wave rule built on it
is unverified against reports of real wave.

These lists name the thermal places of PATTERNS.md section 3. When they are loaded, a name that is generic
or that two sites of one list share is qualified with the nearest GeoNames town (PATTERNS.md section 3), so
every site of a list has a name of its own. The terrain model in `data/` is not in the
repository (see Running it).

## Weather archive (optional tools)

Two stand-alone scripts in `tools/` keep the weather next to the feed, for comparing what aircraft
measure with what the models said and the sondes saw. They share one directory, set by
`EC_WEATHER_DIR` (default `~/ec-weather`), and need only the Python standard library.

`tools/forecast_archive.py` runs once each morning (04:30 UTC suits the 00 UTC model runs). For
ICON-EU, ECMWF IFS 0.25, ICON-D2, ICON-2I and Meteo-France AROME it saves the forecast of the current
UTC day, hourly 06-20 UTC, as issued that morning, from Open-Meteo's forecast API (the
historical-forecast API stitches the freshest runs and answers a different question). The points are
a 0.75 degree grid over 41-49.25 N, 4-16.75 E plus the radiosonde stations; the variables are
temperature, wind and geopotential height at 1000-500 hPa, wind at 10/80/120/180 m, 2 m temperature,
boundary-layer height, surface and sea-level pressure, wherever a model has them. Output is
`$EC_WEATHER_DIR/forecasts/YYYY-MM-DD/<model>.json.gz`, written atomically, with the request and the
model run time; a model already saved for the day is skipped. A point a model refuses (AROME's
real eastern edge slants well inside its published box) is remembered in
`$EC_WEATHER_DIR/outside.json` and left out for 30 days. Open-Meteo's free tier is for
non-commercial use under CC BY 4.0 and limits calls per minute, hour and day, counting a request as
locations x max(1, variables / 10) calls; the script paces itself under those limits, so a full run
takes a little over an hour and about 4,600 of the 10,000 daily calls. Cron line:
`30 4 * * * python3 /path/to/tools/forecast_archive.py`.

`tools/sondes.py` fetches radiosonde profiles. University of Wyoming (BUFR-based, full resolution,
a few hours after launch) serves Payerne, Munich, Stuttgart, Vienna, Innsbruck, Udine/Rivolto, Graz,
Zagreb and Ajaccio; NOAA's IGRA v2 (reported levels only, 1-2 days late, public domain) serves
Novara/Cameri, Cuneo, San Pietro Capofiume, Pratica di Mare and Nimes, and the past of Milano Linate
(to 2021). `sondes.py day 2026-10-24 12` saves every station that launched at that hour under
`$EC_WEATHER_DIR/sondes/`; the file header lists the sources and formats.

## License

The code is MIT, see [LICENSE](LICENSE). The data come from the
[Open Glider Network](https://www.glidernet.org/) under the ODbL; see Data and privacy.

## Acknowledgements

The Open Glider Network, for the feed, the receivers and the device database, and everyone who keeps
them running; EASA and the people who wrote the ADS-L specification; Europe Air Sports and the European
Hang Gliding and Paragliding Union.
