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

# Aircraft category carried in bits 2-5 of the first byte of the OGN "idXXYYYYYY"
# field (bits 0-1 are the address type, 6 no-track, 7 stealth).
AIRCRAFT_CATEGORIES = {
    0: "Unknown",
    1: "Glider",
    2: "Tow plane",
    3: "Helicopter",
    4: "Skydiver",
    5: "Drop plane",
    6: "Hang glider",
    7: "Paraglider",
    8: "Powered aircraft",
    9: "Jet aircraft",
    10: "Unknown",
    11: "Balloon",
    12: "Airship",
    13: "Drone",
    14: "Unknown",
    15: "Static object",
}


def aircraft_category_code(pkt_id):
    """Return the 0-15 aircraft category encoded in an OGN id, or None."""
    if not pkt_id or len(pkt_id) < 2:
        return None
    try:
        return (int(pkt_id[:2], 16) >> 2) & 0x0F
    except ValueError:
        return None



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
        main_logger.info("Attempting to connect to db.")
        try:
            conn = pymysql.connect(
                host="localhost",
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database="ads_l",
                autocommit=True,
            )
            main_logger.info("Connection to database established.")
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


# (month, device_id) pairs whose category is already in the database.
categorised_seen = set()

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
                    # on `category` only. Once per device and month.
                    key = (month, device_id)
                    if category is not None and key not in categorised_seen:
                        cur.execute(
                            """
                            UPDATE monthly_devices SET category = %s
                            WHERE month = %s AND device_id = %s AND category IS NULL
                            """,
                            (category, month, device_id),
                        )
                        if len(categorised_seen) > 200000:
                            categorised_seen.clear()
                        categorised_seen.add(key)
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


def parse_aprs_line(line):
    global device_type_map
    try:
        device_id, rest = line.split(">", 1)
        device_id = device_id.strip()

        # Station and type
        m_station = re.match(r"^\w+,([^,]+),([^:/]+):", rest)
        if m_station:
            routing_info, station = m_station.groups()
        else:
            station = routing_info = None

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
        id_match = re.search(r"\bid([A-F0-9]{8})\b", line)
        pkt_id = id_match.group(1) if id_match else None

        # Signal quality
        qual_match = re.search(r"!W(\d+)!", line)
        quality = int(qual_match.group(1)) if qual_match else None

        category_code = aircraft_category_code(pkt_id)

        # The database is keyed by the 24-bit address, whatever the prefix
        # (OGN, FLR, ICA...); a device not to be tracked is dropped here.
        lookup_id = device_id[-6:]
        if lookup_id in ddb_notrack:
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
            main_logger.warning(
                f"Validation failed for device {device_id}: {', '.join(validation_failures)}"
            )
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
@app.route("/ads-l-map")
def index():
    return render_template("map.html")


@app.route("/ads-l/")
def get_ads_l():
    out = []
    for v in ads_l_devices.values():
        entry = v.copy()
        entry["timestamp"] = entry["timestamp"].isoformat()
        out.append(entry)
    return jsonify(out)


# Address type, from the callsign prefix OGN gives each beacon. Random addresses
# change at every power-up or more often, so one device can count several times.
ADDRESS_PREFIXES = {"ICA": "icao", "FLR": "flarm", "OGN": "ogn", "RND": "random",
                    "PAW": "pilotaware", "FNT": "fanet"}


@app.route("/ads-l/stats")
def ads_l_stats():
    """Per-month counts, oldest month last (as before), every month on record.

    `devices` is the total distinct addresses, unchanged for older clients.
    `addresses` splits it by address type, `categories` by aircraft category
    (recorded only since the column was added; `categorised` says how many rows
    have one) and `partial` marks the current UTC month.
    """
    # A connection of its own: pymysql connections are not thread-safe, and
    # sharing the listener's one corrupted it whenever a page view coincided
    # with a write.
    if SKIP_STATS_DATABASE:
        return "[]"
    try:
        db = get_db_connection(max_retries=1)
    except Exception as e:
        main_logger.error(f"Stats: no database connection: {e}")
        return "[]"
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT month, LEFT(device_id, 3), COUNT(*)
                    FROM monthly_devices
                GROUP BY month, LEFT(device_id, 3)
            """)
            by_prefix = cur.fetchall()
            cur.execute("""
                SELECT month, category, COUNT(*)
                    FROM monthly_devices
                WHERE category IS NOT NULL
                GROUP BY month, category
            """)
            by_category = cur.fetchall()
    finally:
        db.close()

    months = {}
    for month, prefix, n in by_prefix:
        m = months.setdefault(month, {"month": month, "devices": 0, "addresses": {},
                                      "categories": {}, "categorised": 0})
        kind = ADDRESS_PREFIXES.get(prefix, "other")
        m["devices"] += n
        m["addresses"][kind] = m["addresses"].get(kind, 0) + n
    for month, code, n in by_category:
        m = months.get(month)
        if m is None:
            continue
        name = AIRCRAFT_CATEGORIES.get(code, "Unknown")
        m["categories"][name] = m["categories"].get(name, 0) + n
        m["categorised"] += n

    current = datetime.datetime.utcnow().strftime("%Y-%m")
    out = sorted(months.values(), key=lambda m: m["month"], reverse=True)
    for m in out:
        m["partial"] = m["month"] == current
    return respond(out, "ads-l-monthly")


@app.route("/ads-l/live")
def get_live():
    """Positions from the other sources, for the map's layer selector.

    ?layers=flarm,fanet,radio,apps,network  (ADS-L stays on /ads-l/)
    &bbox=west,south,east,north             (optional, degrees)
    """
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


@app.route("/ads-l/sources")
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


@app.route("/ads-l/visibility")
def get_visibility():
    """Daily visibility totals per source, channel and category (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.visibility_stats(), "visibility-daily")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading daily_visibility: {e}")
        return jsonify([])


@app.route("/ads-l/visibility/detail")
def get_visibility_detail():
    """Monthly visibility by source, channel, category and height band (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.detail_stats(), "visibility-by-height")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_visibility_detail: {e}")
        return jsonify([])


@app.route("/ads-l/visibility/grid")
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


@app.route("/ads-l/pattern")
def get_pattern():
    """Radio packets received while circling, by angle to the receiver (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.pattern_stats(), "reception-pattern")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_reception_pattern: {e}")
        return jsonify([])


@app.route("/ads-l/prediction")
def get_prediction():
    """Prediction errors at 5, 10 and 20 s, with and without the turn rate (METHOD.md)."""
    if tracker is None or SKIP_STATS_DATABASE:
        return jsonify([])
    try:
        return respond(tracker.prediction_stats(), "prediction-errors")
    except pymysql.MySQLError as e:
        main_logger.error(f"Error reading monthly_prediction: {e}")
        return jsonify([])


_method_cache = (0, None)


@app.route("/ads-l/method")
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

    global tracker
    tracker = sources.SourceTracker(parse_aprs_line, lambda: get_db_connection(max_retries=1), hidden)
    if not SKIP_STATS_DATABASE:
        Thread(target=tracker.writer_loop, daemon=True).start()
        Thread(target=tracker.visibility_loop, daemon=True).start()
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
