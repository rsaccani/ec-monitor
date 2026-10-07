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
import requests
import io
from io import StringIO
import atexit
from dotenv import load_dotenv
import os
import subprocess

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
    raise Exception("Cannot connect to database after many attempts.")


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

        # Signal quality
        qual_match = re.search(r"!W(\d+)!", line)
        quality = int(qual_match.group(1)) if qual_match else None

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
            "quality": quality,
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
                    raise ConnectionError("No data from OGN for 300s")

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
                        tracker.handle(line)
                    if ">OGADSL" in line:
                        pkt = parse_aprs_line(line)
                        main_logger.debug("[RAW] %s", line)
                        main_logger.debug("[PARSED] %s", pkt)
                        if pkt and pkt["lat"] is not None and pkt["lon"] is not None:
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
            main_logger.info(f"Waiting 5 seconds before reconnecting...")
            time.sleep(5)
            main_logger.info(f"Attempting to reconnect...")
            continue


def update_device_type_map():
    global device_type_map
    try:
        r = requests.get(DEVICE_TYPE_URL)
        r.raise_for_status()
        text = r.text

        reader = csv.DictReader(StringIO(text))
        new_map, notrack, noident = {}, set(), set()
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
            new_map[device_id] = (
                aircraft_model + " (" + registration + ")"
                if aircraft_model
                else "Unknown"
            )

        device_type_map = new_map
        ddb_notrack.clear(); ddb_notrack.update(notrack)
        ddb_noident.clear(); ddb_noident.update(noident)
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
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=60)
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


def _flatten(row, prefix=""):
    out = {}
    for k, v in row.items():
        if isinstance(v, dict):
            out.update(_flatten(v, prefix + k + "."))
        else:
            out[prefix + k] = v
    return out


def respond(rows, name):
    """JSON by default; ?format=csv gives one flat row per entry, for a spreadsheet."""
    if request.args.get("format") != "csv":
        return jsonify(rows)
    flat = [_flatten(r) for r in rows]
    columns = []
    for r in flat:
        columns += [k for k in r if k not in columns]
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(flat)
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# --- ROUTES FLASK ---
# Public paths, proxied by nginx under the monitor's page.
API = "/conspicuity-monitor/api"


@app.route("/demo")
def index():
    return render_template("map.html")


@app.route(API + "/adsl")
def get_ads_l():
    out = []
    for v in ads_l_devices.values():
        entry = v.copy()
        entry["timestamp"] = entry["timestamp"].isoformat()
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
    return jsonify(tracker.live_snapshot(layers, bbox))


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


@app.route(API + "/visibility/grid")
def get_visibility_grid():
    """Monthly visibility per 0.25-degree cell; ?month=YYYY-MM, default current."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    month = request.args.get("month")
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify([])
    try:
        return respond(tracker.grid_stats(month), "visibility-by-cell")
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
    if request.args.get("format") != "csv":
        return jsonify(out)
    # Only the lists of rows go into the CSV; the band edges and thresholds
    # beside them are in the JSON (until 7 October 2026 they broke it).
    return respond([dict({"part": part}, **row) for part, rows in out.items()
                    if isinstance(rows, list) for row in rows if isinstance(row, dict)], name)


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
    """Crewed aircraft of different kinds coming close, as monthly aggregates (METHOD.md, section 10.2)."""
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


def bootstrap():
    global listener_started, conn
    if listener_started:
        return

    main_logger.info("Bootstrapping ADS-L listener")

    conn = get_db_connection()

    global tracker, raw_recorder
    raw_recorder = recorder.from_env(hidden)
    tracker = sources.SourceTracker(parse_aprs_line, lambda: get_db_connection(max_retries=1), hidden)
    if not SKIP_STATS_DATABASE:
        Thread(target=tracker.writer_loop, daemon=True).start()
        Thread(target=tracker.visibility_loop, daemon=True).start()
        Thread(target=tracker.archive_loop, daemon=True).start()
        Thread(target=tracker.warm_loop, daemon=True).start()
    Thread(target=tracker.prune_loop, daemon=True).start()

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
