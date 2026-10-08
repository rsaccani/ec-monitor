"""Every OGN source, not only ADS-L: monthly counts per source and a live layer.

Each APRS packet names its source in the destination field (the "tocall",
e.g. OGFLR, OGNSKY), listed in glidernet/ogn-aprs-protocol/tocalls.txt. A
packet heard by a ground receiver carries the reception metadata the receiver
adds (signal-to-noise in dB, frequency offset in kHz); one injected over the
internet by an app or a platform does not. That decides `via`, so sources not
in the table below are still sorted correctly. ADS-B is radio by definition,
but its receivers add no such metadata, hence the exception.

The listener thread calls `handle()` for every line, about 1,200 a second, so
the per-packet path is kept to string splits and dict lookups: positions are
parsed at most once every LIVE_MIN_INTERVAL seconds per device, ADS-B is never
parsed, and database rows are written by a separate thread, once per device,
source and month (plus one last_seen update per day).
"""

import bisect
import calendar
import collections
import datetime
import logging
import json
import math
import mmap
import os
import struct
import queue
import re
import statistics
import threading
import time
import zlib

import pymysql

logger = logging.getLogger("main")

# tocall -> (label, kind). Kinds: adsl, flarm, fanet, adsb, radio (other radio
# systems), app (runs on a phone), tracker (hardware sending over a cellular or
# satellite link), platform (an aggregator relaying other sources).
SOURCES = {
    "OGADSL": ("ADS-L", "adsl"),
    "OGFLR": ("FLARM", "flarm"),
    "OGNFLR": ("FLARM", "flarm"),
    "OGFLR6": ("FLARM", "flarm"),
    "OGFLR7": ("FLARM", "flarm"),
    "OGNFNT": ("FANET", "fanet"),
    "OGADSB": ("ADS-B", "adsb"),
    "OGNTRK": ("OGN tracker", "radio"),
    "OGPAW": ("PilotAware", "radio"),
    "OGNPAW": ("PilotAware", "radio"),
    "OGMSHT": ("Meshtastic", "radio"),
    "OGNDSX": ("DSX", "radio"),
    "OGSTUX": ("Stratux", "radio"),
    "OGAVZ": ("Aviaze", "radio"),
    "OGBSTOP": ("BirdStop", "radio"),
    # Drone Remote ID (ASTM F3411 / EN 4709-002 over Bluetooth or Wi-Fi),
    # decoded by some OGN receivers and forwarded with "rid" in the comment;
    # not in tocalls.txt. Seen on the raw recording on 7 October 2026.
    "OGNMAV": ("Remote ID", "radio"),
    "OGNSKY": ("SafeSky", "app"),
    "OGNAVI": ("Naviter", "app"),
    "OGLT24": ("LiveTrack24", "app"),
    "OGSKYL": ("SkyLines", "app"),
    "OGEVARIO": ("eVario", "app"),
    "OGNWMN": ("Wingman", "app"),
    "OGNWGL": ("WeGlide", "app"),
    "OGSKYB": ("SkyBase", "app"),
    "OGPGP": ("pgpilot", "app"),
    "OGNVVO": ("VarioVoice", "app"),
    "OGNMYC": ("MyCloudbase", "app"),
    "OGAIRM": ("AirMate", "app"),
    # Not in tocalls.txt: ids "XCG…", paragliders over the internet, and XC
    # Guide (xcguide.app) can send the pilot's position to OGN (2026-10-06).
    "OGNXCG": ("XC Guide", "app"),
    "OGNMTK": ("Microtrak", "tracker"),
    "OGNMKT": ("Microtrak", "tracker"),
    "OGSPOT": ("SPOT", "tracker"),
    "OGSPID": ("Spidertracks", "tracker"),
    "OGCAPT": ("Capturs", "tracker"),
    "OGFLYM": ("Flymaster", "tracker"),
    "OGNINRE": ("Garmin inReach", "tracker"),
    "OGNTTN": ("The Things Network", "tracker"),
    "OGTTN3": ("The Things Network", "tracker"),   # TTN stack v3, not in tocalls.txt
    "OGNHEL": ("Helium", "tracker"),
    "OGAPIK": ("APIK", "tracker"),
    "OGNVOL": ("Volandoo", "platform"),
    "OGNPUR": ("PureTrack", "platform"),
    "FXCAPP": ("flyxc", "platform"),
    "OGNALP": ("Alpium", "platform"),
    "OGNFNO": ("Neurone", "platform"),
}

# Remote ID is broadcast by drones only, so its devices are drones whatever
# the category in their id (7 October 2026).
REMOTE_ID, DRONE_CATEGORY = "OGNMAV", 13

# Not aircraft: receivers announcing their own position, and delayed or
# synthesised copies of packets already counted elsewhere.
EXCLUDED = {"OGNSDR", "OGNSXR", "OGNDELAY", "OGMLAT", "OGNDVS"}

# Tocalls that are the same system under another protocol version or decoder.
# A device heard under two of them is one aircraft on one system: its packets
# are measured as a single stream and the device is counted once. Until
# 4 October 2026 OGFLR7 was measured apart from OGFLR, which split one FLARM
# into two sparser streams.
SAME_SYSTEM = {"OGNFLR": "OGFLR", "OGFLR6": "OGFLR", "OGFLR7": "OGFLR", "OGNPAW": "OGPAW"}
# PilotAware's own ground stations (PW...) forward its devices under the
# generic tocall APRS; the device prefix PAW is what identifies them.
GENERIC_TOCALL = {("APRS", "PAW"): "OGPAW"}

# Meshtastic is a mesh network for people on the ground; OGN receivers decode
# it too. A node counts only when it declares an aircraft type in its id.
AIRCRAFT_ONLY = {"OGMSHT"}

# PilotAware ground stations also rebroadcast the FLARM aircraft they hear,
# under the PilotAware prefix PAW with the address of the FLARM device, the
# symbol /z and category 14 (ground support). In October 2026, 560 of the
# 1,463 PilotAware ids were such rows, their address also heard on FLARM, so
# they made PilotAware look a third larger than it is. From 7 October 2026
# they are not counted as PilotAware devices (METHOD.md, section 1); the
# FLARM device is counted under FLARM. A genuine PilotAware ground vehicle,
# also category 14, is lost with them, and is not an aircraft either.
REBROADCAST_PREFIX, REBROADCAST_CATEGORY = "PAW", 14
REBROADCAST_SOURCES = ("APRS", "OGPAW", "OGNPAW")


def rebroadcast(tocall, device_id, category):
    """True for a PilotAware station's copy of a FLARM aircraft."""
    return (category == REBROADCAST_CATEGORY and device_id.startswith(REBROADCAST_PREFIX)
            and tocall in REBROADCAST_SOURCES)


# The two rules in SQL, applied whenever devices are counted, so rows written
# before a rule applied are filtered too. COALESCE: a NULL category would make
# the second clause NULL and drop the row.
COUNTED_SQL = ("NOT (source IN ({}) AND category IS NULL)".format(", ".join(f"'{s}'" for s in AIRCRAFT_ONLY))
               + " AND NOT (LEFT(device_id, 3) = '{}' AND COALESCE(category, 255) = {} AND source IN ({}))".format(
                   REBROADCAST_PREFIX, REBROADCAST_CATEGORY, ", ".join(f"'{s}'" for s in REBROADCAST_SOURCES)))

# FANET instruments switch to ground tracking once the pilot has landed and
# then send category 15 (static object); some trackers send 14 on the ground.
# A device's category in monthly_sources is the one of its first packet of the
# month, so a pilot first heard after landing was filed as a static object:
# 3,070 of the 9,096 FANET rows of October to the 7th were. From 7 October
# 2026 a later aircraft category replaces a stored 14 or 15 (METHOD.md,
# section 1), except on the PilotAware rebroadcasts above, whose 14 is what
# marks them.
GROUND_CATEGORIES = (14, 15)
AIRCRAFT_CATEGORIES = frozenset(range(1, 14)) - {10}     # 10 is "unknown" in the OGN id
# ADS-B's 0 is "not yet known": OGN's ADS-B decoder sends it until the
# aircraft's emitter category has been received, so the first packet of most
# ADS-B aircraft carries it. Of the October 2026 ADS-B rows also in the raw
# recording, 1,654 were stored as 0 while most of the aircraft's packets
# declared a powered aircraft (1,088), a helicopter (453) or a paraglider (113). From 7 October 2026 a later aircraft
# category replaces it as it replaces 14 and 15 (METHOD.md, section 1).
UNKNOWN_YET = {"OGADSB": 0}


def replaceable(tocall, stored):
    """True when a stored category gives way to a later aircraft category."""
    return stored in GROUND_CATEGORIES or (stored is not None and UNKNOWN_YET.get(tocall) == stored)


UPGRADE_CATEGORY_SQL = (
    "UPDATE monthly_sources SET category = %s "
    "WHERE month = %s AND source = %s AND via = %s AND device_id = %s "
    "AND (category IN (14, 15) OR (category = 0 AND source = 'OGADSB')) "
    f"AND NOT (category = {REBROADCAST_CATEGORY} AND LEFT(device_id, 3) = '{REBROADCAST_PREFIX}')")


def same_system(tocall, device_id):
    """The source a packet is counted under, merging names of one system."""
    return GENERIC_TOCALL.get((tocall, device_id[:3])) or SAME_SYSTEM.get(tocall, tocall)


# The same mapping in SQL, for rows written before it applied.
SAME_SYSTEM_SQL = ("CASE " + " ".join(f"WHEN source = '{a}' AND LEFT(device_id, 3) = '{p}' THEN '{b}'"
                                      for (a, p), b in GENERIC_TOCALL.items())
                   + " " + " ".join(f"WHEN source = '{a}' THEN '{b}'" for a, b in SAME_SYSTEM.items())
                   + " ELSE source END")

# Names of the aircraft categories carried in bits 2-5 of the first byte of
# the OGN "idXXYYYYYY" field (bits 0-1 are the address type, 6 no-track, 7
# stealth). Moved here from app.py on 7 October 2026 with the ADS-L monthly
# counts, so that nightly.py can take the monthly snapshots without app.py.
CATEGORY_NAMES = {
    0: "Unknown", 1: "Glider", 2: "Tow plane", 3: "Helicopter", 4: "Skydiver", 5: "Drop plane",
    6: "Hang glider", 7: "Paraglider", 8: "Powered aircraft", 9: "Jet aircraft", 10: "Unknown",
    11: "Balloon", 12: "Airship", 13: "Drone", 14: "Unknown", 15: "Static object",
}
# Address type, from the callsign prefix OGN gives each beacon. Random addresses
# change at every power-up or more often, so one device can count several times.
ADDRESS_PREFIXES = {"ICA": "icao", "FLR": "flarm", "OGN": "ogn", "RND": "random",
                    "PAW": "pilotaware", "FNT": "fanet"}


def adsl_monthly_stats(connect_db):
    """ADS-L devices per month, oldest month last, every month on record.

    `devices` is the total distinct addresses, unchanged for older clients.
    `addresses` splits it by address type, `categories` by aircraft category
    (recorded only since the column was added; `categorised` says how many rows
    have one) and `partial` marks the current UTC month.
    """
    db = connect_db()
    try:
        with db.cursor() as cur:
            # Older months live only as counts in monthly_devices_summary
            # (archive_loop), with 255 for "no category recorded".
            cur.execute("""
                SELECT month, LEFT(device_id, 3), COUNT(*)
                    FROM monthly_devices
                GROUP BY month, LEFT(device_id, 3)
                UNION ALL
                SELECT month, prefix, SUM(devices)
                    FROM monthly_devices_summary
                GROUP BY month, prefix
            """)
            by_prefix = cur.fetchall()
            cur.execute("""
                SELECT month, category, COUNT(*)
                    FROM monthly_devices
                WHERE category IS NOT NULL
                GROUP BY month, category
                UNION ALL
                SELECT month, category, SUM(devices)
                    FROM monthly_devices_summary
                WHERE category <> 255
                GROUP BY month, category
            """)
            by_category = cur.fetchall()
    finally:
        db.close()
    months = {}
    # COUNT(*) united with SUM() comes back as DECIMAL, which jsonify would
    # turn into strings: hence the int().
    for month, prefix, n in by_prefix:
        n = int(n)
        m = months.setdefault(month, {"month": month, "devices": 0, "addresses": {},
                                      "categories": {}, "categorised": 0})
        kind = ADDRESS_PREFIXES.get(prefix, "other")
        m["devices"] += n
        m["addresses"][kind] = m["addresses"].get(kind, 0) + n
    for month, code, n in by_category:
        n = int(n)
        m = months.get(month)
        if m is None:
            continue
        name = CATEGORY_NAMES.get(code, "Unknown")
        m["categories"][name] = m["categories"].get(name, 0) + n
        m["categorised"] += n
    current = datetime.datetime.utcnow().strftime("%Y-%m")
    out = sorted(months.values(), key=lambda m: m["month"], reverse=True)
    for m in out:
        m["partial"] = m["month"] == current
    return out


# Map layers, by kind. ADS-B stays off the map: about 1,400 aircraft at any
# moment, mostly airliners, would bury everything else and weigh on the poll.
LAYER_OF_KIND = {
    "flarm": "flarm",
    "fanet": "fanet",
    "radio": "radio",
    "app": "apps",
    "tracker": "trackers",
    "platform": "relays",
    "other": "relays",
}

LIVE_MIN_INTERVAL = 4  # seconds between two position parses of one device
LIVE_WINDOW = 15 * 60  # a device unheard for this long leaves the map
# The main page's live count (heard_stats): aircraft heard in the last hour.
HEARD_WINDOW = 60 * 60
HEARD_CACHE_SECONDS = 5
HEARD_KINDS = {2: "powered", 8: "powered", 9: "powered", 1: "glider", 6: "free_flight", 7: "free_flight",
               3: "helicopter", 13: "drone"}
STATS_CACHE_SECONDS = 600

# --- Visibility measure (METHOD.md) -----------------------------------------
GAP_THRESHOLDS = (300, 1000, 3000)      # metres
AIRBORNE_KT = 10 / 1.852                 # 10 km/h, at either end of a segment
# From 6 October 2026 a segment is flight only if both ends exceed a speed
# for the kind of aircraft (METHOD.md, section 2): at 10 km/h at either end a
# powered aircraft taxiing, or a paraglider pilot packing up, was flying, and
# the gap that followed counted as lost signal. Other kinds keep AIRBORNE_KT.
# APRS carries whole knots: 8 kt is 14.8 km/h, the nearest to 15 (until the
# evening of 6 October 2026 15/1.852 kt, which only 9 kt, 16.7 km/h, passed).
FLYING_KT = {6: 8.0, 7: 8.0, 1: 25.0, 2: 40.0, 8: 40.0, 9: 40.0}
# Free-flight packets beyond what a paraglider or a hang glider can do are
# discarded as implausible: a sounding balloon set to "paraglider" at 8,150 m,
# a device reporting 702 km/h (6 October 2026).
FREE_FLIGHT_MAX_KT = {7: 100 / 1.852, 6: 150 / 1.852}
FREE_FLIGHT_MAX_M = 6000
SESSION_BREAK = 20 * 60                  # seconds; longer segments are new sessions
CIRCLING_ROT = 2.0                       # half-turns per minute (6 deg/s)
IMPLAUSIBLE_MS = 500 / 3.6               # 500 km/h: a shared address or a corrupt fix
STALE_SECONDS = 5 * 60                   # a fix older than this on arrival is ignored
VISIBILITY_FLUSH = 15 * 60               # seconds between appends to the database
ARCHIVE_EVERY = 6 * 3600                 # seconds between checks for months to archive
# When each phone app sends a fix while its phone has signal (METHOD.md,
# section 8): (metres moved, floor, heartbeat). It sends once the aircraft has
# moved that far, never sooner than the floor and never later than the
# heartbeat, in seconds; a fixed cadence has floor = heartbeat and no distance.
# Only the time beyond the expected interval plus the tolerance counts as time
# without coverage, so an app that sends once a minute by design is not taken
# for one that lost the network. An app not listed here is left out of the
# judgement until its cadence is known.
APP_CADENCE = {
    "OGNSKY": (None, 2, 2),       # SafeSky, measured on the feed 2026-10-05
    "OGNAVI": (None, 60, 60),     # Naviter, measured on the feed 2026-10-05
    "OGNVVO": (150, 10, 45),      # VarioVoice, OGNScheduler.swift
}
# The same rule for radio, so that questions 2, 3 and 6 compare the channels
# by time without signal (METHOD.md, section 2). FLARM and ADS-L send every
# second and OGN trackers and PilotAware every one or two. FANET's
# specification sets floor((neighbours/10 + 1) * 5 s), slowing down where many
# fly so as not to saturate the channel; on 6 October 2026 the feed showed
# 5 s with fewer than ten FANET devices within 10 km and a median of 17 s with
# ten to nineteen. 15 s, the interval up to 29 neighbours, is lenient for an
# isolated pilot by up to 10 s a gap. Radio systems not listed are left out
# of the judgement, as unlisted apps are.
RADIO_CADENCE = {
    "OGADSL": (None, 1, 1),
    "OGFLR": (None, 1, 1),
    "OGNTRK": (None, 2, 2),
    "OGPAW": (None, 2, 2),
    "OGNFNT": (None, 15, 15),
}
CADENCE_TOLERANCE = 10


def cadence_of(tocall, kind, via):
    """The (metres, floor, heartbeat) rule a source keeps with signal, or None."""
    if kind == "app":
        return APP_CADENCE.get(tocall)
    if via == "radio" and kind != "adsb":
        return RADIO_CADENCE.get(tocall)
    return None


def expected_interval(cadence, speed_ms):
    """Seconds an app is expected to wait before its next fix at this speed."""
    metres, floor, heartbeat = cadence
    if metres is None or not speed_ms:
        return heartbeat
    return min(heartbeat, max(floor, metres / speed_ms))
UNKNOWN_CATEGORY = 255
SYMBOL_CATEGORY = {"g": 7, "'": 1, "^": 8, "X": 3, "O": 11}
# Totals per (day, source, via, category):
# packets, rot_packets, segments, sessions, implausible, air_seconds,
# then seconds beyond each threshold for the last-point estimator,
# then for the packet-based one.
N_TOTALS = 6 + 2 * len(GAP_THRESHOLDS)

_position = re.compile(
    r"^/(\d{2})(\d{2})(\d{2})h(\d{2})(\d{2}\.\d{2})([NS])(.)(\d{3})(\d{2}\.\d{2})([EW])(.)"
    r"(?:(\d{3})/(\d{3}))?")
_extra_precision = re.compile(r"!W(\d)(\d)!")
_rot = re.compile(r" ([+-]\d+(?:\.\d+)?)rot")
_alt = re.compile(r"/A=(-?\d+)")
EARTH_R = 6371000.0


def _distance(lat1, lon1, lat2, lon2):
    """Metres between two points; equirectangular, ample for a few kilometres."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return EARTH_R * math.hypot(x, y)


def _move(lat, lon, course_deg, metres):
    c = math.radians(course_deg)
    dlat = metres * math.cos(c) / EARTH_R
    dlon = metres * math.sin(c) / (EARTH_R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def _over(seconds, error, threshold):
    """Time beyond `threshold` if the error grows linearly from 0 to `error`."""
    return seconds * (1 - threshold / error) if error > threshold else 0.0


def parse_fix(body, now):
    """(epoch, lat, lon, course or None, speed_kt or None, rot or None, symbol)."""
    m = _position.match(body)
    if not m:
        return None
    hh, mi, ss, latd, latm, ns, table, lond, lonm, ew, symbol, course, speed = m.groups()
    latm, lonm = float(latm), float(lonm)
    w = _extra_precision.search(body)
    if w:
        latm += int(w.group(1)) / 1000
        lonm += int(w.group(2)) / 1000
    lat = int(latd) + latm / 60
    lon = int(lond) + lonm / 60
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon
    t = now.replace(hour=int(hh), minute=int(mi), second=int(ss), microsecond=0)
    if t - now > datetime.timedelta(minutes=5):
        t -= datetime.timedelta(days=1)   # a fix from just before midnight
    course = int(course) if course is not None else None
    if course == 0:
        course = None                      # APRS: 000 is "unknown", 360 is north
    speed = int(speed) if speed is not None else None
    r = _rot.search(body)
    rot = float(r.group(1)) if r else None
    a = _alt.search(body)
    alt_m = int(a.group(1)) * 0.3048 if a else None
    return calendar.timegm(t.timetuple()), lat, lon, course, speed, rot, symbol, alt_m


# --- Where and how high (METHOD.md) -------------------------------------------
UNKNOWN_BAND = 255
CELL_DEG = 0.25
DEM_PATH = os.environ.get("ADSL_DEM", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "data", "europe_15s.i16"))


def flying(category, pspeed, speed):
    """True when a segment between two fixes counts as flight (METHOD.md)."""
    floor = FLYING_KT.get(category)
    if floor is None:
        return (pspeed or 0) >= AIRBORNE_KT or (speed or 0) >= AIRBORNE_KT
    return min(pspeed or 0, speed or 0) >= floor


def implausible(category, speed, alt_m):
    """A free-flight fix no paraglider or hang glider can produce."""
    top = FREE_FLIGHT_MAX_KT.get(category)
    if top is None:
        return False
    return (speed or 0) > top or (alt_m or 0) > FREE_FLIGHT_MAX_M


def msl_band(alt_m):
    return UNKNOWN_BAND if alt_m is None else min(4, max(0, int(alt_m // 1000)))


def agl_band(agl_m):
    if agl_m is None:
        return UNKNOWN_BAND
    return 0 if agl_m < 300 else 1 if agl_m < 600 else 2 if agl_m < 1200 else 3 if agl_m < 2000 else 4


class Terrain:
    """Elevation lookups in a raw int16 grid (see the .json beside it), memory-mapped."""

    def __init__(self, path):
        self.ok = False
        try:
            with open(os.path.splitext(path)[0] + ".json") as f:
                meta = json.load(f)
            self.north, self.west = meta["north"], meta["west"]
            self.step, self.rows, self.cols = meta["step_deg"], meta["rows"], meta["cols"]
            self.nodata = meta["nodata"]
            self._f = open(path, "rb")
            self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
            self.ok = len(self._mm) == self.rows * self.cols * 2
            logger.info(f"Terrain model loaded from {path}: {self.ok}")
        except (OSError, ValueError, KeyError) as e:
            logger.info(f"No terrain model ({e}); height above ground will be unknown")

    def _cell(self, r, c):
        v = struct.unpack_from("<h", self._mm, (r * self.cols + c) * 2)[0]
        # ETOPO keeps sea-floor depths: over the sea the ground is the surface.
        # Clamping also lifts land below sea level to 0, a few metres at most.
        return 0 if v == self.nodata or v < 0 else v

    def elevation(self, lat, lon):
        """Metres, 0 over the sea, None outside the model.

        Interpolated between the centres of the four nearest cells (METHOD.md,
        section 3): on a slope the value of the one cell containing the point
        was off by about twice as much, since the cell is the average of
        ground that rises across it.
        """
        if not self.ok:
            return None
        y = (self.north - lat) / self.step
        x = (lon - self.west) / self.step
        if not (0 <= y < self.rows and 0 <= x < self.cols):
            return None
        # Cells are registered by their area, so centres sit at i + 0.5.
        y = min(max(y - 0.5, 0.0), self.rows - 1.0)
        x = min(max(x - 0.5, 0.0), self.cols - 1.0)
        r, c = int(y), int(x)
        r1, c1 = min(r + 1, self.rows - 1), min(c + 1, self.cols - 1)
        dy, dx = y - r, x - c
        return ((self._cell(r, c) * (1 - dx) + self._cell(r, c1) * dx) * (1 - dy) +
                (self._cell(r1, c) * (1 - dx) + self._cell(r1, c1) * dx) * dy)


# Monthly totals per (month, source, via, category, msl band, agl band).
DETAIL_COLUMNS = (
    "segments", "rot_segments", "circling_segments", "air_seconds",
    "p0_300", "p0_1000", "p0_3000", "p1_300", "p1_1000", "p1_3000", "p2_300", "p2_1000", "p2_3000",
    "rot_air", "rot_p1_300", "rot_p1_1000", "rot_p2_300", "rot_p2_1000",
    "circ_air", "circ_p1_300", "circ_p1_1000", "circ_p2_300", "circ_p2_1000",
    "age_le3", "age_le6", "age_le15", "age_le30", "seg_le3", "seg_le6",
    "vanish_2", "vanish_5", "vanish_20",
    "int_le2", "int_le4", "int_le8", "int_le16", "int_le32", "int_le64",
    "cad_air", "cad_late",
    # Time without signal under the rules of 6 October 2026 (METHOD.md, 2):
    # judged and late seconds, from 06:07:56 UTC that day (the hours before
    # the deploy filled from the raw recording). cad_* keep everything since
    # 17:51 UTC on 5 October under whichever rule applied, and are not read.
    "sig_air", "sig_late",
)
INTERVAL_LIMITS = (2, 4, 8, 16, 32, 64)
VANISH_MINUTES = (2, 5, 20)
AGE_LIMITS = (3, 6, 15, 30)
# Monthly totals per (month, cell, group, via).
GRID_COLUMNS = ("segments", "air_seconds", "p0_300", "p1_300", "p1_1000", "p0_1000",
                "cad_air", "cad_late", "sig_air", "sig_late")


def _upsert(table, keys, columns):
    cols = ", ".join(keys + columns)
    marks = ", ".join(["%s"] * (len(keys) + len(columns)))
    adds = ", ".join(f"{c} = {c} + VALUES({c})" for c in columns)
    return f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON DUPLICATE KEY UPDATE {adds}"


SYSTEMS_MIN_COMBO = 5   # aircraft before a combination of systems is listed by name
# The per-pilot circling test (PATTERNS.md, section 2): pilots with at least
# this many thermals, and the bands of thermal counts kept for archived months.
PREFERENCE_MIN_THERMALS = (5, 10)
THERMAL_CELL_MIN = 20     # thermals in a month before a 1-degree cell of climb rates is published
THERMAL_CELLS_PER_DEG = 4 # daily_thermals cells are 0.25 degree (PATTERNS.md, section 3)
THERMAL_PLACES_TOP = 15   # entries in each ranking of thermal places, per kind
PLACES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "places-europe.tsv")
PLACE_NEAR_M = 25000      # a cell with no place inside takes the nearest within this of its centre
# Flying sites name a cell first (PATTERNS.md, section 3, 7 October 2026):
# the one nearest the cell's centre, inside the cell or within 5 km of its edge.
SITE_EDGE_M = 5000
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SITE_FILES = {"fivl": "fivl-sites.tsv", "takeoff": "osm-takeoffs.tsv", "airfield": "osm-airfields.tsv"}
# A site name that is generic or repeated within its list is qualified with
# the nearest town (PATTERNS.md, section 3, 8 October 2026), so that every
# name in a list is unique and says where the site is. Generic: made only of
# these words, compass points and numbers, all compared in lower case.
GENERIC_TAKEOFF_WORDS = frozenset("""
    start startplatz startpunkt startrampe weststartplatz oststartplatz nordstartplatz südstartplatz
    landeplatz launch takeoff take off decollo décollage decollage atterrissage atterraggio despegue
    descolagem zona de del la le les des du di da d l parapente parapendio paragliding paraglider
    paragliders paraglide gleitschirm hängegleiter drachen deltaplano deltaplane delta hang hanggliding
    gliding glider site vol libre free flight flying rampa vôo voo livre aire area place spot point
    zone siklóernyős starthely απογείωση αλεξιπτώτου πλαγιάς парапланерный старт параглајдинг
    узлетиште vzletišče ppg pg gs hg""".split())
GENERIC_AIRFIELD_WORDS = frozenset("""
    airfield airstrip aerodrome aérodrome aeródromo aerodromo aeroporto aéroport airport flugplatz
    segelflugplatz segelfluggelände sonderlandeplatz verkehrslandeplatz landeplatz ultraleichtflugplatz
    ul ulm aviosuperficie campo di volo letiště lotnisko lądowisko аэродром аеродром летище
    селскостопанско бывший аэрадром сельскагаспадарчай авіяцыі aerodrom flygfält flyveplads de
    la le du del base air private gliding glider club strip former disused agricultural abandoned""".split())
COMPASS_WORDS = frozenset("""
    n s e w o ne nw se sw no so nord sud süd ost west est ouest north south east nordost nordwest
    südost südwest nordouest sudouest nordest sudest""".split())
SITE_TOWN_M = 50000       # the nearest town is looked for this far
SITE_SAME_M = 1000        # the same name this close is one site mapped twice
# Probable wave (PATTERNS.md, section 8) is counted per 0.25-degree cell from
# this day, and per 1 degree before; a cell is named by geography, never by a
# take-off: "near Altdorf (CH)" when the nearest town is within WAVE_NEAR_KM of
# the cell's centre, after rounding to 5 km, else "20 km SE of Glarus (CH)".
# 10 km because the centre is itself up to 14 km from a climb in the cell:
# a finer distance would claim a precision the cell does not have.
WAVE_QUARTER_FROM = "2026-10-06"
WAVE_NEAR_KM = 10

# Which lists name a cell for each kind, in order; the town of GeoNames last.
SITE_ORDER = {"paraglider": ("fivl", "takeoff"), "hang_glider": ("fivl", "takeoff"), "glider": ("airfield",)}
# An airfield or base with few aircraft could otherwise show one owner's
# habits (PATTERNS.md, section 10): cells only with this much in the month.
HELICOPTER_CELL_MIN = 10  # helicopter-days
ROUTE_MIN_AIRCRAFT = 5    # distinct aircraft in a month before a route is named (PATTERNS.md, section 6)
THERMAL_BANDS = (1, 5, 10, 20)
# The thermal count taken for each band when an archived month's expected
# histogram is rebuilt without the pilots' own counts: about the middle of
# the band, and 25 for "20 or more" (7 October 2026).
BAND_THERMALS = (2, 7, 14, 25)


def expected_deciles(n, p):
    """For one pilot with n thermals, the chance of each tenth of right-hand share
    (the deciles of `histogram`: 0 under 10%, ..., 9 from 90%) if sides were
    chosen with probability p."""
    out = [0.0] * 10
    for j in range(n + 1):
        out[min(9, int(j / n * 10))] += math.comb(n, j) * p ** j * (1 - p) ** (n - j)
    return out
HOURS_SQL = _upsert("monthly_hours", ["month", "category"], ["segments", "air_seconds"])
DETAIL_SQL = _upsert("monthly_visibility_detail",
                     ("month", "source", "via", "category", "msl_band", "agl_band"), DETAIL_COLUMNS)
# Reception pattern while circling (METHOD.md): received radio packets by the
# angle between course and the bearing to the receiving station.
PATTERN_SECTOR = 30
# Sectors are centred on the heading (stored as 12-23, the first from -15 to
# +15 degrees). Until 6 October 2026 they began at it (stored as 0-11, the
# first from 0 to 30); those rows are kept apart and never added to the new.
PATTERN_CENTRED = 12
PATTERN_DIST_KM = (5, 10, 20, 40)        # band edges; above the last is the last band
COURSE_CHANGE_DEG_S = 6.0
PATTERN_SQL = _upsert("monthly_reception_pattern",
                      ("month", "source", "category", "dist_band", "sector"), ("packets", "snr_sum", "snr_n"))
_snr = re.compile(r" (-?\d+(?:\.\d+)?)dB ")


def _bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


# Sources whose turn rate comes from the aircraft (METHOD.md, "Where the turn
# rate comes from"). Elsewhere the field is a receiver's estimate, a constant
# zero or absent, and is ignored.
TURN_RATE_SOURCES = {"OGFLR", "OGNFLR", "OGFLR7", "OGNTRK", "OGNFNT", "OGNVVO"}

# Short-horizon prediction (METHOD.md, "Predicting a few seconds ahead").
HORIZONS = (5, 10, 20)
PREDICTORS = ("straight", "derived", "transmitted", "last_point", "derived_paired", "transmitted_paired")
LOSS_GAPS = (2, 4, 8, 16, 32)                           # emulated packet loss (METHOD.md, "When packets are lost")
ERROR_BINS = (5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300, 500)   # metres; last bin "over 500"
PREDICTION_COLUMNS = tuple(f"b{i}" for i in range(len(ERROR_BINS) + 1)) + ("n", "error_sum")
PREDICTION_SQL = _upsert("monthly_prediction",
                         ("month", "source", "category", "horizon", "circling", "predictor"),
                         PREDICTION_COLUMNS)
HISTORY_SECONDS = max(HORIZONS) + max(LOSS_GAPS) + 4   # how far back each device's fixes are kept
PREDICTION_EVERY = 3                                # seconds between two scored fixes of one device


def _arc(lat, lon, course_deg, speed_ms, turn_deg_s, seconds):
    """Where an aircraft ends up turning at a constant rate (positive = right)."""
    w = math.radians(turn_deg_s)
    th = math.radians(course_deg)
    if abs(w) < 1e-4:
        east = speed_ms * seconds * math.sin(th)
        north = speed_ms * seconds * math.cos(th)
    else:
        east = speed_ms / w * (math.cos(th) - math.cos(th + w * seconds))
        north = speed_ms / w * (math.sin(th + w * seconds) - math.sin(th))
    dlat = math.degrees(north / EARTH_R)
    dlon = math.degrees(east / (EARTH_R * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


GRID_SQL = _upsert("monthly_visibility_grid",
                   ("month", "lat_idx", "lon_idx", "grp", "via"), GRID_COLUMNS)


_radio_meta = re.compile(r" -?\d+(?:\.\d+)?dB .*?[+-]\d+(?:\.\d+)?kHz")
_id_field = re.compile(r"\bid([0-9A-F]{8}|[0-9A-F]{10})\b")


def id_info(text):
    """(aircraft category or None, no-track flag) from the id field of a packet.

    The OGN id is 8 hex digits: in its first byte bit 7 is stealth, bit 6
    no-track, bits 2-5 the category and bits 0-1 the address type. Naviter
    writes 10 (Naviter_APRS_format.md in glidernet/ogn-aprs-protocol): of the
    40 bits, 39 is stealth, 38 no-track and 34-37 the category.
    """
    m = _id_field.search(text)
    if not m:
        return None, False
    h = m.group(1)
    if len(h) == 8:
        b = int(h[:2], 16)
        return (b >> 2) & 0x0F, bool(b & 0x40)
    w = int(h[:4], 16)
    return (w >> 10) & 0x0F, bool(w & 0x4000)


# Radio followed per aircraft (METHOD.md, section 2): rows of the detail and
# grid tables under this pseudo-source, kind "aircraft", hold sig_* only, so
# that sums of flying time over all rows are not doubled. Many free-flight instruments alternate FLARM, FANET
# and ADS-L under one address: summed system by system such an aircraft
# weighs two or three times (6 October 2026, free flight: 47% without signal
# system by system, 45% per aircraft).
AIRCRAFT = "AIRCRAFT"


def source_info(tocall):
    if tocall == AIRCRAFT:
        return ("Radio, any system", "aircraft")
    return SOURCES.get(tocall, (tocall, "other"))


_places = None          # (by 0.25-degree cell, all places), loaded on first use
_sites = None           # list name -> [(lat, lon, name, gliding)], each name unique in its list
_site_raw = {}          # list name -> {unique name: the name as the list gives it}, where they differ
_place_names = {}       # (lat_idx, lon_idx, kind) -> (name, lat, lon) or None
_site_names = None      # (list, name) -> [(lat, lon, display name)], for site_position
COMPASS_8 = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _load_places():
    """data/places-europe.tsv: GeoNames cities5000 within 34-72 N, 25 W-45 E
    (name, country, lat, lon, population; CC BY 4.0), indexed by thermal cell."""
    global _places
    by_cell = collections.defaultdict(list)
    try:
        with open(PLACES_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                name, cc, lat, lon, pop = line.rstrip("\n").split("\t")
                lat, lon = float(lat), float(lon)
                by_cell[(math.floor(lat * THERMAL_CELLS_PER_DEG), math.floor(lon * THERMAL_CELLS_PER_DEG))].append(
                    (int(pop), name, cc, lat, lon))
    except OSError as e:
        logger.error(f"No place names for thermal cells ({e})")
    _places = by_cell


def _load_sites():
    """The flying-site lists of data/ (README, "Data from others"):
    fivl-sites.tsv (id, name, lat, lon; courtesy of FIVL), osm-takeoffs.tsv
    and osm-airfields.tsv (name, lat, lon, icao[, gliding]; ODbL). A missing
    list is skipped, and the cells fall back to the next one."""
    global _sites
    _sites = {}
    for key, fn in SITE_FILES.items():
        rows = []
        try:
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                header = f.readline().lstrip("# ").rstrip("\n").split("\t")
                col = {h: i for i, h in enumerate(header)}
                for line in f:
                    r = line.rstrip("\n").split("\t")
                    name = r[col["name"]]
                    icao = r[col["icao"]] if "icao" in col and len(r) > col["icao"] else ""
                    if icao:
                        name = f"{name} ({icao})"
                    gliding = "gliding" in col and len(r) > col["gliding"] and r[col["gliding"]] == "1"
                    rows.append((float(r[col["lat"]]), float(r[col["lon"]]), name, gliding))
        except (OSError, KeyError, ValueError, IndexError) as e:
            logger.error(f"Flying sites {fn} not loaded ({e})")
        _sites[key] = _qualify_sites(rows, GENERIC_AIRFIELD_WORDS if key == "airfield" else GENERIC_TAKEOFF_WORDS)
        _site_raw[key] = {r[2]: raw[2] for r, raw in zip(_sites[key], rows) if r[2] != raw[2]}


def _generic(name, words):
    tokens = [t for t in re.split(r"[\s\-/,.()'’]+", name.lower()) if t]
    return bool(tokens) and all(t in words or t in COMPASS_WORDS or t.isdigit() for t in tokens)


def _compass(lat0, lon0, lat1, lon1):
    """The direction from the first point to the second on eight points."""
    return COMPASS_8[int((_bearing(lat0, lon0, lat1, lon1) + 22.5) // 45) % 8]


def _nearest_town(lat, lon):
    """(name, lat, lon) of the GeoNames town nearest a point within SITE_TOWN_M, or None."""
    if _places is None:
        _load_places()
    n = THERMAL_CELLS_PER_DEG
    la, lo = math.floor(lat * n), math.floor(lon * n)
    best = None
    for dl in range(-2, 3):
        for dc in range(-3, 4):
            for p in _places.get((la + dl, lo + dc), ()):
                d = _distance(lat, lon, p[3], p[4])
                if d <= SITE_TOWN_M and (best is None or d < best[0]):
                    best = (d, (p[1], p[3], p[4]))
    return best[1] if best else None


def wave_name(lat_idx, lon_idx):
    """The name of a 0.25-degree cell of probable wave, from the GeoNames town
    nearest its centre within SITE_TOWN_M: "near Altdorf (CH)", or
    "20 km SE of Glarus (CH)" from the town to the centre; None without one."""
    if _places is None:
        _load_places()
    n = THERMAL_CELLS_PER_DEG
    clat, clon = (lat_idx + 0.5) / n, (lon_idx + 0.5) / n
    best = None
    for dl in range(-2, 3):
        for dc in range(-3, 4):
            for p in _places.get((lat_idx + dl, lon_idx + dc), ()):
                d = _distance(clat, clon, p[3], p[4])
                if d <= SITE_TOWN_M and (best is None or d < best[0]):
                    best = (d, p)
    if best is None:
        return None
    d, p = best
    km = 5 * round(d / 5000)
    if km <= WAVE_NEAR_KM:
        return f"near {p[1]} ({p[2]})"
    return f"{km} km {_compass(p[3], p[4], clat, clon)} of {p[1]} ({p[2]})"


def _qualify_sites(rows, words):
    """The rows of one site list with every name unique: a name that is generic
    or occurs more than once gets the nearest town, "Startplatz Ost
    (Lenggries)"; if that is still not unique, the distance and direction
    from the town, "(Lenggries, 4 km SE)", and failing that the position.
    Entries with the same name within SITE_SAME_M of each other are one site
    mapped twice, and keep one name between them."""
    # One site mapped twice: the same name within SITE_SAME_M; the first one
    # stands for the others.
    rep = list(range(len(rows)))
    by_name = collections.defaultdict(list)
    for i, r in enumerate(rows):
        for j in by_name[r[2]]:
            if _distance(r[0], r[1], rows[j][0], rows[j][1]) <= SITE_SAME_M:
                rep[i] = j
                break
        else:
            by_name[r[2]].append(i)
    heads = [i for i in range(len(rows)) if rep[i] == i]
    count = collections.Counter(rows[i][2] for i in heads)
    ambiguous = [i for i in heads if count[rows[i][2]] > 1 or _generic(rows[i][2], words)]
    names = {i: rows[i][2] for i in heads}
    towns = {i: _nearest_town(rows[i][0], rows[i][1]) for i in ambiguous}

    def qualify(name, inner):
        # "Startplatz (GS)" + "Brannenburg" -> "Startplatz (GS, Brannenburg)",
        # one pair of brackets rather than two
        return f"{name[:-1]}, {inner})" if name.endswith(")") else f"{name} ({inner})"

    def level(i, k):
        lat, lon, name = rows[i][0], rows[i][1], rows[i][2]
        town = towns[i]
        if k == 1 and town and town[0].lower() not in name.lower():
            return qualify(name, town[0])
        if k == 1 and town:
            k = 2       # "Hang Gliding Interlaken (Interlaken)" would say nothing
        if k == 2 and town:
            km = max(1, round(_distance(town[1], town[2], lat, lon) / 1000))
            return qualify(name, f"{town[0]}, {km} km {_compass(town[1], town[2], lat, lon)}")
        return qualify(name, f"{lat:.3f}, {lon:.3f}")

    todo = ambiguous
    for k in (1, 2, 3):
        for i in todo:
            names[i] = level(i, k)
        seen = collections.Counter(names.values())
        todo = [i for i in todo if seen[names[i]] > 1]
        if not todo:
            break
    return [(r[0], r[1], names[rep[i]], r[3]) for i, r in enumerate(rows)]


def _nearest_site(sites, lat_idx, lon_idx, prefer_gliding=False):
    """The site nearest the cell's centre among those inside the cell or within
    SITE_EDGE_M of its edge; gliding-tagged airfields first when asked."""
    n = THERMAL_CELLS_PER_DEG
    south, north, west, east = lat_idx / n, (lat_idx + 1) / n, lon_idx / n, (lon_idx + 1) / n
    pad_lat = SITE_EDGE_M / 111320
    pad_lon = pad_lat / max(0.1, math.cos(math.radians((south + north) / 2)))
    clat, clon = (south + north) / 2, (west + east) / 2
    best = None
    for lat, lon, name, gliding in sites:
        if south - pad_lat <= lat <= north + pad_lat and west - pad_lon <= lon <= east + pad_lon:
            key = (0 if gliding or not prefer_gliding else 1, _distance(clat, clon, lat, lon))
            if best is None or key < best[0]:
                best = (key, (name, lat, lon))
    return best[1] if best else None


def site_position(kind, name, lat_idx, lon_idx):
    """(name, lat, lon) of the site of the kind's lists (SITE_ORDER) called
    `name` that lies nearest the cell's centre, or None; the name returned is
    the site's unique one. From 8 October 2026 nightly.py stores that unique
    name and the match is exact; days stored before carry the list's own name,
    which two sites can share ("Startplatz"), and the one nearest the cell is
    taken to be it."""
    global _site_names
    if _sites is None:
        _load_sites()
    if _site_names is None:
        # Days stored before 8 October 2026 carry the names as the lists give
        # them, which may be generic or repeated: those are indexed too.
        _site_names = collections.defaultdict(list)
        for which, rows in _sites.items():
            for lat, lon, site, _ in rows:
                _site_names[(which, site[:255])].append((lat, lon, site))
                raw = _site_raw.get(which, {}).get(site, site)
                if raw != site:
                    _site_names[(which, raw[:255])].append((lat, lon, site))
    n = THERMAL_CELLS_PER_DEG
    clat, clon = (lat_idx + 0.5) / n, (lon_idx + 0.5) / n
    best = None
    for which in SITE_ORDER.get(kind, ()):
        for lat, lon, site in _site_names.get((which, name), ()):
            d = _distance(clat, clon, lat, lon)
            if best is None or d < best[0]:
                best = (d, (site, lat, lon))
    return best[1] if best else None


def place_name(lat_idx, lon_idx, kind=None):
    """The name of a 0.25-degree cell for one kind of aircraft; see place_site."""
    site = place_site(lat_idx, lon_idx, kind)
    return site[0] if site else None


def place_site(lat_idx, lon_idx, kind=None):
    """The name of a 0.25-degree cell for one kind of aircraft (PATTERNS.md,
    section 3): a flying site of the kind's lists (SITE_ORDER), else the most
    populous town inside the cell, else the nearest within PLACE_NEAR_M of its
    centre, as "Bassano del Grappa (IT)"; None if there is none (the page then
    shows coordinates). A site's name is given as its list gives it. Returns
    (name, lat, lon), the position being the site's or the town's, or None."""
    key = (lat_idx, lon_idx, kind)
    if key in _place_names:
        return _place_names[key]
    if _sites is None:
        _load_sites()
    for which in SITE_ORDER.get(kind, ()):
        site = _nearest_site(_sites.get(which, ()), lat_idx, lon_idx, prefer_gliding=(which == "airfield"))
        if site:
            _place_names[key] = site
            return site
    if _places is None:
        _load_places()
    inside = _places.get((lat_idx, lon_idx))
    best = max(inside) if inside else None
    if best is None:
        n = THERMAL_CELLS_PER_DEG
        clat, clon = (lat_idx + 0.5) / n, (lon_idx + 0.5) / n
        near = None
        for dl in (-1, 0, 1):
            for dc in (-2, -1, 0, 1, 2):
                for p in _places.get((lat_idx + dl, lon_idx + dc), ()):
                    d = _distance(clat, clon, p[3], p[4])
                    if d <= PLACE_NEAR_M and (near is None or d < near[0]):
                        near = (d, p)
        best = near[1] if near else None
    site = (f"{best[1]} ({best[2]})", best[3], best[4]) if best else None
    _place_names[key] = site
    return site


class SourceTracker:
    def __init__(self, parse_line, connect_db, hidden=lambda device_id: False):
        self.parse_line = parse_line
        self.hidden = hidden      # devices their owners asked OGN not to track
        self.connect_db = connect_db
        self.live = {}            # device_id -> compact dict
        self.live_parsed_at = {}  # device_id -> monotonic time of last parse
        self.heard = {}           # address -> [last time, {system: last time}, category]; hear()
        self.heard_cache = (0, None)
        # (month, source, via, device_id) written today -> the category queued
        # with it. Reset every UTC day: a device seen again tomorrow gets its
        # daily last_seen update anyway, and keeping a whole month of worldwide
        # ADS-B here grew without bound.
        self.seen = {}
        self.seen_day = None
        self.writes = queue.Queue(maxsize=200000)
        self.stats_cache = (0, None)
        # Visibility measure: last fix per (device, source, via), and totals.
        self.last_fix = {}
        self.totals = collections.defaultdict(lambda: [0.0] * N_TOTALS)
        self.visibility_cache = (0, None)
        self.detail = collections.defaultdict(lambda: [0.0] * len(DETAIL_COLUMNS))
        self.grid = collections.defaultdict(lambda: [0.0] * len(GRID_COLUMNS))
        self.terrain = Terrain(DEM_PATH)
        self.stations = {}        # receiver name -> (lat, lon), from their own position reports
        self.pattern = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
        self.history = {}         # (device, source, via) -> deque of recent fixes
        self.scored_at = {}       # (device, source, via) -> time of the last scored fix
        self.rot_devices = set()  # devices that have reported a non-zero turn rate
        # Flying time per aircraft, whatever source or channel each fix came
        # by: last fix per 24-bit address, and seconds per (month, category).
        self.addr_fix = {}
        # Radio per aircraft: last radio fix per address, and when each radio
        # system was last heard from it.
        self.radio_fix = {}
        self.radio_systems = {}
        self.hours = collections.defaultdict(lambda: [0.0, 0.0])
        self.hours_cache = (0, None)
        self.systems_cache = (0, None)
        self._tick, self._now, self._month = None, None, None
        self._days = {}
        self.prediction = collections.defaultdict(lambda: [0.0] * len(PREDICTION_COLUMNS))
        self.prediction_cache = (0, None)
        self.pattern_cache = (0, None)
        self.detail_cache = (0, None)
        self.grid_cache = {}
        # The nightly measures (nightly.py), read from their own tables.
        self.patterns_cache = (0, None)
        self.drones_cache = (0, None)
        self.quality_cache = (0, None)
        self.snapshots_cache = (0, None)
        self.encounters_cache = (0, None)
        self.snapshot_cache = {}  # (month, endpoint) -> (time, JSON text)
        # One lock per statistic, so a slow one (the device counts) never holds
        # up the others, and a thread-local flag with which warm_loop forces a
        # recomputation while visitors keep reading the cached copy.
        self._stat_locks = {}
        self._stat_locks_guard = threading.Lock()
        self._force = threading.local()

    # --- per packet ---------------------------------------------------------

    def handle(self, line):
        try:
            src, rest = line.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            return
        path = head.split(",")
        tocall = same_system(path[0], src)
        # FANET ground stations send their own beacon under the aircraft tocall,
        # with the receiver symbol: they are stations, not devices.
        if (tocall == "OGNSDR" or body[26:27] == "&") and body.startswith("/"):
            m = _position.match(body)
            if m and not (m.group(4) == "00" and m.group(8) == "000"):  # no position yet
                g = m.groups()
                lat = int(g[3]) + float(g[4]) / 60
                lon = int(g[7]) + float(g[8]) / 60
                self.stations[src] = (-lat if g[5] == "S" else lat, -lon if g[9] == "W" else lon)
            return
        if tocall in EXCLUDED or not body.startswith("/") or "h" not in body[:8]:
            return  # status lines and anything that is not a timed position
        if body[26:27] == "_" or (body[8:10] == "00" and body[17:20] == "000"):
            # A weather station (FANET forwards them), or a position within 1
            # degree of 0,0: a device with no fix, or a FLARM packet decoded by
            # a receiver that does not know where it is, since FLARM sends only
            # the low bits of the position and the receiver supplies the rest.
            return

        if self.hidden(src):
            return              # not tracked, not counted, not measured
        id_category, no_track = id_info(body)
        if no_track:
            return              # the device itself asks not to be tracked
        if id_category is None and tocall in AIRCRAFT_ONLY:
            return              # a node on the ground, not an aircraft
        if tocall == REMOTE_ID:
            id_category = DRONE_CATEGORY    # whatever the id declares (from 7 October 2026)
        label, kind = source_info(tocall)
        via = "radio" if kind == "adsb" or _radio_meta.search(body) else "net"

        # The clock and the month string change once a second at most, packets
        # arrive a thousand times a second: compute them once per second.
        tick = int(time.time())
        if tick != self._tick:
            self._tick = tick
            self._now = datetime.datetime.utcnow()
            self._month = self._now.strftime("%Y-%m")
        now, month = self._now, self._month
        if kind != "adsb" and not rebroadcast(tocall, src, id_category):
            self.hear(src[-6:], label, id_category, tick)
        day = now.day
        if day != self.seen_day:
            self.seen = {}
            self.seen_day = day
        key = (month, tocall, via, src)
        stored = self.seen.get(key, False)
        if stored is False:
            self.seen[key] = id_category
            try:
                self.writes.put_nowait(("new", month, tocall, via, src, id_category, now))
            except queue.Full:
                pass  # the writer is stuck; the next day retries
        elif replaceable(tocall, stored) and id_category in AIRCRAFT_CATEGORIES:
            # Heard on the ground first, or by ADS-B before its emitter
            # category, now an aircraft (from 7 October 2026).
            self.seen[key] = id_category
            try:
                self.writes.put_nowait(("category", month, tocall, via, src, id_category, now))
            except queue.Full:
                pass

        if kind != "adsb":
            self.measure(src, tocall, via, body, now, path[-1] if via == "radio" else None, id_category)

        if kind == "adsb" or kind == "adsl":
            return  # ADS-B is not mapped; ADS-L has its own, richer feed
        t = time.monotonic()
        if t - self.live_parsed_at.get(src, 0) < LIVE_MIN_INTERVAL:
            return
        self.live_parsed_at[src] = t
        pkt = self.parse_line(line)
        if not pkt or pkt["lat"] is None or pkt["lon"] is None:
            return
        self.live[src] = {
            "id": src,
            "layer": "radio" if kind == "other" and via == "radio" else LAYER_OF_KIND[kind],
            "source": label,
            "via": via,
            "lat": round(pkt["lat"], 5),
            "lon": round(pkt["lon"], 5),
            "alt": pkt["altitude"],
            "hdg": pkt["heading"],
            "spd": pkt["speed"],
            "vs": pkt["vspeed"],
            # The APRS symbol is unreliable outside FLARM and OGN trackers
            # (SafeSky marks powered aircraft as gliders), so only the OGN
            # device database's model is passed on; the category covers the rest.
            "type": pkt.get("ddb_model"),
            "category": pkt.get("category"),
            "station": pkt["station"] if via == "radio" else None,
            "sig": pkt.get("signal") if via == "radio" else None,
            "fix": pkt.get("gps_fix"),
            "sats": pkt.get("gps_sats"),
            "t": pkt["timestamp"].isoformat(),
            "_mono": t,
            "_raw": line,
        }

    def hear(self, address, label, category, tick):
        """Aircraft heard in the last HEARD_WINDOW, for the main page's live box
        (from 7 October 2026): address -> [last time, {system: last time},
        last category declared]. Pruned every minute in prune_loop."""
        h = self.heard.get(address)
        if h is None:
            self.heard[address] = [tick, {label: tick}, category]
            return
        h[0] = tick
        h[1][label] = tick
        if category is not None:
            h[2] = category

    def heard_stats(self):
        """Distinct aircraft heard in the last HEARD_WINDOW on any system but ADS-B.

        One aircraft is one 24-bit address: it counts once in `aircraft`, once
        per system in `by_system`, and in `by_kind` by the last category it
        declared. The exclusions are those of the measures (handle): receivers
        and synthetic packets, weather and FANET ground stations, positions
        near 0,0, no-track devices and the OGN device database's hidden ones,
        Meshtastic nodes without an aircraft category, PilotAware rebroadcasts.
        """
        stamp, cached = self.heard_cache
        if cached is not None and time.time() - stamp < HEARD_CACHE_SECONDS:
            return cached
        cutoff = time.time() - HEARD_WINDOW
        aircraft, by_system, by_kind = 0, collections.Counter(), collections.Counter()
        for last, systems, category in list(self.heard.values()):
            if last < cutoff:
                continue
            aircraft += 1
            for label, t in list(systems.items()):
                if t >= cutoff:
                    by_system[label] += 1
            by_kind[HEARD_KINDS.get(category, "other")] += 1
        out = {"window_minutes": HEARD_WINDOW // 60, "aircraft": aircraft,
               "by_system": dict(by_system.most_common()),
               "by_kind": {k: by_kind.get(k, 0) for k in ("powered", "glider", "free_flight", "helicopter",
                                                           "drone", "other")},
               "adsl": by_system.get(source_info("OGADSL")[0], 0),
               "as_of": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}
        self.heard_cache = (time.time(), out)
        return out

    def measure(self, src, tocall, via, body, now, station=None, id_category=None):
        """Accumulate the visibility totals of METHOD.md for one packet."""
        fix = parse_fix(body, now)
        if fix is None:
            return
        t, lat, lon, course, speed, rot, symbol, alt_m = fix
        # Only a turn rate the aircraft sends counts, and only once the device
        # has shown it is really sending one (a non-zero value).
        if rot is not None:
            if tocall not in TURN_RATE_SOURCES:
                rot = None
            elif rot != 0:
                self.rot_devices.add((src, tocall))
            elif (src, tocall) not in self.rot_devices:
                rot = None
        if id_category is not None:
            category = id_category
        else:
            category = SYMBOL_CATEGORY.get(symbol, UNKNOWN_CATEGORY)
        key = (src, tocall, via)
        prev = self.last_fix.get(key)
        dn = int(t // 86400)
        day = self._days.get(dn)
        if day is None:
            day = self._days[dn] = datetime.datetime.utcfromtimestamp(dn * 86400).strftime("%Y-%m-%d")
        tot = self.totals[(day, tocall, via, category)]
        if implausible(category, speed, alt_m):
            # The device is sending, so no silence may run across this fix:
            # it ends the track, and the next plausible fix starts a new one.
            tot[4] += 1
            self.last_fix.pop(key, None)
            self.addr_fix.pop(src[-6:], None)
            self.radio_fix.pop(src[-6:], None)
            return
        tot[0] += 1
        if rot is not None:
            tot[1] += 1
        if prev is not None and t <= prev[0]:
            return                          # older than what we already have
        if calendar.timegm(now.timetuple()) - t > STALE_SECONDS:
            return                          # relayed late; would open a false gap
        self.count_hours(day[:7], src[-6:], category, t, lat, lon, speed)
        if via == "radio" and tocall in RADIO_CADENCE:
            self.count_radio_aircraft(day[:7], src[-6:], category, tocall, t, lat, lon, speed, alt_m)
        if station is not None and course is not None:
            self.record_pattern(day[:7], tocall, category, station, lat, lon, t, course, rot, prev, body, speed)
        self.record_prediction(day[:7], tocall, category, key, (t, lat, lon, course, speed, rot))
        self.last_fix[key] = (t, lat, lon, course, speed, rot, alt_m, category, day[:7])
        if prev is None:
            return
        pt, plat, plon, pcourse, pspeed, prot, palt = prev[:7]
        seconds = t - pt
        if seconds > VANISH_MINUTES[0] * 60 and (pspeed or 0) >= FLYING_KT.get(category, AIRBORNE_KT):
            self.count_vanish(day[:7], tocall, via, category, plat, plon, palt, seconds)
        if seconds > SESSION_BREAK:
            tot[3] += 1
            return
        # Last-point estimator: the error is the distance actually covered.
        e0 = _distance(plat, plon, lat, lon)
        if e0 > IMPLAUSIBLE_MS * max(seconds, 1):
            tot[4] += 1
            return
        if not flying(category, pspeed, speed):
            return
        tot[2] += 1
        tot[5] += seconds
        # Without the turn rate: straight along the course whatever the packet says.
        if pcourse is not None and pspeed:
            elat, elon = _move(plat, plon, pcourse, pspeed * 0.514444 * seconds)
            e2 = _distance(elat, elon, lat, lon)
        else:
            e2 = e0
        # Packet-based estimator: the same, unless the turn rate says it is circling.
        circling = prot is not None and abs(prot) >= CIRCLING_ROT
        e1 = e0 if circling else e2
        n = len(GAP_THRESHOLDS)
        o0 = [_over(seconds, e0, d) for d in GAP_THRESHOLDS]
        o1 = [_over(seconds, e1, d) for d in GAP_THRESHOLDS]
        o2 = [_over(seconds, e2, d) for d in GAP_THRESHOLDS]
        for i in range(n):
            tot[6 + i] += o0[i]
            tot[6 + n + i] += o1[i]

        # Where and how high: attributed to the point where it was last seen.
        month = day[:7]
        ground = self.terrain.elevation(plat, plon)
        agl = palt - ground if palt is not None and ground is not None else None
        det = self.detail[(month, tocall, via, category, msl_band(palt), agl_band(agl))]
        det[0] += 1
        det[3] += seconds
        for i in range(n):
            det[4 + i] += o0[i]
            det[7 + i] += o1[i]
            det[10 + i] += o2[i]
        if prot is not None:
            det[1] += 1
            det[13] += seconds
            det[14] += o1[0]; det[15] += o1[1]; det[16] += o2[0]; det[17] += o2[1]
        for i, x in enumerate(AGE_LIMITS):
            det[23 + i] += min(seconds, x)
        for i, x in enumerate(INTERVAL_LIMITS):
            if seconds <= x:
                det[32 + i] += 1
        if seconds <= 3:
            det[27] += 1
        if seconds <= 6:
            det[28] += 1
        if circling:
            det[2] += 1
            det[18] += seconds
            det[19] += o1[0]; det[20] += o1[1]; det[21] += o2[0]; det[22] += o2[1]
        kind = source_info(tocall)[1]
        # Time without signal: only the part of the gap beyond the interval
        # the source keeps by design, at the slower end of the segment (an
        # aircraft that slows down makes a distance-based app wait longer).
        cadence = cadence_of(tocall, kind, via)
        late = None
        if cadence is not None:
            slow = min(pspeed or 0, speed or 0) * 0.514444
            late = max(0.0, seconds - expected_interval(cadence, slow) - CADENCE_TOLERANCE)
            det[38] += seconds
            det[39] += late
            det[40] += seconds
            det[41] += late
        grp = "adsl" if kind == "adsl" else "radio" if kind == "other" and via == "radio" else LAYER_OF_KIND[kind]
        cell_key = (month, math.floor(plat / CELL_DEG), math.floor(plon / CELL_DEG), grp, via)
        cell = self.grid[cell_key]
        cell[0] += 1
        cell[1] += seconds
        cell[2] += o0[0]
        cell[3] += o1[0]
        cell[4] += o1[1]
        cell[5] += o0[1]
        if late is not None:
            cell[6] += seconds
            cell[7] += late
            cell[8] += seconds
            cell[9] += late

    def count_hours(self, month, address, category, t, lat, lon, speed):
        """Flying time per aircraft (METHOD.md): one address, all its sources.

        Every source and channel feeds the same timeline, so an aircraft heard
        by FLARM, FANET and ADS-L at once counts its time once, and so does a
        phone app that uses the device's own address. The segment rules are
        those of the visibility measure.
        """
        prev = self.addr_fix.get(address)
        if prev is not None and t <= prev[0]:
            return                          # already covered by another source
        self.addr_fix[address] = (t, lat, lon, speed)
        if prev is None:
            return
        seconds = t - prev[0]
        if seconds > SESSION_BREAK:
            return
        if _distance(prev[1], prev[2], lat, lon) > IMPLAUSIBLE_MS * max(seconds, 1):
            return
        if not flying(category, prev[3], speed):
            return
        h = self.hours[(month, category)]
        h[0] += 1
        h[1] += seconds
        if category in (6, 7):
            # Free flight per aircraft, where it was last seen: the
            # denominator of question 5 from 6 October 2026 (group "pga";
            # "pg", summed over every source and channel, stopped growing).
            pg = self.grid[(month, math.floor(prev[1] / CELL_DEG), math.floor(prev[2] / CELL_DEG), "pga", "radio")]
            pg[0] += 1
            pg[1] += seconds
            pg[6] += seconds
            pg[8] += seconds

    def count_radio_aircraft(self, month, address, category, tocall, t, lat, lon, speed, alt_m):
        """Time without signal by radio for the aircraft, whichever system is heard.

        One timeline per address over every radio system with a known
        interval; a silence counts beyond the longest interval among the
        systems heard from that address in the last SESSION_BREAK, plus the
        tolerance, i.e. only when every system is late on its own interval.
        Until 6 October 2026 evening the shortest was used, which judged an
        instrument's FANET packets by FLARM's 1 s. The segment rules are those of the visibility measure.
        """
        heard = self.radio_systems.setdefault(address, {})
        heard[tocall] = t
        prev = self.radio_fix.get(address)
        if prev is not None and t <= prev[0]:
            return                          # already covered by another system
        self.radio_fix[address] = (t, lat, lon, speed, alt_m)
        if prev is None:
            return
        seconds = t - prev[0]
        if seconds > SESSION_BREAK:
            return
        if _distance(prev[1], prev[2], lat, lon) > IMPLAUSIBLE_MS * max(seconds, 1):
            return
        if not flying(category, prev[3], speed):
            return
        interval = max(RADIO_CADENCE[s][2] for s, ts in heard.items() if t - ts <= SESSION_BREAK)
        late = max(0.0, seconds - interval - CADENCE_TOLERANCE)
        ground = self.terrain.elevation(prev[1], prev[2])
        agl = prev[4] - ground if prev[4] is not None and ground is not None else None
        det = self.detail[(month, AIRCRAFT, "radio", category, msl_band(prev[4]), agl_band(agl))]
        det[40] += seconds
        det[41] += late
        cell = self.grid[(month, math.floor(prev[1] / CELL_DEG), math.floor(prev[2] / CELL_DEG), "aircraft", "radio")]
        cell[8] += seconds
        cell[9] += late

    def count_vanish(self, month, tocall, via, category, lat, lon, alt_m, silent_seconds):
        """One disappearance, at the height of the last position seen (METHOD.md)."""
        ground = self.terrain.elevation(lat, lon)
        agl = alt_m - ground if alt_m is not None and ground is not None else None
        det = self.detail[(month, tocall, via, category, msl_band(alt_m), agl_band(agl))]
        for i, minutes in enumerate(VANISH_MINUTES):
            if silent_seconds > minutes * 60:
                det[29 + i] += 1

    def record_pattern(self, month, tocall, category, station, lat, lon, t, course, rot, prev, body="", speed=None):
        """One received radio packet while circling: count it by relative angle."""
        if (speed or 0) < FLYING_KT.get(category, AIRBORNE_KT):
            return      # an aircraft turning on the ground is not circling (from 6 October 2026 evening)
        if rot is not None:
            circling = abs(rot) >= CIRCLING_ROT
        elif prev is not None and prev[3] is not None and 0 < t - prev[0] <= 10:
            turn = ((course - prev[3] + 540) % 360) - 180
            circling = abs(turn) / (t - prev[0]) >= COURSE_CHANGE_DEG_S
        else:
            circling = False
        where = self.stations.get(station)
        if not circling or where is None:
            return
        km = _distance(lat, lon, where[0], where[1]) / 1000
        band = sum(1 for edge in PATTERN_DIST_KM if km >= edge)
        relative = (_bearing(lat, lon, where[0], where[1]) - course) % 360
        sector = int(((relative + PATTERN_SECTOR / 2) % 360) // PATTERN_SECTOR)
        row = self.pattern[(month, tocall, category, band, PATTERN_CENTRED + sector)]
        row[0] += 1
        m = _snr.search(body)
        if m and km > 0.1:
            # Signal-to-noise corrected by the free-space loss over the distance.
            row[1] += float(m.group(1)) + 20 * math.log10(km)
            row[2] += 1

    def record_prediction(self, month, tocall, category, key, fix):
        """Score the four predictions of METHOD.md ending at this fix."""
        hist = self.history.get(key)
        if hist is None:
            hist = self.history[key] = collections.deque()
        t, lat, lon = fix[0], fix[1], fix[2]
        while hist and t - hist[0][0] > HISTORY_SECONDS:
            hist.popleft()
        hist.append(fix)
        # Score a sample: at most one end fix every PREDICTION_EVERY seconds per device.
        if t - self.scored_at.get(key, 0) < PREDICTION_EVERY:
            return
        self.scored_at[key] = t
        fixes = list(hist)[:-1]
        for h in HORIZONS:
            best = None
            for i, f in enumerate(fixes):
                d = abs((t - f[0]) - h)
                if d <= 1 and (best is None or d < best[0]):
                    best = (d, i)
            if best is None:
                continue
            i = best[1]
            st, slat, slon, scourse, sspeed, srot = fixes[i]
            if scourse is None or not sspeed or sspeed < AIRBORNE_KT:
                continue
            seconds = t - st
            if _distance(slat, slon, lat, lon) > IMPLAUSIBLE_MS * seconds:
                continue                    # a shared address or a corrupt fix
            v = sspeed * 0.514444
            derived = None
            for j in range(i - 1, -1, -1):
                pt, _, _, pcourse, _, _ = fixes[j]
                if st - pt > 6:
                    break
                if st - pt >= 2 and pcourse is not None:
                    derived = (((scourse - pcourse + 540) % 360) - 180) / (st - pt)
                    break
            transmitted = srot * 3.0 if srot is not None else None
            rate = transmitted if transmitted is not None else derived
            circling = 1 if rate is not None and abs(rate) >= CIRCLING_ROT * 3.0 else 0
            preds = {
                "straight": _arc(slat, slon, scourse, v, 0.0, seconds),
                "last_point": (slat, slon),
            }
            if derived is not None:
                preds["derived"] = _arc(slat, slon, scourse, v, derived, seconds)
            if transmitted is not None:
                preds["transmitted"] = _arc(slat, slon, scourse, v, transmitted, seconds)
            if derived is not None and transmitted is not None:
                preds["derived_paired"] = preds["derived"]
                preds["transmitted_paired"] = preds["transmitted"]
            if transmitted is not None:
                # Emulated loss: the receiver kept only the start and the fix g
                # seconds before it, and derives the turn rate from that pair.
                for g in LOSS_GAPS:
                    old = None
                    for j in range(i - 1, -1, -1):
                        pt = fixes[j][0]
                        if st - pt > g + 0.5:
                            break
                        if abs((st - pt) - g) <= 0.5 and fixes[j][3] is not None:
                            old = fixes[j]
                            break
                    if old is None:
                        continue
                    rate_g = (((scourse - old[3] + 540) % 360) - 180) / (st - old[0])
                    preds["derived_g%d" % g] = _arc(slat, slon, scourse, v, rate_g, seconds)
                    preds["transmitted_g%d" % g] = preds["transmitted"]
            for name, (plat, plon) in preds.items():
                err = _distance(plat, plon, lat, lon)
                row = self.prediction[(month, tocall, category, h, circling, name)]
                b = bisect.bisect_right(ERROR_BINS, err)
                row[b] += 1
                row[-2] += 1
                row[-1] += err

    def visibility_loop(self):
        """Append the accumulated totals every VISIBILITY_FLUSH seconds.

        Rows are only ever inserted, each tagged with the flush time, so the
        service needs no UPDATE privilege on the table; readers sum them.
        """
        conn = None
        while True:
            time.sleep(VISIBILITY_FLUSH)
            totals, self.totals = self.totals, collections.defaultdict(lambda: [0.0] * N_TOTALS)
            detail, self.detail = self.detail, collections.defaultdict(lambda: [0.0] * len(DETAIL_COLUMNS))
            grid, self.grid = self.grid, collections.defaultdict(lambda: [0.0] * len(GRID_COLUMNS))
            pattern, self.pattern = self.pattern, collections.defaultdict(lambda: [0.0, 0.0, 0.0])
            prediction, self.prediction = self.prediction, collections.defaultdict(lambda: [0.0] * len(PREDICTION_COLUMNS))
            hours, self.hours = self.hours, collections.defaultdict(lambda: [0.0, 0.0])
            self.addr_fix = {k: v for k, v in list(self.addr_fix.items())
                             if time.time() - v[0] < SESSION_BREAK}
            self.radio_fix = {k: v for k, v in list(self.radio_fix.items())
                              if time.time() - v[0] < SESSION_BREAK}
            self.radio_systems = {k: v for k, v in list(self.radio_systems.items())
                                  if time.time() - max(v.values()) < SESSION_BREAK}
            self.history = {k: v for k, v in list(self.history.items())
                            if v and time.time() - v[-1][0] < HISTORY_SECONDS}
            self.scored_at = {k: v for k, v in list(self.scored_at.items())
                              if time.time() - v < HISTORY_SECONDS}
            cutoff = time.time() - SESSION_BREAK
            keep = {}
            for k, v in list(self.last_fix.items()):
                if v[0] > cutoff:
                    keep[k] = v
                elif (v[4] or 0) >= FLYING_KT.get(v[7], AIRBORNE_KT):
                    # Silent for more than SESSION_BREAK and last seen flying: gone.
                    self.count_vanish(v[8], k[1], k[2], v[7], v[1], v[2], v[6], SESSION_BREAK + 1)
            self.last_fix = keep
            if not totals and not detail and not grid and not pattern and not prediction and not hours:
                continue
            flushed = datetime.datetime.utcnow().replace(microsecond=0)
            rows = [(day, tocall, via, cat, flushed, int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4])) +
                    tuple(round(x, 1) for x in v[5:])
                    for (day, tocall, via, cat), v in totals.items()]
            try:
                if conn is None:
                    conn = self.connect_db()
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO daily_visibility
                           (day, source, via, category, flushed_at, packets, rot_packets,
                            segments, sessions, implausible, air_seconds,
                            p0_300, p0_1000, p0_3000, p1_300, p1_1000, p1_3000)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        rows,
                    )
            except pymysql.MySQLError as e:
                logger.error(f"Error writing to database (daily_visibility, {len(rows)} rows): {e}")
                conn = None
            try:
                if conn is None:
                    conn = self.connect_db()
                with conn.cursor() as cur:
                    cur.executemany(DETAIL_SQL, [k + tuple(round(x, 1) for x in v) for k, v in detail.items()])
                    cur.executemany(GRID_SQL, [k + tuple(round(x, 1) for x in v) for k, v in grid.items()])
            except pymysql.MySQLError as e:
                logger.error(f"Error writing to database (visibility detail/grid, {len(detail)}+{len(grid)} rows): {e}")
                conn = None
            try:
                if conn is None:
                    conn = self.connect_db()
                with conn.cursor() as cur:
                    cur.executemany(PATTERN_SQL, [k + tuple(round(x, 2) for x in v) for k, v in pattern.items()])
                    cur.executemany(PREDICTION_SQL, [k + tuple(round(x, 1) for x in v) for k, v in prediction.items()])
            except pymysql.MySQLError as e:
                logger.error(f"Error writing to database (pattern/prediction, {len(pattern)}+{len(prediction)} rows): {e}")
                conn = None
            try:
                if conn is None:
                    conn = self.connect_db()
                with conn.cursor() as cur:
                    cur.executemany(HOURS_SQL, [k + tuple(round(x, 1) for x in v) for k, v in hours.items()])
            except pymysql.MySQLError as e:
                logger.error(f"Error writing to database (hours, {len(hours)} rows): {e}")
                conn = None

    def archive_loop(self):
        """Keep device addresses for the current and the previous month only.

        A device address can be traced to an aircraft and its pilot, and once a
        month is over its counts no longer change. So every month older than
        the previous one is reduced to counts in the *_summary tables and its
        addresses are deleted, in one transaction per table. The previous month
        is kept whole for the month-to-month return of device ids, and for the
        per-pilot circling test, which reads the two months together. From
        7 October 2026 the per-pilot thermals of nightly.py are archived the
        same way, and each reduction also keeps what the figures that need
        addresses publish (archive_once).
        """
        time.sleep(60)
        while True:
            self.archive_once()
            time.sleep(ARCHIVE_EVERY)

    def archive_once(self):
        """One pass of archive_loop.

        Each table older than keep_from is reduced, in one transaction with
        the deletion of its addresses, by every step listed for it: SQL run
        with keep_from as its parameters, or a method given the cursor. From
        7 October 2026 the reductions also keep what the figures that need
        addresses would publish: systems per aircraft, the return of devices
        from one month to the next, and the per-pilot circling test.
        """
        today = datetime.datetime.utcnow().date().replace(day=1)
        keep_from = (today - datetime.timedelta(days=1)).strftime("%Y-%m")
        older = "month < %s"
        steps = [
            ("monthly_devices", older, [
                """INSERT INTO monthly_devices_summary (month, prefix, category, devices)
                   SELECT month, LEFT(device_id, 3), COALESCE(category, 255), COUNT(*)
                       FROM monthly_devices WHERE month < %s
                   GROUP BY month, LEFT(device_id, 3), COALESCE(category, 255)"""]),
            ("monthly_sources", older, [
                f"""INSERT INTO monthly_sources_summary (month, source, via, category, devices, multi_day)
                   SELECT month, src, via, cat, COUNT(*), SUM(DATE(last_seen) > DATE(first_seen))
                       FROM (SELECT month, {SAME_SYSTEM_SQL} AS src, via, device_id,
                                    MIN(COALESCE(category, 255)) AS cat,
                                    MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen
                                 FROM monthly_sources WHERE month < %s AND {COUNTED_SQL} GROUP BY 1, 2, 3, 4) d
                   GROUP BY month, src, via, cat""",
                # Return: of the devices of a month, how many were heard again
                # the month after, per source whatever the channel. The month
                # after is the previous month at worst, so it is still whole.
                f"""INSERT INTO monthly_return_summary (month, source, devices, returned)
                   SELECT a.month, a.src, COUNT(*), SUM(b.device_id IS NOT NULL)
                       FROM (SELECT DISTINCT month, {SAME_SYSTEM_SQL} AS src, device_id
                                 FROM monthly_sources WHERE month < %s AND {COUNTED_SQL}) a
                       LEFT JOIN (SELECT DISTINCT month, {SAME_SYSTEM_SQL} AS src, device_id
                                      FROM monthly_sources WHERE {COUNTED_SQL}) b
                         ON b.src = a.src AND b.device_id = a.device_id
                        AND b.month = DATE_FORMAT(STR_TO_DATE(CONCAT(a.month, '-01'), '%%Y-%%m-%%d')
                                                  + INTERVAL 1 MONTH, '%%Y-%%m')
                   GROUP BY a.month, a.src""",
                self.archive_systems]),
            ("daily_circling_pilot", "day < CONCAT(%s, '-01')", [self.archive_circling]),
            # Routes by address (PATTERNS.md, section 6), reduced to flights and
            # distinct aircraft per route and month (from 7 October 2026).
            ("daily_routes", "day < CONCAT(%s, '-01')", [
                """INSERT INTO monthly_routes_summary (month, airfield_a, airfield_b, flights, aircraft)
                   SELECT DATE_FORMAT(day, '%%Y-%%m'), airfield_a, airfield_b, SUM(flights), COUNT(DISTINCT address)
                       FROM daily_routes WHERE day < CONCAT(%s, '-01') GROUP BY 1, 2, 3"""]),
        ]
        for table, where, summaries in steps:
            conn = None
            try:
                conn = self.connect_db()
                conn.begin()
                with conn.cursor() as cur:
                    month = "DATE_FORMAT(day, '%%Y-%%m')" if table.startswith("daily_") else "month"
                    cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT {month}) FROM {table} WHERE {where}", (keep_from,))
                    n, months = cur.fetchone()
                    if n:
                        for summarise in summaries:
                            if callable(summarise):
                                summarise(cur, keep_from)
                            else:
                                cur.execute(summarise, (keep_from,) * summarise.count("%s"))
                        cur.execute(f"DELETE FROM {table} WHERE {where}", (keep_from,))
                conn.commit()
                if n:
                    logger.info(f"Archived {table}: {n} addresses of {months} month(s) before {keep_from} reduced to counts")
                    self.stats_cache = (0, None)
                    self.systems_cache = (0, None)
                    self.patterns_cache = (0, None)
            except pymysql.MySQLError as e:
                logger.error(f"Error archiving {table}: {e}")
                if conn is not None:
                    try:
                        conn.rollback()
                    except pymysql.MySQLError:
                        pass
            finally:
                if conn is not None:
                    conn.close()

    def archive_systems(self, cur, keep_from):
        """Systems per aircraft of the months being archived, as systems_stats publishes them."""
        cur.execute(f"SELECT month, source, device_id, category FROM monthly_sources "
                    f"WHERE month < %s AND {COUNTED_SQL}", (keep_from,))
        for g in self.systems_groups(cur.fetchall()):
            cat = UNKNOWN_CATEGORY if g["category"] is None else g["category"]
            cur.execute("""INSERT INTO monthly_systems_summary
                               (month, category, one, two, three_or_more, radio_and_phone, phone_only,
                                other_combinations, adsl, adsl_with_flarm, adsl_with_fanet, adsl_only)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (g["month"], cat, g["one"], g["two"], g["three_or_more"], g["radio_and_phone"],
                         g["phone_only"], g["other_combinations"], g["adsl"], g["adsl_with_flarm"],
                         g["adsl_with_fanet"], g["adsl_only"]))
            for c in g["combinations"]:
                cur.execute("INSERT INTO monthly_systems_combo_summary (month, category, systems, aircraft) "
                            "VALUES (%s, %s, %s, %s)", (g["month"], cat, c["systems"][:255], c["aircraft"]))

    def archive_circling(self, cur, keep_from):
        """The per-pilot circling figures of the months being archived, without addresses."""
        cur.execute("""SELECT DATE_FORMAT(day, '%%Y-%%m'), category, SUM(right_hand + left_hand), SUM(right_hand)
                           FROM daily_circling_pilot WHERE day < CONCAT(%s, '-01')
                       GROUP BY 1, address, 2""", (keep_from,))
        groups = collections.defaultdict(list)
        for month, cat, n, r in cur.fetchall():
            groups[(month, int(cat))].append((int(n), int(r)))
        for (month, cat), pilots in groups.items():
            bins = collections.Counter((sum(1 for e in THERMAL_BANDS if n >= e) - 1, min(9, int(r / n * 10)))
                                       for n, r in pilots if n)
            for (b, decile), k in bins.items():
                cur.execute("INSERT INTO monthly_circling_pilot_summary "
                            "(month, category, thermal_band, share_decile, pilots) VALUES (%s, %s, %s, %s, %s)",
                            (month, cat, b, decile, k))
            p = self.pooled_right(pilots)
            for x in self.preference_sums(pilots, p):
                cur.execute("""INSERT INTO monthly_circling_preference
                                   (month, category, min_thermals, pilots, thermals, right_hand, chance_var_sum,
                                    share_sum, share_sq_sum, right_80, left_80, expected_80)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (month, cat, x["min_thermals"], x["pilots"], sum(n for n, _ in pilots),
                             sum(r for _, r in pilots), x["chance_var_sum"], x["share_sum"], x["share_sq_sum"],
                             x["right_80"], x["left_80"], x["expected_80"]))

    def stat_lock(self, name):
        with self._stat_locks_guard:
            return self._stat_locks.setdefault(name, threading.Lock())

    def forcing(self):
        return getattr(self._force, "on", False)

    def warm_loop(self):
        """Recompute every statistic before its cache expires.

        Until 6 October 2026 a cache was rebuilt by the first request after it
        expired, and since all statistics shared one lock that visitor waited
        for all of them, the device counts alone taking about three seconds.
        """
        time.sleep(20)
        while True:
            self._force.on = True
            started = time.time()
            try:
                now = datetime.datetime.utcnow()
                previous = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
                for name, f in (("detail", self.detail_stats), ("prediction", self.prediction_stats),
                                ("grid", lambda: self.grid_stats(now.strftime("%Y-%m"))),
                                ("grid previous", lambda: self.grid_stats(previous)),
                                ("hours", self.hours_stats), ("systems", self.systems_stats),
                                ("pattern", self.pattern_stats), ("sources", self.monthly_stats),
                                ("visibility", self.visibility_stats), ("patterns", self.patterns_stats),
                                ("drones", self.drones_stats), ("quality", self.quality_stats),
                                ("snapshots", self.snapshots_stats), ("encounters", self.encounters_stats)):
                    try:
                        f()
                    except Exception as e:      # one failing statistic must not stop the others
                        logger.error(f"Warming {name} statistics: {e}")
            finally:
                self._force.on = False
            logger.debug(f"Statistics warmed in {time.time() - started:.1f} s")
            time.sleep(max(60, STATS_CACHE_SECONDS - 120 - (time.time() - started)))

    def visibility_stats(self, days=62):
        """Sums per day, source, via and category for the last `days` days."""
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.visibility_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("visibility"):
            stamp, cached = self.visibility_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT day, source, via, category,
                               SUM(packets), SUM(rot_packets), SUM(segments), SUM(sessions),
                               SUM(implausible), SUM(air_seconds), SUM(p0_300), SUM(p0_1000), SUM(p0_3000),
                               SUM(p1_300), SUM(p1_1000), SUM(p1_3000)
                            FROM daily_visibility
                        WHERE day >= CURDATE() - INTERVAL %s DAY
                        GROUP BY day, source, via, category
                    """, (days,))
                    rows = cur.fetchall()
            finally:
                conn.close()
            out = []
            for r in rows:
                label, kind = source_info(r[1])
                out.append({
                    "day": r[0].isoformat(), "source": r[1], "label": label, "kind": kind,
                    "via": r[2], "category": None if r[3] == UNKNOWN_CATEGORY else int(r[3]),
                    "packets": int(r[4]), "rot_packets": int(r[5]), "segments": int(r[6]),
                    "sessions": int(r[7]), "implausible": int(r[8]), "air_seconds": float(r[9]),
                    "last_point": {str(d): float(r[10 + i]) for i, d in enumerate(GAP_THRESHOLDS)},
                    "estimate": {str(d): float(r[13 + i]) for i, d in enumerate(GAP_THRESHOLDS)},
                })
            self.visibility_cache = (time.time(), out)
            return out

    def detail_stats(self):
        """Monthly totals per source, channel, category and height bands."""
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.detail_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("detail"):
            stamp, cached = self.detail_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT month, source, via, category, msl_band, agl_band, " +
                                ", ".join(DETAIL_COLUMNS) + " FROM monthly_visibility_detail")
                    rows = cur.fetchall()
            finally:
                conn.close()
            out = []
            for r in rows:
                label, kind = source_info(r[1])
                d = {"month": r[0], "source": r[1], "label": label, "kind": kind, "via": r[2],
                     "category": None if r[3] == UNKNOWN_CATEGORY else int(r[3]),
                     "msl_band": None if r[4] == UNKNOWN_BAND else int(r[4]),
                     "agl_band": None if r[5] == UNKNOWN_BAND else int(r[5])}
                for c, v in zip(DETAIL_COLUMNS, r[6:]):
                    d[c] = float(v)
                # The cadence rule question 5 applies to this source, if any,
                # so that the page shows the rule the code follows.
                cad = cadence_of(r[1], kind, r[2])
                d["cadence_m"], d["cadence_floor"], d["cadence_heartbeat"] = cad or (None, None, None)
                out.append(d)
            self.detail_cache = (time.time(), out)
            return out

    def hours_stats(self):
        """Flying time per month and category, each aircraft counted once."""
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.hours_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("hours"):
            stamp, cached = self.hours_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT month, category, segments, air_seconds FROM monthly_hours ORDER BY month")
                    rows = cur.fetchall()
            finally:
                conn.close()
            out = [{"month": r[0], "category": None if r[1] == UNKNOWN_CATEGORY else int(r[1]),
                    "segments": float(r[2]), "air_seconds": float(r[3])} for r in rows]
            self.hours_cache = (time.time(), out)
            return out

    def systems_stats(self):
        """How many aircraft are heard on more than one system (METHOD.md).

        One aircraft is one 24-bit address. Its systems are the sources it was
        heard by that month, whatever the channel (FANET by radio and through
        an internet gateway is one system), grouped as radio, phone app or
        tracker; platforms that relay other sources are left out, and so are
        aircraft heard by ADS-B alone (6 October 2026: 10,820 such addresses,
        almost all airliners, sat among "powered aircraft"). Only counts
        leave this function, and combinations shared by fewer than
        SYSTEMS_MIN_COMBO aircraft are pooled so that no rare aircraft stands out.
        """
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.systems_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("systems"):
            stamp, cached = self.systems_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT month, source, device_id, category FROM monthly_sources WHERE {COUNTED_SQL}")
                    rows = cur.fetchall()
                    # Months whose addresses are gone, as archive_systems kept them.
                    cur.execute("""SELECT month, category, one, two, three_or_more, radio_and_phone, phone_only,
                                          other_combinations, adsl, adsl_with_flarm, adsl_with_fanet, adsl_only
                                       FROM monthly_systems_summary""")
                    archived = cur.fetchall()
                    cur.execute("SELECT month, category, systems, aircraft FROM monthly_systems_combo_summary "
                                "ORDER BY aircraft DESC")
                    combos = collections.defaultdict(list)
                    for month, cat, systems, n in cur.fetchall():
                        combos[(month, int(cat))].append({"systems": systems, "aircraft": int(n)})
            finally:
                conn.close()
            out = self.systems_groups(rows)
            live = {g["month"] for g in out}
            for r in archived:
                if r[0] in live:
                    continue
                cat = int(r[1])
                out.append({"month": r[0], "category": None if cat == UNKNOWN_CATEGORY else cat,
                            "one": int(r[2]), "two": int(r[3]), "three_or_more": int(r[4]),
                            "radio_and_phone": int(r[5]), "phone_only": int(r[6]),
                            "combinations": combos.get((r[0], cat), []), "other_combinations": int(r[7]),
                            "adsl": int(r[8]), "adsl_with_flarm": int(r[9]), "adsl_with_fanet": int(r[10]),
                            "adsl_only": int(r[11])})
            out.sort(key=lambda g: (g["month"], g["category"] if g["category"] is not None else -1))
            self.systems_cache = (time.time(), out)
            return out

    @staticmethod
    def systems_groups(rows):
        """Systems per aircraft from (month, source, device_id, category) rows; counts only."""
        systems = collections.defaultdict(set)
        category = {}
        for month, tocall, device_id, cat in rows:
            addr = device_id[-6:]
            label, kind = source_info(same_system(tocall, device_id))
            if kind == "platform":
                continue
            cls = "phone" if kind == "app" else "tracker" if kind == "tracker" else "radio"
            systems[(month, addr)].add((label, cls))
            if cat is not None and cat != UNKNOWN_CATEGORY and (month, addr) not in category:
                category[(month, addr)] = int(cat)
        groups = {}
        for key, sys in systems.items():
            if {x[0] for x in sys} == {"ADS-B"}:
                continue    # heard by ADS-B alone: almost all airliners, left out as everywhere else
            month = key[0]
            cat = category.get(key)
            g = groups.setdefault((month, cat), {"n": [0, 0, 0], "radio_phone": 0, "phone_only": 0,
                                                 "combos": collections.Counter(), "adsl": [0, 0, 0, 0]})
            names = sorted({x[0] for x in sys})
            classes = {x[1] for x in sys}
            g["n"][min(len(names), 3) - 1] += 1
            if "phone" in classes and "radio" in classes:
                g["radio_phone"] += 1
            elif classes == {"phone"}:
                g["phone_only"] += 1
            if len(names) > 1:
                g["combos"][" + ".join(names)] += 1
            if "ADS-L" in names:
                a = g["adsl"]
                a[0] += 1
                a[1] += "FLARM" in names
                a[2] += "FANET" in names
                a[3] += len(names) == 1
        out = []
        for (month, cat), g in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1] if x[0][1] is not None else -1)):
            combos = [{"systems": k, "aircraft": v} for k, v in g["combos"].most_common() if v >= SYSTEMS_MIN_COMBO]
            pooled = sum(v for v in g["combos"].values() if v < SYSTEMS_MIN_COMBO)
            out.append({"month": month, "category": cat,
                        "one": g["n"][0], "two": g["n"][1], "three_or_more": g["n"][2],
                        "radio_and_phone": g["radio_phone"], "phone_only": g["phone_only"],
                        "combinations": combos, "other_combinations": pooled,
                        "adsl": g["adsl"][0], "adsl_with_flarm": g["adsl"][1],
                        "adsl_with_fanet": g["adsl"][2], "adsl_only": g["adsl"][3]})
        return out

    def pattern_stats(self):
        """Received radio packets while circling, by source, category, distance band and sector."""
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.pattern_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("pattern"):
            stamp, cached = self.pattern_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT month, source, category, dist_band, sector, packets, snr_sum, snr_n "
                                "FROM monthly_reception_pattern")
                    rows = cur.fetchall()
            finally:
                conn.close()
            out = [{"month": r[0], "source": r[1], "label": source_info(r[1])[0],
                    "category": None if r[2] == UNKNOWN_CATEGORY else int(r[2]),
                    "dist_band": int(r[3]), "centred": int(r[4]) >= PATTERN_CENTRED,
                    "sector_deg": int(r[4]) % PATTERN_CENTRED * PATTERN_SECTOR,
                    "packets": int(r[5]), "snr_sum": float(r[6]), "snr_n": int(r[7])} for r in rows]
            self.pattern_cache = (time.time(), out)
            return out

    def prediction_stats(self):
        """Prediction errors by source, category, horizon, circling and predictor."""
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.prediction_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("prediction"):
            stamp, cached = self.prediction_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT month, source, category, horizon, circling, predictor, " +
                                ", ".join(PREDICTION_COLUMNS) + " FROM monthly_prediction")
                    rows = cur.fetchall()
            finally:
                conn.close()
            out = []
            for r in rows:
                d = {"month": r[0], "source": r[1], "label": source_info(r[1])[0],
                     "category": None if r[2] == UNKNOWN_CATEGORY else int(r[2]),
                     "horizon": int(r[3]), "circling": bool(r[4]), "predictor": r[5],
                     "bins_m": list(ERROR_BINS)}
                for c, v in zip(PREDICTION_COLUMNS, r[6:]):
                    d[c] = float(v)
                out.append(d)
            self.prediction_cache = (time.time(), out)
            return out

    # --- the nightly measures (nightly.py; METHOD.md section 10, PATTERNS.md) --

    def _cached(self, name, attr, compute):
        """The cache and lock pattern of the statistics above, for one more."""
        stamp, cached = getattr(self, attr)
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock(name):
            stamp, cached = getattr(self, attr)
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    out = compute(cur)
            finally:
                conn.close()
            setattr(self, attr, (time.time(), out))
            return out

    @staticmethod
    def _nightly_days(cur):
        cur.execute("SELECT day, hours_read, hours_missing, notes FROM nightly_runs ORDER BY day")
        return [{"day": r[0].isoformat(), "hours_read": int(r[1]),
                 "hours_missing": [int(h) for h in r[2].split(",") if h], "notes": r[3] or None}
                for r in cur.fetchall()]

    @staticmethod
    def pooled_right(pilots):
        """The share of right-hand thermals among all of these pilots' thermals."""
        total = sum(n for n, _ in pilots)
        return sum(r for _, r in pilots) / total if total else None

    @staticmethod
    def preference_sums(pilots, p):
        """The sums the per-pilot test is made of (PATTERNS.md, section 2).

        `pilots` is a list of (thermals, right-hand thermals), `p` the share of
        right-hand thermals in the population. If every pilot chose each side
        with probability p, the right-hand share of a pilot with n thermals
        would vary by p(1-p)/n; an observed variance larger than that, and
        more pilots at 80% or more on one side than the binomial expects,
        point to personal preference (or to gaggles, which a pilot follows).
        Kept as sums so that an archived month, whose addresses are gone,
        still gives the same figures (archive_circling).
        """
        out = []
        for nmin in PREFERENCE_MIN_THERMALS:
            sel = [(n, r) for n, r in pilots if n >= nmin]
            x = {"min_thermals": nmin, "pilots": len(sel), "chance_var_sum": 0.0, "share_sum": 0.0,
                 "share_sq_sum": 0.0, "right_80": 0, "left_80": 0, "expected_80": 0.0,
                 "expected_histogram": [0.0] * 10}
            for n, r in sel:
                share = r / n
                x["share_sum"] += share
                x["share_sq_sum"] += share * share
                x["right_80"] += share >= 0.8
                x["left_80"] += share <= 0.2
                if p is not None:
                    x["chance_var_sum"] += p * (1 - p) / n
                    x["expected_80"] += sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j)
                                            for j in range(n + 1) if j / n >= 0.8 or j / n <= 0.2)
                    for d, v in enumerate(expected_deciles(n, p)):
                        x["expected_histogram"][d] += v
            out.append(x)
        return out

    @staticmethod
    def preference_row(x, p, histogram, expected=None, approximate=False):
        """The published row of one preference test, from its sums.

        `expected_histogram` is the number of pilots per decile that chance
        alone would give, each pilot with its own thermal count (from
        7 October 2026); for an archived month the counts are known only by
        band, and it is marked `expected_approximate`.
        """
        n = x["pilots"]
        row = {"min_thermals": x["min_thermals"], "pilots": n, "p_right": round(p, 4) if p is not None else None,
               "observed_var": None, "chance_var": None, "ratio": None,
               "right_80": None, "left_80": None, "expected_80": None, "histogram": None,
               "expected_histogram": None, "expected_approximate": approximate}
        if n >= 5 and p is not None and 0 < p < 1:
            mean = x["share_sum"] / n
            obs = max(0.0, x["share_sq_sum"] / n - mean * mean)
            chance = x["chance_var_sum"] / n
            row.update(observed_var=round(obs, 5), chance_var=round(chance, 5),
                       ratio=round(obs / chance, 2) if chance else None,
                       right_80=int(x["right_80"]), left_80=int(x["left_80"]),
                       expected_80=round(x["expected_80"], 1), histogram=histogram,
                       expected_histogram=[round(v, 2) for v in expected] if expected else None)
        return row

    @classmethod
    def preference_test(cls, pilots, p):
        """The published rows of the test for a list of (thermals, right-hand thermals)."""
        out = []
        for x in cls.preference_sums(pilots, p):
            hist = collections.Counter(min(9, int(r / n * 10)) for n, r in pilots if n >= x["min_thermals"])
            out.append(cls.preference_row(x, p, [hist[b] for b in range(10)], x["expected_histogram"]))
        return out

    def routes(self, cur):
        """Routes of powered aircraft (PATTERNS.md, section 6): unordered pairs of
        airfields, per month, with flights and distinct aircraft; a pair only
        with ROUTE_MIN_AIRCRAFT aircraft that month, the rest as "other routes".
        Months whose addresses are kept are counted from daily_routes; older
        ones from monthly_routes_summary, which archive_once fills."""
        cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), airfield_a, airfield_b, SUM(flights), COUNT(DISTINCT address)
                           FROM daily_routes GROUP BY 1, 2, 3""")
        rows = [(r[0], r[1], r[2], int(r[3]), int(r[4])) for r in cur.fetchall()]
        cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), COUNT(DISTINCT address) FROM daily_routes GROUP BY 1""")
        aircraft_all = {r[0]: int(r[1]) for r in cur.fetchall()}
        live = {r[0] for r in rows}
        cur.execute("SELECT month, airfield_a, airfield_b, flights, aircraft FROM monthly_routes_summary")
        rows += [(r[0], r[1], r[2], int(r[3]), int(r[4])) for r in cur.fetchall() if r[0] not in live]
        out = []
        other = collections.defaultdict(lambda: [0, 0, 0])
        for month, a, b, flights, aircraft in rows:
            if aircraft >= ROUTE_MIN_AIRCRAFT:
                out.append({"month": month, "airfield_a": a, "airfield_b": b, "flights": flights, "aircraft": aircraft})
            else:
                o = other[month]
                o[0] += 1
                o[1] += flights
                o[2] += aircraft
        for month, (n, flights, aircraft) in other.items():
            out.append({"month": month, "airfield_a": "other routes", "airfield_b": None, "routes": n,
                        "flights": flights, "aircraft_sum": aircraft})
        out.sort(key=lambda r: (r["month"], -r["flights"]))
        return {"min_aircraft": ROUTE_MIN_AIRCRAFT, "routes": out, "aircraft_on_routes": aircraft_all}

    def thermal_places(self, cur):
        """The busiest and the strongest thermal places of the current and the
        previous month, per kind, among places with at least THERMAL_CELL_MIN
        thermals in the window (PATTERNS.md, section 3). A place is a
        0.25-degree cell, or up to the four cells around the grid corner
        nearest a site, when they are named after that site (8 October 2026)."""
        now = datetime.datetime.utcnow().date()
        since = (now.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        # Every cell, with no minimum: the threshold applies to the merged place,
        # so a cell under it can count towards a neighbour named after the same site.
        cur.execute("""SELECT kind, lat_idx, lon_idx, SUM(thermals), SUM(climb_sum), SUM(climbs)
                           FROM daily_thermals WHERE day >= %s AND kind IN ('glider', 'paraglider', 'hang_glider')
                       GROUP BY 1, 2, 3""", (since,))
        found = cur.fetchall()
        # The site most of a cell's thermals were flown from (nightly.py,
        # daily_thermal_sites); without one, the site nearest the cell's
        # centre or the town (place_name).
        cur.execute("""SELECT kind, lat_idx, lon_idx, site, SUM(thermals) FROM daily_thermal_sites
                           WHERE day >= %s GROUP BY 1, 2, 3, 4""", (since,))
        # A stored name is resolved to its site first, so that a site's old
        # (generic) and new (unique) names add up in the same cell.
        by_site = collections.Counter()
        for kind, la, lo, site, th in cur.fetchall():
            la, lo = int(la), int(lo)
            found_site = site_position(kind, site, la, lo)
            by_site[(kind, la, lo, found_site or (site, None, None))] += int(th)
        from_site = {}
        for (kind, la, lo, site), th in by_site.items():
            key = (kind, la, lo)
            if key not in from_site or th > from_site[key][0]:
                from_site[key] = (th, site)
        n = THERMAL_CELLS_PER_DEG
        groups = collections.defaultdict(list)
        for kind, la, lo, th, csum, cn in found:
            la, lo = int(la), int(lo)
            named = from_site.get((kind, la, lo))
            if named:
                site = named[1] if named[1][1] is not None else None
                name = named[1][0]
            else:
                site = place_site(la, lo, kind)
                name = site[0] if site else None
            cell = {"la": la, "lo": lo, "name": name,
                    "named_by": "take-off" if named else "nearest", "thermals": int(th),
                    "climb_sum": float(csum), "climbs": int(cn)}
            # A place is the site's own block: the 2 by 2 cells around the grid
            # corner nearest to it. Cells named after the same site (its name
            # and position, never the name alone) merge within that block; one
            # named after it elsewhere, or with no site found, stays a place
            # of its own, and so does a cell with no name.
            key = ("cell", kind, la, lo)
            cell["site"] = site
            if site is not None:
                ci, cj = round(site[1] * n), round(site[2] * n)
                if la in (ci - 1, ci) and lo in (cj - 1, cj):
                    key = ("site", kind, site[0], site[1], site[2])
            groups[key].append(cell)
        places = []
        for key, group in groups.items():
            th = sum(c["thermals"] for c in group)
            if th < THERMAL_CELL_MIN:
                continue
            csum = sum(c["climb_sum"] for c in group)
            cn = sum(c["climbs"] for c in group)
            top = max(group, key=lambda c: c["thermals"])
            place = {"kind": key[1], "south": min(c["la"] for c in group) / n,
                     "north": (max(c["la"] for c in group) + 1) / n,
                     "west": min(c["lo"] for c in group) / n, "east": (max(c["lo"] for c in group) + 1) / n,
                     "cells": len(group), "name": top["name"], "named_by": top["named_by"],
                     "thermals": th, "mean_climb": round(csum / cn, 2) if cn else None,
                     "away_km": None, "away_dir": None, "label": top["name"]}
            # A place outside its site's block (a cell of its own) is labelled with
            # its distance and direction from the site, to its centre, so that
            # thermals found far from a take-off are not read as its own.
            site = top["site"]
            if key[0] == "cell" and site is not None:
                clat, clon = (place["south"] + place["north"]) / 2, (place["west"] + place["east"]) / 2
                km = max(5, 5 * round(_distance(site[1], site[2], clat, clon) / 5000))
                place["away_km"] = km
                place["away_dir"] = _compass(site[1], site[2], clat, clon)
                place["label"] = f"{top['name']}, {km} km {place['away_dir']}"
            places.append(place)
        out = {"from": since.isoformat(), "to": now.isoformat(), "min_thermals": THERMAL_CELL_MIN,
               "busiest": [], "strongest": []}
        for kind in ("glider", "paraglider", "hang_glider"):
            mine = [c for c in places if c["kind"] == kind]
            out["busiest"] += sorted(mine, key=lambda c: -c["thermals"])[:THERMAL_PLACES_TOP]
            out["strongest"] += sorted((c for c in mine if c["mean_climb"] is not None),
                                       key=lambda c: -c["mean_climb"])[:THERMAL_PLACES_TOP]
        return out

    @staticmethod
    def with_free_flight(rows, sums):
        """rows plus, for every row of a paraglider or hang glider, the same row
        summed over both under the kind free_flight."""
        together = {}
        for r in rows:
            if r["kind"] not in ("paraglider", "hang_glider"):
                continue
            key = tuple((k, v) for k, v in r.items() if k != "kind" and k not in sums)
            t = together.get(key)
            if t is None:
                t = together[key] = dict(r, kind="free_flight")
            else:
                for k in sums:
                    t[k] += r[k]
        return rows + list(together.values())

    def patterns_stats(self):
        """Flying time by local solar hour and weekday, circling direction, parked aircraft."""
        def compute(cur):
            cur.execute("""SELECT DATE_FORMAT(local_date, '%Y-%m'), WEEKDAY(local_date), solar_hour, category,
                                  SUM(air_seconds), SUM(aircraft)
                               FROM daily_hours_solar GROUP BY 1, 2, 3, 4""")
            hours = [{"month": r[0], "weekday": int(r[1]), "solar_hour": int(r[2]),
                      "category": None if r[3] == UNKNOWN_CATEGORY else int(r[3]),
                      "air_seconds": float(r[4]), "aircraft_hours": int(r[5])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(local_date, '%Y-%m'), WEEKDAY(local_date), COUNT(DISTINCT local_date)
                               FROM daily_hours_solar GROUP BY 1, 2""")
            dates = [{"month": r[0], "weekday": int(r[1]), "dates": int(r[2])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), category, SUM(thermals_right), SUM(thermals_left),
                                  SUM(degrees_right), SUM(degrees_left), SUM(seconds_right), SUM(seconds_left),
                                  SUM(aircraft)
                               FROM daily_circling GROUP BY 1, 2""")
            circling = [{"month": r[0], "category": int(r[1]), "thermals_right": int(r[2]), "thermals_left": int(r[3]),
                         "degrees_right": float(r[4]), "degrees_left": float(r[5]),
                         "seconds_right": float(r[6]), "seconds_left": float(r[7]), "aircraft_days": int(r[8])}
                        for r in cur.fetchall()]
            p_month = {(c["month"], c["category"]): c["thermals_right"] / (c["thermals_right"] + c["thermals_left"])
                       for c in circling if c["thermals_right"] + c["thermals_left"]}
            # The per-pilot test over the months whose addresses are kept.
            now = datetime.datetime.utcnow().date()
            since = (now.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
            cur.execute("""SELECT category, SUM(right_hand + left_hand), SUM(right_hand)
                               FROM daily_circling_pilot WHERE day >= %s GROUP BY address, category""", (since,))
            by_cat = collections.defaultdict(list)
            for cat, n, r in cur.fetchall():
                by_cat[int(cat)].append((int(n), int(r)))
            preference = []
            for cat, pilots in sorted(by_cat.items()):
                p = self.pooled_right(pilots)
                for row in self.preference_test(pilots, p):
                    preference.append(dict({"from": since.isoformat(), "to": now.isoformat(), "category": cat}, **row))
            # Archived months: the same test month by month, from the sums
            # kept when their addresses were deleted (archive_circling).
            cur.execute("""SELECT month, category, thermal_band, share_decile, pilots
                               FROM monthly_circling_pilot_summary""")
            deciles = collections.defaultdict(lambda: [0] * 10)
            band_pilots = collections.defaultdict(collections.Counter)   # (month, cat) -> band -> pilots
            for month, cat, b, decile, k in cur.fetchall():
                band_pilots[(month, int(cat))][int(b)] += int(k)
                for nmin in PREFERENCE_MIN_THERMALS:
                    if THERMAL_BANDS[int(b)] >= nmin:
                        deciles[(month, int(cat), nmin)][int(decile)] += int(k)
            cur.execute("""SELECT month, category, min_thermals, pilots, thermals, right_hand, chance_var_sum,
                                  share_sum, share_sq_sum, right_80, left_80, expected_80
                               FROM monthly_circling_preference ORDER BY month, category, min_thermals""")
            for r in cur.fetchall():
                x = dict(zip(("min_thermals", "pilots"), (int(r[2]), int(r[3]))),
                         chance_var_sum=float(r[6]), share_sum=float(r[7]), share_sq_sum=float(r[8]),
                         right_80=int(r[9]), left_80=int(r[10]), expected_80=float(r[11]))
                p = int(r[5]) / int(r[4]) if r[4] else None
                expected = None
                if p is not None and 0 < p < 1:
                    # Each band's pilots taken at the band's typical count.
                    expected = [0.0] * 10
                    for b, k in band_pilots[(r[0], int(r[1]))].items():
                        if THERMAL_BANDS[b] >= int(r[2]):
                            for d, v in enumerate(expected_deciles(BAND_THERMALS[b], p)):
                                expected[d] += k * v
                row = self.preference_row(x, p, deciles.get((r[0], int(r[1]), int(r[2]))), expected, approximate=True)
                preference.append(dict({"from": r[0] + "-01", "to": r[0], "category": int(r[1])}, **row))
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), category_a, category_b, SUM(same_side), SUM(opposite_side)
                               FROM daily_gaggles GROUP BY 1, 2, 3""")
            gaggles = []
            for month, a, b, same, opp in cur.fetchall():
                pa, pb = p_month.get((month, int(a))), p_month.get((month, int(b)))
                # Two pilots choosing a side independently agree with this probability.
                expected = pa * pb + (1 - pa) * (1 - pb) if pa is not None and pb is not None else None
                gaggles.append({"month": month, "category_a": int(a), "category_b": int(b), "same_side": int(same),
                                "opposite_side": int(opp),
                                "same_share_by_chance": round(expected, 4) if expected is not None else None})
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), system_name, category, SUM(aircraft), SUM(seconds), SUM(packets)
                               FROM daily_parked GROUP BY 1, 2, 3""")
            parked = [{"month": r[0], "system": r[1], "category": int(r[2]), "aircraft_days": int(r[3]),
                       "seconds": float(r[4]), "packets": int(r[5])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), method, towed_kind, terrain, height_band, duration_band,
                                  SUM(launches)
                               FROM daily_launches GROUP BY 1, 2, 3, 4, 5, 6""")
            launches = [{"month": r[0], "method": r[1], "towed_kind": r[2], "terrain": r[3] or None,
                         "height_band": None if r[4] == UNKNOWN_BAND else int(r[4]),
                         "duration_band": None if r[5] == UNKNOWN_BAND else int(r[5]), "launches": int(r[6])}
                        for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), method, lat_idx, lon_idx, SUM(launches)
                               FROM daily_launches GROUP BY 1, 2, 3, 4""")
            launch_cells = [{"month": r[0], "method": r[1], "lat": int(r[2]), "lon": int(r[3]),
                             "launches": int(r[4])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), tows_band, SUM(tugs)
                               FROM daily_tug_tows GROUP BY 1, 2""")
            tugs = [{"month": r[0], "tows_band": int(r[1]), "tug_days": int(r[2])} for r in cur.fetchall()]
            # Thermals: by kind, solar hour and the two bands; cells only where at
            # least THERMAL_CELL_MIN thermals were flown in the month, so that a
            # cell never stands for a handful of identifiable flights.
            # terrain: plain (relief within 5 km under 600 m), mountain, unknown;
            # empty before 7 October 2026 (PATTERNS.md, section 12).
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind, terrain, solar_hour, climb_band, radius_band,
                                  SUM(thermals), SUM(climb_sum), SUM(climbs), SUM(radius_sum)
                               FROM daily_thermals GROUP BY 1, 2, 3, 4, 5, 6""")
            thermals = [{"month": r[0], "kind": r[1], "terrain": r[2] or None, "solar_hour": int(r[3]),
                         "climb_band": None if r[4] == UNKNOWN_BAND else int(r[4]),
                         "radius_band": None if r[5] == UNKNOWN_BAND else int(r[5]), "thermals": int(r[6]),
                         "climb_sum": float(r[7]), "climbs": int(r[8]), "radius_sum": float(r[9])}
                        for r in cur.fetchall()]
            # Paragliders and hang gliders apart since 7 October 2026 (the kind
            # "free_flight" before), and also together, summed here.
            thermals = self.with_free_flight(thermals, ("thermals", "climb_sum", "climbs", "radius_sum"))
            # 1-degree cells, aggregated from the 0.25-degree cells stored since
            # 7 October 2026 (rows of 6 October were recomputed at 0.25).
            n = THERMAL_CELLS_PER_DEG
            cur.execute(f"""SELECT DATE_FORMAT(day, '%Y-%m'), kind, FLOOR(lat_idx / {n}), FLOOR(lon_idx / {n}),
                                   SUM(thermals), SUM(climb_sum), SUM(climbs)
                                FROM daily_thermals GROUP BY 1, 2, 3, 4 HAVING SUM(thermals) >= {THERMAL_CELL_MIN}""")
            thermal_cells = [{"month": r[0], "kind": r[1], "lat": int(r[2]), "lon": int(r[3]), "thermals": int(r[4]),
                              "climb_sum": float(r[5]), "climbs": int(r[6])} for r in cur.fetchall()]
            thermal_places = self.thermal_places(cur)
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind_a, kind_b, vsep_band, SUM(thermals)
                               FROM daily_mixed_thermals GROUP BY 1, 2, 3, 4""")
            mixed = [{"month": r[0], "kind_a": r[1], "kind_b": r[2], "vsep_band": int(r[3]), "thermals": int(r[4])}
                     for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind, terrain, solar_hour, agl_band, SUM(air_seconds)
                               FROM daily_agl_hours GROUP BY 1, 2, 3, 4, 5""")
            agl = [{"month": r[0], "kind": r[1], "terrain": r[2] or None, "solar_hour": int(r[3]),
                    "agl_band": None if r[4] == UNKNOWN_BAND else int(r[4]), "air_seconds": float(r[5])}
                   for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind, terrain, solar_hour, SUM(circling_seconds),
                                  SUM(air_seconds)
                               FROM daily_circling_time GROUP BY 1, 2, 3, 4""")
            circling_time = [{"month": r[0], "kind": r[1], "terrain": r[2] or None, "solar_hour": int(r[3]),
                              "circling_seconds": float(r[4]), "air_seconds": float(r[5])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind, terrain, launch, start_hour, duration_band,
                                  extent_band, path_band, SUM(flights), SUM(seconds), SUM(path_m)
                               FROM daily_flights GROUP BY 1, 2, 3, 4, 5, 6, 7, 8""")
            flights = [{"month": r[0], "kind": r[1], "terrain": r[2] or None, "launch": r[3] or None,
                        "start_hour": int(r[4]), "duration_band": int(r[5]), "extent_band": int(r[6]),
                        "path_band": int(r[7]), "flights": int(r[8]), "seconds": float(r[9]), "path_m": float(r[10])}
                       for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%%Y-%%m'), day >= %s, lat_idx, lon_idx, SUM(climbs)
                               FROM daily_wave GROUP BY 1, 2, 3, 4""", (WAVE_QUARTER_FROM,))
            wave = []
            for month, quarter, la, lo, climbs in cur.fetchall():
                la, lo = int(la), int(lo)
                # "lat" and "lon" stay the 1-degree cell for every row, as the
                # page has read them since 7 October; a quarter-degree row adds
                # its bounds and its name.
                row = {"month": month, "lat": la, "lon": lo, "climbs": int(climbs), "cell_deg": 1, "name": None}
                if quarter:
                    q = THERMAL_CELLS_PER_DEG
                    row.update({"lat": la // q, "lon": lo // q, "cell_deg": 1 / q, "south": la / q,
                                "north": (la + 1) / q, "west": lo / q, "east": (lo + 1) / q,
                                "name": wave_name(la, lo)})
                wave.append(row)
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind, emitter, altitude_ref, altitude_band, speed_band,
                                  SUM(segments), SUM(seconds), SUM(aircraft)
                               FROM daily_cruise GROUP BY 1, 2, 3, 4, 5, 6""")
            cruise = [{"month": r[0], "kind": r[1], "emitter": r[2], "altitude_ref": r[3], "altitude_band": int(r[4]),
                       "speed_band": int(r[5]), "segments": int(r[6]), "seconds": float(r[7]),
                       "aircraft_days": int(r[8])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), night, agl_band, SUM(air_seconds), SUM(aircraft)
                               FROM daily_helicopter_night GROUP BY 1, 2, 3""")
            helicopters = [{"month": r[0], "night": bool(r[1]), "agl_band": None if r[2] == UNKNOWN_BAND else int(r[2]),
                            "air_seconds": float(r[3]), "aircraft_days": int(r[4])} for r in cur.fetchall()]
            cur.execute(f"""SELECT DATE_FORMAT(day, '%Y-%m'), lat_idx, lon_idx, night, SUM(air_seconds), SUM(aircraft)
                                FROM daily_helicopter_night GROUP BY 1, 2, 3, 4
                                HAVING SUM(aircraft) >= {HELICOPTER_CELL_MIN}""")
            helicopter_cells = [{"month": r[0], "lat": int(r[1]), "lon": int(r[2]), "night": bool(r[3]),
                                 "air_seconds": float(r[4]), "aircraft_days": int(r[5])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), share_band, SUM(tugs), SUM(tow_seconds), SUM(air_seconds)
                               FROM daily_tug_time GROUP BY 1, 2""")
            tug_time = [{"month": r[0], "share_band": int(r[1]), "tug_days": int(r[2]), "tow_seconds": float(r[3]),
                         "air_seconds": float(r[4])} for r in cur.fetchall()]
            # Paragliders and hang gliders apart since 7 October 2026, and
            # together as free_flight, summed here as for the thermals.
            agl = self.with_free_flight(agl, ("air_seconds",))
            circling_time = self.with_free_flight(circling_time, ("circling_seconds", "air_seconds"))
            flights = self.with_free_flight(flights, ("flights", "seconds", "path_m"))
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind, terrain, class, landing, distance_band, path_band,
                                  SUM(flights)
                               FROM daily_flight_classes GROUP BY 1, 2, 3, 4, 5, 6, 7""")
            flight_classes = [{"month": r[0], "kind": r[1], "terrain": r[2], "class": r[3], "landing": r[4],
                               "distance_band": int(r[5]), "path_band": int(r[6]), "flights": int(r[7])}
                              for r in cur.fetchall()]
            routes = self.routes(cur)
            return {"days": self._nightly_days(cur), "hours": hours, "dates": dates, "circling": circling,
                    "flight_classes": flight_classes, "routes": routes,
                    "preference": preference, "gaggles": gaggles, "parked": parked, "launches": launches,
                    "thermals": thermals, "thermal_cells": thermal_cells, "thermal_places": thermal_places,
                    "mixed_thermals": mixed,
                    "agl_hours": agl, "circling_time": circling_time, "flights": flights, "wave": wave,
                    "cruise": cruise,
                    "helicopters": helicopters, "helicopter_cells": helicopter_cells, "tug_time": tug_time,
                    "pattern_bands": {"climb_ms": [0, 0.5, 1, 1.5, 2, 3, 4], "radius_m": [0, 30, 50, 80, 120, 200],
                                      "vsep_m": [0, 50, 100, 200, 300],
                                      "agl_m": [0, 50, 120, 300, 600, 1200, 2000],
                                      "flight_minutes": [0, 10, 30, 60, 120, 240, 480],
                                      "flight_extent_km": [0, 1, 5, 20, 50, 100, 300],
                                      "flight_path_km": [0, 5, 20, 50, 100, 300, 500],
                                      "cruise_altitude_ft": [0, 3000, 5000, 7000, 9000, 11000, 15000],
                                      "cruise_speed_kmh": [0, 100, 150, 200, 250, 300],
                                      "tug_tow_share": [0, 0.25, 0.5, 0.75]},
                    "launch_cells": launch_cells, "tug_tows": tugs,
                    "launch_bands": {"release_agl_m": [0, 300, 450, 600, 900],
                                     "winch_top_agl_m": [0, 300, 400, 500, 700],
                                     "tow_minutes": [0, 3, 5, 8, 12], "tows_per_tug": [1, 2, 6, 11]}}
        return self._cached("patterns", "patterns_cache", compute)

    # Which devices declaring a drone are drones (nightly.py, METHOD.md 10.1).
    DRONE_EVIDENCE = {1: "confirmed", 2: "uncertain", 3: "crewed_adsb_emitter", 4: "crewed_ddb_thermal",
                      5: "stray", 6: "crewed_climb_extent"}

    def drones_stats(self):
        """Drones per 1-degree cell, height and speed band, systems, extent and encounters.

        Every list is split by `evidence`, confirmed or uncertain; addresses
        with crewed evidence, and those whose category 13 was a stray packet
        among others, are in none of them and appear only in `classes`, with
        how many and how long they flew, so the page can say how many were
        set aside and on what grounds.
        """
        def compute(cur):
            name = self.DRONE_EVIDENCE.get
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), evidence, lat_idx, lon_idx, SUM(air_seconds), SUM(aircraft)
                               FROM daily_drone_cells GROUP BY 1, 2, 3, 4""")
            cells = [{"month": r[0], "evidence": name(int(r[1])), "lat": int(r[2]), "lon": int(r[3]),
                      "air_seconds": float(r[4]), "drone_days": int(r[5])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), evidence, height_band, speed_band, SUM(air_seconds)
                               FROM daily_drones GROUP BY 1, 2, 3, 4""")
            bands = [{"month": r[0], "evidence": name(int(r[1])),
                      "height_band": None if r[2] == UNKNOWN_BAND else int(r[2]),
                      "speed_band": int(r[3]), "air_seconds": float(r[4])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), evidence, systems, SUM(air_seconds)
                               FROM daily_drones GROUP BY 1, 2, 3""")
            systems = [{"month": r[0], "evidence": name(int(r[1])), "systems": r[2], "air_seconds": float(r[3])}
                       for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), evidence, extent_band, SUM(drones)
                               FROM daily_drone_extent GROUP BY 1, 2, 3""")
            extent = [{"month": r[0], "evidence": name(int(r[1])), "extent_band": int(r[2]), "drone_days": int(r[3])}
                      for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), evidence, drone_systems, other_category, other_systems,
                                  distance_band, shared_system, SUM(encounters)
                               FROM daily_drone_encounters GROUP BY 1, 2, 3, 4, 5, 6, 7""")
            encounters = [{"month": r[0], "evidence": name(int(r[1])), "drone_systems": r[2],
                           "other_category": int(r[3]), "other_systems": r[4], "distance_band": int(r[5]),
                           "shared_system": bool(r[6]), "encounters": int(r[7])} for r in cur.fetchall()]
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), evidence, SUM(addresses), SUM(air_seconds)
                               FROM daily_drone_classes GROUP BY 1, 2""")
            classes = [{"month": r[0], "evidence": name(int(r[1])), "address_days": int(r[2]),
                        "air_seconds": float(r[3])} for r in cur.fetchall()]
            return {"days": self._nightly_days(cur), "height_bands_m": [0, 50, 120, 300],
                    "speed_bands_kmh": [0, 20, 50, 100], "extent_bands_m": [0, 1000, 3000, 10000],
                    "distance_bands_m": [0, 300, 600, 1000], "classes": classes, "cells": cells, "bands": bands,
                    "systems": systems, "extent": extent, "encounters": encounters}
        return self._cached("drones", "drones_cache", compute)

    def encounters_stats(self):
        """Crewed aircraft of different kinds coming close, per month (METHOD.md 10.2).

        Aggregates only: per kind pair, threshold, distance and closing-speed
        band and the systems of each side; never an event, a date finer than
        the month, or a place.
        """
        def compute(cur):
            cur.execute("""SELECT DATE_FORMAT(day, '%Y-%m'), kind_a, kind_b, threshold, distance_band, closing_band,
                                  systems_a, systems_b, shares_radio, shares_any, SUM(encounters)
                               FROM daily_crewed_encounters GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10""")
            rows = [{"month": r[0], "kind_a": r[1], "kind_b": r[2], "threshold": r[3],
                     "distance_band": int(r[4]), "closing_band": None if r[5] == UNKNOWN_BAND else int(r[5]),
                     "systems_a": r[6], "systems_b": r[7], "shares_radio": bool(r[8]), "shares_any": bool(r[9]),
                     "encounters": int(r[10])} for r in cur.fetchall()]
            return {"days": self._nightly_days(cur),
                    "thresholds": {"wide": {"metres": 1000, "vertical_m": 150, "seconds": 10},
                                   "close": {"metres": 300, "vertical_m": 100, "seconds": 10}},
                    "distance_bands_m": {"wide": [0, 300, 600, 1000], "close": [0, 100, 200, 300]},
                    "closing_bands_kmh": [0, 50, 100, 200, 400], "encounters": rows}
        return self._cached("encounters", "encounters_cache", compute)

    def quality_stats(self, month=None):
        """Data-quality findings per system and per OGN receiver, last 7 days and this month.

        With `month` (YYYY-MM, for the monthly snapshot), the last 7 days and
        the whole of that month instead, uncached.
        """
        def compute(cur):
            if month:
                first = month + "-01"
                windows = (("last_7_days", f"day > LAST_DAY('{first}') - INTERVAL 7 DAY AND day <= LAST_DAY('{first}')"),
                           ("this_month", f"day >= '{first}' AND day <= LAST_DAY('{first}')"))
            else:
                windows = (("last_7_days", "day >= CURDATE() - INTERVAL 7 DAY"),
                           ("this_month", "day >= DATE_FORMAT(CURDATE(), '%Y-%m-01')"))
            systems, receivers, summary = [], [], []
            for label, where in windows:
                cur.execute(f"""SELECT name, check_name, SUM(count), SUM(total) FROM daily_quality
                                    WHERE scope = 'system' AND {where} GROUP BY 1, 2""")
                systems += [{"window": label, "system": r[0], "check": r[1], "count": int(r[2]), "total": int(r[3])}
                            for r in cur.fetchall()]
                cur.execute(f"""SELECT name, check_name, COUNT(*), SUM(count), SUM(total), AVG(value), MAX(day)
                                    FROM daily_quality
                                    WHERE scope = 'receiver' AND name <> '*' AND {where} GROUP BY 1, 2""")
                receivers += [{"window": label, "receiver": r[0], "check": r[1], "days_flagged": int(r[2]),
                               "count": int(r[3]), "total": int(r[4]),
                               "value": round(float(r[5]), 4) if r[5] is not None else None,
                               "last_flagged": r[6].isoformat()} for r in cur.fetchall()]
                cur.execute(f"""SELECT check_name, SUM(count), SUM(total), COUNT(*) FROM daily_quality
                                    WHERE scope = 'receiver' AND name = '*' AND {where} GROUP BY 1""")
                summary += [{"window": label, "check": r[0], "receiver_days_flagged": int(r[1]),
                             "receiver_days_judged": int(r[2]), "days": int(r[3])} for r in cur.fetchall()]
            return {"days": self._nightly_days(cur), "systems": systems, "receivers": receivers,
                    "receivers_summary": summary}
        if month:
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValueError(month)
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    return compute(cur)
            finally:
                conn.close()
        return self._cached("quality", "quality_cache", compute)

    # --- monthly snapshots (METHOD.md, section 9) ---------------------------

    SNAPSHOT_ENDPOINTS = ("adsl/monthly", "sources", "hours", "systems", "visibility", "visibility/detail",
                          "visibility/grid", "pattern", "prediction", "patterns", "drones", "quality",
                          "encounters")

    def snapshot_bodies(self, month, only=None):
        """What every statistics endpoint publishes, for the snapshot of `month`.

        The same functions the endpoints call, so a snapshot is the page's
        data as it stood the night after the month ended; the grid and the
        data-quality windows are those of that month. adsl/monthly needs only
        the database; the rest are read through this tracker, whose caches are
        empty in nightly.py, so nothing is stale.
        """
        calls = (
            ("adsl/monthly", lambda: adsl_monthly_stats(self.connect_db)),
            ("sources", self.monthly_stats), ("hours", self.hours_stats), ("systems", self.systems_stats),
            ("visibility", self.visibility_stats), ("visibility/detail", self.detail_stats),
            ("visibility/grid", lambda: self.grid_stats(month)), ("pattern", self.pattern_stats),
            ("prediction", self.prediction_stats), ("patterns", self.patterns_stats),
            ("drones", self.drones_stats), ("quality", lambda: self.quality_stats(month)),
            ("encounters", self.encounters_stats),
        )
        out = {}
        for name, f in calls:
            if only is not None and name not in only:
                continue
            try:
                out[name] = f()
            except Exception as e:      # one failing statistic must not cost the others their snapshot
                logger.error(f"Snapshot {month} {name}: {e}")
        return out

    def snapshots_stats(self):
        """The monthly snapshots on record: month, endpoint, METHOD.md commit, date, size."""
        def compute(cur):
            cur.execute("SELECT month, endpoint, method_commit, deployed_commit, created_at, bytes "
                        "FROM monthly_snapshot ORDER BY month, endpoint")
            return [{"month": r[0], "endpoint": r[1], "method_commit": r[2], "deployed_commit": r[3],
                     "created_at": r[4].isoformat(), "bytes": int(r[5])} for r in cur.fetchall()]
        return self._cached("snapshots", "snapshots_cache", compute)

    def snapshot_body(self, month, endpoint):
        """One snapshot as the JSON text it was stored as, or None."""
        key = (month, endpoint)
        stamp, body = self.snapshot_cache.get(key, (0, None))
        if body is not None and time.time() - stamp < STATS_CACHE_SECONDS:
            return body
        conn = self.connect_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT body FROM monthly_snapshot WHERE month = %s AND endpoint = %s", key)
                r = cur.fetchone()
        finally:
            conn.close()
        if r is None:
            return None
        body = zlib.decompress(r[0]).decode("utf-8")
        if len(self.snapshot_cache) > 50:
            self.snapshot_cache.clear()
        self.snapshot_cache[key] = (time.time(), body)
        return body

    def grid_stats(self, month=None):
        """Per 0.25-degree cell, group and channel for one month (default: current)."""
        month = month or datetime.datetime.utcnow().strftime("%Y-%m")
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.grid_cache.get(month, (0, None))
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("grid:" + month):
            stamp, cached = self.grid_cache.get(month, (0, None))
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT lat_idx, lon_idx, grp, via, " + ", ".join(GRID_COLUMNS) +
                                " FROM monthly_visibility_grid WHERE month = %s", (month,))
                    rows = cur.fetchall()
            finally:
                conn.close()
            out = []
            for r in rows:
                d = {"lat": r[0] * CELL_DEG, "lon": r[1] * CELL_DEG, "group": r[2], "via": r[3]}
                for c, v in zip(GRID_COLUMNS, r[4:]):
                    d[c] = float(v)
                out.append(d)
            self.grid_cache[month] = (time.time(), out)
            return out

    # --- background threads -------------------------------------------------

    def prune_loop(self):
        while True:
            time.sleep(60)
            # list() snapshots the dict in one step; the listener keeps writing.
            cutoff = time.monotonic() - LIVE_WINDOW
            self.live = {k: v for k, v in list(self.live.items()) if v["_mono"] > cutoff}
            self.live_parsed_at = {k: v for k, v in list(self.live_parsed_at.items()) if v > cutoff}
            # The live count's window; a busy hour holds some 20,000 addresses.
            # Systems are left to the read side, which skips the stale ones.
            heard_cutoff = time.time() - HEARD_WINDOW
            self.heard = {k: v for k, v in list(self.heard.items()) if v[0] >= heard_cutoff}

    def writer_loop(self):
        conn = None
        while True:
            batch = [self.writes.get()]
            time.sleep(2)
            while len(batch) < 5000:
                try:
                    batch.append(self.writes.get_nowait())
                except queue.Empty:
                    break
            try:
                if conn is None:
                    conn = self.connect_db()
                self.write_batch(conn, batch)
            except pymysql.MySQLError as e:
                logger.error(f"Error writing to database (monthly_sources, {len(batch)} rows): {e}")
                conn = None
                time.sleep(10)

    @staticmethod
    def write_batch(conn, batch):
        # INSERT IGNORE for a new device, then last_seen for every row: after
        # a restart `seen` is empty, and the row may well exist already.
        news = [x[1:] for x in batch if x[0] == "new"]
        inserts = [(m, s, v, d, c, n, n) for m, s, v, d, c, n in news]
        touches = [(n, m, s, v, d) for m, s, v, d, c, n in news]
        # An aircraft category replaces a stored ground one (GROUND_CATEGORIES),
        # or ADS-B's "not yet known" 0 (UNKNOWN_YET),
        # whether the device was first heard on the ground today or on an
        # earlier day of the month. Last, so that a missing privilege cannot
        # hold up the two statements above.
        upgrades = [(c, m, s, v, d) for _, m, s, v, d, c, n in batch if c in AIRCRAFT_CATEGORIES]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT IGNORE INTO monthly_sources
                   (month, source, via, device_id, category, first_seen, last_seen)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                inserts,
            )
            cur.executemany(
                """UPDATE monthly_sources SET last_seen = %s
                   WHERE month = %s AND source = %s AND via = %s AND device_id = %s""",
                touches,
            )
            if upgrades:
                cur.executemany(UPGRADE_CATEGORY_SQL, upgrades)

    # --- read side ----------------------------------------------------------

    def live_snapshot(self, layers, bbox=None):
        out = []
        for d in list(self.live.values()):
            if d["layer"] not in layers:
                continue
            if bbox and not (bbox[1] <= d["lat"] <= bbox[3] and
                             (bbox[0] <= d["lon"] <= bbox[2] if bbox[0] <= bbox[2]
                              else d["lon"] >= bbox[0] or d["lon"] <= bbox[2])):
                continue
            out.append({k: v for k, v in d.items() if not k.startswith("_")})
        return out

    def live_raw(self, device_id):
        """The last packet kept for one device, for the map's details box."""
        d = self.live.get(device_id)
        return d["_raw"] if d else None

    def monthly_stats(self):
        """Per month and source: distinct devices, split by radio or network.

        `multi_day` counts devices heard on at least two different days of
        the month, which shows whether a source's ids are stable: an app that
        hands out a new id per session will have almost none.
        """
        # Served without waiting while fresh; only the warm thread (or a cold
        # start) computes, under a lock of this statistic alone.
        stamp, cached = self.stats_cache
        if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
            return cached
        with self.stat_lock("stats"):
            stamp, cached = self.stats_cache
            if cached is not None and not self.forcing() and time.time() - stamp < STATS_CACHE_SECONDS:
                return cached
            conn = self.connect_db()
            try:
                with conn.cursor() as cur:
                    # Months older than the previous one live only as counts in
                    # monthly_sources_summary (archive_loop); a month is never
                    # in both tables.
                    cur.execute(f"""
                        SELECT month, src, via, COUNT(*), SUM(DATE(last_seen) > DATE(first_seen))
                            FROM (SELECT month, {SAME_SYSTEM_SQL} AS src, via, device_id,
                                         MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen
                                      FROM monthly_sources WHERE {COUNTED_SQL} GROUP BY 1, 2, 3, 4) d
                        GROUP BY month, src, via
                        UNION ALL
                        SELECT month, source, via, SUM(devices), SUM(multi_day)
                            FROM monthly_sources_summary
                        GROUP BY month, source, via
                    """)
                    rows = cur.fetchall()
                    # The same, by aircraft category (the one seen first that
                    # month), for adoption by kind of aircraft.
                    cur.execute(f"""
                        SELECT month, {SAME_SYSTEM_SQL} AS src, via, category, COUNT(DISTINCT device_id)
                            FROM monthly_sources WHERE {COUNTED_SQL}
                        GROUP BY 1, 2, 3, 4
                        UNION ALL
                        SELECT month, source, via, category, devices
                            FROM monthly_sources_summary
                    """)
                    cat_rows = cur.fetchall()
                    # Of a month's devices, how many were heard again the month
                    # after, kept when the month was archived (from 7 October 2026).
                    cur.execute("SELECT month, source, devices, returned FROM monthly_return_summary")
                    returns = collections.defaultdict(list)
                    for month, tocall, n, back in cur.fetchall():
                        returns[month].append({"source": tocall, "label": source_info(tocall)[0],
                                               "devices": int(n), "heard_next_month": int(back)})
            finally:
                conn.close()
            current = datetime.datetime.utcnow().strftime("%Y-%m")
            by_cat = {}
            for month, tocall, via, cat, n in cat_rows:
                label, kind = source_info(tocall)
                by_cat.setdefault(month, []).append({
                    "source": tocall, "label": label, "kind": kind, "via": via,
                    "category": None if cat is None or cat == UNKNOWN_CATEGORY else int(cat),
                    "devices": int(n),
                })
            months = {}
            for month, tocall, via, n, multi in rows:
                label, kind = source_info(tocall)
                months.setdefault(month, []).append({
                    "source": tocall, "label": label, "kind": kind, "via": via,
                    "devices": int(n), "multi_day": int(multi or 0),
                })
            out = [{"month": m, "partial": m == current, "sources": months[m],
                    "by_category": by_cat.get(m, []), "return": returns.get(m, [])}
                   for m in sorted(months, reverse=True)]
            self.stats_cache = (time.time(), out)
            return out
