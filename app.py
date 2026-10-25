import collections
import socket
import threading
import time
import datetime
import re
import logging
import sys
from flask import Flask, jsonify, render_template, request
from threading import Thread
import pymysql
import csv
import json
import requests
import io
from io import StringIO
import atexit
from dotenv import load_dotenv
import os
import shutil
import subprocess

import check
import recorder
import sources

load_dotenv()

HOST = "aprs.glidernet.org"
PORT = 14580
SKIP_STATS_DATABASE = False

# Dictionary in memory: device_id -> latest packet data
ads_l_devices = {}

app = Flask(__name__)

# Configure logging to flush immediately - use only one handler
main_logger = logging.getLogger("main")

# Clear any existing handlers first to prevent duplication
main_logger.handlers.clear()

# Set log level and configure single handler
main_logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
main_logger.addHandler(handler)

# Set the root logger to ERROR level to suppress RAW/PARSED messages
logging.getLogger().setLevel(logging.ERROR)

listener_started = False

# Every other OGN source: monthly counts and the live layers (sources.py)
tracker = None
raw_recorder = None  # recorder.Recorder when EC_RAW_DIR is set

# Database connection for statistics
conn = None

# Global mapping: device_id -> aircraft type
device_type_map = {}
# Devices whose owners asked OGN not to track them, or not to identify them.
ddb_notrack = set()
ddb_noident = set()
# Identified devices by registration and competition number, normalised
# (upper case, no dashes or spaces), for the device check's shortcut: name ->
# tuple of device ids, and each device's card once, as (type, model,
# registration, cn). Until 24 October 2026 every name held a dict per device,
# the same card twice for registration and competition number: 22 MB.
ddb_by_reg = {}
ddb_cards = {}


def ddb_named(name):
    """The card of every identified device that answers to this name."""
    cards = ddb_cards
    return [{"id": d, "type": c[0], "model": c[1], "registration": c[2], "cn": c[3]}
            for d in ddb_by_reg.get(name, ()) for c in (cards.get(d),) if c is not None]


def hidden(device_id):
    """True when the OGN device database says this device must not be tracked."""
    return device_id[-6:] in ddb_notrack

# URL of the OGN device database
DEVICE_TYPE_URL = "https://ddb.glidernet.org/download/"


# ---  SUPPORT FUNCTIONS ---

# Names of the aircraft categories in the OGN id (sources.CATEGORY_NAMES).
AIRCRAFT_CATEGORIES = sources.CATEGORY_NAMES


def get_aircraft_type_description(symbol1, symbol2):
    # Mappa per symbol1 \
    alternative_map = {"\\": "Drop plane", "^": "Powered aircraft"}

    # Mappa predefinita
    default_map = {
        "z": "Unknown",
        "'": "Glider",
        "X": "Helicopter",
        "g": "Paraglider or hang-glider",
        "^": "Jet aircraft",
        "z": "UFO",
        "\\": "Drop plane",
        "O": "Balloon",
    }

    if symbol1 == "/":
        current_map = default_map
    elif symbol1 == "\\":
        current_map = alternative_map
    else:
        current_map = default_map

    description = current_map.get(symbol2, "Unknown")

    return description


class DatabaseUnavailable(pymysql.MySQLError):
    """Connection failure; a MySQLError, so every route's own handler answers it."""


def get_db_connection(max_retries=30, retry_delay=10):
    if SKIP_STATS_DATABASE == True:
        return None
    retries = 0
    while retries < max_retries:
        main_logger.debug("Attempting to connect to db.")
        try:
            conn = pymysql.connect(
                host="localhost",
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database="ads_l",
                autocommit=True,
            )
            main_logger.debug("Connection to database established.")
            return conn
        except pymysql.MySQLError as e:
            retries += 1
            main_logger.error(
                f"Error connecting database (attempt {retries}/{max_retries}): {e}"
            )
            if retries < max_retries:
                main_logger.info(f"New attempt in {retry_delay} seconds...")
                time.sleep(retry_delay)
    raise DatabaseUnavailable("Cannot connect to database after many attempts.")


# (month, device_id) -> the category last written to the database.
categorised_seen = {}

# Errors that repeat identically on every attempt, so retrying only stalls the
# listener: access denied (1044, 1045), command or column denied (1142, 1143),
# unknown column (1054), missing table (1146). Anything else, including a
# connection left in a bad state ("Packet sequence number wrong", (0, '')),
# gets a fresh connection.
PERMANENT_DB_ERRORS = {1044, 1045, 1054, 1142, 1143, 1146}


def record_monthly_device(device_id, device_type, category=None):
    global conn
    max_retries = 3
    retry_delay = 3

    for attempt in range(max_retries):
        try:
            if conn is not None:
                with conn.cursor() as cur:
                    month = datetime.datetime.utcnow().strftime("%Y-%m")
                    now = datetime.datetime.utcnow()
                    cur.execute(
                        """
                        INSERT IGNORE INTO monthly_devices
                        (month, device_id, device_type, first_seen, category)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (month, device_id, device_type, now, category),
                    )
                    # Rows inserted before the category column existed get it
                    # the first time the device is heard again. A separate
                    # UPDATE, because ON DUPLICATE KEY UPDATE needs the UPDATE
                    # privilege on every inserted column, and ads_user has it
                    # on `category` only. Once per device and month, and once
                    # more when a device first heard on the ground (category
                    # 14 or 15, sources.GROUND_CATEGORIES) declares an aircraft,
                    # which then replaces it (from 7 October 2026).
                    key = (month, device_id)
                    written = categorised_seen.get(key, False)
                    upgrade = category in sources.AIRCRAFT_CATEGORIES
                    if category is not None and (written is False or
                                                 (upgrade and written in sources.GROUND_CATEGORIES)):
                        cur.execute(
                            """
                            UPDATE monthly_devices SET category = %s
                            WHERE month = %s AND device_id = %s
                              AND (category IS NULL OR (%s AND category IN (14, 15)))
                            """,
                            (category, month, device_id, upgrade),
                        )
                        if len(categorised_seen) > 200000:
                            categorised_seen.clear()
                        categorised_seen[key] = category
                break  # Se tutto va bene, esci dal loop
        except pymysql.MySQLError as e:
            main_logger.error(
                f"Error writing to database (attempt {attempt + 1}/{max_retries}): {e}"
            )
            code = e.args[0] if e.args else None
            if code in PERMANENT_DB_ERRORS:
                return
            if attempt < max_retries - 1:
                main_logger.info(f"Will retry connecting in {retry_delay} second...")
                time.sleep(retry_delay)
                conn = get_db_connection()  # Riconnetti al database
            else:
                main_logger.error("Cannot write to database after many attempts.")


def connect_ogn():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
    except AttributeError:
        pass  # non Linux
    s.settimeout(10)
    s.connect((HOST, PORT))
    login_line = f"user ADSLMAP-1 pass -1 vers ADS-L webmap 1.0 filter r/0/0/20000\n"
    s.send(login_line.encode())
    main_logger.info("OGN connection established")
    return s


def is_valid_latitude(lat):
    """Validate latitude is between -90 and 90 degrees"""
    return -90 <= lat <= 90


def is_valid_longitude(lon):
    """Validate longitude is between -180 and 180 degrees"""
    return -180 <= lon <= 180


def is_valid_altitude(alt):
    """Validate altitude is reasonable for aircraft (-1,500 to 50,000 ft)"""
    return -1500 <= alt <= 50000


def is_valid_speed(speed):
    """Validate speed is reasonable (0-1000 knots)"""
    return 0 <= speed <= 1000


def is_valid_heading(heading):
    """Validate heading: APRS uses 1-360, with 360 for north and 000 for unknown"""
    return 0 <= heading <= 360


# Rejected packets are counted and summed up once an hour. Logged one by one
# they filled the system log (and logcheck's mail) with some 1,700 lines a
# day, each carrying a device address.
REJECT_SUMMARY_EVERY = 3600
_rejected = {}
_rejected_since = time.time()
_rejected_lock = threading.Lock()


def note_rejected(failures):
    global _rejected_since
    with _rejected_lock:
        for f in failures:
            field = f.split(" ", 1)[0]
            _rejected[field] = _rejected.get(field, 0) + 1
        if time.time() - _rejected_since < REJECT_SUMMARY_EVERY:
            return
        summary = ", ".join(f"{k} {v}" for k, v in sorted(_rejected.items()))
        _rejected.clear()
        _rejected_since = time.time()
    main_logger.info("Packets rejected in the last hour, by invalid field: %s", summary)


def parse_aprs_line(line):
    global device_type_map
    try:
        device_id, rest = line.split(">", 1)
        device_id = device_id.strip()

        # Receiving station: the last element of the path, after the q
        # construct (a relay, as in OGMSHT,RELAY*,qAS,station, comes before it).
        head = rest.split(":", 1)[0].split(",")
        q = [p for p in head[1:] if p.startswith("q")]
        routing_info = q[0] if q else None
        station = head[-1] if len(head) > 1 else None

        # Lat/Lon and timestamp GPS
        m_gps = re.search(
            r"/(\d{2})(\d{2})(\d{2})h(\d{2,3})(\d{2}\.\d+)([NS])([\\/])(\d{2,3})(\d{2}\.\d+)([EW])(.)",
            line,
        )
        if m_gps:
            hh, mm, ss, deg_lat, min_lat, ns, symbol1, deg_lon, min_lon, ew, symbol2 = (
                m_gps.groups()
            )
            lat = int(deg_lat) + float(min_lat) / 60
            lat = lat if ns == "N" else -lat
            lon = int(deg_lon) + float(min_lon) / 60
            lon = lon if ew == "E" else -lon
            aircraft_aprs = get_aircraft_type_description(symbol1, symbol2)
        else:
            lat = lon = None
            aircraft_aprs = "Unknown"

        # Heading and speed
        m_heading = re.search(r"[EW][\^X\'Ozg>\\\!](\d{3})\/(\d{3})\/", line)
        heading = int(m_heading.group(1)) if m_heading else None
        speed = int(m_heading.group(2)) if m_heading else None

        # Altitude in feet
        # A=-00012 is valid (a device on the ground near sea level). The FL
        # fallback must be a token of its own: unanchored, it used to match
        # the first "F" inside the hex id, e.g. "id3F04EA30" -> 400 ft.
        alt_match = re.search(r"A=(-?\d+)", line)
        if alt_match:
            altitude = float(alt_match.group(1))
        else:
            fl_match = re.search(r"(?<!\S)FL(\d+(?:\.\d+)?)(?!\S)", line)
            altitude = float(fl_match.group(1)) * 100 if fl_match else None

        # Vertical speed
        vs_match = re.search(r"([+-]?\d+)fpm", line)
        vspeed = int(vs_match.group(1)) if vs_match else None

        # Flight / callsign
        flight_match = re.search(r"A3:([^\s]+)", line)
        flight = flight_match.group(1) if flight_match else None

        # Signal dB
        sig_match = re.search(r"(-?\d+\.?\d*)dB", line)
        signal = float(sig_match.group(1)) if sig_match else None

        # Offset frequency
        freq_match = re.search(r"([+-]?\d+\.\d+)kHz", line)
        freq_offset = float(freq_match.group(1)) if freq_match else None

        # GPS fix / satellites
        gps_match = re.search(r"gps(\d+)x(\d+)", line)
        gps_fix = int(gps_match.group(1)) if gps_match else None
        gps_sats = int(gps_match.group(2)) if gps_match else None

        # packet ID
        id_match = re.search(r"\bid([A-F0-9]{8}|[A-F0-9]{10})\b", line)
        pkt_id = id_match.group(1) if id_match else None

        # Turn rate, in half-turns a minute (3 degrees a second each), and the
        # pressure altitude, in hundreds of feet, when the packet has them
        rot_match = re.search(r" ([+-]\d+(?:\.\d+)?)rot", line)
        rot = float(rot_match.group(1)) if rot_match else None
        fl_match = re.search(r"(?<!\S)FL(\d+(?:\.\d+)?)(?!\S)", line)
        fl = round(float(fl_match.group(1)) * 100) if fl_match else None

        category_code, no_track = sources.id_info(line)

        # The database is keyed by the 24-bit address, whatever the prefix
        # (OGN, FLR, ICA...); a device not to be tracked is dropped here.
        lookup_id = device_id[-6:]
        if lookup_id in ddb_notrack or no_track:
            return None
        ddb_model = device_type_map.get(lookup_id)
        aircraft_type = ddb_model or aircraft_aprs

        # Validate parsed values
        validation_failures = []

        if lat is not None and not is_valid_latitude(lat):
            validation_failures.append(f"latitude {lat}")

        if lon is not None and not is_valid_longitude(lon):
            validation_failures.append(f"longitude {lon}")

        if altitude is not None and not is_valid_altitude(altitude):
            validation_failures.append(f"altitude {altitude}")

        if speed is not None and not is_valid_speed(speed):
            validation_failures.append(f"speed {speed}")

        if heading is not None and not is_valid_heading(heading):
            validation_failures.append(f"heading {heading}")

        if validation_failures:
            note_rejected(validation_failures)
            return None

        return {
            "device_id": device_id,
            "aircraft_type": aircraft_type,
            "ddb_model": ddb_model,
            "routing_info": routing_info,
            "station": station,
            "lat": lat,
            "lon": lon,
            "heading": heading,
            "speed": speed,
            "altitude": altitude,
            "vspeed": vspeed,
            "flight": flight,
            "signal": signal,
            "freq_offset": freq_offset,
            "gps_fix": gps_fix,
            "gps_sats": gps_sats,
            "pkt_id": pkt_id,
            "category_code": category_code,
            "category": AIRCRAFT_CATEGORIES.get(category_code),
            "rot": rot,
            "fl": fl,
            "timestamp": datetime.datetime.utcnow(),
            "raw": line,
        }
    except Exception as e:
        main_logger.error("parse_aprs_line failed: %s", e)
        return None


def ads_l_listener():
    global ads_l_devices
    while True:
        try:
            s = connect_ogn()
            s.settimeout(300)
            buffer = ""
            last_rx = time.time()
            last_keepalive = time.time()

            while True:
                data = s.recv(4096)
                if not data:
                    raise ConnectionError("OGN closed the connection")

                last_rx = time.time()
                buffer += data.decode(errors="ignore")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if raw_recorder is not None:
                        raw_recorder.write(line)
                    if tracker is not None:
                        try:
                            tracker.handle(line)
                        except Exception:
                            main_logger.exception("tracker.handle failed on: %s", line)
                    if ">OGADSL" in line:
                        pkt = parse_aprs_line(line)
                        main_logger.debug("[RAW] %s", line)
                        main_logger.debug("[PARSED] %s", pkt)
                        if pkt and pkt["lat"] is not None and pkt["lon"] is not None:
                            if tracker is not None:
                                pkt.update(tracker.live_context("OGADSL", "radio" if pkt["signal"] is not None else "net", pkt))
                            ads_l_devices[pkt["device_id"]] = pkt
                            record_monthly_device(
                                pkt["device_id"],
                                "ADSL",  # ADSL / ADSB / FLARM
                                pkt["category_code"],
                            )
                    else:
                        main_logger.debug("[RAW] %s", line)

                # Send keepalive message every 15 minutes to prevent server timeout
                if time.time() - last_keepalive > 900:  # 900 seconds = 15 minutes
                    try:
                        keepalive_msg = "# keepalive\n"
                        s.send(keepalive_msg.encode())
                        last_keepalive = time.time()
                    except Exception as e:
                        main_logger.error(f"Failed to send keepalive: {e}")

                if time.time() - last_rx > 300:
                    raise ConnectionError("OGN feed stalled")

        except (socket.timeout, ConnectionError) as e:
            main_logger.error(f"Connection error: {e}")
            try:
                s.close()
            except:
                pass
            # Whatever is heard from here on is a new stream: no track, silence or
            # flight may run across the cut (24 October 2026). The recording gets
            # a marker at the same instant, so that a replay cuts where this did.
            try:
                if tracker is not None:
                    tracker.feed_break(time.time())
                if raw_recorder is not None:
                    raw_recorder.mark(sources.FEED_BREAK)
            except Exception:
                main_logger.exception("feed break failed")
            main_logger.info(f"Waiting 5 seconds before reconnecting...")
            time.sleep(5)
            main_logger.info(f"Attempting to reconnect...")
            continue


def update_device_type_map():
    global device_type_map, ddb_by_reg, ddb_cards
    try:
        r = requests.get(DEVICE_TYPE_URL)
        r.raise_for_status()
        text = r.text

        reader = csv.DictReader(StringIO(text))
        new_map, notrack, noident, by_reg, cards = {}, set(), set(), {}, {}
        for row in reader:
            device_id = row["DEVICE_ID"].strip().strip("'")  # remove quotes
            aircraft_model = row["AIRCRAFT_MODEL"].strip().strip("'")
            registration = row["REGISTRATION"].strip().strip("'")
            # The pilot's privacy choices in the OGN device database, which the
            # OGN data usage terms require every service to respect.
            if row.get("TRACKED", "").strip().strip("'") == "N":
                notrack.add(device_id)
            if row.get("IDENTIFIED", "").strip().strip("'") == "N":
                noident.add(device_id)
                continue
            cn = (row.get("CN") or "").strip().strip("'")
            kind = (row.get("DEVICE_TYPE") or "").strip().strip("'")
            for key in ("REGISTRATION", "CN"):
                name = re.sub(r"[\s-]", "", (row.get(key) or "").strip().strip("'")).upper()
                if len(name) >= 2:
                    # Every device that answers to the name, once each: the
                    # first row used to win, and 6,571 names have several.
                    ids = by_reg.get(name, ())
                    if device_id not in ids:
                        by_reg[name] = ids + (device_id,)
                    if device_id not in cards:
                        cards[device_id] = (sys.intern(kind) if kind else None,
                                            sys.intern(aircraft_model) if aircraft_model else None,
                                            registration or None, cn or None)
            if not aircraft_model:
                new_map[device_id] = "Unknown"
            elif registration:
                new_map[device_id] = aircraft_model + " (" + registration + ")"
            else:
                new_map[device_id] = aircraft_model

        device_type_map = new_map
        ddb_notrack.clear(); ddb_notrack.update(notrack)
        ddb_noident.clear(); ddb_noident.update(noident)
        # Cards first: a lookup between the two assignments finds every id it needs.
        ddb_cards = cards
        ddb_by_reg = by_reg
        main_logger.info(f"[Device map] Loaded {len(device_type_map)} entries, "
                         f"{len(notrack)} not to be tracked, {len(noident)} not to be identified")

    except Exception as e:
        main_logger.error(f"Error updating device type map: {e}")


def periodic_device_type_update(interval=3600):
    """Update the device type map every interval seconds."""
    while True:
        update_device_type_map()
        time.sleep(interval)


def prune_old_devices():
    global ads_l_devices
    while True:
        time.sleep(60)
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=sources.LIVE_WINDOW)
        ads_l_devices = {
            k: v for k, v in ads_l_devices.items() if v["timestamp"] > cutoff
        }


def close_db(exception=None):
    global conn
    if conn is not None:
        main_logger.info("Closing DB")
        try:
            conn.close()
        except Exception as e:
            main_logger.error(f"Error closing database connection: {e}")
        finally:
            conn = None


def start_listener():
    """Start the APRS listener in a background thread."""
    main_logger.info("Starting APRS listener thread...")
    listener_thread = Thread(target=ads_l_listener, daemon=True)
    listener_thread.start()
    main_logger.info(f"Listener thread started with ID: {listener_thread.ident}")
    return listener_thread


def _cell(v):
    """One CSV cell: nested lists and dicts as compact JSON, and no formula."""
    if isinstance(v, (list, tuple, dict)):
        v = json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        v = "'" + v
    return v


def _flatten(row, prefix=""):
    out = {}
    for k, v in row.items():
        if isinstance(v, dict):
            out.update(_flatten(v, prefix + k + "."))
        else:
            out[prefix + k] = _cell(v)
    return out


# Serialised bodies of the heavy statistics. The tracker keeps each statistic
# as one list or dict until it recomputes it, so a body is reused for as long
# as that same object comes back. The key holds only what a route reads (its
# name, the format and the month), so an invented parameter such as ?cb=1,
# which nginx's cache treats as a new URL, costs a lookup and not 7 MB of
# JSON. One entry per key, so the cache stays as small as the statistics.
# An entry not served for BODY_IDLE_S goes, and with it the statistic it holds
# on to: a month asked for once would otherwise stay in memory for good.
BODY_IDLE_S = 3600
_bodies = {}
_bodies_lock = threading.Lock()


def _cached(source, key, build):
    """build() -> (body, mimetype, headers); redone only when `source` is not the object last served."""
    now = time.time()
    with _bodies_lock:
        hit = _bodies.get(key)
    if hit is None or hit[0] is not source:
        body, mimetype, headers = build()
        hit = [source, body, mimetype, headers, now]
        with _bodies_lock:
            for k in [k for k, v in _bodies.items() if now - v[4] > BODY_IDLE_S]:
                del _bodies[k]
            _bodies[key] = hit
    hit[4] = now
    return app.response_class(hit[1], mimetype=hit[2], headers=hit[3])


def respond(rows, name, source=None, extra="", build_rows=None):
    """JSON by default; ?format=csv gives one flat row per entry, for a spreadsheet.

    `source` is the object the tracker caches (default `rows`); `extra` is any
    route parameter that changes the answer; `build_rows` makes the CSV's rows when
    `rows` is not itself a list of them. A sources.Frozen statistic is already its
    JSON text, and is read back only for the CSV.
    """
    csv_out = request.args.get("format") == "csv"
    src = rows if source is None else source
    if not csv_out and isinstance(rows, sources.Frozen):
        # Already the body. Kept in _bodies it would also keep the previous
        # cycle's text alive until the next visit: 17 MB on 24 October 2026.
        return app.response_class(rows.text, mimetype="application/json")

    def build():
        if not csv_out:
            return app.json.dumps(rows) + "\n", "application/json", None
        if build_rows is not None:
            tree = build_rows()
        else:
            tree = rows.thaw() if isinstance(rows, sources.Frozen) else rows
        flat = [_flatten(r) for r in tree]
        columns = []
        for r in flat:
            columns += [k for k in r if k not in columns]
        buf = StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat)
        return buf.getvalue(), "text/csv", {"Content-Disposition": f'attachment; filename="{name}.csv"'}

    return _cached(src, (name, csv_out, extra), build)


# --- ROUTES FLASK ---
# Public paths, proxied by nginx under the monitor's page.
API = "/conspicuity-monitor/api"


@app.route("/demo")
def index():
    return render_template("map.html")


def ddb_card(device_id):
    """The competition number for the live map, only for a device the OGN device
    database lists as identified (ddb_cards holds no other); the registration is
    already in the model string of device_type_map."""
    c = ddb_cards.get(device_id[-6:])
    return {"cn": c[3] if c is not None else None}


@app.route(API + "/adsl")
def get_ads_l():
    out = []
    for v in ads_l_devices.values():
        entry = v.copy()
        entry["timestamp"] = entry["timestamp"].isoformat()
        entry.update(ddb_card(entry["device_id"]))
        entry["rx"] = tracker.rx_list(entry["device_id"], "OGADSL", entry["lat"], entry["lon"]) if tracker else []
        out.append(entry)
    return jsonify(out)


@app.route(API + "/adsl/monthly")
def ads_l_stats():
    """ADS-L devices per month, by address type and category (sources.adsl_monthly_stats)."""
    # A connection of its own: pymysql connections are not thread-safe, and
    # sharing the listener's one corrupted it whenever a page view coincided
    # with a write.
    if SKIP_STATS_DATABASE:
        return "[]"
    try:
        out = sources.adsl_monthly_stats(lambda: get_db_connection(max_retries=1))
    except Exception as e:
        main_logger.error(f"Error reading the ADS-L monthly counts: {e}")
        return "[]"
    return respond(out, "ads-l-monthly")


@app.route(API + "/heard")
def get_heard():
    """Distinct aircraft heard in the last hour on any system but ADS-B (sources.heard_stats)."""
    if tracker is None:
        return jsonify({})
    return jsonify(tracker.heard_stats())


@app.route(API + "/live")
def get_live():
    """Positions from the other sources, for the map's layer selector.

    ?layers=flarm,fanet,radio,apps,network  (ADS-L stays on /adsl)
    &bbox=west,south,east,north             (optional, degrees)
    ?id=FLR123456                           the last raw packet of one device,
                                            fetched when its details are opened
    """
    if request.args.get("id"):
        raw = tracker.live_raw(request.args["id"]) if tracker else None
        return jsonify({"raw": raw})
    layers = set((request.args.get("layers") or "").split(",")) & set(sources.LAYER_OF_KIND.values())
    bbox = None
    try:
        if request.args.get("bbox"):
            bbox = [float(x) for x in request.args["bbox"].split(",")][:4]
            if len(bbox) != 4:
                bbox = None
    except ValueError:
        bbox = None
    if tracker is None or not layers:
        return jsonify([])
    out = tracker.live_snapshot(layers, bbox)
    for d in out:
        d.update(ddb_card(d["id"]))
    return jsonify(out)


_check_id = re.compile(r"^(?:0X|[A-Z]{3})?([0-9A-F]{6})$")


@app.route(API + "/check")
def get_check():
    """What the feed heard from one device in the last 24 hours (check.py).

    ?id=DD1234 (the 24-bit address, with or without a prefix such as FLR or
    0x) or a registration or competition number the OGN device database
    shows as identified. Rate-limited per IP in nginx.
    """
    q = re.sub(r"[\s-]", "", request.args.get("id") or "").upper()[:16]
    m = _check_id.match(q)
    # "0x" before six hex digits is how the page asks for that device ID itself,
    # which settles an ambiguity between an ID and a registration.
    named = [] if m and q.startswith("0X") else ddb_named(q)
    if len(named) > 1 or (m and named):
        # An uncertain match gets no position: the visitor picks a device and
        # the check runs again by its id. Only what the public OGN device
        # database shows (identified devices only, so nothing hidden here).
        candidates = list(named)
        if m and not any(c["id"] == m.group(1) for c in candidates):
            info = ddb_info(m.group(1))
            candidates.append({"id": m.group(1), "type": None, "model": info.get("model"),
                               "registration": None, "cn": None, "as_address": True})
        return jsonify({"query": q, "found": True, "ambiguous": True,
                        "candidates": candidates[:50], "candidates_total": len(candidates)})
    if m:
        address = m.group(1)
    elif named:
        address = named[0]["id"]
    else:
        return jsonify({"query": q, "found": False})
    if address in ddb_notrack:
        # The owner's choice in the OGN device database, public there too.
        return jsonify({"query": q, "address": address, "found": True, "not_tracked": True})
    if tracker is None or tracker.check is None:
        return jsonify({"query": q, "address": address, "found": True, "unavailable": True})
    out = dict(tracker.check.report(address))
    out.update({"query": q, "found": True})
    # The usual pattern of the same source and kind of aircraft, beside the device's own.
    out["systems"] = [dict(s, pattern_typical=tracker.typical_pattern(s["tocall"], s["category"]))
                      for s in out.get("systems", [])]
    return jsonify(out)


def ddb_info(address):
    """The device database's model and registration, only for identified devices."""
    if address in ddb_noident:
        return {"identified": False}
    model = device_type_map.get(address)
    return {"identified": True, "model": model} if model else {}


@app.route(API + "/sources")
def get_sources():
    """Monthly distinct devices per OGN source, split by radio or network."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        months = tracker.monthly_stats()
        if request.args.get("format") == "csv":
            return respond([dict(month=m["month"], partial=m["partial"], **src)
                            for m in months for src in m["sources"]], "ogn-sources-monthly")
        return jsonify(months)
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_sources: {e}")
        return jsonify([])


@app.route(API + "/visibility")
def get_visibility():
    """Daily visibility totals per source, channel and category (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.visibility_stats(), "visibility-daily")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_visibility: {e}")
        return jsonify([])


@app.route(API + "/hours")
def get_hours():
    """Flying time per month and category, each aircraft counted once (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.hours_stats(), "flying-hours")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_hours: {e}")
        return jsonify([])


@app.route(API + "/systems")
def get_systems():
    """Aircraft heard on one or more systems, by month and category (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.systems_stats(), "systems-per-aircraft")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_sources for systems: {e}")
        return jsonify([])


@app.route(API + "/visibility/detail")
def get_visibility_detail():
    """Monthly visibility by source, channel, category and height band (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.detail_stats(), "visibility-by-height")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_visibility_detail: {e}")
        return jsonify([])


_first_month = [0, None]


def valid_month(month):
    """A YYYY-MM between the first month in the database and the current one."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", month)
    if not m or not 1 <= int(m.group(2)) <= 12:
        return False
    if time.time() - _first_month[0] > 3600 or _first_month[1] is None:
        conn = get_db_connection(max_retries=1)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MIN(month) FROM monthly_visibility_grid")
                row = cur.fetchone()
        finally:
            conn.close()
        _first_month[:] = [time.time(), row[0] if row and row[0] else "2000-01"]
    return _first_month[1] <= month <= datetime.datetime.utcnow().strftime("%Y-%m")


@app.route(API + "/visibility/grid")
def get_visibility_grid():
    """Monthly visibility per 0.25-degree cell; ?month=YYYY-MM, default current."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    month = request.args.get("month")
    try:
        if month and not valid_month(month):
            return jsonify({"error": "month must be YYYY-MM, from the first month on record to the current one"}), 400
        rows = tracker.grid_stats(month)
        return respond(rows, "visibility-by-cell", extra=month or "")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_visibility_grid: {e}")
        return jsonify([])


@app.route(API + "/pattern")
def get_pattern():
    """Radio packets received while circling, by angle to the receiver (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.pattern_stats(), "reception-pattern")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_reception_pattern: {e}")
        return jsonify([])


@app.route(API + "/prediction")
def get_prediction():
    """Prediction errors at 5, 10 and 20 s, with and without the turn rate (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.prediction_stats(), "prediction-errors")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_prediction: {e}")
        return jsonify([])


def respond_parts(out, name):
    """A statistic made of several lists: JSON as it is, or one CSV with a `part` column."""
    # Only the lists of rows go into the CSV; the band edges and thresholds
    # beside them are in the JSON (until 7 October 2026 they broke it).
    return respond(out, name, source=out, build_rows=lambda: [
        dict({"part": part}, **row)
        for part, rows in (out.thaw() if isinstance(out, sources.Frozen) else out).items()
        if isinstance(rows, list) for row in rows if isinstance(row, dict)])


@app.route(API + "/patterns")
def get_patterns():
    """How light aviation flies (PATTERNS.md), and parked aircraft transmitting (METHOD.md, section 10.3)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        return respond_parts(tracker.patterns_stats(), "patterns")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading the nightly patterns: {e}")
        return jsonify({})


@app.route(API + "/drones")
def get_drones():
    """Drones by cell, height, speed, systems and extent, and their encounters (METHOD.md, section 10.1)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        return respond_parts(tracker.drones_stats(), "drones")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading the nightly drone tables: {e}")
        return jsonify({})


@app.route(API + "/encounters")
def get_encounters():
    """Crewed aircraft coming close, and where, as monthly aggregates (METHOD.md, section 10.2)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        return respond_parts(tracker.encounters_stats(), "crewed-encounters")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_crewed_encounters: {e}")
        return jsonify({})


@app.route(API + "/quality")
def get_quality():
    """Data-quality findings per system and per OGN receiver (METHOD.md, section 10.4)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        return respond_parts(tracker.quality_stats(), "data-quality")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_quality: {e}")
        return jsonify({})


@app.route(API + "/stations")
def get_stations():
    """Ground stations judged per day, the fitted k and stations judged for sensitivity, and the names to search (METHOD.md, section 10.5)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        return respond_parts(tracker.stations_stats(), "stations")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_stations: {e}")
        return jsonify({})


@app.route(API + "/station-map")
def get_station_map():
    """The stations heard in the last 30 days with their position, country and region, and the last day
    heard and with traffic, for the map of the stations page (METHOD.md, section 10.5)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        out = tracker.station_map()
        def rows():
            d = out.thaw() if isinstance(out, sources.Frozen) else out
            return [dict(zip(d["fields"], r)) for r in d.get("stations", [])]
        return respond(out, "station-map", build_rows=rows)
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_stations: {e}")
        return jsonify({})


@app.route(API + "/coverage-gaps")
def get_coverage_gaps():
    """Where a new station would help most: lost flying time per cell, last 28 days (METHOD.md, section 10.5)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        return respond_parts(tracker.coverage_gaps_stats(), "coverage-gaps")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_coverage_gaps: {e}")
        return jsonify({})


@app.route(API + "/stations/<path:name>")
def get_station(name):
    """One station by its exact name: the last 30 days (with its sensitivity against the neighbours) and its notes (METHOD.md, section 10.5)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({})
    try:
        out = tracker.station_stats(name)
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_stations: {e}")
        return jsonify({})
    # Not found is an answer, not an error: 200 with `found` false and the names
    # that differ only in case, for the page to suggest.
    return jsonify(out if out is not None else {"found": False, "similar": []})


@app.route(API + "/snapshots")
def get_snapshots():
    """The monthly snapshots on record (METHOD.md, section 9), each a download of /snapshot."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.snapshots_stats(), "snapshots")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_snapshot: {e}")
        return jsonify([])


@app.route(API + "/snapshot")
def get_snapshot():
    """?month=YYYY-MM&endpoint=name: what that endpoint published the night after the month ended."""
    month, endpoint = request.args.get("month", ""), request.args.get("endpoint", "")
    if not re.fullmatch(r"\d{4}-\d{2}", month) or endpoint not in sources.SourceTracker.SNAPSHOT_ENDPOINTS:
        return jsonify({"error": "month=YYYY-MM and endpoint=one of /snapshots"}), 400
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify({"error": "no database"}), 503
    try:
        body = tracker.snapshot_body(month, endpoint)
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_snapshot: {e}")
        return jsonify({"error": "not available"}), 503
    if body is None:
        return jsonify({"error": "no such snapshot"}), 404
    name = f"ec-monitor-{month}-{endpoint.replace('/', '-')}.json"
    return app.response_class(body, mimetype="application/json",
                              headers={"Content-Disposition": f'inline; filename="{name}"'})


_method_cache = (0, None)


@app.route(API + "/method")
def get_method():
    """METHOD.md and its commit history, as deployed (read from this checkout).

    Served from here because the site's Content-Security-Policy lets pages
    connect only to their own origin, and because the rules the running code
    follows are the ones in this checkout.
    """
    global _method_cache
    if _method_cache[1] is not None and time.time() - _method_cache[0] < 600:
        return jsonify(_method_cache[1])
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "METHOD.md"), encoding="utf-8") as f:
            text = f.read()
        # Version 1 is the commit tagged method-v1; the page lists only what
        # changed after it (the drafting of day one stays in the repository).
        v1 = subprocess.run(["git", "-C", here, "rev-list", "-n", "1", "method-v1"],
                            capture_output=True, text=True, timeout=10).stdout.strip()
        v1_date = subprocess.run(["git", "-C", here, "log", "-1", "--format=%cI", v1],
                                 capture_output=True, text=True, timeout=10).stdout.strip() if v1 else ""
        log = subprocess.run(
            ["git", "-C", here, "log", "--format=%H%x1f%cI%x1f%B%x1e"]
            + ([v1 + "..HEAD"] if v1 else []) + ["--", "METHOD.md"],
            capture_output=True, text=True, timeout=10).stdout
        deployed = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        main_logger.error(f"Error reading METHOD.md or its history: {e}")
        return jsonify({"text": None, "commits": []})
    commits = []
    for entry in log.split("\x1e"):
        parts = entry.strip("\n").split("\x1f")
        if len(parts) == 3:
            sha, date, body = parts
            lines = [l for l in body.strip().split("\n")
                     if not l.startswith(("Co-Authored-By:", "Claude-Session:"))]
            commits.append({"sha": sha, "date": date, "title": lines[0] if lines else "",
                            "body": "\n".join(lines[1:]).strip()})
    out = {"text": text, "commits": commits, "deployed": deployed,
           "version1": {"sha": v1, "date": v1_date} if v1 else None}
    _method_cache = (time.time(), out)
    return jsonify(out)


@app.route("/device-map")
def show_device_map():
    return jsonify(device_type_map)


def _deep_sizes(roots):
    """Bytes and object count reachable from each (name, object), largest first.
    Objects shared between roots are counted once, under the first root that
    reaches them. Modules, classes, functions and locks are not followed: they
    are code, not data. Containers are copied before they are read, because the
    listener keeps writing to them meanwhile.

    Objects already counted are marked in one bit per 16 bytes of address
    space (CPython aligns every object to 16), 8 KB per MB of heap touched: a
    set of ids would itself take hundreds of MB for a few million objects."""
    pages = {}
    stop = (type(sys), type, type(_deep_sizes), type(threading.Lock()), threading.Thread)
    out = []
    for name, root in roots:
        size = objects = 0
        stack = [root]
        while stack:
            obj = stack.pop()
            if isinstance(obj, stop):
                continue
            i = id(obj)
            page = pages.get(i >> 20)
            if page is None:
                page = pages[i >> 20] = bytearray(8192)
            bit = (i & 0xFFFFF) >> 4
            if page[bit >> 3] & (1 << (bit & 7)):
                continue
            page[bit >> 3] |= 1 << (bit & 7)
            size += sys.getsizeof(obj, 0)
            objects += 1
            try:
                if isinstance(obj, dict):
                    for k, v in list(obj.items()):
                        stack.append(k)
                        stack.append(v)
                elif isinstance(obj, (list, tuple, set, frozenset, collections.deque)):
                    stack.extend(list(obj))
                else:
                    d = getattr(obj, "__dict__", None)
                    if isinstance(d, dict):
                        stack.append(d)
                    for cls in type(obj).__mro__:
                        slots = cls.__dict__.get("__slots__", ())
                        for slot in (slots,) if isinstance(slots, str) else slots:
                            if slot not in ("__dict__", "__weakref__") and hasattr(obj, slot):
                                stack.append(getattr(obj, slot))
            except RuntimeError:
                pass        # changed while copied: a few objects missed, the total stays close
        out.append({"name": name, "mb": round(size / 1e6, 2), "objects": objects,
                    "len": len(root) if hasattr(root, "__len__") and not isinstance(root, str) else None})
    out.sort(key=lambda r: -r["mb"])
    return out


@app.route("/internal/memory")
def internal_memory():
    """Where the worker's memory is, by structure: sizes and counts only, no
    content. Not under API, so nginx does not proxy it; a request that came
    through a proxy is refused anyway. It walks every object once, so it takes
    seconds and is for occasional use from the host (24 October 2026)."""
    if request.remote_addr != "127.0.0.1" or request.headers.get("X-Real-IP") \
            or request.headers.get("X-Forwarded-For"):
        return "Not found", 404
    t0 = time.time()
    roots = []
    if tracker is not None:
        roots += [("tracker." + k, v) for k, v in vars(tracker).items()]
        if tracker.check is not None:
            roots += [("check." + k, v) for k, v in vars(tracker.check).items()]
    if raw_recorder is not None:
        roots += [("recorder." + k, v) for k, v in vars(raw_recorder).items()]
    for label, module in (("app", sys.modules[__name__]), ("sources", sources), ("check", check)):
        for k, v in list(vars(module).items()):
            if isinstance(v, (dict, list, set, tuple, collections.deque)) and not k.startswith("__"):
                roots.append((f"{label}.{k}", v))
    rows = _deep_sizes(roots)
    with open("/proc/self/status") as f:
        status = {l.split(":")[0]: l.split(":")[1].strip() for l in f if l.startswith(("VmRSS", "RssAnon", "RssFile", "VmSwap"))}
    return jsonify({"status": status, "seconds": round(time.time() - t0, 1),
                    "total_mb": round(sum(r["mb"] for r in rows), 1), "rows": rows})


WORKER_STARTED = time.time()


def _dir_files(path):
    """(name, bytes) of the regular files directly in path; empty if it is missing."""
    try:
        return [(e.name, e.stat().st_size) for e in os.scandir(path) if e.is_file()]
    except OSError:
        return []


@app.route("/internal/resources")
def internal_resources():
    """What the monitor costs now, read in milliseconds: the worker's memory and
    CPU, the host's memory and disk, the size of each table, the raw recording
    and the data files, and the last nightly runs (RESOURCES.md, 25 October
    2026). Localhost only, as /internal/memory, which is the slow breakdown by
    structure."""
    if request.remote_addr != "127.0.0.1" or request.headers.get("X-Real-IP") \
            or request.headers.get("X-Forwarded-For"):
        return "Not found", 404
    out = {"at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}
    with open("/proc/self/status") as f:
        status = {l.split(":")[0]: l.split(":")[1].strip() for l in f
                  if l.startswith(("VmRSS", "VmHWM", "RssAnon", "RssFile", "VmSwap"))}
    cpu = os.times()
    out["worker"] = {"status": status, "uptime_s": round(time.time() - WORKER_STARTED),
                     "cpu_s": round(cpu.user + cpu.system, 1)}
    with open("/proc/meminfo") as f:
        mem = {l.split(":")[0]: int(l.split()[1]) // 1024 for l in f
               if l.startswith(("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"))}
    disk = shutil.disk_usage("/")
    out["host"] = {"memory_mb": mem, "load": os.getloadavg(),
                   "disk_gb": {"total": round(disk.total / 1e9, 1), "free": round(disk.free / 1e9, 1)}}
    raw = _dir_files(os.path.expanduser(os.getenv("EC_RAW_DIR") or ""))
    days = collections.Counter()
    for name, size in raw:
        days[name[:8]] += size
    out["raw"] = {"files": len(raw), "mb": round(sum(s for _, s in raw) / 1e6),
                  "mb_by_day": {d: round(s / 1e6) for d, s in sorted(days.items())[-8:]}}
    out["data_mb"] = {n: round(s / 1e6, 1) for n, s in sorted(_dir_files(os.path.join(os.path.dirname(__file__), "data")),
                                                                key=lambda x: -x[1]) if s >= 1e6}
    db = None
    try:
        db = get_db_connection(max_retries=1)
        with db.cursor() as cur:
            cur.execute("""SELECT table_name, table_rows, ROUND((data_length + index_length) / 1048576, 1)
                               FROM information_schema.tables WHERE table_schema = DATABASE()
                           ORDER BY data_length + index_length DESC""")
            tables = cur.fetchall()
            cur.execute("SELECT * FROM nightly_runs ORDER BY day DESC LIMIT 10")
            cols = [c[0] for c in cur.description]
            runs = [dict(zip(cols, r)) for r in cur.fetchall()]
        out["db"] = {"mb": round(sum(float(t[2] or 0) for t in tables), 1),
                     "tables": [{"name": t[0], "rows": t[1], "mb": float(t[2] or 0)} for t in tables]}
        out["nightly"] = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in runs]
    except pymysql.MySQLError as e:
        out["db_error"] = str(e)
    finally:
        if db is not None:
            db.close()
    return jsonify(out)


def bootstrap():
    global listener_started, conn
    if listener_started:
        return

    main_logger.info("Bootstrapping ADS-L listener")

    conn = get_db_connection()

    global tracker, raw_recorder
    raw_recorder = recorder.from_env(hidden)
    tracker = sources.SourceTracker(parse_aprs_line, lambda: get_db_connection(max_retries=1), hidden)
    tracker.freeze = lambda out: app.json.dumps(out) + "\n"     # exactly the body respond() makes
    if not SKIP_STATS_DATABASE:
        Thread(target=tracker.writer_loop, daemon=True).start()
        Thread(target=tracker.visibility_loop, daemon=True).start()
        Thread(target=tracker.archive_loop, daemon=True).start()
        Thread(target=tracker.warm_loop, daemon=True).start()
        atexit.register(tracker.flush_on_exit)   # runs before close_db: atexit is last in, first out
    Thread(target=tracker.prune_loop, daemon=True).start()
    tracker.check = check.DeviceCheck(tracker.stations, tracker.terrain, ddb_info)
    Thread(target=tracker.check.loop, daemon=True).start()

    Thread(target=ads_l_listener, daemon=True).start()
    Thread(target=periodic_device_type_update, daemon=True).start()
    Thread(target=prune_old_devices, daemon=True).start()

    listener_started = True


# Register the close_db function to run when the application exits
atexit.register(close_db)

bootstrap()

# --- MAIN ---
if __name__ == "__main__":
    try:
        app.run()
    finally:
        close_db()
