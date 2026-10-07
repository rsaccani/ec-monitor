#!/usr/bin/env python3
"""Measures computed each night from the raw recording of one UTC day (METHOD.md, section 10).

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
CIRCLE_MIN_TURN = 720                          # two full turns make a climb
CIRCLE_MAX_KT = 200 / 1.852                    # faster than any of the three circles
# A pilot who leaves the core and comes back, or a dropout, splits one climb
# into runs; runs on the same side within 10 minutes and 3 km are one thermal.
THERMAL_MERGE_S, THERMAL_MERGE_M = 600, 3000
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

# Which devices declaring a drone are drones (METHOD.md 10.4, 7 October 2026).
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
# A thermal (10.2) that gains this much height, by a device whose day covers
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

TABLES = ("daily_drone_classes", "daily_hours_solar", "daily_circling", "daily_circling_pilot", "daily_gaggles", "daily_parked",
          "daily_drones", "daily_drone_cells", "daily_drone_extent", "daily_drone_encounters",
          "daily_quality", "nightly_runs")


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
    """(address -> AIRCRAFT_TYPE, addresses not to be tracked) from the OGN device database.

    The owners' choices are honoured as app.py does: TRACKED=N drops the
    device from every measure, IDENTIFIED=N keeps it but its type is not used.
    Without the database (a failed download) every declared drone without an
    ADS-B emitter category stays uncertain, and the log says so.
    """
    try:
        text = urllib.request.urlopen(DDB_URL, timeout=120).read().decode("utf-8", "replace")
    except OSError as e:
        logger.error(f"OGN device database not loaded ({e}): no aircraft types this run")
        return {}, set()
    types, notrack = {}, set()
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
        except (IndexError, KeyError, ValueError):
            continue
    logger.info(f"OGN device database: {len(types):,} types, {len(notrack):,} not to be tracked")
    return types, notrack


class CircleTrack:
    """One address on one system, while it may be circling."""
    __slots__ = ("cat", "last", "run", "run_s", "run_t0", "run_pos", "run_samples", "thermal", "thermals")

    def __init__(self, cat):
        self.cat = cat
        self.last = None          # (t, course, kt, lat, lon)
        self.run = 0.0            # degrees turned on one side, signed (+ = right, clockwise from above)
        self.run_s = 0.0
        self.run_t0 = None
        self.run_pos = None
        self.run_samples = {}     # 5-second bucket -> (lat, lon, alt)
        self.thermal = None       # open thermal: [side, t0, t1, lat, lon, degrees, seconds, samples]
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
            else:
                if th is not None:
                    self.thermals.append(th)
                self.thermal = [side, self.run_t0, t, lat, lon, abs(self.run), self.run_s, self.run_samples]
            self.run_samples = {}
        elif self.run_samples:
            self.run_samples = {}
        self.run = 0.0
        self.run_s = 0.0
        self.run_t0 = None

    def finish(self):
        if self.last is not None:
            self.close_run(self.last[0], self.last[3], self.last[4])
        if self.thermal is not None:
            self.thermals.append(self.thermal)
            self.thermal = None


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
        self.ddb_types, self.notrack = ddb
        self.emitters = collections.defaultdict(set)    # address -> ADS-B emitter categories heard
        self.declared = collections.defaultdict(set)    # address with a category 13 -> system labels
        self.addr_cats = collections.defaultdict(collections.Counter)   # address -> declared category -> fixes
        self.drone_hours = collections.defaultdict(float)   # (local day, hour, address) -> s
        self._evidence = None
        self.major, self.major13 = {}, set()      # (address, tocall) -> majority category; vote()
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
            if in_day:
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
        elif crewed_airborne and alt_m is not None and t - self.last_drone_t <= ENCOUNTER_ACTIVE:
            self.crewed_fix(address, category, t, lat, lon, alt_m)
        if kind == "adsb":
            return                              # ADS-B: only the other aircraft of an encounter
        self.timeline(address, category, t, lat, lon, kt, alt_m)
        if (category in CIRCLE_CATEGORIES or category == DRONE) and course:
            self.circle(address, tocall, category, t, course, kt, lat, lon, alt_m)
        if category in PARKED_CATEGORIES:
            self.park(address, label, category, t, lat, lon, alt_m)

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
            return
        if category in GROUND_CATEGORIES:
            return
        local = prev[0] + seconds / 2 + prev[2] * SOLAR_SECONDS_PER_DEGREE
        if category == DRONE:
            # Per address, so that a crewed aircraft declared a drone can be
            # filed apart once the day's evidence is in (evidence()).
            self.drone_hours[(int(local // 86400), int(local % 86400 // 3600), address)] += seconds
        else:
            k = (int(local // 86400), category, int(local % 86400 // 3600))
            self.hours_air[k] += seconds
            self.hours_ac[k].add(address)
        if category == DRONE:
            ground = self.terrain.elevation(prev[1], prev[2])
            agl = prev[4] - ground if prev[4] is not None and ground is not None else None
            kmh = ((prev[3] or 0) + (kt or 0)) / 2 * 1.852
            self.drone_air[(math.floor(prev[1]), math.floor(prev[2]), band(agl, DRONE_HEIGHT_EDGES),
                            band(kmh, DRONE_SPEED_EDGES), address)] += seconds

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

    def evidence(self):
        """address with a category 13 -> one of the classes of EVIDENCE_NAMES.

        Evaluated once the whole day is in (METHOD.md 10.4). Remote ID is a
        confirmed drone on its own. For the rest, an address whose majority
        is 13 on none of its systems is stray; then crewed evidence: an ADS-B
        emitter category of a crewed aircraft, a crewed type in the device
        database together with at least one thermal flown that day (10.2),
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
                    if x[7]:
                        first, last = x[7][min(x[7])], x[7][max(x[7])]
                        gain = unpack(last)[2] - unpack(first)[2]
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
        """Every table's rows for this day, as {table: (columns, rows)}."""
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
                continue                        # 10.1 leaves ground support and static objects out
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

        # 5. quality
        out["daily_quality"] = (("day", "scope", "name", "check_name", "count", "total", "value"), self.quality(day))
        return out

    def gaggles(self, day, thermals):
        """Pairs of thermals of two aircraft flown together, same side or opposite."""
        thermals.sort()
        pairs = collections.defaultdict(lambda: [0, 0])
        for i, (t0, t1, addr, cat, side, samples) in enumerate(thermals):
            for u0, u1, addr2, cat2, side2, samples2 in thermals[i + 1:]:
                if u0 > t1:
                    break
                if addr2 == addr:
                    continue
                close = 0
                small, large = (samples, samples2) if len(samples) <= len(samples2) else (samples2, samples)
                for b, x in small.items():
                    y = large.get(b)
                    if y is None:
                        continue
                    lat, lon, alt = unpack(x)
                    lat2, lon2, alt2 = unpack(y)
                    if abs(alt - alt2) <= GAGGLE_VERT_M and dist(lat, lon, lat2, lon2) <= GAGGLE_M:
                        close += 1
                if close * GAGGLE_BUCKET >= GAGGLE_MIN_S:
                    k = (min(cat, cat2), max(cat, cat2))
                    pairs[k][0 if side == side2 else 1] += 1
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


def snapshot(month, dry_run):
    """Store what every statistics endpoint publishes for `month` (METHOD.md, section 9).

    Taken the night the month's last day is computed. The JSON comes from
    the functions the endpoints call (sources.SourceTracker.snapshot_bodies),
    serialised with sorted keys, and is stored compressed with the METHOD.md
    commit and the code commit deployed at that moment. A rerun replaces it.
    """
    tracker = sources.SourceTracker(None, lambda: connect_db(autocommit=True))
    bodies = tracker.snapshot_bodies(month)
    method_commit = git("log", "-1", "--format=%H", "--", "METHOD.md")
    deployed = git("rev-parse", "HEAD")
    now = datetime.datetime.utcnow().replace(microsecond=0)
    rows = []
    for name, obj in bodies.items():
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
        rows.append((month, name, method_commit, deployed, now, len(text), zlib.compress(text.encode("utf-8"), 9)))
    missing = [e for e in sources.SourceTracker.SNAPSHOT_ENDPOINTS if e not in bodies]
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
            cur.execute("DELETE FROM monthly_snapshot WHERE month = %s", (month,))
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


def write(day, tables, run_row):
    conn = connect_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute(f"DELETE FROM {table} WHERE day = %s", (day.isoformat(),))
            for table, (cols, rows) in tables.items():
                if rows:
                    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                                    rows)
            cur.execute("INSERT INTO nightly_runs (day, finished_at, hours_read, hours_missing, line_count, seconds) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", run_row)
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
    tables = n.rows()
    kept = sum(r[-1] for r in tables["daily_drone_encounters"][1])
    logger.info(f"Drone encounters: {n.encounters_all} found, {kept} kept once crewed aircraft declared drones are set aside")
    run_row = (day.isoformat(), datetime.datetime.utcnow().replace(microsecond=0), read,
               ",".join(str(h) for h in missing), n.lines, round(time.time() - started, 1))
    if args.dry_run:
        print_dry_run(tables, run_row)
        # The climb evidence, address by address under a salted hash.
        import hashlib
        import secrets
        salt = secrets.token_hex(8)
        ev = n.evidence()
        print("\n== declared drones that circled: class, largest climb in a thermal (m), extent (km)")
        for a, c in sorted(n.max_climb.items(), key=lambda x: -x[1]):
            print(f"   {hashlib.sha1((salt + a).encode()).hexdigest()[:8]} {EVIDENCE_NAMES[ev[a]]:20s} "
                  f"{c:6.0f} {n.drone_extent.get(a, 0) / 1000:7.1f}")
    else:
        write(day, tables, run_row)
        logger.info(f"{day}: {sum(len(r) for _, r in tables.values())} rows written from {read} hours "
                    f"({n.lines:,} lines) in {time.time() - started:.0f} s")
    if month_ends or args.snapshot:
        # After the day's rows, so that the snapshot holds the whole month.
        snapshot(day.strftime("%Y-%m"), args.dry_run)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    logger.info(f"Peak memory {rss / 1024:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
