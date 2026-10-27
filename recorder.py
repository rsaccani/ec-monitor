"""Short-lived recording of the raw OGN feed, for re-running new algorithms.

Off unless EC_RAW_DIR is set. METHOD.md, section 9, says why it exists and how
long anything is kept: the measures had to change several times in the first
weeks (seconds instead of metres, each source's own cadence), and every change
started again from zero. With a few days of the feed on disk a new algorithm
can be run on real data before it is deployed.

Lines are recorded as they arrive, before any filter of the tracker, so that a
changed tracker can be replayed on them. Each line is prefixed with the epoch
time of reception. Files are hourly, written by this process only and
compressed by a separate `zstd` process once the hour is over, so at most an
hour of uncompressed feed is on disk. Files older than EC_RAW_DAYS days are
deleted, and so are the oldest hours whenever the disk holding the recording
has less than EC_RAW_MIN_FREE_GB free (default 10, from 25 October 2026), down
to the last two days: a full disk would stop MariaDB, mail and the sites, and
a summer day may hold several times the feed of an October one.

EC_RAW_KEEP is a comma-separated list:
  europe        positions inside 35-72 N, 25 W-45 E only (default); world keeps all
  no-airliners  drop ADS-B packets from jets, any ADS-B packet above
                AIRLINER_FT or faster than AIRLINER_KT, and surface reports
                (no altitude) (default): the aircraft type in ADS-B ids is
                mostly "powered" or "unknown", airliners at FL360 included, so
                the type alone lets most of them through, and at dawn half of
                what remained was airliners taxiing
  no-jets       drop ADS-B packets from jet aircraft only
  no-adsb       drop every ADS-B packet
Lines without a position (status lines) are kept wherever they come from.
A line "<epoch> # feed break" is written whenever the service reconnects to the
feed (from 24 October 2026): what came before it and what came after are not
one continuous stream, and a replay must not measure a silence across it. The
recorder also writes one when it starts more than 30 seconds after the last
write (from 25 October 2026), since a reboot or a crash is no reconnection.
From 27 October 2026 the recording also holds, as lines of the same form, the
comment lines the APRS server sends ("# aprsc <version> <date> <time> GMT <server>
<address>", which carry its name and clock) and a sample of the receive queue of
the feed's socket every QUEUE_EVERY seconds ("# rcvq <bytes>", bytes received by the
kernel and not yet read by the worker). Neither is a packet: they are kept to tell
a feed we lost from a feed that was thin. They hold no > and no packet is read from them.
Packets whose id carries the no-track flag, and devices hidden by the OGN
device database, are never recorded.
"""
import glob
import logging
import os
import re
import shutil
import subprocess
import threading
import time

import sources

logger = logging.getLogger("ads_l_map")

# zstd level for finished hours. Until 7 October 2026 it was 19, which took
# about 9 minutes of the host's only CPU for a busy daytime hour (220 MB) and
# slowed the nightly batch by a third when the two overlapped; level 9 takes
# about 20 seconds for files a quarter larger (ratio 5.9 against 7.5, measured
# on that hour), and decompresses as fast.
ZSTD_LEVEL = int(os.environ.get("EC_RAW_ZSTD_LEVEL", "9"))

_position = re.compile(r"[/@]\d{6}h(\d{2})(\d{2}\.\d{2})([NS]).(\d{3})(\d{2}\.\d{2})([EW])")
_id_field = re.compile(r" id([0-9A-Fa-f]{2})[0-9A-Fa-f]{6}")
JET = 9                     # OGN aircraft type in the id field
# Light aircraft with ADS-B Out fly below both; airliners above either.
AIRLINER_FT = 15000
AIRLINER_KT = 200
_speed = re.compile(r".{27}(\d{3})/(\d{3})")      # course/speed after the symbol
_altitude = re.compile(r"/A=(-?\d{5,6})")
FLUSH_SECONDS = 1.0
QUEUE_EVERY = 5             # seconds between samples of the socket's receive queue
BREAK_AFTER = 30            # seconds since the last write: a start later than this marks a feed break
NAME = "%Y%m%d-%H"          # one file per UTC hour: <name>.aprs, then <name>.aprs.zst
EUROPE = (35, 72, -25, 45)  # lat min, max, lon min, max: "europe" here, EC_SCOPE=europe in nightly.py


def outside_europe(body):
    """True for a packet body whose position lies outside EUROPE; False inside it or with no position."""
    p = _position.match(body)
    if not p:
        return False
    lat = int(p.group(1)) + float(p.group(2)) / 60
    lon = int(p.group(4)) + float(p.group(5)) / 60
    if p.group(3) == "S":
        lat = -lat
    if p.group(6) == "W":
        lon = -lon
    return not (EUROPE[0] <= lat <= EUROPE[1] and EUROPE[2] <= lon <= EUROPE[3])


def from_env(hidden):
    path = os.getenv("EC_RAW_DIR")
    if not path:
        return None
    days = float(os.getenv("EC_RAW_DAYS", "7"))
    min_free = float(os.getenv("EC_RAW_MIN_FREE_GB", "10"))
    keep = {k.strip() for k in os.getenv("EC_RAW_KEEP", "europe,no-airliners").split(",") if k.strip()}
    unknown = keep - {"europe", "world", "no-airliners", "no-jets", "no-adsb"}
    if unknown:
        raise ValueError(f"EC_RAW_KEEP: unknown {sorted(unknown)}")
    return Recorder(path, days, keep, hidden, min_free)


class Recorder:
    def __init__(self, path, days, keep, hidden, min_free=10):
        self.dir = os.path.expanduser(path)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.days = days
        self.min_free = min_free * 1e9
        self.europe = "world" not in keep
        self.no_airliners = "no-airliners" in keep
        self.no_jets = "no-jets" in keep or self.no_airliners
        self.no_adsb = "no-adsb" in keep
        self.hidden = hidden
        self.hour = None
        self.fd = None
        self.pending = []
        self.flushed_at = time.time()
        self.lock = threading.Lock()
        self.children = []
        logger.info("Raw feed recording to %s, %s days, keep %s", self.dir, days, sorted(keep))
        last = self.last_write()                # before rotate creates this hour's file
        now = time.time()
        self.rotate(now)
        # A start after a stop (a reboot, a crash) is a break the listener never
        # sees, since it only marks reconnections: mark it here, so that a replay
        # does not read the downtime as silence. A reload is not one, since the
        # old worker writes every second until it exits.
        if last is not None and now - last > BREAK_AFTER:
            self.mark(sources.FEED_BREAK)
            logger.info("Raw feed recording: feed break marked, last write %.0f s ago", now - last)

    def last_write(self):
        """Modification time of the newest recording, or None; zstd keeps that of the file it compresses."""
        files = glob.glob(os.path.join(self.dir, "*.aprs")) + glob.glob(os.path.join(self.dir, "*.aprs.zst"))
        return max((os.path.getmtime(f) for f in files), default=None)

    def keep(self, line):
        src, _, rest = line.partition(">")
        head, _, body = rest.partition(":")
        tocall = head.partition(",")[0]
        m = _id_field.search(body)
        if m:
            b = int(m.group(1), 16)
            if b & 0x40:
                return False                    # the device asks not to be tracked
            if tocall == "OGADSB" and self.no_jets and (b >> 2) & 0x0F == JET:
                return False
        if tocall == "OGADSB" and head.rpartition(",")[2] not in sources.REMOTE_ID_GATEWAYS:
            # Remote ID forwarded under OGADSB is kept whatever its altitude and
            # speed: its formatter was still being checked in October 2026.
            if self.no_adsb:
                return False
            if self.no_airliners:
                v, a = _speed.match(body), _altitude.search(body)
                if a is None or int(a.group(1)) > AIRLINER_FT or (v and int(v.group(2)) > AIRLINER_KT):
                    return False
        if self.europe and outside_europe(body):
            return False
        if self.hidden(src):
            return False
        return True

    def write(self, line):
        """Called by the listener for every line, in arrival order."""
        try:
            if not self.keep(line):
                return
            now = time.time()
            self.pending.append("%.3f %s\n" % (now, line))
            if now - self.flushed_at >= FLUSH_SECONDS:
                self.flush(now)
        except Exception as e:      # recording must never stop the listener
            logger.error("Raw feed recording: %s", e)

    def comment(self, text):
        """Record a comment line of the server or a queue sample (from 27 October 2026), stamped like a
        packet and flushed with the packets. A > is replaced, so that no reader that splits on one
        takes the line for a packet; the length is capped."""
        try:
            now = time.time()
            self.pending.append("%.3f %s\n" % (now, text.replace(">", "}")[:200]))
            if now - self.flushed_at >= FLUSH_SECONDS:
                self.flush(now)
        except Exception as e:
            logger.error("Raw feed recording: %s", e)

    def queue(self, nbytes):
        """Record the bytes waiting in the socket's receive queue."""
        self.comment("# rcvq %d" % nbytes)

    def mark(self, text):
        """Record a line that is not a packet (sources.FEED_BREAK, from 24 October 2026),
        with the epoch like any other and written at once, so that a replay meets
        it between the lines before and after it. It starts with a # like the
        server's own comment lines, which are never recorded, so no APRS line can be
        mistaken for it, and it holds no > for the readers that split on one."""
        try:
            now = time.time()
            self.pending.append("%.3f %s\n" % (now, text))
            self.flush(now)
        except Exception as e:
            logger.error("Raw feed recording: %s", e)

    def flush(self, now):
        with self.lock:
            if self.pending:
                # One write of whole lines to an O_APPEND file, so the old and
                # the new worker can overlap during a reload without mixing lines.
                # A failed write (a full disk) drops the lines: kept, they would
                # pile up in memory until the worker is killed for it.
                lines, self.pending = self.pending, []
                try:
                    os.write(self.fd, "".join(lines).encode("utf-8", "replace"))
                except OSError as e:
                    logger.error("Raw feed recording: %s, %d lines lost", e, len(lines))
            if time.strftime(NAME, time.gmtime(now)) != self.hour:
                self.rotate(now)
            self.flushed_at = now

    def rotate(self, now):
        if self.fd is not None:
            os.close(self.fd)
        self.hour = time.strftime(NAME, time.gmtime(now))
        current = os.path.join(self.dir, self.hour + ".aprs")
        self.fd = os.open(current, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.children = [c for c in self.children if c.poll() is None]
        # Every finished hour, including any left by a stop or a crash.
        for path in sorted(glob.glob(os.path.join(self.dir, "*.aprs"))):
            if path != current and not os.path.exists(path + ".zst"):
                self.children.append(subprocess.Popen(
                    ["nice", "zstd", f"-{ZSTD_LEVEL}", "-q", "--rm", path],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL))
        cutoff = time.strftime(NAME, time.gmtime(now - self.days * 86400))
        for path in glob.glob(os.path.join(self.dir, "*.aprs*")):
            if os.path.basename(path)[:len(cutoff)] < cutoff:
                os.remove(path)
        self.free_space(now)

    def free_space(self, now):
        """Delete the oldest compressed hours while the disk has less than
        min_free bytes free, keeping the last two days."""
        floor = time.strftime(NAME, time.gmtime(now - 2 * 86400))
        removed = 0
        for path in sorted(glob.glob(os.path.join(self.dir, "*.aprs.zst"))):
            if shutil.disk_usage(self.dir).free >= self.min_free:
                break
            if os.path.basename(path)[:len(floor)] >= floor:
                logger.error("Raw feed recording: disk below %.0f GB free with only two days kept",
                             self.min_free / 1e9)
                break
            os.remove(path)
            removed += 1
        if removed:
            logger.warning("Raw feed recording: %d oldest hours deleted to keep %.0f GB free",
                           removed, self.min_free / 1e9)
