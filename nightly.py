#!/usr/bin/env python3
"""Measures computed each night from the raw recording of one UTC day (METHOD.md section 10, PATTERNS.md).

The live service measures what it can afford packet by packet. Some questions
need a whole day of a device's fixes at once: where a thermal begins and ends,
whether an aircraft sat still for half an hour, how far a drone went from where
it took off, which receiver reports altitudes 40 m off its neighbours. Decided
on 7 October 2026: a batch, run once a night over the previous UTC day of the
raw recording (recorder.py), writing aggregate tables only. The live service is
not involved and loses nothing if the batch fails.

One pass over the 24 hourly files of the day, plus the first minutes of the
next day's first file, where the last fixes of the day arrive late. Fixes are
filed by the time written in them, as the service does; the data-quality
counts are filed by the time of reception, since a clock that is wrong is the
very thing they count. The rules of the service apply: the source table, the
exclusions, the stale and future rules, the airborne rule and the implausible
segments all come from sources.py. Devices whose owners asked not to be
tracked never reach the recording (recorder.py drops the no-track flag and the
OGN device database's TRACKED=N on arrival), and the no-track flag is checked
again here.

Rerunning a day replaces its rows: every table is keyed by the day it was
computed from, and the rows of that day are deleted before the new ones are
inserted, in one transaction. --dry-run prints the aggregates instead of
writing them.

Usage, from the service checkout (~/ads-l-map), as the service user:
    nice -n 19 python3 nightly.py [--day YYYY-MM-DD] [--dry-run]
The day defaults to yesterday (UTC). The database user and password come from
.env beside this file, as for the service; app.py is not imported, since
importing it starts the listener.
"""
import argparse
import array
import calendar
import collections
import csv
import io
import datetime
import json
import logging
import math
import os
import resource
import re
import statistics
import subprocess
import sys
import time
import urllib.request
import zlib

import pymysql
from dotenv import load_dotenv

import sources

logger = logging.getLogger("nightly")

HERE = os.path.dirname(os.path.abspath(__file__))
STALE = sources.STALE_SECONDS
FREE_FLIGHT = (6, 7)
CREWED = set(range(1, 10))              # categories 1-9: gliders to jets, crewed by definition
DRONE = sources.DRONE_CATEGORY
GROUND_CATEGORIES = sources.GROUND_CATEGORIES   # ground support and static object: not flight
REMOTE_ID = sources.REMOTE_ID                   # a drone whatever its id says

# --- 1. Flying time by local solar hour -------------------------------------
# Hours of the day in local solar time (UTC + longitude/15), so that 14:00 is
# mid-afternoon in Lisbon and in Bucharest alike, which UTC and time zones are
# not; the weekday is that of the local solar date (decided 7 October 2026).
SOLAR_SECONDS_PER_DEGREE = 240

# --- 2. Circling direction --------------------------------------------------
# The direction comes from the change of course between successive fixes, so
# it works for every source that sends a course, with or without a turn rate.
# Only gaps up to 5 s are read: at 15 s a paraglider circling in 20 s turns
# 270 degrees, which reads as 90 the other way.
CIRCLE_CATEGORIES = (1, 6, 7)
CIRCLE_MAX_GAP = 5
CIRCLE_MIN_RATE, CIRCLE_MAX_RATE = 5.0, 45.0   # deg/s: from 72 s to 8 s a turn
CIRCLE_MIN_TURN = 720                          # two full turns to one side make a circling episode
CIRCLE_MAX_KT = 200 / 1.852                    # faster than any of the three circles
# A pilot who leaves the core and comes back, or a dropout, splits one climb
# into runs; runs on the same side within 10 minutes and 3 km are one thermal.
THERMAL_MERGE_S, THERMAL_MERGE_M = 600, 3000
# A circling episode is a thermal only if its largest climb, the largest rise
# from a low point to the highest point after it, gains this much height
# (8 October 2026). Two
# turns alone also catch a spiral descent to land and the search turns
# before a core is found, which drew the mean climb of the cells of Schänis
# and Unterwössen to -1.0 m/s on 6 and 7 October 2026. The same span gives
# the thermal its duration and its climb rate (PATTERNS.md, section 2).
THERMAL_MIN_GAIN = 50
# Two aircraft share a thermal (a gaggle) when, for at least 60 s of the time
# both are circling, they are within 500 m horizontally and 300 m vertically.
# Positions are compared on a 5-second grid.
GAGGLE_BUCKET = 5
GAGGLE_MIN_S, GAGGLE_M, GAGGLE_VERT_M = 60, 500, 300

# --- 3. Parked aircraft transmitting ----------------------------------------
PARKED_CATEGORIES = CREWED
PARKED_SPREAD_M = 100        # the diagonal of the box holding every fix of the run
PARKED_MIN_S = 30 * 60
PARKED_AGL = (-60, 60)       # median height above the terrain model: on the ground, within its error
PARKED_AGL_EVERY = 60        # seconds between terrain lookups in a run

# --- 4. Drones ----------------------------------------------------------------
DRONE_HEIGHT_EDGES = (50, 120, 300)        # m above ground; 120 m is the open-category ceiling
DRONE_SPEED_EDGES = (20, 50, 100)          # km/h
DRONE_EXTENT_EDGES = (1000, 3000, 10000)   # m from the first fix of a session
ENCOUNTER_S, ENCOUNTER_M, ENCOUNTER_VERT_M = 10, 1000, 150
ENCOUNTER_EVERY = 600                      # one encounter per pair of aircraft per 10 minutes
ENCOUNTER_BANDS = (300, 600, 1000)
ENCOUNTER_WINDOW = STALE + 2 * ENCOUNTER_S  # fixes kept for matching, by packet time
ENCOUNTER_ACTIVE = 900                      # crewed fixes are indexed near drones heard this recently
CELL_LAT, CELL_LON = 0.02, 0.04             # index cells, at least 1.2 km wide up to 72 N
ACTIVE_DEG = 0.1                            # "near a drone": its 0.1-degree square or one of the 8 around
SWEEP_EVERY = 60                            # seconds of packet time between sweeps of the two indexes
UNKNOWN_BAND = sources.UNKNOWN_BAND

# Which devices declaring a drone are drones (METHOD.md 10.1, 7 October 2026).
# A crewed aircraft set up by mistake as category 13 flies like a fixed-wing
# drone, so the flight cannot decide; two declarations made apart from the
# device's own setting can: the emitter category of an ADS-B transponder,
# set by whoever installed it, and the aircraft type in the OGN device
# database, which can be stale and so counts only together with a thermal.
CONFIRMED, UNCERTAIN, CREWED_ADSB, CREWED_DDB, STRAY, CREWED_CLIMB = 1, 2, 3, 4, 5, 6
EVIDENCE_NAMES = {CONFIRMED: "confirmed", UNCERTAIN: "uncertain", CREWED_ADSB: "crewed_adsb_emitter",
                  CREWED_DDB: "crewed_ddb_thermal", STRAY: "stray", CREWED_CLIMB: "crewed_climb_extent"}
CREWED_CLASSES = (CREWED_ADSB, CREWED_DDB, CREWED_CLIMB)
SET_ASIDE = CREWED_CLASSES + (STRAY,)
# A thermal (PATTERNS.md, section 2) that gains this much height, by a device whose day covers
# more than this distance, is a crewed glider or paraglider on a
# cross-country even without a database entry: a drone loitering over a
# point turns, but it does not climb in circles across a hundred kilometres
# (7 October 2026).
CLIMB_M, CLIMB_EXTENT_M = 100, 100000
# Every measure takes, from 7 October 2026, the category an address declares
# most often on that system that day (METHOD.md, section 10): a stray packet
# no longer moves a segment, a thermal or a drone into another kind. On
# 6 October 2026, 55 of the 145 addresses with a category 13 sent it in one
# packet only, all their others declaring another kind, and the stray
# categories of all systems came with the 65,536 m altitude and the 500 km/h
# jumps 20 to 250 times as often as normal packets. FANET's switch between
# paraglider or hang glider and static object is its ground mode and is kept
# packet by packet. ADS-B's 0 ("not yet known") takes no part in the vote.
FANET_GROUND = {6, 7, 15}
# A receiver whose radio packets disagree with their address's majority this
# often is flagged on the data-quality page; the median receiver was at
# 0.01% on 6 and 7 October 2026, the worst two near 45%.
RELAY_STRAY_SHARE, RELAY_STRAY_MIN = 0.02, 1000
# For comparing the two rules only: NIGHTLY_PACKET_CATEGORY=1 makes the
# measures take each packet's own category again, as before 7 October 2026.
PACKET_CATEGORY = bool(os.getenv("NIGHTLY_PACKET_CATEGORY"))
EMITTER = re.compile(r" ([A-D][0-7]):")
CREWED_EMITTERS = {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "B1", "B2", "B3", "B4"}
UAV_EMITTER = "B6"
# AIRCRAFT_TYPE of the DDB (download/?t=1): 1 glider or motor glider, 2 plane,
# 3 ultralight, 4 helicopter, 5 drone, 6 other (paragliders, "Unknown",
# ground stations). Checked against the models filed under each on
# 7 October 2026 (ASK-21 in 1, Cessna 172 in 2, EC 135 in 4, DJI in 5).
DDB_URL = "https://ddb.glidernet.org/download/?t=1"
DDB_CREWED = {1, 2, 3, 4}
DDB_DRONE = 5

# --- 5. Data quality ----------------------------------------------------------
LATE_S = 60
AHEAD_S = 5
ALTITUDE_SENTINEL_M = 60000     # 2^16 m = 65,536 m: an unsigned 16-bit field gone negative
JUMP_MIN_M = 2000               # a jump is a shared address or a corrupt fix, not GPS noise
JUMPS_PER_ADDRESS = 3
RECEIVER_MOVED_M = 2000
RECEIVER_ALTITUDE_M = 500
RELAY_OFFSET_M = 20
RELAY_MIN_SHARED = 50
RELAY_LATE_SHARE, RELAY_LATE_MIN = 0.05, 100
RELAY_GROUP_S = 20              # seconds after the first reception of a fix to wait for other receivers
RELAY_SOURCES = {"OGFLR"}       # FLARM: the receiver converts ellipsoid height to MSL itself

# The two families of tables (7 October 2026), written by the same run in two
# transactions, conspicuity (METHOD.md) first: an error in one is logged and
# recorded in nightly_runs.notes and leaves the other's day written. An error
# while the day is read stops both, since both are computed from it.
FAMILIES = {
    "conspicuity": ("daily_drone_classes", "daily_drones", "daily_drone_cells", "daily_drone_extent",
                    "daily_drone_encounters", "daily_crewed_encounters", "daily_parked", "daily_quality"),
    "patterns": ("daily_thermal_sites", "daily_flight_classes", "daily_routes", "daily_hours_solar", "daily_circling", "daily_circling_pilot", "daily_gaggles",
                 "daily_mixed_thermals", "daily_thermals", "daily_agl_hours", "daily_circling_time",
                 "daily_flights", "daily_wave", "daily_launches", "daily_tug_tows", "daily_tug_time",
                 "daily_cruise", "daily_helicopter_night"),
}
TABLES = FAMILIES["conspicuity"] + FAMILIES["patterns"]
# The statistics endpoints whose monthly snapshot belongs to the patterns family.
PATTERN_SNAPSHOTS = ("patterns",)


def band(value, edges):
    if value is None:
        return UNKNOWN_BAND
    return sum(1 for e in edges if value >= e)


def dist(lat1, lon1, lat2, lon2):
    return sources._distance(lat1, lon1, lat2, lon2)


def hour_path(raw_dir, day, hour):
    """The file of one hour: the uncompressed one while it exists (it is complete
    once a zstd is running on it, and the only one before), else the .zst."""
    base = os.path.join(raw_dir, day.strftime("%Y%m%d") + "-%02d.aprs" % hour)
    if os.path.exists(base):
        return base
    if os.path.exists(base + ".zst"):
        return base + ".zst"
    return None


def read_lines(path, stop=None):
    """(reception epoch, line) of one hourly file, until `stop` if given."""
    cmd = ["zstdcat", "-q", path] if path.endswith(".zst") else ["cat", path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace")
            sp = line.find(" ")
            try:
                epoch = float(line[:sp])
            except ValueError:
                continue
            if stop is not None and epoch >= stop:
                break
            yield epoch, line[sp + 1:].rstrip("\n")
    finally:
        proc.kill()
        proc.wait()


# Thermal positions are kept for the whole day, a few hundred thousand of
# them, so each is packed into one integer: latitude and longitude in units of
# 1e-5 degree (about a metre), altitude in metres plus 1,000.
def pack(lat, lon, alt):
    a = max(0, min(65535, round(alt) + 1000))
    return ((round(lat * 1e5) + 9000000) << 42) | ((round(lon * 1e5) + 18000000) << 16) | a


def unpack(x):
    return (((x >> 42) - 9000000) / 1e5, (((x >> 16) & ((1 << 26) - 1)) - 18000000) / 1e5,
            (x & 0xFFFF) - 1000)


def weighted_median(pairs):
    """Median of values given as (value, weight)."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return None


def fanet_ground(tocall, a, b):
    return tocall == "OGNFNT" and {a, b} <= FANET_GROUND and 15 in (a, b)


def load_ddb():
    """(address -> AIRCRAFT_TYPE, addresses not to be tracked, address -> AIRCRAFT_MODEL)
    from the OGN device database.

    The owners' choices are honoured as app.py does: TRACKED=N drops the
    device from every measure, IDENTIFIED=N keeps it but its type is not used.
    Without the database (a failed download) every declared drone without an
    ADS-B emitter category stays uncertain, and the log says so.
    """
    try:
        text = urllib.request.urlopen(DDB_URL, timeout=120).read().decode("utf-8", "replace")
    except OSError as e:
        logger.error(f"OGN device database not loaded ({e}): no aircraft types this run")
        return {}, set(), {}
    types, notrack, models = {}, set(), {}
    reader = csv.reader(io.StringIO(text))
    header = [h.strip("#' ") for h in next(reader)]
    col = {h: i for i, h in enumerate(header)}
    for row in reader:
        try:
            dev = row[col["DEVICE_ID"]].strip("'")
            if row[col["TRACKED"]].strip("'") == "N":
                notrack.add(dev)
                continue
            if row[col["IDENTIFIED"]].strip("'") == "N":
                continue
            types[dev] = int(row[col["AIRCRAFT_TYPE"]].strip("'") or 0)
            models[dev] = row[col["AIRCRAFT_MODEL"]].strip("'")
        except (IndexError, KeyError, ValueError):
            continue
    logger.info(f"OGN device database: {len(types):,} types, {len(notrack):,} not to be tracked")
    return types, notrack, models


def wind_at(levels, z):
    """Wind speed at height z from [(height, u, v)] sorted by height, interpolating
    the components linearly; the nearest level outside the range."""
    for a, b in zip(levels, levels[1:]):
        if a[0] <= z <= b[0]:
            f = (z - a[0]) / (b[0] - a[0])
            return math.hypot(a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]))
    a = levels[0] if z < levels[0][0] else levels[-1]
    return math.hypot(a[1], a[2])


def climb_span(samples):
    """(metres gained, seconds) of the largest climb within a circling episode:
    the largest rise from a sampled low point to the highest point after it,
    found with a running minimum; None without an altitude."""
    if not samples:
        return None
    best = (0, 0)
    low = low_b = None
    for b in sorted(samples):
        alt = unpack(samples[b])[2]
        if low is None or alt < low:
            low, low_b = alt, b
        elif alt - low > best[0]:
            best = (alt - low, (b - low_b) * GAGGLE_BUCKET)
    return best


class CircleTrack:
    """One address on one system, while it may be circling."""
    __slots__ = ("cat", "last", "run", "run_s", "run_m", "run_t0", "run_pos", "run_samples", "thermal", "thermals")
    # (category, outcome) -> circling episodes, for the log: "thermal", "no_gain", "no_altitude"
    outcomes = collections.Counter()

    def __init__(self, cat):
        self.cat = cat
        self.last = None          # (t, course, kt, lat, lon)
        self.run = 0.0            # degrees turned on one side, signed (+ = right, clockwise from above)
        self.run_s = 0.0
        self.run_m = 0.0          # metres flown along the circles (ground speed times time)
        self.run_t0 = None
        self.run_pos = None
        self.run_samples = {}     # 5-second bucket -> (lat, lon, alt)
        # open circling episode: [side, t0, t1, lat, lon, degrees, seconds, samples, metres along
        # the circles]; a thermal once closed adds [metres gained, seconds of the climb] (keep)
        self.thermal = None
        self.thermals = []

    def close_run(self, t, lat, lon):
        if abs(self.run) >= CIRCLE_MIN_TURN:
            side = 1 if self.run > 0 else -1
            th = self.thermal
            if (th is not None and th[0] == side and self.run_t0 - th[2] <= THERMAL_MERGE_S
                    and dist(th[3], th[4], self.run_pos[0], self.run_pos[1]) <= THERMAL_MERGE_M):
                th[2], th[3], th[4] = t, lat, lon
                th[5] += abs(self.run)
                th[6] += self.run_s
                th[7].update(self.run_samples)
                th[8] += self.run_m
            else:
                if th is not None:
                    self.keep(th)
                self.thermal = [side, self.run_t0, t, lat, lon, abs(self.run), self.run_s, self.run_samples,
                                self.run_m]
            self.run_samples = {}
        elif self.run_samples:
            self.run_samples = {}
        self.run = 0.0
        self.run_s = 0.0
        self.run_m = 0.0
        self.run_t0 = None

    def finish(self):
        if self.last is not None:
            self.close_run(self.last[0], self.last[3], self.last[4])
        if self.thermal is not None:
            self.keep(self.thermal)
            self.thermal = None

    def keep(self, th):
        """A closed circling episode is kept as a thermal only if it climbed
        THERMAL_MIN_GAIN (PATTERNS.md, section 2)."""
        span = climb_span(th[7])
        if span is not None and span[0] >= THERMAL_MIN_GAIN:
            th.extend(span)
            self.thermals.append(th)
            outcome = "thermal"
        else:
            outcome = "no_altitude" if span is None else "no_gain"
        CircleTrack.outcomes[(self.cat, outcome)] += 1


# --- 7. Launches (PATTERNS.md, section 7, 7 October 2026) ---------------------
TOWED = {1: "glider", 6: "hang_glider"}
TUGS = {2, 8}                     # tow plane and powered aircraft; a helicopter never tows here
TOW_START_AGL = 100               # a tow begins near the ground, not a formation met aloft
RELEASE_EDGES = (300, 450, 600, 900)          # m above ground at release
TOW_MINUTES_EDGES = (3, 5, 8, 12)
TUG_TOWS_EDGES = (2, 6, 11)                   # tows per tug per day: 1, 2-5, 6-10, more than 10
WINCH_LOW, WINCH_HIGH, WINCH_S = 50, 200, 60  # from under 50 m to 200 m above ground within 60 s
WINCH_CLIMB = 4.0                             # m/s on average: a cable launch climbs at 10 or more
WINCH_KMH = (70, 150)                         # the speed of a glider on the cable, once off the ground
WINCH_TOP_S = 150                             # the top of the launch is sought this long after it began
WINCH_TOP_EDGES = (300, 400, 500, 700)
LAUNCH_SESSION = sources.SESSION_BREAK        # an airborne fix after this long unheard starts a flight
LAUNCH_MATCH = (-300, 600)                    # a tow or winch counts for a flight starting this close
# A flight is a launch only when it is first heard within 150 m of the ground:
# above that it was joined in the air, after a gap in coverage or out of it
# (6 October 2026: 2,153 glider "launches" against 1,745 glider flights).
# 150 m is a winch launch's first few seconds, an aerotow's first minute or
# a paraglider just off a hill, and above the terrain model's error.
LAUNCH_MAX_AGL = 150
# A glider launch with no tow and no winch found is read by how it climbs over
# its first 150 m gained (PATTERNS.md section 7, 7 October 2026): 6 m/s or more
# is a cable launch the winch rule missed, mostly because coverage began
# mid-launch; 1 to 5 m/s, straight, at 90-150 km/h, is an aerotow whose tug is
# not heard or a self-launch, which climb alike; anything else is "other".
CLIMB_GAIN = 150
CLIMB_WINCH = 6.0
CLIMB_TOW = (1.0, 5.0)
CLIMB_TOW_KMH = (90, 150)
CLIMB_STRAIGHT = 3.0          # deg/s of turning on average, at most: no circling
CLIMB_WAIT = 600              # a launch that has not gained 150 m in 10 minutes shows no climb
# Self-launching motorgliders, by the model in the OGN device database: only
# this independent evidence turns "aerotow or self-launch" into "self-launch".
# Names as owners write them; a pattern too wide would take pure gliders in.
SELF_LAUNCH_MODELS = re.compile(
    r"stemme|arcus ?m\b|dg-?808|dg-?400|ash ?-?26 ?e|ash ?-?31 ?mi|asg ?-?32 ?mi|antares|taurus|"
    r"silent|dimona|falke|sf ?-?25|sinus", re.I)


# --- 8. Patterns of flight (PATTERNS.md, 7 October 2026) -------------------------
# Thermals are split further: paragliders and hang gliders circle at
# different radii and speeds (7 October 2026); so are flights, height by
# hour and circling time, the same evening.
THERMAL_KIND = {7: "paraglider", 6: "hang_glider", 1: "glider"}
FREE_FLIGHT_KIND = {7: "paraglider", 6: "hang_glider"}
# Terrain class of a point: the relief within 5 km (highest minus lowest
# ground, Nightly.relief) under 600 m is "plain" (plain and hills), 600 m or
# more "mountain"; "unknown" outside the terrain model. Chosen on 7 October
# 2026 after the relief distribution of 6 October (PATTERNS.md, section 12).
MOUNTAIN_RELIEF = 600
# daily_thermals keeps its cells at 0.25 degree: lat_idx = floor(lat * 4)
# (sources.THERMAL_CELLS_PER_DEG; 1 degree until 7 October 2026).
THERMAL_CELLS_PER_DEG = sources.THERMAL_CELLS_PER_DEG
# Flights shorter than this are not counted (PATTERNS.md, section 6): on
# 6 October 2026, 493 glider flights whose start was not seen lasted a median
# of 6 seconds, fragments at the edge of coverage.
FLIGHT_MIN_S = 120
# Flight classes (PATTERNS.md, section 6, 7 October 2026). Unpowered: the
# glide range from the take-off is (take-off altitude - landing altitude) x
# L/D x 1.25, with a typical glide ratio per kind and a quarter more for the
# lift found on the way, so that a flight within it could have been a glide
# home without climbing anywhere.
GLIDE_RATIO = {7: 8, 6: 12, 1: 35}
GLIDE_MARGIN = 1.25
# Powered fixed-wing: airfields from data/osm-airfields.tsv, within 3 km of
# the take-off and of the landing; local within 25 km of the departure.
POWERED_FIXED = {2, 8, 9}
AIRFIELD_MATCH_M = 3000
START_SITE_M = 2000           # a free-flight take-off within this of a FIVL site or OSM take-off
POWERED_LOCAL_M = 25000
# A route is published only with this many distinct aircraft in the month:
# one flown by one or two aircraft tells one person's movements.
ROUTE_MIN_AIRCRAFT = 5
# A landing inferred when a flight ends in silence (end_flight): the last fix
# below 100 m over the ground and lower than 30 to 60 s before.
INFER_AGL = 100
# A flight landing within this of its take-off is back home: never cross-country.
HOME_M = 1000
# A paraglider is called cross-country only beyond this from its take-off: a
# low first climb gives a glide range of 2-3 km, and drifting down the valley
# from a hill is not a cross-country flight (Rodolfo, 7 October 2026).
PARAGLIDER_CROSS_M = 5000
# The first climb of a flight that starts on the ground with no launch found
# ends when the aircraft has come down this far from its highest point.
FIRST_CLIMB_DROP = 50
INFER_BACK = (30, 60)
PATTERN_KIND = {6: "free_flight", 7: "free_flight", 1: "glider", 2: "powered", 8: "powered", 9: "powered",
                3: "helicopter"}
RADIUS_EDGES = (30, 50, 80, 120, 200)         # m
CLIMB_EDGES = (0.5, 1, 1.5, 2, 3, 4)          # m/s, a thermal's average
CLIMB_MIN_S = 20                              # a thermal sampled over less time gives no climb rate
VSEP_EDGES = (50, 100, 200)                   # m between two aircraft sharing a thermal
AGL_HOUR_EDGES = (50, 120, 300, 600, 1200, 2000)
FLIGHT_MIN_EDGES = (10, 30, 60, 120, 240, 480)
# A flight goes on across a silence of any length up to FLIGHT_MAX_GAP when it
# was airborne on both sides (the timeline has only airborne segments), neither
# side was within LAUNCH_MAX_AGL of the ground, and the distance between them
# could be flown in the time at FLIGHT_MAX_KMH of its kind: a glider out of
# coverage for half an hour is one flight; a landing, a ground stop or a drive
# to another site is not. Generous maxima, about the fastest each kind flies.
FLIGHT_MAX_GAP = 2 * 3600
# A fix standing still on the ground (at most 3 kt, within 30 m of the terrain
# model) after the last airborne one ends the flight: a stop at the field or a
# relaunch on the hill. The first rule of 7 October 2026, any 2-minute break
# with both sides within 150 m of the ground, split paragliders soaring a
# ridge low and slow into wind, and raised the flight counts instead.
FLIGHT_STOP_KT, FLIGHT_STOP_AGL = 3, 30
# Only where standing still means being on the ground: a helicopter or a drone
# hovering low, or a balloon, looks the same as one that has landed (the
# second rule of the same day cut helicopter flights to a median of 2.4 min).
FLIGHT_STOP_CATEGORIES = {1, 2, 6, 7, 8, 9}
FLIGHT_MAX_KMH = {7: 70, 6: 120, 1: 280, 2: 350, 8: 350, 9: 900, 3: 300, 13: 150}
FLIGHT_MAX_KMH_OTHER = 350
FLIGHT_EXTENT_KM = (1, 5, 20, 50, 100, 300)
FLIGHT_PATH_KM = (5, 20, 50, 100, 300, 500)
WAVE_MIN_M = 2500                             # the climb must start this high
WAVE_GAIN_M, WAVE_RATE, WAVE_S, WAVE_WINDOW = 300, 1.0, 180, 600
WAVE_TURN = 3.0                               # deg/s of turning on average, at most
WAVE_NET_TURN = 360                           # and less than one net turn in the window
WAVE_GAP = 30                                 # a silence longer than this restarts the window
WAVE_QUIET = 1800                             # one wave climb per glider per half hour
# A climb that passes the test above is a candidate; it counts as probable wave
# only over mountains and in wind (PATTERNS.md, section 8, 8 October 2026). On 6
# October 2026 the forecast wind at the height of every climb was 1-13 km/h, Alps
# included, while 10 of the 12 Alpine climbs of 7 October, a south föhn day, had
# 18-50 km/h; relief alone kept the calm Alpine climbs of the 6th. Unverified
# against any report of real wave.
WAVE_RELIEF_M = 1000        # highest minus lowest ground within 5 km (Nightly.relief): a ridge to set a wave off
WAVE_WIND_KMH = 20          # model wind at the climb's altitude and hour; about 11 kt, a cautious floor
# The wind comes from the Open-Meteo historical forecast API (CC BY 4.0; README),
# one request per night for all candidates, at points rounded to 0.1 degree and
# interpolated in height between pressure levels.
WAVE_WX_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
WAVE_WX_LEVELS = (900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400)   # hPa, about 1 to 7 km
WAVE_WX_TIMEOUT, WAVE_WX_RETRY_S = 60, 15
# A wave climb is filed at the midpoint of the climb, in cells of a quarter of a
# degree (sources.THERMAL_CELLS_PER_DEG), from 8 October 2026; 1 degree and the
# start of the climb before. A 1-degree cell named the place by its largest
# town, Lugano for climbs over the Gotthard (PATTERNS.md, section 8).


# --- 9. Powered aircraft and helicopters (PATTERNS.md sections 9-11, 7 October 2026) ---
POWERED = {2, 8, 9}
HELICOPTER = 3
FL = re.compile(r" FL(\d+(?:\.\d+)?)")
LEVEL_FT = 150                      # a level segment stays within 150 ft of its first altitude
LEVEL_S, LEVEL_GAP = 120, 30        # for at least 2 minutes, no silence over 30 s
CRUISE_AGL_M = 300                  # cruise distributions: level segments above 300 m over the ground
CRUISE_ALT_EDGES_FT = (3000, 5000, 7000, 9000, 11000, 15000)
CRUISE_KMH_EDGES = (100, 150, 200, 250, 300)
EMITTERS_OF_INTEREST = ("A1", "B4", "A7")
NIGHT_SUN_DEG = -6                  # civil dusk to civil dawn
TUG_SHARE_EDGES = (0.25, 0.5, 0.75)


def sun_elevation(t, lat, lon):
    """Degrees of the sun above the horizon (NOAA's approximation, a fraction of a degree)."""
    d = t / 86400 + 2440587.5 - 2451545.0              # days since J2000
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360
    gmst = (280.46061837 + 360.98564736629 * d) % 360
    ha = math.radians((gmst + lon - ra + 540) % 360 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


class PoweredTrack:
    """One powered aircraft or helicopter, for its level segments."""
    __slots__ = ("t", "runs")

    def __init__(self):
        self.t = None
        self.runs = {}            # reference -> [t0, alt0, t1, speed sum, n, agl0, alt sum]


class CrewedEncounters:
    """Crewed aircraft of different kinds coming close (METHOD.md 10.2, 7 October 2026).

    One aircraft is one address, its kind the majority category of its
    packets that day over all its systems, ADS-B included. Fixes of every
    source enter, one per address and second, both aircraft airborne at
    the speed of their kind. An encounter is a fix of each within
    ENCOUNTER_S and within one of the THRESHOLDS; one per pair, threshold
    and ENCOUNTER_EVERY, filed by its closest approach. A pair that flies
    together (FORMATION) is left out for the day: aerotows, formations and
    one pilot carrying two devices under two addresses.
    """
    KIND = {6: "free_flight", 7: "free_flight", 1: "glider", 2: "powered", 8: "powered", 9: "powered",
            3: "helicopter"}
    THRESHOLDS = {"wide": (1000, 150), "close": (300, 100)}
    DISTANCE_EDGES = {"wide": (300, 600), "close": (100, 200)}
    # Closing speed, km/h: from two paragliders converging to two powered
    # aircraft head on (a light aircraft cruises at 150-250 km/h).
    CLOSING_EDGES = (50, 100, 200, 400)
    # Flying together: within 300 m at a relative speed under 20 km/h for
    # 60 s, with gaps of at most 20 s between such contacts. A crossing at
    # 100 km/h stays within 300 m for about 20 s; a tow or a formation for
    # minutes. The rule of the first count (within 300 m for 5 minutes over
    # 3 km) is kept beside it for what moves slowly apart and together.
    FORMATION_M, FORMATION_KMH, FORMATION_S, FORMATION_GAP = 300, 20, 60, 20
    ESCORT_M, ESCORT_S, ESCORT_KM, ESCORT_GAP = 300, 300, 3.0, 60
    DT, WINDOW = ENCOUNTER_S, STALE + 40
    # Index cells at least twice the widest threshold (2 km) up to about 65 N,
    # and 20-second buckets: a fix in one half of a cell can only match fixes
    # of this cell and the neighbour on that side, so 2 x 2 cells and 2 buckets
    # are read instead of 3 x 3 and 3 (the first version, 26 minutes a day).
    CELL_LAT, CELL_LON, BUCKET = 0.02, 0.05, 2 * ENCOUNTER_S

    def __init__(self):
        self.last = {}                          # address -> (t, lat, lon)
        # (cell, 10-second bucket) -> {address: its latest fix there}: at a
        # 10 s tolerance the earlier fixes of the same bucket add nothing, and
        # in a gaggle comparing every second of every neighbour cost minutes.
        self.index = collections.defaultdict(dict)
        self.order = collections.deque()
        self.max_t = -1e18
        self.slow = {}                          # pair -> [first t, last t]
        self.near = {}                          # pair -> [first t, lat, lon, last t]
        self.formation, self.escort = set(), set()
        self.open = {}                          # (pair, threshold) -> [t0, d, closing, kinds]
        self.done = []
        # (towed address, tug address, towed category, t0, t1, start lat, lon, release height above ground)
        self.tows = []

    def fix(self, address, kind, t, lat, lon, alt, course, kt, cat=None, agl=None):
        prev = self.last.get(address)
        if prev is not None:
            if t <= prev[0]:
                return                          # one fix per address and second, whatever the system
            if dist(prev[1], prev[2], lat, lon) > sources.IMPLAUSIBLE_MS * max(t - prev[0], 1):
                self.last[address] = (t, lat, lon)
                return
        self.last[address] = (t, lat, lon)
        fl, fo, ft = lat / self.CELL_LAT, lon / self.CELL_LON, t / self.BUCKET
        cl, co, tb = int(fl // 1), int(fo // 1), int(ft // 1)
        nl = (cl, cl - 1 if fl - cl < 0.5 else cl + 1)
        no = (co, co - 1 if fo - co < 0.5 else co + 1)
        nt = (tb, tb - 1 if ft - tb < 0.5 else tb + 1)
        vel = None
        if course and kt is not None:
            vel = (kt * 1.852 * math.sin(math.radians(course)), kt * 1.852 * math.cos(math.radians(course)))
        wide_m, wide_v = self.THRESHOLDS["wide"]
        for a_ in nl:
            for b_ in no:
                for c_ in nt:
                    bucket = self.index.get((a_, b_, c_))
                    if not bucket:
                        continue
                    for o in bucket.values():
                        ot, olat, olon, oalt, oaddr, okind, ovel, ocat, oagl = o
                        if oaddr == address or abs(ot - t) > self.DT or abs(oalt - alt) > wide_v:
                            continue
                        d = dist(lat, lon, olat, olon)
                        if d > wide_m:
                            continue
                        pair = (address, oaddr) if address < oaddr else (oaddr, address)
                        rel = (math.hypot(vel[0] - ovel[0], vel[1] - ovel[1])
                               if vel is not None and ovel is not None else None)
                        if d <= self.FORMATION_M and rel is not None and rel < self.FORMATION_KMH:
                            # The towed side, if this pair can be a tow: its
                            # position and height above ground at this contact.
                            if cat in TOWED and ocat in TUGS:
                                towed = (address, oaddr, cat, lat, lon, agl)
                            elif ocat in TOWED and cat in TUGS:
                                towed = (oaddr, address, ocat, olat, olon, oagl)
                            else:
                                towed = None
                            f = self.slow.get(pair)
                            if f is None or t - f[1] > self.FORMATION_GAP:
                                if f is not None:
                                    self.close_slow(f)
                                self.slow[pair] = [t, t, towed, towed]
                            else:
                                f[1] = max(f[1], t)
                                if towed is not None:
                                    f[3] = towed
                                    if f[2] is None:
                                        f[2] = towed
                                if f[1] - f[0] >= self.FORMATION_S:
                                    self.formation.add(pair)
                        if d <= self.ESCORT_M:
                            e = self.near.get(pair)
                            if e is None or t - e[3] > self.ESCORT_GAP:
                                self.near[pair] = [t, lat, lon, t]
                            else:
                                e[3] = max(e[3], t)
                                if e[3] - e[0] > self.ESCORT_S and dist(e[1], e[2], lat, lon) > self.ESCORT_KM * 1000:
                                    self.escort.add(pair)
                        if okind == kind:
                            continue
                        kinds = tuple(sorted((kind, okind)))
                        for name, (hm, vm) in self.THRESHOLDS.items():
                            if d > hm or abs(oalt - alt) > vm:
                                continue
                            key = (pair, name)
                            enc = self.open.get(key)
                            start = min(t, ot)
                            if enc is not None and start - enc[0] <= ENCOUNTER_EVERY:
                                if d < enc[1]:
                                    enc[1], enc[2] = d, rel
                                continue
                            if enc is not None:
                                self.done.append((key,) + tuple(enc))
                            self.open[key] = [start, d, rel, kinds]
        k = (cl, co, tb)
        if k not in self.index:
            self.order.append(k)
        self.index[k][address] = (t, lat, lon, alt, address, kind, vel, cat, agl)
        self.max_t = max(self.max_t, t)
        while self.order and self.order[0][2] * self.BUCKET < self.max_t - self.WINDOW:
            self.index.pop(self.order.popleft(), None)

    def close_slow(self, f):
        """A run of flying together has ended: a tow if it began near the ground
        with a glider or hang glider behind a powered aircraft; released where
        the run ended."""
        t0, t1, start, end = f
        if start is None or end is None or t1 - t0 < self.FORMATION_S:
            return
        if start[5] is None or start[5] >= TOW_START_AGL:
            return
        self.tows.append((start[0], start[1], start[2], t0, t1, start[3], start[4], end[5]))

    def finish(self):
        for key, enc in self.open.items():
            self.done.append((key,) + tuple(enc))
        self.open = {}
        for f in self.slow.values():
            self.close_slow(f)
        self.slow = {}


class ParkedRun:
    """Consecutive fixes of one address on one system inside a box of PARKED_SPREAD_M."""
    __slots__ = ("cat", "t0", "t1", "la0", "la1", "lo0", "lo1", "packets", "agl", "agl_t")

    def __init__(self, cat, t, lat, lon):
        self.cat = cat
        self.t0 = self.t1 = t
        self.la0 = self.la1 = lat
        self.lo0 = self.lo1 = lon
        self.packets = 1
        self.agl = []
        self.agl_t = -1e9


class Nightly:
    def __init__(self, day, ddb=({}, set())):
        self.day = day
        self.c = True             # METHOD.md: drones, encounters, parked, quality
        self.p = True             # PATTERNS.md; switched off if its work fails during the pass
        self.ddb_types, self.notrack = ddb[0], ddb[1]
        self.ddb_models = ddb[2] if len(ddb) > 2 else {}
        self.emitters = collections.defaultdict(set)    # address -> ADS-B emitter categories heard
        self.declared = collections.defaultdict(set)    # address with a category 13 -> system labels
        self.addr_cats = collections.defaultdict(collections.Counter)   # address -> declared category -> fixes
        self.drone_hours = collections.defaultdict(float)   # (local day, hour, address) -> s
        self._evidence = None
        self.major, self.major13 = {}, set()      # (address, tocall) -> majority category; vote()
        self.addr_major = {}                      # address -> majority over all its systems; vote()
        self.crewed = CrewedEncounters()
        self.failed = {}                          # family -> what went wrong, for nightly_runs.notes
        self.tow_pairs = set()
        self.agl_time = collections.Counter()      # (category, solar hour, AGL band) -> s; drones apart
        self.addr_hour_air = collections.Counter() # (address, solar hour) -> s, gliders and free flight
        self.drone_agl = collections.Counter()     # (solar hour, AGL band, address) -> s
        self.flight = {}                           # address -> [t0, lat0, lon0, last t, max d, path, cat, lon]
        self.flights = []                          # (address, cat, t0, lon0, seconds, max d, path)
        self.wave_q = collections.defaultdict(collections.deque)   # glider -> (t, alt, net turn, abs turn)
        self.wave_last = {}                        # glider -> (t, course, net, abs)
        self.wave_quiet = {}
        self.waves = []                            # (address, t0, lat, lon, altitude, time of the climb's midpoint)
        self.powered = {}                         # address -> PoweredTrack
        self.levels = collections.Counter()       # (address, category, reference, alt band, speed band) -> segments
        self.level_s = collections.Counter()
        self.heli = collections.defaultdict(lambda: [0.0, set()])   # (lat, lon, night, agl band) -> s, aircraft
        self.addr_air = collections.Counter()     # tug candidates -> airborne seconds
        self.launch_last = {}                     # glider or hang glider -> t of its last fix
        self.climb_open = {}                      # glider -> its launch climb so far
        self.climbs = {}                          # (glider, session start) -> (m/s, km/h, deg/s turned)
        self.session_method = {}                  # (address, session start) -> launch method
        self.sessions_of = collections.defaultdict(list)   # address -> session starts
        self.launch_air = {}                      # -> t of its last airborne fix
        self.sessions = []                        # (address, category, t, lat, lon): flights begun
        self.winch_recent = collections.defaultdict(collections.deque)   # glider -> (t, agl, kmh)
        self.winch_open = {}                      # glider -> [t low, lat, lon, top agl]
        self.winches = []                         # (address, t low, lat, lon, top agl)
        self.rx_cat = collections.Counter()       # receiver -> radio packets with a voted category
        self.rx_cat_stray = collections.Counter()
        self.d0 = calendar.timegm(day.timetuple())
        self.d1 = self.d0 + 86400
        self.terrain = sources.Terrain(sources.DEM_PATH)
        self.lines = 0
        # stream state, as in the service
        self.last_fix = {}                     # (src, tocall, via) -> (t, lat, lon)
        self.addr_fix = {}                     # address -> (t, lat, lon, kt, alt)
        self.addr_systems = collections.defaultdict(set)     # address -> systems, platforms left out
        self.addr_platforms = collections.defaultdict(set)   # address -> platforms that relayed it
        # 1. hours
        self.hours_air = collections.defaultdict(float)     # (local day number, category, hour) -> s
        self.hours_ac = collections.defaultdict(set)
        # 2. circling
        self.circles = {}                      # (address, tocall) -> CircleTrack
        # 3. parked
        self.parked_runs = {}                  # (address, label) -> ParkedRun
        self.parked = collections.defaultdict(lambda: [set(), 0.0, 0])   # (label, cat) -> [addresses, s, packets]
        # 4. drones
        self.drone_air = collections.defaultdict(float)     # (lat, lon, hband, sband, address) -> s
        self.drone_session = {}                # address -> [last t, lat0, lon0, max distance]
        self.drone_extent = {}                 # address -> largest distance from a session's first fix
        self.drone_index = collections.defaultdict(collections.deque)    # cell -> (t, lat, lon, alt, address)
        self.crewed_index = collections.defaultdict(collections.deque)   # cell -> (t, lat, lon, alt, address, cat)
        self.drone_active = {}                 # 0.1-degree square -> last packet time of a drone in it
        self.swept_at = -1e18
        self.last_drone_t = -1e18
        self.encounters_open = {}              # (drone, other) -> [t0, min distance, other category]
        self.encounters = []                   # (drone, other, other category, min distance)
        # 5. quality
        self.q = collections.defaultdict(lambda: collections.Counter())  # label -> check -> count
        self.categories = collections.defaultdict(collections.Counter)   # (label, address) -> category -> n
        self.jumps = collections.Counter()     # (label, address) -> jumps
        self.addresses = set()                 # (label, address)
        self.rx_pos = {}                       # receiver -> [lat0, lon0, beacons, far beacons, max distance]
        self.rx_alt = collections.defaultdict(lambda: array.array("h"))   # receiver -> altitude minus terrain, m
        self.rx_relay = collections.Counter()  # receiver -> relayed radio packets
        self.rx_late = collections.Counter()
        self.relay_groups = {}                 # (address, packet time) -> [reception epoch, {receiver: alt}]
        self.relay_order = collections.deque()
        self.relay_pairs = collections.defaultdict(lambda: array.array("h"))  # (rx a, rx b) -> alt a - alt b, m

    # --- the pass ---------------------------------------------------------------

    def progress(self):
        """Memory and the size of what grows, for the log."""
        anon = None
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("RssAnon:"):
                        anon = int(line.split()[1]) // 1024
        except OSError:
            pass
        samples = sum(len(x[7]) for tr in self.circles.values() for x in tr.thermals)
        return (f"anon {anon} MB; streams {len(self.last_fix):,}, addresses {len(self.addr_fix):,}, "
                f"circle tracks {len(self.circles):,} with {sum(len(t.thermals) for t in self.circles.values()):,} "
                f"thermals and {samples:,} samples, parked runs {len(self.parked_runs):,}, "
                f"relay pairs {len(self.relay_pairs):,} ({sum(len(v) for v in self.relay_pairs.values()):,}), "
                f"device categories {len(self.categories):,}")

    def vote(self, files):
        """First pass: the category each address declares most often on each system.

        Only the id field of each packet is read, so this costs a fraction
        of the main pass. The same exclusions apply as there.
        """
        votes = collections.defaultdict(collections.Counter)
        for path, stop in files:
            if path is None:
                continue
            for epoch, line in read_lines(path, stop):
                try:
                    src, rest = line.split(">", 1)
                    head, body = rest.split(":", 1)
                except ValueError:
                    continue
                if not body.startswith("/") or body[26:27] in ("&", "_"):
                    continue
                raw = head.split(",", 1)[0]
                if raw in sources.EXCLUDED or raw in ("OGNSDR", "OGNSXR"):
                    continue
                cat, no_track = sources.id_info(body)
                if cat is None or no_track:
                    continue
                tocall = sources.same_system(raw, src)
                if sources.rebroadcast(tocall, src, cat) or sources.UNKNOWN_YET.get(tocall) == cat:
                    continue
                votes[(src[-6:], tocall)][cat] += 1
        self.major = {k: c.most_common(1)[0][0] for k, c in votes.items()}
        per_address = collections.defaultdict(collections.Counter)
        for (a, _), c in votes.items():
            per_address[a].update(c)
        self.addr_major = {a: c.most_common(1)[0][0] for a, c in per_address.items()}
        self.major13 = {a for (a, _), m in self.major.items() if m == DRONE}
        logger.info(f"Majority categories: {len(self.major):,} address-systems, {len(self.major13):,} addresses "
                    f"declaring a drone on at least one system")

    def run(self, raw_dir, hours=range(24)):
        files = [(hour_path(raw_dir, self.day, h) if h in hours else None, None) for h in range(24)]
        missing = [h for h, (p, _) in enumerate(files) if p is None]
        nxt = hour_path(raw_dir, self.day + datetime.timedelta(days=1), 0)
        if nxt is not None:
            files.append((nxt, self.d1 + STALE + 1))
        self.vote(files)
        for path, stop in files:
            if path is None:
                continue
            for epoch, line in read_lines(path, stop):
                self.lines += 1
                self.handle(epoch, line)
            logger.info(f"{os.path.basename(path)}: {self.lines:,} lines, {self.progress()}")
            if os.getenv("NIGHTLY_TRACE"):
                import tracemalloc
                for stat in tracemalloc.take_snapshot().statistics("lineno")[:12]:
                    logger.info(f"  {stat}")
        self.finish()
        return 24 - len(missing), missing

    def handle(self, epoch, line):
        try:
            src, rest = line.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            return
        if not body.startswith("/"):
            return                              # status lines, objects, messages
        path = head.split(",")
        raw_tc = path[0]
        in_day = self.d0 <= epoch < self.d1     # quality is filed by reception
        if raw_tc == "OGNSDR" or raw_tc == "OGNSXR":
            if in_day and self.c:
                self.receiver_beacon(src, body)
            return
        if body[26:27] == "&" or raw_tc in sources.EXCLUDED:
            return                              # FANET ground stations, delayed and synthetic copies
        if body[26:27] == "_":
            return                              # weather stations
        tocall = sources.same_system(raw_tc, src)
        id_category, no_track = sources.id_info(body)
        if no_track:
            return
        if id_category is None and tocall in sources.AIRCRAFT_ONLY:
            return                              # a Meshtastic node on the ground
        if sources.rebroadcast(tocall, src, id_category):
            return                              # a PilotAware station repeating a FLARM aircraft
        label, kind = sources.source_info(tocall)
        q = self.q[label] if in_day else None
        if q is not None:
            q["positions"] += 1
        if body[1:7] == "______":
            if q is not None:
                q["no_timestamp"] += 1
            return
        if "h" not in body[:8]:
            return
        if body[8:10] == "00" and body[17:20] == "000":
            if q is not None:
                q["position_0_0"] += 1
            return
        m = sources._position.match(body)
        if not m:
            return
        hh, mi, ss, latd, latm, ns, _table, lond, lonm, ew, symbol, course, speed = m.groups()
        latm, lonm = float(latm), float(lonm)
        w = sources._extra_precision.search(body)
        if w:
            latm += int(w.group(1)) / 1000
            lonm += int(w.group(2)) / 1000
        lat = int(latd) + latm / 60
        lon = int(lond) + lonm / 60
        if ns == "S":
            lat = -lat
        if ew == "W":
            lon = -lon
        a = sources._alt.search(body)
        alt_m = int(a.group(1)) * 0.3048 if a else None
        course = int(course) if course is not None else None
        kt = int(speed) if speed is not None else None
        via = "radio" if kind == "adsb" or sources._radio_meta.search(body) else "net"
        if tocall == REMOTE_ID:
            category = DRONE
        elif id_category is not None:
            category = id_category
        else:
            category = sources.SYMBOL_CATEGORY.get(symbol, sources.UNKNOWN_CATEGORY)
        address = src[-6:]
        if address in self.notrack:
            return                              # the owner asked OGN not to track it

        # Packet time, as sources.parse_fix: the clock of the day of arrival,
        # the previous day when more than 5 minutes ahead.
        sod = int(hh) * 3600 + int(mi) * 60 + int(ss)
        now = int(epoch)
        t = now - now % 86400 + sod
        if t - epoch > STALE:
            t -= 86400
        if q is not None:
            delay = (epoch % 86400) - sod
            if delay < -43200:
                delay += 86400
            elif delay > 43200:
                delay -= 86400
            q["fixes"] += 1
            if delay > LATE_S:
                q["late_60s"] += 1
            elif delay < -AHEAD_S:
                q["ahead_5s"] += 1
            if alt_m is not None:
                q["with_altitude"] += 1
                if alt_m >= ALTITUDE_SENTINEL_M:
                    q["altitude_sentinel"] += 1
                elif alt_m == 0:
                    q["altitude_zero"] += 1
            if course is not None:
                q["with_course"] += 1
                if course > 360:
                    q["heading_over_360"] += 1
            if category in FREE_FLIGHT:         # the packet's own category, before the vote
                q["free_flight_fixes"] += 1
                if sources.implausible(category, kt, alt_m):
                    q["free_flight_implausible"] += 1
            m = self.major.get((address, tocall))
            stray = (id_category is not None and m is not None and id_category != m
                     and not fanet_ground(tocall, m, id_category) and sources.UNKNOWN_YET.get(tocall) != id_category)
            if id_category is not None and m is not None:
                q["categorised"] += 1
                q["category_stray"] += stray
            if id_category is not None:
                self.categories[(label, address)][id_category] += 1
            if via == "radio" and kind != "adsb":
                rx = path[-1]
                self.rx_relay[rx] += 1
                if id_category is not None and m is not None:
                    self.rx_cat[rx] += 1
                    self.rx_cat_stray[rx] += stray
                if delay > LATE_S:
                    self.rx_late[rx] += 1
                if tocall in RELAY_SOURCES and alt_m is not None:
                    self.relay(epoch, address, sod, rx, alt_m)

        # --- the measures: fixes of this day, received in time ---------------
        if epoch - t > STALE or not (self.d0 <= t < self.d1):
            return
        raw_category = category
        m = self.major.get((address, tocall))
        if (m is not None and tocall != REMOTE_ID and not fanet_ground(tocall, m, category)
                and not PACKET_CATEGORY):
            category = m                        # the day's majority on this system
        if kind == "adsb":
            # The emitter category, read before any filter of the measures: an
            # ADS-B id often carries a free-flight category at airliner speed,
            # and that fix is dropped as implausible a few lines down.
            e = EMITTER.search(body)
            if e:
                self.emitters[address].add(e.group(1))
        if sources.implausible(category, kt, alt_m):
            # The device is sending, so no silence may run across this fix.
            self.last_fix.pop((src, tocall, via), None)
            self.addr_fix.pop(address, None)
            return
        key = (src, tocall, via)
        prev = self.last_fix.get(key)
        if prev is not None and t <= prev[0]:
            return                              # a copy through another receiver, or older
        self.last_fix[key] = (t, lat, lon)
        if kind != "platform":
            self.addr_systems[address].add(label)
        else:
            self.addr_platforms[address].add(label)
        if q is not None:
            self.addresses.add((label, address))
            if prev is not None:
                d = dist(prev[1], prev[2], lat, lon)
                if d > JUMP_MIN_M and d > sources.IMPLAUSIBLE_MS * max(t - prev[0], 1):
                    self.jumps[(label, address)] += 1

        if category == DRONE or raw_category == DRONE:
            self.declared[address].add(label)
        if id_category is not None and kind != "adsb":
            self.addr_cats[address][raw_category] += 1
        crewed_airborne = category in CREWED and (kt or 0) >= sources.FLYING_KT.get(category, sources.AIRBORNE_KT)
        if category == DRONE:
            self.drone_fix(address, t, lat, lon, alt_m)
        elif self.c and crewed_airborne and alt_m is not None and t - self.last_drone_t <= ENCOUNTER_ACTIVE:
            self.crewed_fix(address, category, t, lat, lon, alt_m)
        am = self.addr_major.get(address)
        ck = CrewedEncounters.KIND.get(am)
        agl = None
        if self.p and am in TOWED and alt_m is not None:
            ground = self.terrain.elevation(lat, lon)
            agl = alt_m - ground if ground is not None else None
            self.pattern(self.launch_fix, address, am, t, lat, lon, agl, kt, alt_m, course)
        if (ck is not None and alt_m is not None and not PACKET_CATEGORY
                and (kt or 0) >= sources.FLYING_KT.get(am, sources.AIRBORNE_KT)):
            self.crewed.fix(address, ck, t, lat, lon, alt_m, course, kt, am, agl)
        if self.p and (am in POWERED or am == HELICOPTER) and alt_m is not None:
            f = FL.search(body) if kind == "adsb" else None
            self.pattern(self.powered_fix, address, am, t, lat, lon, alt_m, kt, course,
                         float(f.group(1)) * 100 if f else None)
        if kind == "adsb":
            return                              # ADS-B: only the other aircraft of an encounter
        self.timeline(address, category, t, lat, lon, kt, alt_m)
        if self.p and category == 1 and course and alt_m is not None:
            self.pattern(self.wave_fix, address, t, lat, lon, alt_m, course)
        if (category in CIRCLE_CATEGORIES or category == DRONE) and course:
            self.circle(address, tocall, category, t, course, kt, lat, lon, alt_m)
        if self.c and category in PARKED_CATEGORIES:
            self.park(address, label, category, t, lat, lon, alt_m)

    # --- 9-11. powered aircraft ------------------------------------------------------------

    def powered_fix(self, address, cat, t, lat, lon, alt, kt, course, fl_ft):
        """Level segments of powered aircraft and helicopters (PATTERNS.md, section 9)."""
        tr = self.powered.get(address)
        if tr is None:
            tr = self.powered[address] = PoweredTrack()
        if tr.t is not None and t <= tr.t:
            return                              # one fix per address and second, whatever the system
        tr.t = t
        # Per altitude reference: pressure (ADS-B's FL) and the altitude every
        # system sends (GPS, or whatever the transponder gives).
        refs = [("gps", alt / 0.3048)]
        if fl_ft is not None:
            refs.append(("pressure", fl_ft))
        agl = None
        for ref, ft in refs:
            run = tr.runs.get(ref)
            if run is not None and (t - run[2] > LEVEL_GAP or abs(ft - run[1]) > LEVEL_FT):
                self.close_level(address, cat, ref, run)
                run = None
            if run is None:
                if agl is None:
                    ground = self.terrain.elevation(lat, lon)
                    agl = alt - ground if ground is not None else -1e9
                run = tr.runs[ref] = [t, ft, t, 0.0, 0, agl, 0.0]
            run[2] = t
            run[3] += (kt or 0) * 1.852
            run[4] += 1
            run[6] += ft

    def close_level(self, address, cat, ref, run):
        t0, ft0, t1, vsum, n, agl0, ftsum = run
        if t1 - t0 < LEVEL_S or n < 2 or agl0 < CRUISE_AGL_M:
            return
        key = (address, cat, ref, band(ftsum / n, CRUISE_ALT_EDGES_FT), band(vsum / n, CRUISE_KMH_EDGES))
        self.levels[key] += 1
        self.level_s[key] += t1 - t0

    # --- 7. launches ------------------------------------------------------------------

    def launch_fix(self, address, cat, t, lat, lon, agl, kt, alt_m=None, course=None):
        """Flights begun, and winch launches, of a glider or hang glider (PATTERNS.md, section 7)."""
        last = self.launch_last.get(address)
        if last is not None and t <= last:
            return                              # one fix per address and second
        self.launch_last[address] = t
        kmh = (kt or 0) * 1.852
        if (kt or 0) >= sources.FLYING_KT.get(cat, sources.AIRBORNE_KT):
            prev = self.launch_air.get(address)
            if prev is None or t - prev > LAUNCH_SESSION:
                self.sessions.append((address, cat, t, lat, lon, agl))
                if cat == 1 and agl is not None and agl <= LAUNCH_MAX_AGL and alt_m is not None:
                    # [t0, alt0, speed sum, fixes, degrees turned, last course]
                    self.climb_open[address] = [t, alt_m, 0.0, 0, 0.0, course]
            self.launch_air[address] = t
        c = self.climb_open.get(address)
        if c is not None and alt_m is not None and t > c[0]:
            c[2] += kmh
            c[3] += 1
            if course and c[5]:
                c[4] += abs((course - c[5] + 540) % 360 - 180)
            if course:
                c[5] = course
            if alt_m - c[1] >= CLIMB_GAIN or t - c[0] > CLIMB_WAIT:
                gained = alt_m - c[1] >= CLIMB_GAIN
                self.climbs[(address, c[0])] = ((alt_m - c[1]) / (t - c[0]) if gained else None,
                                                c[2] / c[3], c[4] / (t - c[0]))
                del self.climb_open[address]
        if cat != 1 or agl is None:
            return                              # winches launch gliders
        w = self.winch_open.get(address)
        if w is not None:
            if t - w[0] <= WINCH_TOP_S:
                w[3] = max(w[3], agl)
                return
            self.winches.append((address,) + tuple(w))
            del self.winch_open[address]
        q = self.winch_recent[address]
        q.append((t, agl, kmh, lat, lon, alt_m))
        while q and t - q[0][0] > WINCH_S:
            q.popleft()
        if agl < WINCH_HIGH:
            return
        # From under 50 m to here within 90 s, at cable speed once off the
        # ground, and by a real climb: the height above the ground also grows
        # when a glider flies off a ridge or over a valley, with no climb at all
        # (on 6 October 2026 that alone made 1,016 "winch launches"); and fast, since
        # an aerotow whose tug is not heard, or a self-launch, also climbs 150 m
        # in 90 s (the second count, with 90 s and no climb rate, still found 996).
        for i, (t0, a0, _, la0, lo0, alt0) in enumerate(q):
            if a0 < WINCH_LOW:
                climb = [x for x in list(q)[i + 1:] if x[1] >= WINCH_LOW]
                if (climb and all(WINCH_KMH[0] <= x[2] <= WINCH_KMH[1] for x in climb)
                        and alt0 is not None and alt_m is not None
                        and alt_m - alt0 >= WINCH_HIGH - WINCH_LOW
                        and (alt_m - alt0) / max(t - t0, 1) >= WINCH_CLIMB):
                    self.winch_open[address] = [t0, la0, lo0, agl]
                    q.clear()
                break

    def launch_rows(self, day):
        """daily_launches and daily_tug_tows, and the tow pairs for the log."""
        for address, w in self.winch_open.items():
            self.winches.append((address,) + tuple(w))
        self.winch_open = {}
        tows = self.crewed.tows
        towed_runs = collections.defaultdict(list)
        for towed, tug, cat, t0, t1, la, lo, rel in tows:
            towed_runs[towed].append((t0, t1))
        # A winch climb flown in formation is a tow, whatever its climb rate.
        winches = [w for w in self.winches
                   if not any(a - 120 <= w[1] <= b for a, b in towed_runs.get(w[0], ()))]
        rows = collections.Counter()
        for towed, tug, cat, t0, t1, la, lo, rel in tows:
            rows[("aerotow", TOWED[cat], self.terrain_of(la, lo), math.floor(la), math.floor(lo), band(rel, RELEASE_EDGES),
                  band((t1 - t0) / 60, TOW_MINUTES_EDGES))] += 1
        for address, t0, la, lo, top in winches:
            rows[("winch", "glider", self.terrain_of(la, lo), math.floor(la), math.floor(lo), band(top, WINCH_TOP_EDGES),
                  UNKNOWN_BAND)] += 1
        starts = collections.defaultdict(list)
        for towed, tug, cat, t0, t1, la, lo, rel in tows:
            starts[towed].append(t0)
        for address, t0, la, lo, top in winches:
            starts[address].append(t0)
        towed_starts = {(towed, t0) for towed, tug, cat, t0, t1, la, lo, rel in tows}
        # A session whose flight lasts under FLIGHT_MIN_S, or which never made a
        # flight, is a fragment at the edge of coverage: not a launch either.
        flight_s = collections.defaultdict(list)
        for address, cat, t0, lon0, sec, maxd, path, lat0, *_ in self.flights:
            flight_s[address].append((t0, sec))
        self.launch_fragments = 0
        for address, cat, t, la, lo, agl in self.sessions:
            self.sessions_of[address].append(t)
            matched = [s for s in starts.get(address, ()) if LAUNCH_MATCH[0] <= s - t <= LAUNCH_MATCH[1]]
            if matched:
                self.session_method[(address, t)] = "aerotow" if (address, matched[0]) in towed_starts else "winch"
                continue
            # First heard high up: a flight joined in the air, kept out of the launch split.
            if agl is None or agl > LAUNCH_MAX_AGL:
                method = "start_unseen"
            elif cat != 1:
                method = "no_tow_seen"
            else:
                method = self.climb_method(address, t)
            self.session_method[(address, t)] = method
            near = [sec for t0, sec in flight_s.get(address, ()) if LAUNCH_MATCH[0] <= t0 - t <= LAUNCH_MATCH[1]]
            if not near or max(near) < FLIGHT_MIN_S:
                self.launch_fragments += 1
                continue
            rows[(method, TOWED[cat], self.terrain_of(la, lo), math.floor(la), math.floor(lo),
                  UNKNOWN_BAND, UNKNOWN_BAND)] += 1
        tugs = collections.Counter(tug for _, tug, _, _, _, _, _, _ in tows)
        tug_rows = collections.Counter(band(n, TUG_TOWS_EDGES) for n in tugs.values())
        self.tow_pairs = {(a, b) if a < b else (b, a) for a, b, *_ in tows}
        return ([(day,) + k + (n,) for k, n in sorted(rows.items())],
                [(day, b, n) for b, n in sorted(tug_rows.items())])

    def flight_launch(self, address, t0):
        """The launch method of the session a glider flight began with (empty if none)."""
        best = None
        for t in self.sessions_of.get(address, ()):
            if LAUNCH_MATCH[0] <= t0 - t <= LAUNCH_MATCH[1] and (best is None or abs(t0 - t) < abs(t0 - best)):
                best = t
        return self.session_method.get((address, best), "other") if best is not None else "other"

    # Terrain relief around a point: the highest minus the lowest ground within
    # RELIEF_M, from the terrain model, cached per RELIEF_CELL degrees. For
    # choosing where "plain" ends and "mountain" begins (7 October 2026): the
    # threshold is to be set after looking at the distribution.
    RELIEF_M, RELIEF_CELL = 5000, 0.02

    def relief(self, lat, lon):
        key = (int(lat // self.RELIEF_CELL), int(lon // self.RELIEF_CELL))
        cache = self.__dict__.setdefault("_relief", {})
        if key in cache:
            return cache[key]
        tr = self.terrain
        value = None
        if tr.ok:
            clat, clon = (key[0] + 0.5) * self.RELIEF_CELL, (key[1] + 0.5) * self.RELIEF_CELL
            dlat = self.RELIEF_M / 111320
            dlon = dlat / max(0.1, math.cos(math.radians(clat)))
            r0 = max(0, int((tr.north - (clat + dlat)) / tr.step))
            r1 = min(tr.rows - 1, int((tr.north - (clat - dlat)) / tr.step))
            c0 = max(0, int((clon - dlon - tr.west) / tr.step))
            c1 = min(tr.cols - 1, int((clon + dlon - tr.west) / tr.step))
            if r0 <= r1 and c0 <= c1:
                hi, lo = -1e9, 1e9
                for r in range(r0, r1 + 1):
                    a = array.array("h")
                    a.frombytes(tr._mm[(r * tr.cols + c0) * 2:(r * tr.cols + c1 + 1) * 2])
                    vals = [0 if v == tr.nodata or v < 0 else v for v in a]
                    hi, lo = max(hi, max(vals)), min(lo, min(vals))
                value = hi - lo
        cache[key] = value
        return value

    def terrain_of(self, lat, lon):
        r = self.relief(lat, lon)
        return "unknown" if r is None else "mountain" if r >= MOUNTAIN_RELIEF else "plain"

    def relief_histograms(self):
        """Relief around thermals (one system per aircraft) and around flight starts,
        per kind, in 100 m steps (3,000 m and above together)."""
        hist = collections.defaultdict(collections.Counter)
        best = {}
        for (address, tocall), tr in self.circles.items():
            if tr.cat not in CIRCLE_CATEGORIES:
                continue
            th = [x for x in tr.thermals if self.d0 <= x[1] < self.d1]
            if th and (address not in best or len(th) > len(best[address][1])):
                best[address] = (tr.cat, th)
        for address, (cat, th) in best.items():
            for x in th:
                r = self.relief(x[3], x[4])
                if r is not None:
                    hist[("thermals", THERMAL_KIND.get(cat))][min(30, int(r // 100))] += 1
        for address, cat, t0, lon0, sec, maxd, path, lat0, *_ in self.flights:
            if self.d0 <= t0 < self.d1:
                r = self.relief(lat0, lon0)
                if r is not None:
                    kind = THERMAL_KIND.get(cat) or (PATTERN_KIND.get(cat, "other") if cat != DRONE else "drone")
                    hist[("flight starts", kind)][min(30, int(r // 100))] += 1
        return hist

    def climb_method(self, address, t):
        """A glider launch with no tow or winch found, by its climb (PATTERNS.md, section 7)."""
        c = self.climbs.get((address, t))
        if c is None or c[0] is None:
            return "other"
        rate, kmh, turn = c
        if rate >= CLIMB_WINCH:
            return "winch"
        if (CLIMB_TOW[0] <= rate <= CLIMB_TOW[1] and CLIMB_TOW_KMH[0] <= kmh <= CLIMB_TOW_KMH[1]
                and turn <= CLIMB_STRAIGHT):
            model = self.ddb_models.get(address, "")
            return "self_launch" if model and SELF_LAUNCH_MODELS.search(model) else "tow_or_self"
        return "other"

    # --- 1. flying time per aircraft, and drone time ------------------------------

    def timeline(self, address, category, t, lat, lon, kt, alt_m):
        """sources.SourceTracker.count_hours, filed by local solar hour."""
        prev = self.addr_fix.get(address)
        if prev is not None and t <= prev[0]:
            return
        self.addr_fix[address] = (t, lat, lon, kt, alt_m)
        if prev is None:
            return
        seconds = t - prev[0]
        if seconds > sources.SESSION_BREAK:
            return
        if dist(prev[1], prev[2], lat, lon) > sources.IMPLAUSIBLE_MS * max(seconds, 1):
            return
        if not sources.flying(category, prev[3], kt):
            if self.p and category in FLIGHT_STOP_CATEGORIES and (kt or 0) <= FLIGHT_STOP_KT and alt_m is not None:
                f = self.flight.get(address)
                if f is not None and not f[11] and t - f[3] <= sources.SESSION_BREAK:
                    ground = self.terrain.elevation(lat, lon)
                    if ground is not None and alt_m - ground <= FLIGHT_STOP_AGL:
                        # Seen standing on the ground since its last airborne
                        # fix: the landing, where and at what altitude.
                        f[11] = (lat, lon, alt_m)
            return
        if category in GROUND_CATEGORIES:
            return
        local = prev[0] + seconds / 2 + prev[2] * SOLAR_SECONDS_PER_DEGREE
        if self.p:
            self.pattern(self.pattern_segment, address, category, prev, t, lat, lon, kt, seconds, local, alt_m)
        if self.c and category == DRONE:
            ground = self.terrain.elevation(prev[1], prev[2])
            agl = prev[4] - ground if prev[4] is not None and ground is not None else None
            kmh = ((prev[3] or 0) + (kt or 0)) / 2 * 1.852
            self.drone_air[(math.floor(prev[1]), math.floor(prev[2]), band(agl, DRONE_HEIGHT_EDGES),
                            band(kmh, DRONE_SPEED_EDGES), address)] += seconds

    def pattern(self, f, *args):
        """Run a piece of the patterns family's per-fix work. If it fails, the
        family is switched off for the rest of the day and recorded, and the
        conspicuity measures go on (7 October 2026)."""
        try:
            f(*args)
        except Exception as e:
            logger.exception("The patterns family failed during the pass; it is not computed for this day")
            self.failed["patterns"] = f"pass: {type(e).__name__}: {e}"[:200]
            self.p = False

    def pattern_segment(self, address, category, prev, t, lat, lon, kt, seconds, local, alt_m=None):
        """One airborne segment of the timeline, for PATTERNS.md sections 1, 4, 6, 10 and 11."""
        hour = int(local % 86400 // 3600)
        ground = self.terrain.elevation(prev[1], prev[2])
        if category in TUGS:
            self.addr_air[address] += seconds
        if category == HELICOPTER:
            night = int(sun_elevation(prev[0] + seconds / 2, prev[1], prev[2]) < NIGHT_SUN_DEG)
            h = self.heli[(math.floor(prev[1]), math.floor(prev[2]), night,
                           band(prev[4] - ground if prev[4] is not None and ground is not None else None,
                                AGL_HOUR_EDGES))]
            h[0] += seconds
            h[1].add(address)
        agl_band = band(prev[4] - ground if prev[4] is not None and ground is not None else None, AGL_HOUR_EDGES)
        if category == DRONE:
            self.drone_agl[(hour, agl_band, address, self.terrain_of(prev[1], prev[2]))] += seconds
        else:
            terrain = self.terrain_of(prev[1], prev[2])
            self.agl_time[(category, hour, agl_band, terrain)] += seconds
            if category in CIRCLE_CATEGORIES:
                self.addr_hour_air[(address, hour, terrain)] += seconds
        self.flight_segment(address, category, prev, t, lat, lon, seconds,
                            prev[4] - ground if prev[4] is not None and ground is not None else None,
                            alt_m, alt_m - ground if alt_m is not None and ground is not None else None)
        if category == DRONE:
            # Per address, so that a crewed aircraft declared a drone can be
            # filed apart once the day's evidence is in (evidence()).
            self.drone_hours[(int(local // 86400), int(local % 86400 // 3600), address)] += seconds
        else:
            k = (int(local // 86400), category, int(local % 86400 // 3600))
            self.hours_air[k] += seconds
            self.hours_ac[k].add(address)

    def flight_segment(self, address, category, prev, t, lat, lon, seconds, agl=None, alt_now=None, agl_now=None):
        """Flights (PATTERNS.md, section 6): the airborne segments of one address,
        across silences the aircraft could have flown through (FLIGHT_MAX_GAP,
        FLIGHT_MAX_KMH), a new flight after a landing or a longer silence.

        f: [t0, lat0, lon0, last t, max distance, path, category, lon0, last lat,
        last lon, height above ground at the last segment, the landing (lat, lon,
        alt) once seen standing on the ground, altitude and height above ground
        of the first airborne fix]
        """
        f = self.flight.get(address)
        low = lambda h: h is None or h <= LAUNCH_MAX_AGL
        if f is not None and f[11]:
            self.end_flight(address, f)
            f = None
        if f is not None and prev[0] - f[3] > sources.SESSION_BREAK:
            gap = prev[0] - f[3]
            across = dist(f[8], f[9], prev[1], prev[2])
            if (gap > FLIGHT_MAX_GAP or low(f[10]) or low(agl)
                    or across > FLIGHT_MAX_KMH.get(category, FLIGHT_MAX_KMH_OTHER) / 3.6 * gap):
                self.end_flight(address, f)
                f = None
            else:
                f[5] += across                  # the straight line across the silence: a lower bound
        if f is None:
            f = self.flight[address] = [prev[0], prev[1], prev[2], prev[0], 0.0, 0.0, category, prev[2],
                                        prev[1], prev[2], agl, False, prev[4], agl, [], None, [prev[4], False]]
        f[3] = t
        f[5] += dist(prev[1], prev[2], lat, lon)
        f[4] = max(f[4], dist(f[1], f[2], lat, lon))
        f[8], f[9], f[10] = lat, lon, agl
        # For a landing inferred at the end (end_flight): the last fix, and
        # its altitude every 10 s over the last 90 s.
        f[15] = (alt_now, agl_now)
        # The top of the first climb: the highest altitude until the aircraft has
        # come down FIRST_CLIMB_DROP from it (PATTERNS.md, section 6).
        top = f[16]
        if not top[1] and alt_now is not None:
            if top[0] is None or alt_now > top[0]:
                top[0] = alt_now
            elif top[0] - alt_now > FIRST_CLIMB_DROP:
                top[1] = True
        if alt_now is not None:
            hist = f[14]
            if not hist or t - hist[-1][0] >= 10:
                hist.append((t, alt_now))
                while hist and t - hist[0][0] > 90:
                    hist.pop(0)

    def end_flight(self, address, f):
        """A flight is over: (address, category, t0, lon0, seconds, max distance,
        path, lat0, start altitude, start height above ground, landing or None,
        "seen", "inferred" or "none").

        A landing is seen when the aircraft is heard standing on the ground. A
        flight that ends in silence is taken as landed at its last fix when
        that fix is below INFER_AGL above the ground and lower than the
        aircraft was 30 to 60 s earlier (PATTERNS.md, section 6); a powered
        aircraft also needs an aerodrome there, checked in flight_classes.
        """
        landing, how = f[11] or None, "seen" if f[11] else "none"
        if landing is None and f[15] is not None and f[15][1] is not None and f[15][1] < INFER_AGL:
            last_t, last_alt = f[3], f[15][0]
            before = [a for t, a in f[14] if INFER_BACK[0] <= last_t - t <= INFER_BACK[1]]
            if before and last_alt is not None and last_alt < max(before):
                landing, how = (f[8], f[9], last_alt), "inferred"
        self.flights.append((address, f[6], f[0], f[7], f[3] - f[0], f[4], f[5], f[1], f[12], f[13], landing, how,
                             f[16][0]))

    def site_near(self, which, lat, lon, radius):
        """The site of a list of sources._sites ("fivl", "takeoff", "airfield")
        nearest a point within `radius` metres, as the list names it, or None."""
        cache = self.__dict__.setdefault("_site_idx", {})
        idx = cache.get(which)
        if idx is None:
            if sources._sites is None:
                sources._load_sites()
            idx = cache[which] = collections.defaultdict(list)
            for slat, slon, name, gliding in sources._sites.get(which, ()):
                idx[(int(slat // 0.05), int(slon // 0.05))].append((slat, slon, name))
        best = None
        cl, co = int(lat // 0.05), int(lon // 0.05)
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                for slat, slon, name in idx.get((cl + a, co + b), ()):
                    d = dist(lat, lon, slat, slon)
                    if d <= radius and (best is None or d < best[0]):
                        best = (d, name)
        return best[1] if best else None

    def airfield_near(self, lat, lon):
        """The OSM airfield nearest a point within AIRFIELD_MATCH_M, as "name (ICAO)", or None."""
        return self.site_near("airfield", lat, lon, AIRFIELD_MATCH_M)

    def start_site(self, cat, lat, lon):
        """Where a glider or free-flight flight took off, by name: a FIVL site,
        else an OSM take-off, within START_SITE_M (free flight); the nearest
        OSM airfield within AIRFIELD_MATCH_M (gliders)."""
        if cat == 1:
            return self.airfield_near(lat, lon)
        return self.site_near("fivl", lat, lon, START_SITE_M) or self.site_near("takeoff", lat, lon, START_SITE_M)

    def thermal_sites(self, best):
        """(kind, lat_idx, lon_idx, site) -> thermals: each thermal of the day
        filed by the take-off site of the flight it was flown in, in its
        0.25-degree cell, for naming thermal places (PATTERNS.md, section 3)."""
        by_address = collections.defaultdict(list)
        for address, cat, t0, lon0, sec, maxd, path, lat0, *_ in self.flights:
            by_address[address].append((t0, t0 + sec, lat0, lon0, cat))
        out = collections.Counter()
        n = THERMAL_CELLS_PER_DEG
        for address, (cat, th) in best.items():
            kind = THERMAL_KIND.get(cat)
            if kind is None:
                continue
            for x in th:
                if not self.d0 <= x[1] < self.d1:
                    continue
                flight = next((f for f in by_address.get(address, ()) if f[0] - 60 <= x[1] <= f[1] + 60), None)
                site = self.start_site(cat, flight[2], flight[3]) if flight else None
                if site:
                    out[(kind, math.floor(x[3] * n), math.floor(x[4] * n), site[:255])] += 1
        return out

    def flight_classes(self, kind_of):
        """Flight classes of PATTERNS.md section 6: rows per kind, terrain, class
        and length bands, and the routes of powered aircraft by address (kept
        for the two months of METHOD.md section 9, then reduced to counts)."""
        rows, routes = collections.Counter(), collections.Counter()
        tow_agl = collections.defaultdict(list)
        for towed, tug, cat, t0, t1, la, lo, rel in self.crewed.tows:
            if rel is not None:
                tow_agl[towed].append((t0, rel))
        for address, t0, la, lo, top in self.winches:
            tow_agl[address].append((t0, top))
        self.first_climb_starts = collections.Counter()
        for address, cat, t0, lon0, sec, maxd, path, lat0, alt0, agl0, landing, how, climb_top in self.flights:
            if not self.d0 <= t0 < self.d1 or sec < FLIGHT_MIN_S:
                continue
            if how == "inferred" and cat in POWERED_FIXED and self.airfield_near(landing[0], landing[1]) is None:
                # Low and descending, but nowhere a powered aircraft lands: it
                # may have left coverage low; its end stays unseen.
                landing, how = None, "none"
            kind = kind_of(cat, address)
            terrain = self.terrain_of(lat0, lon0)
            if cat in GLIDE_RATIO:
                if landing is None:
                    cls, d = "end_unseen", maxd
                else:
                    start = alt0
                    launched = []
                    if cat == 1:
                        launched = [h for t, h in tow_agl.get(address, ()) if LAUNCH_MATCH[0] <= t0 - t <= LAUNCH_MATCH[1]]
                        ground = self.terrain.elevation(lat0, lon0)
                        if launched and ground is not None:
                            start = ground + max(launched)   # the release, or the top of the winch launch
                    if not launched and agl0 is not None and agl0 <= LAUNCH_MAX_AGL and climb_top is not None:
                        # Started on the ground with no launch found: from the
                        # top of its first climb, else its reach would be zero.
                        start = max(climb_top, start) if start is not None else climb_top
                        self.first_climb_starts[kind] += 1
                    if start is None or landing[2] is None:
                        cls, d = "no_altitude", maxd
                    else:
                        reach = max(0.0, start - landing[2]) * GLIDE_RATIO[cat] * GLIDE_MARGIN
                        home = dist(lat0, lon0, landing[0], landing[1])
                        if home > reach and home > (PARAGLIDER_CROSS_M if cat == 7 else HOME_M):
                            cls, d = "cross", home
                        elif maxd > reach:
                            cls, d = "out_and_return", maxd
                        else:
                            cls, d = "local", maxd
            elif cat in POWERED_FIXED:
                if landing is None:
                    cls, d = "end_unseen", maxd
                else:
                    a, b = self.airfield_near(lat0, lon0), self.airfield_near(landing[0], landing[1])
                    if a is None or b is None:
                        cls, d = "no_airfield", maxd
                    elif a == b:
                        cls, d = ("local" if maxd <= POWERED_LOCAL_M else "out_and_return"), maxd
                    else:
                        cls, d = "one_way", dist(lat0, lon0, landing[0], landing[1])
                        routes[(min(a, b), max(a, b), address)] += 1
            else:
                continue
            rows[(kind, terrain, cls, how, band(d / 1000, FLIGHT_EXTENT_KM), band(path / 1000, FLIGHT_PATH_KM))] += 1
        return rows, routes

    def wave_fix(self, address, t, lat, lon, alt, course):
        """Probably wave (PATTERNS.md): a glider climbing on average 1 m/s for 3 minutes
        and 300 m, above 2,500 m, hardly turning."""
        last = self.wave_last.get(address)
        if last is not None and t <= last[0]:
            return
        q = self.wave_q[address]
        if last is None or t - last[0] > WAVE_GAP:
            q.clear()
            net = turned = 0.0
        else:
            d = (course - last[1] + 540) % 360 - 180
            net, turned = last[2] + d, last[3] + abs(d)
        self.wave_last[address] = (t, course, net, turned)
        q.append((t, alt, net, turned, lat, lon))
        while q and t - q[0][0] > WAVE_WINDOW:
            q.popleft()
        if alt < WAVE_MIN_M + WAVE_GAIN_M or t < self.wave_quiet.get(address, 0):
            return
        for t0, a0, n0, u0, la0, lo0 in q:
            if t - t0 < WAVE_S:
                break
            if a0 < WAVE_MIN_M:
                continue
            gain = alt - a0
            if (gain >= WAVE_GAIN_M and gain / (t - t0) >= WAVE_RATE and abs(net - n0) < WAVE_NET_TURN
                    and (turned - u0) / (t - t0) <= WAVE_TURN):
                self.waves.append((address, t0, (la0 + lat) / 2, (lo0 + lon) / 2, (a0 + alt) / 2, (t0 + t) / 2))
                self.wave_quiet[address] = t + WAVE_QUIET
                q.clear()
                return

    def wave_climbs(self):
        """The day's candidate climbs that are probable wave: relief of at least
        WAVE_RELIEF_M and a model wind of at least WAVE_WIND_KMH at the climb.
        If the wind cannot be fetched, no climb of the day is kept and the day's
        notes say so: unchecked climbs would bring back the calm-day climbs the
        rule exists to drop, and the raw recording allows a recompute."""
        cand = [w for w in self.waves if self.d0 <= w[1] < self.d1]
        relief = [w for w in cand if (self.relief(w[2], w[3]) or 0) >= WAVE_RELIEF_M]
        if not relief:
            logger.info(f"Wave: {len(cand)} candidate climbs, {len(cand)} rejected by relief, none to check for wind")
            return []
        try:
            wind = self.wave_wind(relief)
        except Exception as e:
            logger.error(f"Wave: {len(cand)} candidate climbs, {len(cand) - len(relief)} rejected by relief, "
                         f"{len(relief)} with the wind unknown ({type(e).__name__}: {e}); no wave kept for the day")
            self.failed["wave"] = f"wind unknown: {type(e).__name__}"[:200]
            return []
        kept = [w for w, v in zip(relief, wind) if v is not None and v >= WAVE_WIND_KMH]
        logger.info(f"Wave: {len(cand)} candidate climbs, {len(cand) - len(relief)} rejected by relief "
                    f"under {WAVE_RELIEF_M} m, {len(relief) - len(kept)} by wind under {WAVE_WIND_KMH} km/h "
                    f"({sum(1 for v in wind if v is None)} without a wind value), {len(kept)} kept")
        return kept

    def wave_wind(self, climbs):
        """Model wind speed (km/h) at each climb's midpoint, altitude and hour, from
        one Open-Meteo request; retried once."""
        pts = sorted({(round(w[2], 1), round(w[3], 1)) for w in climbs})
        hourly = ",".join(f"{v}_{p}hPa" for p in WAVE_WX_LEVELS for v in ("wind_speed", "wind_direction",
                                                                             "geopotential_height"))
        url = (f"{WAVE_WX_URL}?latitude={','.join(str(p[0]) for p in pts)}"
               f"&longitude={','.join(str(p[1]) for p in pts)}&start_date={self.day}&end_date={self.day}"
               f"&hourly={hourly}&wind_speed_unit=kmh&timezone=GMT")
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(url, timeout=WAVE_WX_TIMEOUT) as r:
                    data = json.load(r)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                logger.warning(f"Wave: the wind request failed ({type(e).__name__}: {e}); retrying once")
                time.sleep(WAVE_WX_RETRY_S)
        if isinstance(data, dict):
            data = [data]
        grid = dict(zip(pts, data))
        out = []
        for w in climbs:
            h = grid[(round(w[2], 1), round(w[3], 1))]["hourly"]
            i = min(len(h["time"]) - 1, max(0, int(round((w[5] - self.d0) / 3600))))
            levels = []
            for p in WAVE_WX_LEVELS:
                z, ws, wd = (h[f"{v}_{p}hPa"][i] for v in ("geopotential_height", "wind_speed", "wind_direction"))
                if None not in (z, ws, wd):
                    levels.append((z, -ws * math.sin(math.radians(wd)), -ws * math.cos(math.radians(wd))))
            levels.sort()
            out.append(wind_at(levels, w[4]) if levels else None)
        return out

    # --- 2. circling ----------------------------------------------------------------

    def circle(self, address, tocall, category, t, course, kt, lat, lon, alt_m):
        key = (address, tocall)
        tr = self.circles.get(key)
        if tr is None:
            tr = self.circles[key] = CircleTrack(category)
        p = tr.last
        if p is not None and t <= p[0]:
            return
        tr.last = (t, course, kt, lat, lon)
        if p is None:
            return
        gap = t - p[0]
        if (gap > CIRCLE_MAX_GAP or not sources.flying(category, p[2], kt)
                or max(kt or 0, p[2] or 0) > CIRCLE_MAX_KT):
            tr.close_run(p[0], p[3], p[4])
            return
        d = (course - p[1] + 540) % 360 - 180     # positive: heading increasing, clockwise seen from above
        rate = abs(d) / gap
        if CIRCLE_MIN_RATE <= rate <= CIRCLE_MAX_RATE:
            if tr.run != 0 and (tr.run > 0) != (d > 0):
                tr.close_run(p[0], p[3], p[4])
            if tr.run_t0 is None:
                tr.run_t0, tr.run_pos = p[0], (p[3], p[4])
            tr.run += d
            tr.run_s += gap
            tr.run_m += ((kt or 0) + (p[2] or 0)) / 2 * 0.514444 * gap
            if alt_m is not None:
                b = int(t // GAGGLE_BUCKET)
                if b not in tr.run_samples:
                    tr.run_samples[b] = pack(lat, lon, alt_m)
        else:
            tr.close_run(p[0], p[3], p[4])

    # --- 3. parked --------------------------------------------------------------------

    def park(self, address, label, category, t, lat, lon, alt_m):
        key = (address, label)
        r = self.parked_runs.get(key)
        if r is not None:
            if t <= r.t1:
                return
            la0, la1, lo0, lo1 = min(r.la0, lat), max(r.la1, lat), min(r.lo0, lon), max(r.lo1, lon)
            if (t - r.t1 > sources.SESSION_BREAK or category != r.cat or
                    ((la0, la1, lo0, lo1) != (r.la0, r.la1, r.lo0, r.lo1)
                     and dist(la0, lo0, la1, lo1) >= PARKED_SPREAD_M)):
                self.close_parked(key, r)
                r = None
            else:
                r.la0, r.la1, r.lo0, r.lo1 = la0, la1, lo0, lo1
                r.t1 = t
                r.packets += 1
        if r is None:
            r = self.parked_runs[key] = ParkedRun(category, t, lat, lon)
        if alt_m is not None and t - r.agl_t >= PARKED_AGL_EVERY:
            ground = self.terrain.elevation(lat, lon)
            if ground is not None:
                r.agl.append(alt_m - ground)
                r.agl_t = t

    def close_parked(self, key, r):
        if r.t1 - r.t0 >= PARKED_MIN_S and r.agl:
            agl = statistics.median(r.agl)
            if PARKED_AGL[0] <= agl <= PARKED_AGL[1]:
                p = self.parked[(key[1], r.cat)]
                p[0].add(key[0])
                p[1] += r.t1 - r.t0
                p[2] += r.packets

    # --- 4. drones ----------------------------------------------------------------------

    def drone_fix(self, address, t, lat, lon, alt_m):
        s = self.drone_session.get(address)
        if s is None or t - s[0] > sources.SESSION_BREAK:
            if s is not None:
                self.drone_extent[address] = max(self.drone_extent.get(address, 0), s[3])
            s = self.drone_session[address] = [t, lat, lon, 0.0]
        else:
            s[0] = max(s[0], t)
            s[3] = max(s[3], dist(s[1], s[2], lat, lon))
        self.drone_extent.setdefault(address, 0.0)
        self.last_drone_t = max(self.last_drone_t, t)
        self.drone_active[(int(lat // ACTIVE_DEG), int(lon // ACTIVE_DEG))] = t
        if alt_m is None:
            return
        cell = (int(lat // CELL_LAT), int(lon // CELL_LON))
        self.drone_index[cell].append((t, lat, lon, alt_m, address))
        self.prune(self.drone_index[cell], t)
        for dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 0), (0, 1), (1, -1), (1, 0), (1, 1)):
            q = self.crewed_index.get((cell[0] + dc[0], cell[1] + dc[1]))
            if q:
                for ct, clat, clon, calt, caddr, ccat in q:
                    self.match(address, t, lat, lon, alt_m, caddr, ccat, ct, clat, clon, calt)

    def crewed_fix(self, address, category, t, lat, lon, alt_m):
        if t - self.swept_at >= SWEEP_EVERY:
            self.sweep(t)
        c1 = (int(lat // ACTIVE_DEG), int(lon // ACTIVE_DEG))
        near = False
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                last = self.drone_active.get((c1[0] + a, c1[1] + b))
                if last is not None and t - last <= ENCOUNTER_ACTIVE:
                    near = True
        if not near:
            return
        cell = (int(lat // CELL_LAT), int(lon // CELL_LON))
        q = self.crewed_index[cell]
        q.append((t, lat, lon, alt_m, address, category))
        self.prune(q, t)
        for dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 0), (0, 1), (1, -1), (1, 0), (1, 1)):
            dq = self.drone_index.get((cell[0] + dc[0], cell[1] + dc[1]))
            if dq:
                for dt_, dlat, dlon, dalt, daddr in dq:
                    self.match(daddr, dt_, dlat, dlon, dalt, address, category, t, lat, lon, alt_m)

    def sweep(self, t):
        """Drop what no longer can match from both indexes. A cell is pruned on
        its own appends as well, but one an aircraft has left keeps its fixes
        until this sweep: before it, a day of drones kept 280,000 fixes an hour."""
        self.swept_at = t
        for index in (self.crewed_index, self.drone_index):
            for cell in list(index):
                q = index[cell]
                self.prune(q, t)
                if not q:
                    del index[cell]
        for c in [c for c, last in self.drone_active.items() if t - last > ENCOUNTER_ACTIVE]:
            del self.drone_active[c]

    @staticmethod
    def prune(q, t):
        while q and q[0][0] < t - ENCOUNTER_WINDOW:
            q.popleft()

    def match(self, drone, dt_, dlat, dlon, dalt, other, ocat, ot, olat, olon, oalt):
        if drone == other or abs(dt_ - ot) > ENCOUNTER_S or abs(dalt - oalt) > ENCOUNTER_VERT_M:
            return
        d = dist(dlat, dlon, olat, olon)
        if d > ENCOUNTER_M:
            return
        key = (drone, other)
        e = self.encounters_open.get(key)
        start = min(dt_, ot)
        if e is not None and start - e[0] <= ENCOUNTER_EVERY:
            e[1] = min(e[1], d)
            return
        if e is not None:
            self.encounters.append((drone, other, e[2], e[1]))
        self.encounters_open[key] = [start, d, ocat]

    # --- 5. quality: receivers ------------------------------------------------------------

    def receiver_beacon(self, name, body):
        if body[8:10] == "00" and body[17:20] == "000":
            return                              # no position yet
        m = sources._position.match(body)
        if not m:
            return
        g = m.groups()
        lat = int(g[3]) + float(g[4]) / 60
        lon = int(g[7]) + float(g[8]) / 60
        if g[5] == "S":
            lat = -lat
        if g[9] == "W":
            lon = -lon
        r = self.rx_pos.get(name)
        if r is None:
            r = self.rx_pos[name] = [lat, lon, 0, 0, 0.0]
        d = dist(r[0], r[1], lat, lon)
        r[2] += 1
        if d > RECEIVER_MOVED_M:
            r[3] += 1
        r[4] = max(r[4], d)
        a = sources._alt.search(body)
        if a and len(self.rx_alt[name]) < 400:
            ground = self.terrain.elevation(lat, lon)
            if ground is not None:
                self.rx_alt[name].append(max(-32000, min(32000, round(int(a.group(1)) * 0.3048 - ground))))

    def relay(self, epoch, address, sod, rx, alt_m):
        """Group the receptions of one FLARM fix by several receivers."""
        k = (address, sod)
        g = self.relay_groups.get(k)
        if g is None:
            self.relay_groups[k] = (epoch, {rx: alt_m})
            self.relay_order.append(k)
        else:
            g[1].setdefault(rx, alt_m)
        while self.relay_order:
            k0 = self.relay_order[0]
            g0 = self.relay_groups[k0]
            if epoch - g0[0] <= RELAY_GROUP_S:
                break
            self.relay_order.popleft()
            del self.relay_groups[k0]
            self.relay_pair(g0[1])

    def relay_pair(self, heard):
        if len(heard) < 2:
            return
        items = sorted(heard.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                diff = max(-32000, min(32000, round(items[i][1] - items[j][1])))
                self.relay_pairs[(items[i][0], items[j][0])].append(diff)

    # --- end of the day -----------------------------------------------------------------------

    def finish(self):
        while self.relay_order:
            k = self.relay_order.popleft()
            self.relay_pair(self.relay_groups.pop(k)[1])
        for key, r in list(self.parked_runs.items()):
            self.close_parked(key, r)
        for address, s in self.drone_session.items():
            self.drone_extent[address] = max(self.drone_extent.get(address, 0), s[3])
        for key, e in self.encounters_open.items():
            self.encounters.append((key[0], key[1], e[2], e[1]))
        for tr in self.circles.values():
            tr.finish()
        self.crewed.finish()
        for address, tr in self.powered.items():
            for ref, run in tr.runs.items():
                self.close_level(address, self.addr_major.get(address), ref, run)
        for address, f in self.flight.items():
            self.end_flight(address, f)
        self.flight = {}

    def evidence(self):
        """address with a category 13 -> one of the classes of EVIDENCE_NAMES.

        Evaluated once the whole day is in (METHOD.md 10.1). Remote ID is a
        confirmed drone on its own. For the rest, an address whose majority
        is 13 on none of its systems is stray; then crewed evidence: an ADS-B
        emitter category of a crewed aircraft, a crewed type in the device
        database together with at least one thermal flown that day (PATTERNS.md 2),
        or a thermal that gains CLIMB_M on a day covering more than
        CLIMB_EXTENT_M, since a drone turns but does not climb in circles
        across a cross-country. Then a drone is confirmed by the UAV emitter
        category or by the database's drone type; anything else, including
        conflicts the flight does not settle, is uncertain.
        """
        if self._evidence is not None:
            return self._evidence
        thermals = collections.Counter()
        climbs = set()
        self.max_climb = collections.Counter()
        for (address, _), tr in self.circles.items():
            if address in self.declared:
                for x in tr.thermals:
                    if not self.d0 <= x[1] < self.d1:
                        continue
                    thermals[address] += 1
                    gain = x[9]
                    self.max_climb[address] = max(self.max_climb[address], gain)
                    if gain >= CLIMB_M:
                        climbs.add(address)
        out = {}
        remote_id = sources.source_info(REMOTE_ID)[0]
        for address in self.declared:
            if remote_id in self.declared[address]:
                # Remote ID is broadcast by drones only, by regulation: a drone
                # whatever else is known about it (from 7 October 2026).
                out[address] = CONFIRMED
                continue
            if address not in self.major13:
                out[address] = STRAY            # 13 was never the day's majority on any of its systems
                continue
            emitted = self.emitters.get(address, set())
            ddb = self.ddb_types.get(address)
            if emitted & CREWED_EMITTERS:
                out[address] = CREWED_ADSB
            elif ddb in DDB_CREWED and thermals[address]:
                out[address] = CREWED_DDB
            elif address in climbs and self.drone_extent.get(address, 0) > CLIMB_EXTENT_M:
                out[address] = CREWED_CLIMB
            elif UAV_EMITTER in emitted or ddb == DDB_DRONE:
                out[address] = CONFIRMED
            else:
                out[address] = UNCERTAIN
        self._evidence = out
        return out

    def systems_of(self, address):
        """The systems an address was heard on, or the platforms if only they relayed it."""
        own = self.addr_systems.get(address)
        if own:
            return " + ".join(sorted(own))[:255]
        relayed = self.addr_platforms.get(address)
        if relayed:
            return (" + ".join(sorted(relayed)) + " (platform)")[:255]
        return "unknown"

    def rows(self):
        """Every table's rows for this day, as {family: {table: (columns, rows)}}.

        Two families (from 7 October 2026): "conspicuity" (METHOD.md) and
        "patterns" (PATTERNS.md). Each is computed apart, so an error in one
        is recorded in self.failed and leaves the other's rows intact; the
        patterns family is computed first, since the crewed encounters report
        the tows the launches find.
        """
        out = {"conspicuity": {}, "patterns": {}}
        for family, compute in (("patterns", self.rows_patterns), ("conspicuity", self.rows_conspicuity)):
            if family in self.failed:
                continue                        # already failed during the pass
            try:
                out[family] = compute()
            except Exception as e:              # one family must not cost the other its day
                logger.exception(f"The {family} family failed while its rows were computed")
                self.failed[family] = f"rows: {type(e).__name__}: {e}"[:200]
                out[family] = {}
        return out

    def rows_patterns(self):
        """PATTERNS.md: hours, circling, thermals and the rest, launches."""
        day = self.day.isoformat()
        out = {}
        # 1. hours
        # Drone time by the day's evidence: a crewed aircraft declared a drone
        # leaves the drone row and is filed as unknown, since neither its
        # transponder nor the database names an OGN category.
        ev = self.evidence()
        dh = collections.defaultdict(lambda: [0.0, set()])
        def main_other(address):
            c = self.addr_cats.get(address, collections.Counter())
            rest = [(n, k) for k, n in c.items() if k != DRONE]
            return max(rest)[1] if rest else sources.UNKNOWN_CATEGORY
        for (ln, hour, address), sec in self.drone_hours.items():
            e = ev.get(address, UNCERTAIN)
            cat = (sources.UNKNOWN_CATEGORY if e in CREWED_CLASSES
                   else main_other(address) if e == STRAY else DRONE)
            if cat in GROUND_CATEGORIES:
                continue                        # PATTERNS.md 1 leaves ground support and static objects out
            x = dh[(ln, cat, hour)]
            x[0] += sec
            x[1].add(address)
        merged = {}
        for k, sec in self.hours_air.items():
            merged[k] = [sec, set(self.hours_ac[k])]
        for k, (sec, who) in dh.items():
            m = merged.setdefault(k, [0.0, set()])
            m[0] += sec
            m[1] |= who
        rows = []
        for (ln, cat, hour), (sec, who) in merged.items():
            local_date = datetime.date(1970, 1, 1) + datetime.timedelta(days=ln)
            rows.append((day, local_date.isoformat(), cat, hour, round(sec, 1), len(who)))
        out["daily_hours_solar"] = (("day", "local_date", "category", "solar_hour", "air_seconds", "aircraft"), rows)

        # 2. circling: one system per address, the one with most thermals that day
        best = {}
        for (address, tocall), tr in self.circles.items():
            if tr.cat not in CIRCLE_CATEGORIES:
                continue                        # tracked only as evidence for declared drones
            th = [x for x in tr.thermals if self.d0 <= x[1] < self.d1]
            if th and (address not in best or len(th) > len(best[address][1])):
                best[address] = (tr.cat, th)
        per_cat = collections.defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0, 0.0, 0])
        pilots = []
        thermals = []
        for address, (cat, th) in best.items():
            c = per_cat[cat]
            right = sum(1 for x in th if x[0] > 0)
            c[0] += right
            c[1] += len(th) - right
            for x in th:
                i = 0 if x[0] > 0 else 1
                c[2 + i] += x[5]
                c[4 + i] += x[6]
                thermals.append((x[1], x[2], address, cat, x[0], x[7]))
            c[6] += 1
            pilots.append((day, address, cat, right, len(th) - right))
        out["daily_circling"] = (("day", "category", "thermals_right", "thermals_left", "degrees_right", "degrees_left",
                                  "seconds_right", "seconds_left", "aircraft"),
                                 [(day, cat) + tuple(round(v, 1) if isinstance(v, float) else v for v in c)
                                  for cat, c in sorted(per_cat.items())])
        out["daily_circling_pilot"] = (("day", "address", "category", "right_hand", "left_hand"), pilots)
        out["daily_gaggles"] = (("day", "category_a", "category_b", "same_side", "opposite_side"), self.gaggles(day, thermals))
        out["daily_thermal_sites"] = (("day", "kind", "lat_idx", "lon_idx", "site", "thermals"),
                                      [(day,) + k + (v,) for k, v in sorted(self.thermal_sites(best).items())])
        out["daily_mixed_thermals"] = (("day", "kind_a", "kind_b", "vsep_band", "thermals"),
                                       [(day,) + k + (n,) for k, n in sorted(self.mixed.items())])
        # 7. launches (PATTERNS.md 7): before the flights, which carry their launch
        # method, and before the encounters, which report the tows among them
        launches, tug_tows = self.launch_rows(day)
        out["daily_launches"] = (("day", "method", "towed_kind", "terrain", "lat_idx", "lon_idx", "height_band",
                                  "duration_band", "launches"), launches)
        out["daily_tug_tows"] = (("day", "tows_band", "tugs"), tug_tows)
        out.update(self.pattern_rows(day, best))

        return out

    def rows_conspicuity(self):
        """METHOD.md: parked aircraft, drones, crewed encounters, data quality."""
        day = self.day.isoformat()
        ev = self.evidence()
        out = {}
        # 3. parked
        out["daily_parked"] = (("day", "system_name", "category", "aircraft", "seconds", "packets"),
                               [(day, label, cat, len(v[0]), round(v[1], 1), v[2])
                                for (label, cat), v in sorted(self.parked.items())])

        # 4. drones
        # Crewed evidence takes an address out of every drone measure; the
        # others carry their evidence (confirmed or uncertain) as a dimension.
        def drone_class(address):
            e = ev.get(address, UNCERTAIN)
            return None if e in SET_ASIDE else e
        fine = collections.defaultdict(lambda: [0.0, set()])
        cells = collections.defaultdict(lambda: [0.0, set()])
        classes = collections.defaultdict(lambda: [set(), 0.0])
        for (la, lo, hb, sb, address), sec in self.drone_air.items():
            classes[ev.get(address, UNCERTAIN)][1] += sec
            e = drone_class(address)
            if e is None:
                continue
            f = fine[(e, la, lo, hb, sb, self.systems_of(address))]
            f[0] += sec
            f[1].add(address)
            c = cells[(e, la, lo)]
            c[0] += sec
            c[1].add(address)
        for address in self.declared:
            classes[ev.get(address, UNCERTAIN)][0].add(address)
        out["daily_drones"] = (("day", "evidence", "lat_idx", "lon_idx", "height_band", "speed_band", "systems",
                                "air_seconds", "aircraft"),
                               [(day,) + k + (round(v[0], 1), len(v[1])) for k, v in sorted(fine.items())])
        out["daily_drone_cells"] = (("day", "evidence", "lat_idx", "lon_idx", "air_seconds", "aircraft"),
                                    [(day,) + k + (round(v[0], 1), len(v[1])) for k, v in sorted(cells.items())])
        ext = collections.Counter()
        for address, d in self.drone_extent.items():
            e = drone_class(address)
            if e is not None:
                ext[(e, band(d, DRONE_EXTENT_EDGES))] += 1
        out["daily_drone_extent"] = (("day", "evidence", "extent_band", "drones"),
                                     [(day,) + k + (n,) for k, n in sorted(ext.items())])
        enc = collections.Counter()
        for drone, other, ocat, d in self.encounters:
            e = drone_class(drone)
            if e is None:
                continue                        # the "drone" was a crewed aircraft
            ds, os_ = self.addr_systems.get(drone, set()), self.addr_systems.get(other, set())
            enc[(e, self.systems_of(drone), ocat, self.systems_of(other), band(d, ENCOUNTER_BANDS),
                 int(bool(ds & os_)))] += 1
        out["daily_drone_encounters"] = (("day", "evidence", "drone_systems", "other_category", "other_systems",
                                          "distance_band", "shared_system", "encounters"),
                                         [(day,) + k + (n,) for k, n in sorted(enc.items())])
        out["daily_drone_classes"] = (("day", "evidence", "addresses", "air_seconds"),
                                      [(day, e, len(v[0]), round(v[1], 1)) for e, v in sorted(classes.items())])
        self.encounters_all = len(self.encounters)

        # 6. crewed aircraft of different kinds (METHOD.md 10.2)
        out["daily_crewed_encounters"] = (("day", "kind_a", "kind_b", "threshold", "distance_band", "closing_band",
                                           "systems_a", "systems_b", "shares_radio", "shares_any", "encounters"),
                                          self.crewed_rows(day))

        # 5. quality
        out["daily_quality"] = (("day", "scope", "name", "check_name", "count", "total", "value"), self.quality(day))
        return out

    def pattern_rows(self, day, best):
        """Thermals, height by hour, circling time, flights and wave (PATTERNS.md)."""
        ev = self.evidence()

        def kind_of(cat, address=None):
            if cat == DRONE:
                e = ev.get(address, UNCERTAIN)
                return {CONFIRMED: "drone_confirmed", UNCERTAIN: "drone_uncertain"}.get(e, "other")
            # Paragliders and hang gliders apart (7 October 2026); the
            # statistic adds them up again as free_flight.
            return FREE_FLIGHT_KIND.get(cat) or PATTERN_KIND.get(cat, "other")

        def solar_hour(t, lon):
            return int((t + lon * SOLAR_SECONDS_PER_DEGREE) % 86400 // 3600)

        therm = collections.defaultdict(lambda: [0, 0.0, 0, 0.0])     # -> thermals, climb sum, climbs, radius sum
        circ = collections.Counter()
        for address, (cat, th) in best.items():
            kind = kind_of(cat)
            tkind = THERMAL_KIND.get(cat, kind)
            for x in th:
                hour = solar_hour((x[1] + x[2]) / 2, x[4])
                terrain = self.terrain_of(x[3], x[4])
                circ[(kind, terrain, hour)] += x[6]
                # The climb of the thermal: its largest climb, from a low point to the
                # highest after it (8 October 2026; first to last sample before).
                climb = x[9] / x[10] if x[10] >= CLIMB_MIN_S else None
                # Radius = distance flown along the circles / angle turned. Over whole
                # turns the wind adds to the ground speed on one side what it takes
                # on the other, so the mean speed is close to the airspeed.
                radius = x[8] / math.radians(x[5]) if x[5] else None
                # Cells of a quarter of a degree, about 28 by 19 km in the Alps,
                # so that a thermal place can be named (from 7 October 2026).
                r = therm[(tkind, terrain, hour, math.floor(x[3] * THERMAL_CELLS_PER_DEG),
                           math.floor(x[4] * THERMAL_CELLS_PER_DEG), band(climb, CLIMB_EDGES),
                           band(radius, RADIUS_EDGES))]
                r[0] += 1
                if climb is not None:
                    r[1] += climb
                    r[2] += 1
                if radius is not None:
                    r[3] += radius
        out = {"daily_thermals": (("day", "kind", "terrain", "solar_hour", "lat_idx", "lon_idx", "climb_band",
                                   "radius_band",
                                   "thermals", "climb_sum", "climbs", "radius_sum"),
                                  [(day,) + k + (v[0], round(v[1], 2), v[2], round(v[3], 1))
                                   for k, v in sorted(therm.items())])}
        agl = collections.Counter()
        for (cat, hour, b, terrain), sec in self.agl_time.items():
            agl[(kind_of(cat), terrain, hour, b)] += sec
        for (hour, b, address, terrain), sec in self.drone_agl.items():
            agl[(kind_of(DRONE, address), terrain, hour, b)] += sec
        out["daily_agl_hours"] = (("day", "kind", "terrain", "solar_hour", "agl_band", "air_seconds"),
                                  [(day,) + k + (round(v, 1),) for k, v in sorted(agl.items())])
        # The share circling is taken over the airborne time of aircraft that
        # flew at least one thermal that day: a thermal is seen only with fixes
        # at most 5 s apart, and time heard on FANET or a slow app alone would
        # count as gliding (over all airborne time, 1.6% for free flight on
        # 6 October 2026).
        air = collections.Counter()
        circled = {a: kind_of(cat) for a, (cat, th) in best.items()}
        for (address, hour, terrain), sec in self.addr_hour_air.items():
            if address in circled:
                air[(circled[address], terrain, hour)] += sec
        out["daily_circling_time"] = (("day", "kind", "terrain", "solar_hour", "circling_seconds", "air_seconds"),
                                      [(day, k, tr, h, round(circ[(k, tr, h)], 1), round(air[(k, tr, h)], 1))
                                       for (k, tr, h) in sorted(set(circ) | set(air))])
        fl = collections.defaultdict(lambda: [0, 0.0, 0.0])
        for address, cat, t0, lon0, sec, maxd, path, lat0, *_ in self.flights:
            if not self.d0 <= t0 < self.d1 or sec < FLIGHT_MIN_S:
                continue
            launch = self.flight_launch(address, t0) if cat == 1 else ""
            f = fl[(kind_of(cat, address), self.terrain_of(lat0, lon0), launch, solar_hour(t0, lon0),
                    band(sec / 60, FLIGHT_MIN_EDGES),
                    band(maxd / 1000, FLIGHT_EXTENT_KM), band(path / 1000, FLIGHT_PATH_KM))]
            f[0] += 1
            f[1] += sec
            f[2] += path
        cls_rows, routes = self.flight_classes(kind_of)
        out["daily_flight_classes"] = (("day", "kind", "terrain", "class", "landing", "distance_band", "path_band",
                                        "flights"),
                                       [(day,) + k + (n,) for k, n in sorted(cls_rows.items())])
        out["daily_routes"] = (("day", "airfield_a", "airfield_b", "address", "flights"),
                               [(day,) + k + (n,) for k, n in sorted(routes.items())])
        out["daily_flights"] = (("day", "kind", "terrain", "launch", "start_hour", "duration_band", "extent_band", "path_band",
                                 "flights", "seconds", "path_m"),
                                [(day,) + k + (v[0], round(v[1], 1), round(v[2])) for k, v in sorted(fl.items())])
        wave = collections.Counter((math.floor(la * THERMAL_CELLS_PER_DEG), math.floor(lo * THERMAL_CELLS_PER_DEG))
                                   for _, t0, la, lo, *_ in self.wave_climbs())
        out["daily_wave"] = (("day", "lat_idx", "lon_idx", "climbs"),
                             [(day,) + k + (n,) for k, n in sorted(wave.items())])

        # Powered aircraft and helicopters (sections 9 to 11)
        cruise = collections.defaultdict(lambda: [0, 0.0, set()])
        for (address, cat, ref, ab, sb), n in self.levels.items():
            emitted = self.emitters.get(address, set())
            em = next((e for e in EMITTERS_OF_INTEREST if e in emitted), "other" if emitted else "none")
            kind = "helicopter" if cat == HELICOPTER else "powered"
            c = cruise[(kind, em, ref, ab, sb)]
            c[0] += n
            c[1] += self.level_s[(address, cat, ref, ab, sb)]
            c[2].add(address)
        out["daily_cruise"] = (("day", "kind", "emitter", "altitude_ref", "altitude_band", "speed_band",
                                "segments", "seconds", "aircraft"),
                               [(day,) + k + (v[0], round(v[1], 1), len(v[2])) for k, v in sorted(cruise.items())])
        out["daily_helicopter_night"] = (("day", "lat_idx", "lon_idx", "night", "agl_band", "air_seconds", "aircraft"),
                                         [(day,) + k + (round(v[0], 1), len(v[1])) for k, v in sorted(self.heli.items())])
        tow_s = collections.Counter()
        for towed, tug, cat, t0, t1, la, lo, rel in self.crewed.tows:
            tow_s[tug] += t1 - t0
        tugs = collections.defaultdict(lambda: [0, 0.0, 0.0])
        for tug, sec in tow_s.items():
            air = max(self.addr_air.get(tug, 0.0), sec)
            r = tugs[band(sec / air if air else None, TUG_SHARE_EDGES)]
            r[0] += 1
            r[1] += sec
            r[2] += air
        out["daily_tug_time"] = (("day", "share_band", "tugs", "tow_seconds", "air_seconds"),
                                 [(day, b, v[0], round(v[1], 1), round(v[2], 1)) for b, v in sorted(tugs.items())])
        return out

    def crewed_rows(self, day):
        """The rows of daily_crewed_encounters, and for the log the counts before the formation rules."""
        ce = self.crewed
        radio = {label for tc, (label, kind) in sources.SOURCES.items()
                 if kind in ("adsl", "flarm", "fanet", "adsb", "radio")}
        rows = collections.Counter()
        self.crewed_summary = collections.Counter()
        for (pair, name), t0, d, rel, kinds in ce.done:
            together = pair in ce.formation or pair in ce.escort
            self.crewed_summary[(kinds, name, "all")] += 1
            if pair in self.tow_pairs:
                self.crewed_summary[(kinds, name, "tow pair")] += 1
            if pair in ce.formation:
                self.crewed_summary[(kinds, name, "formation")] += 1
            if pair in ce.escort and pair not in ce.formation:
                self.crewed_summary[(kinds, name, "escort only")] += 1
            if together:
                continue
            a, b = pair
            if CrewedEncounters.KIND[self.addr_major[a]] != kinds[0]:
                a, b = b, a
            sa, sb = self.addr_systems.get(a, set()), self.addr_systems.get(b, set())
            dband = band(d, CrewedEncounters.DISTANCE_EDGES[name])
            cband = band(rel, CrewedEncounters.CLOSING_EDGES)
            rows[(kinds[0], kinds[1], name, dband, cband, self.systems_of(a), self.systems_of(b),
                  int(bool(sa & sb & radio)), int(bool(sa & sb)))] += 1
        self.crewed_pairs = (len(ce.formation), len(ce.escort - ce.formation), len(ce.formation - ce.escort))
        return [(day,) + k + (n,) for k, n in sorted(rows.items())]

    def gaggles(self, day, thermals):
        """Pairs of thermals of two aircraft flown together, same side or opposite."""
        thermals.sort()
        pairs = collections.defaultdict(lambda: [0, 0])
        self.mixed = collections.Counter()      # (kind a, kind b, vertical separation band) -> thermals shared
        for i, (t0, t1, addr, cat, side, samples) in enumerate(thermals):
            for u0, u1, addr2, cat2, side2, samples2 in thermals[i + 1:]:
                if u0 > t1:
                    break
                if addr2 == addr:
                    continue
                close = 0
                dz = []
                small, large = (samples, samples2) if len(samples) <= len(samples2) else (samples2, samples)
                for b, x in small.items():
                    y = large.get(b)
                    if y is None:
                        continue
                    lat, lon, alt = unpack(x)
                    lat2, lon2, alt2 = unpack(y)
                    if abs(alt - alt2) <= GAGGLE_VERT_M and dist(lat, lon, lat2, lon2) <= GAGGLE_M:
                        close += 1
                        dz.append(abs(alt - alt2))
                if close * GAGGLE_BUCKET >= GAGGLE_MIN_S:
                    k = (min(cat, cat2), max(cat, cat2))
                    pairs[k][0 if side == side2 else 1] += 1
                    ka, kb = sorted((PATTERN_KIND.get(cat, "other"), PATTERN_KIND.get(cat2, "other")))
                    if ka != kb:
                        self.mixed[(ka, kb, band(statistics.median(dz), VSEP_EDGES))] += 1
        return [(day, a, b, v[0], v[1]) for (a, b), v in sorted(pairs.items())]

    def quality(self, day):
        rows = []
        cat_change = collections.Counter()
        ground_mode = collections.Counter()
        with_cat = collections.Counter()
        for (label, address), c in self.categories.items():
            cats = {k for k, n in c.items() if n >= 2}   # a single corrupt packet does not make a change
            if not cats:
                continue
            with_cat[label] += 1
            if label == "FANET" and 15 in cats and len(cats - {15}) == 1 and cats - {15} <= set(FREE_FLIGHT):
                ground_mode[label] += 1                    # a landed pilot's instrument in ground-tracking mode
            elif len(cats) > 1:
                cat_change[label] += 1
        ev = self.evidence()
        declared = collections.Counter()
        dclass = collections.Counter()
        for address, labels in self.declared.items():
            conflict = ev[address] == UNCERTAIN and self.ddb_types.get(address) in DDB_CREWED
            for label in labels:
                declared[label] += 1
                dclass[(label, ev[address])] += 1
                if conflict:
                    dclass[(label, "ddb_conflict")] += 1     # a crewed database type, and no thermal to settle it
        for label in sorted(declared):
            crewed = sum(dclass[(label, c)] for c in CREWED_CLASSES)
            for name, n in (("declared_drone_stray", dclass[(label, STRAY)]),
                            ("declared_drone_crewed", crewed),
                            ("declared_drone_crewed_adsb_emitter", dclass[(label, CREWED_ADSB)]),
                            ("declared_drone_crewed_ddb_thermal", dclass[(label, CREWED_DDB)]),
                            ("declared_drone_crewed_climb_extent", dclass[(label, CREWED_CLIMB)]),
                            ("declared_drone_uncertain", dclass[(label, UNCERTAIN)]),
                            ("declared_drone_uncertain_ddb_crewed", dclass[(label, "ddb_conflict")]),
                            ("declared_drone_confirmed", dclass[(label, CONFIRMED)])):
                rows.append((day, "system", label, name, n, declared[label], None))
        addresses = collections.Counter(label for label, _ in self.addresses)
        jumped = collections.Counter(label for (label, _), n in self.jumps.items() if n >= JUMPS_PER_ADDRESS)
        for label, q in sorted(self.q.items()):
            checks = (
                ("late_60s", q["late_60s"], q["fixes"]),
                ("ahead_5s", q["ahead_5s"], q["fixes"]),
                ("altitude_sentinel", q["altitude_sentinel"], q["with_altitude"]),
                ("altitude_zero", q["altitude_zero"], q["with_altitude"]),
                ("position_0_0", q["position_0_0"], q["positions"]),
                ("no_timestamp", q["no_timestamp"], q["positions"]),
                ("heading_over_360", q["heading_over_360"], q["with_course"]),
                ("category_stray", q["category_stray"], q["categorised"]),
                ("free_flight_implausible", q["free_flight_implausible"], q["free_flight_fixes"]),
                ("address_jumps", jumped[label], addresses[label]),
                ("category_change", cat_change[label], with_cat[label]),
                ("fanet_ground_mode", ground_mode[label], with_cat[label]),
            )
            for name, n, total in checks:
                if total:
                    rows.append((day, "system", label, name, n, total, None))

        # Receivers: one row per receiver flagged, and one per check with the
        # number flagged among those judged (name '*').
        judged = collections.Counter()
        flagged = collections.Counter()
        for name, (_, _, beacons, far, maxd) in self.rx_pos.items():
            judged["receiver_moved"] += 1
            if maxd > RECEIVER_MOVED_M:
                flagged["receiver_moved"] += 1
                rows.append((day, "receiver", name, "receiver_moved", far, beacons, round(maxd)))
        for name, offs in self.rx_alt.items():
            if not offs:
                continue
            judged["receiver_altitude"] += 1
            med = statistics.median(offs)
            if abs(med) > RECEIVER_ALTITUDE_M:
                flagged["receiver_altitude"] += 1
                rows.append((day, "receiver", name, "receiver_altitude",
                             sum(1 for x in offs if abs(x) > RECEIVER_ALTITUDE_M), len(offs), round(med)))
        for name, n in self.rx_relay.items():
            if n < RELAY_LATE_MIN:
                continue
            judged["relay_late_60s"] += 1
            late = self.rx_late[name]
            if late / n > RELAY_LATE_SHARE:
                flagged["relay_late_60s"] += 1
                rows.append((day, "receiver", name, "relay_late_60s", late, n, round(late / n, 4)))
        for name, n in self.rx_cat.items():
            if n < RELAY_STRAY_MIN:
                continue
            judged["relay_category_stray"] += 1
            k = self.rx_cat_stray[name]
            if k / n >= RELAY_STRAY_SHARE:
                flagged["relay_category_stray"] += 1
                rows.append((day, "receiver", name, "relay_category_stray", k, n, round(k / n, 4)))
        for name, (med, n) in self.relay_offsets().items():
            judged["relay_altitude_offset"] += 1
            if abs(med) > RELAY_OFFSET_M:
                flagged["relay_altitude_offset"] += 1
                rows.append((day, "receiver", name, "relay_altitude_offset", n, n, med))
        for check, n in sorted(judged.items()):
            rows.append((day, "receiver", "*", check, flagged[check], n, None))
        return rows

    def relay_offsets(self):
        """Each receiver's altitude offset against the others hearing the same FLARM fixes.

        First, every receiver's median difference against all its partners.
        A receiver heard mostly beside one faulty neighbour would then show
        that neighbour's error with the sign reversed, so the figure kept is
        the median against the partners whose own first figure is within
        RELAY_OFFSET_M: (median in m, shared fixes), for receivers with at
        least RELAY_MIN_SHARED such fixes.
        """
        pair_med = {}
        for (a, b), diffs in self.relay_pairs.items():
            v = sorted(diffs)
            pair_med[(a, b)] = (v[len(v) // 2], len(v))
        by_rx = collections.defaultdict(list)    # receiver -> [(partner, median of rx - partner, n)]
        for (a, b), (m, n) in pair_med.items():
            by_rx[a].append((b, m, n))
            by_rx[b].append((a, -m, n))
        first = {r: weighted_median([(m, n) for _, m, n in v]) for r, v in by_rx.items()}
        out = {}
        for r, v in by_rx.items():
            sane = [(m, n) for p, m, n in v if abs(first.get(p) or 0) <= RELAY_OFFSET_M]
            n = sum(x[1] for x in sane)
            if n >= RELAY_MIN_SHARED:
                out[r] = (weighted_median(sane), n)
        return out


def snapshot_endpoints(family):
    """The endpoints whose monthly snapshot belongs to a family."""
    every = sources.SourceTracker.SNAPSHOT_ENDPOINTS
    if family == "patterns":
        return tuple(PATTERN_SNAPSHOTS)
    if family == "conspicuity":
        return tuple(e for e in every if e not in PATTERN_SNAPSHOTS)
    return tuple(every)


def connect_db(autocommit=False):
    """As app.get_db_connection, without importing app (which starts the listener).

    Without DB_USER in the environment (a test outside the service checkout,
    which has no .env) the client's own ~/.my.cnf is used; it is only ever
    read from in that case, since a dry run writes nothing.
    """
    if os.getenv("DB_USER"):
        return pymysql.connect(host="localhost", user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
                               database="ads_l", autocommit=autocommit)
    return pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"), database="ads_l",
                           autocommit=autocommit)


def git(*args):
    try:
        return subprocess.run(["git", "-C", HERE] + list(args), capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def snapshot(month, dry_run, family="all"):
    """Store what every statistics endpoint publishes for `month` (METHOD.md, section 9).

    Taken the night the month's last day is computed. The JSON comes from
    the functions the endpoints call (sources.SourceTracker.snapshot_bodies),
    serialised with sorted keys, and is stored compressed with the METHOD.md
    commit and the code commit deployed at that moment. A rerun replaces it.
    """
    tracker = sources.SourceTracker(None, lambda: connect_db(autocommit=True))
    mine = snapshot_endpoints(family)
    bodies = {k: v for k, v in tracker.snapshot_bodies(month, mine).items()}
    method_commit = git("log", "-1", "--format=%H", "--", "METHOD.md")
    deployed = git("rev-parse", "HEAD")
    now = datetime.datetime.utcnow().replace(microsecond=0)
    rows = []
    for name, obj in bodies.items():
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
        rows.append((month, name, method_commit, deployed, now, len(text), zlib.compress(text.encode("utf-8"), 9)))
    missing = [e for e in mine if e not in bodies]
    if dry_run:
        print(f"\n== monthly_snapshot {month}: method {method_commit}, deployed {deployed}")
        for r in rows:
            print(f"   {r[1]:18s} {r[5]:>10,} bytes, {len(r[6]):>9,} compressed")
        if missing:
            print(f"   not taken (see the log): {', '.join(missing)}")
        return
    conn = connect_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM monthly_snapshot WHERE month = %s AND endpoint IN ({', '.join(['%s'] * len(mine))})",
                        (month,) + tuple(mine))
            cur.executemany("INSERT INTO monthly_snapshot (month, endpoint, method_commit, deployed_commit, "
                            "created_at, bytes, body) VALUES (%s, %s, %s, %s, %s, %s, %s)", rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    logger.info(f"Snapshot of {month}: {len(rows)} endpoints stored" +
                (f", not taken: {', '.join(missing)}" if missing else ""))


def write_family(day, family, tables):
    """Replace one family's rows of the day, in one transaction of its own."""
    conn = connect_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            for table in FAMILIES[family]:
                cur.execute(f"DELETE FROM {table} WHERE day = %s", (day.isoformat(),))
            for table in FAMILIES[family]:
                cols, rows = tables.get(table, ((), []))
                if rows:
                    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                                    rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_run(run_row, notes):
    """The day's nightly_runs row, with what failed, if anything, in notes."""
    conn = connect_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM nightly_runs WHERE day = %s", (run_row[0],))
            cur.execute("INSERT INTO nightly_runs (day, finished_at, hours_read, hours_missing, line_count, seconds, "
                        "notes) VALUES (%s, %s, %s, %s, %s, %s, %s)", run_row + (notes[:255],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def print_dry_run(tables, run_row):
    """The aggregates that would be written. daily_circling_pilot holds addresses
    and is shown as counts only."""
    print(f"day {run_row[0]}: hours read {run_row[2]}, missing {run_row[3] or 'none'}, "
          f"lines {run_row[4]:,}, {run_row[5]:.0f} s")
    for table, (cols, rows) in tables.items():
        print(f"\n== {table}: {len(rows)} rows")
        if table == "daily_hours_solar":
            by = collections.Counter()
            for r in rows:
                by[r[2]] += r[4]
            print("   hours by category: " + ", ".join(f"{c}: {v / 3600:.1f}" for c, v in sorted(by.items())))
        if table == "daily_routes":
            # Addresses: shown only as counts, and a route by name only with
            # ROUTE_MIN_AIRCRAFT distinct aircraft (on a single day here).
            pairs = collections.defaultdict(lambda: [0, set()])
            for r in rows:
                pairs[(r[1], r[2])][0] += r[4]
                pairs[(r[1], r[2])][1].add(r[3])
            shown = sorted(((len(v[1]), v[0], k) for k, v in pairs.items() if len(v[1]) >= ROUTE_MIN_AIRCRAFT),
                           reverse=True)
            print(f"   routes {len(pairs)}, flights {sum(v[0] for v in pairs.values())}; with "
                  f"{ROUTE_MIN_AIRCRAFT}+ aircraft: {len(shown)}")
            for n_ac, n_fl, (a, b) in shown[:15]:
                print(f"   {n_ac:3d} aircraft {n_fl:3d} flights  {a} - {b}")
            continue
        if table == "daily_circling_pilot":
            n = collections.Counter(min(r[3] + r[4], 10) for r in rows)
            print("   pilots by thermals (10 = 10 or more): " + ", ".join(f"{k}:{v}" for k, v in sorted(n.items())))
            continue
        print("   " + " | ".join(cols[1:]))
        shown = rows if table not in ("daily_hours_solar", "daily_drones") else rows[:40]
        for r in shown:
            print("   " + " | ".join(str(x) for x in r[1:]))
        if len(shown) < len(rows):
            print(f"   ... {len(rows) - len(shown)} more")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", help="UTC day, YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--dry-run", action="store_true", help="print the aggregates instead of writing them")
    ap.add_argument("--hours", help="only these hours of the day, e.g. 12-13 (for testing)")
    ap.add_argument("--snapshot", action="store_true",
                    help="also take the monthly snapshot of the day's month, even if it is not its last day")
    ap.add_argument("--snapshot-only", action="store_true", help="take the snapshot and skip the recording")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    load_dotenv(os.path.join(HERE, ".env"))
    raw_dir = os.path.expanduser(os.getenv("EC_RAW_DIR") or "")
    if not raw_dir or not os.path.isdir(raw_dir):
        logger.error("EC_RAW_DIR is not set or not a directory: nothing to read")
        return 1
    if args.day:
        day = datetime.date.fromisoformat(args.day)
    else:
        day = datetime.datetime.utcnow().date() - datetime.timedelta(days=1)
    month_ends = (day + datetime.timedelta(days=1)).day == 1
    if args.snapshot_only:
        snapshot(day.strftime("%Y-%m"), args.dry_run)
        return 0
    started = time.time()
    if os.getenv("NIGHTLY_TRACE"):
        import tracemalloc
        tracemalloc.start()
    n = Nightly(day, load_ddb())
    hours = range(24)
    if args.hours:
        first, _, last = args.hours.partition("-")
        hours = range(int(first), int(last or first) + 1)
    read, missing = n.run(raw_dir, hours)
    if not read:
        logger.error(f"No recording for {day}")
        return 1
    families = n.rows()
    tables = {**families["conspicuity"], **families["patterns"]}
    # daily_quality.check_name is varchar(64) (widened on 7 October 2026 after a
    # 35-character name made a recompute fail): fail here, in a dry run too.
    long = {r[3] for r in tables.get("daily_quality", ((), []))[1] if len(r[3]) > 64}
    if long:
        raise ValueError(f"check names longer than daily_quality.check_name allows: {sorted(long)}")
    if CircleTrack.outcomes:
        logger.info("Circling episodes by category and outcome (every system): "
                    + ", ".join(f"{c}/{o}: {v}" for (c, o), v in sorted(CircleTrack.outcomes.items())))
    if getattr(n, "first_climb_starts", None):
        logger.info(f"Flights classed from the top of their first climb: {dict(n.first_climb_starts)}")
    if "daily_drone_encounters" in tables:
        kept = sum(r[-1] for r in tables["daily_drone_encounters"][1])
        logger.info(f"Drone encounters: {n.encounters_all} found, {kept} kept once crewed aircraft declared drones "
                    f"are set aside")
        f, escort_only, formation_only = n.crewed_pairs
        logger.info(f"Crewed pairs flying together: {f} by the formation rule ({formation_only} of them missed by the "
                    f"5-minute rule), {escort_only} more by the 5-minute rule alone")
        for (kinds, name, what), c in sorted(n.crewed_summary.items()):
            logger.info(f"Crewed encounters {kinds[0]} x {kinds[1]} {name} {what}: {c}")
    run_row = (day.isoformat(), datetime.datetime.utcnow().replace(microsecond=0), read,
               ",".join(str(h) for h in missing), n.lines, round(time.time() - started, 1))
    if args.dry_run:
        print_dry_run(tables, run_row)
    if args.dry_run and "patterns" not in n.failed:
        # Flight medians per kind, for checking the rule; aggregates only.
        by = collections.defaultdict(list)
        for address, cat, t0, lon0, sec, maxd, path, lat0, *_ in n.flights:
            if n.d0 <= t0 < n.d1:
                by[FREE_FLIGHT_KIND.get(cat) or (PATTERN_KIND.get(cat, "other") if cat != DRONE else "drone")].append(
                    (sec, maxd, path))
                if cat == 1:
                    by["glider:" + n.flight_launch(address, t0)].append((sec, maxd, path))
        print("\n== flights: kind, count, median minutes, km from the start, path km; "
              "all, then at least FLIGHT_MIN_S")
        for kind, v in sorted(by.items()):
            kept = [x for x in v if x[0] >= FLIGHT_MIN_S]
            line = f"   {kind:24s} {len(v):6d} {statistics.median(x[0] for x in v) / 60:7.1f} " \
                   f"{statistics.median(x[1] for x in v) / 1000:7.1f} {statistics.median(x[2] for x in v) / 1000:7.1f}"
            if kept:
                line += f"  |  {len(kept):6d} {statistics.median(x[0] for x in kept) / 60:7.1f} " \
                        f"{statistics.median(x[1] for x in kept) / 1000:7.1f} {statistics.median(x[2] for x in kept) / 1000:7.1f}"
            print(line)
        print(f"   launches dropped as fragments: {n.launch_fragments}")
    if args.dry_run and "patterns" not in n.failed:
        print("\n== terrain relief within 5 km (max - min ground), 100 m steps from 0 to 3,000+, counts")
        for (what, kind), h in sorted(n.relief_histograms().items()):
            total = sum(h.values())
            cum, med = 0, None
            for b in range(31):
                cum += h[b]
                if med is None and cum >= total / 2:
                    med = b * 100
            print(f"   {what:13s} {kind:12s} n={total:5d} median~{med} m: " + " ".join(str(h[b]) for b in range(31)))
    if args.dry_run and "conspicuity" not in n.failed:
        # The climb evidence, address by address under a salted hash.
        import hashlib
        import secrets
        salt = secrets.token_hex(8)
        ev = n.evidence()
        print("\n== declared drones that circled: class, largest climb in a thermal (m), extent (km)")
        for a, c in sorted(n.max_climb.items(), key=lambda x: -x[1]):
            print(f"   {hashlib.sha1((salt + a).encode()).hexdigest()[:8]} {EVIDENCE_NAMES[ev[a]]:20s} "
                  f"{c:6.0f} {n.drone_extent.get(a, 0) / 1000:7.1f}")
    failed = dict(n.failed)
    written = []
    for family in ("conspicuity", "patterns"):
        if family in failed:
            continue
        if args.dry_run:
            written.append(family)
            continue
        try:
            write_family(day, family, families[family])
            written.append(family)
        except Exception as e:
            logger.exception(f"The {family} family could not be written; its previous rows of {day} are kept")
            failed[family] = f"write: {type(e).__name__}: {e}"[:200]
    notes = "; ".join(f"{k} failed ({v})" for k, v in failed.items())
    if not args.dry_run:
        write_run(run_row, notes)
        logger.info(f"{day}: {sum(len(families[f][t][1]) for f in written for t in families[f])} rows written "
                    f"({', '.join(written) or 'nothing'}) from {read} hours ({n.lines:,} lines) in "
                    f"{time.time() - started:.0f} s" + (f"; {notes}" if notes else ""))
    elif notes:
        logger.error(notes)
    if month_ends or args.snapshot:
        # After the day's rows, so that the snapshot holds the whole month; a
        # family whose day failed keeps the month's snapshot it had, if any.
        for family in written:
            snapshot(day.strftime("%Y-%m"), args.dry_run, family)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    logger.info(f"Peak memory {rss / 1024:.0f} MB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
