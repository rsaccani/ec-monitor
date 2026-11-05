"""Device check: what the feed heard from one address in the last 24 hours.

Behind /api/check and the page conspicuity.eu/check/ (from 10 October 2026).
A pilot on take-off asks whether the device is being heard, and how well; a
family in the evening asks when and where it was heard last. Both questions
are answered from the same state, kept here per 24-bit address and per
system (FLARM, FANET, ADS-L, an app...), fed by SourceTracker.handle after
its own filters: devices hidden by the OGN device database, packets with the
no-track flag, receivers, ground stations and ADS-B never get here.

What is kept is shaped by the OGN data usage terms and by what the page must
not become. The terms allow showing OGN data for 24 hours, so an address
unheard for that long is dropped whole. The page must not turn into a track
recorder, so no series of positions is kept: the last fix of each system,
and small samples (intervals, signal, frequency offset, delay) from which
medians are computed. Answers are cached per address for CACHE_S, so asking
every second still yields one fix a minute. Only the "live" part of an
answer is read fresh on every call: when each system was last heard and
through which receiver, with no position and no distance, because a pilot
who has just switched the device on needs to see it heard within seconds and
already knows where the aircraft is. nginx caches the whole answer for a few
seconds more, so this is as fresh as that.

Random addresses (FLARM's privacy mode, callsign prefix RND) change every few
seconds and can never be looked up, so they are not kept at all.

The state is pickled every SAVE_EVERY seconds and at exit, and read back at
start, because a deploy reloads the worker and would otherwise empty it. The
receivers' positions (SourceTracker.stations) are saved with it.

The timed samples (signal, frequency offset, and the reference of other
devices) are half a million records over a day, and as lists of tuples of
Python numbers they took some 75 MB of the state's 100 (24 October 2026).
They are kept packed in Samples instead, 6 to 12 bytes a record.
"""

import atexit
import logging
import math
import os
import pickle
import statistics
import struct
import sys
import threading
import time

import sources

logger = logging.getLogger("main")

WINDOW = 24 * 3600          # the OGN terms: nothing older than 24 hours is shown
CACHE_S = 60                # one answer per address per minute
SAVE_EVERY = 300
GAP_S = 60                  # a silence longer than this (or 3 heartbeats) is listed
# After a pause longer than this the system's samples start again, so that a
# pilot flying every day never sees yesterday's samples in today's medians.
# The pause itself is listed as a silence only if the aircraft was flying at
# both ends: an aircraft can stay out of every receiver's range for hours
# (56% of lost tracks came back after 20 minutes to over 2 hours, 6-8
# October 2026), while a pause that starts or ends on the ground is the
# time between two flights. So is any pause over FLIGHT_MAX_S, longer than a
# day's flight, even when both ends are airborne: a pilot who landed out of
# range yesterday and took off out of range today. Every sample that could outlive a session (signal, frequency
# offset, the reference of other devices) also carries its time and is
# dropped after WINDOW.
SESSION_S = 3 * 3600
FLIGHT_MAX_S = 12 * 3600
ACTIVE_S = 3600             # a receiver that relayed nothing for an hour is not "nearest"
# Distance bands for the signal comparison, km. Signal falls with distance,
# so a device's dB means something only next to other devices at the same
# distance from the same receiver.
BANDS = (2, 5, 10, 20, 40, 80)
N_INTERVALS = 30            # samples per system: enough for a median, small
N_SIGNAL = 15               # per (receiver, band)
N_SAMPLES = 20              # frequency offset, delay
N_GAPS = 10
N_REF = 40                  # reference samples per (receiver, system, band)
# The reception pattern of one device (25 October 2026): the airborne receptions
# from stations farther than PATTERN_MIN_KM (closer ones hear every side and
# hide a shadow, METHOD.md 7), by the side of the aircraft the station sees, in
# 12 sectors of 30 degrees centred on the nose; the signal scaled to 10 km with
# sources.SNR_SLOPE_DB (METHOD.md 10.5). At most N_PATTERN records a system, the
# newest kept: 7 bytes each, 2.5 kB for a system that fills it.
PATTERN_MIN_KM = 20
N_PATTERN = 360
PATTERN_MIN_N = 10          # receptions a sector needs to count
PATTERN_MIN_SECTORS = 8     # sectors with PATTERN_MIN_N each, or no pattern is shown
PATTERN_DAYS = 30           # the days of daily_device_pattern (nightly.PATTERN_KEEP_DAYS)
STATE_VERSION = 4           # 2: samples carry their time (10 October 2026); 3: packed (24 October 2026);
                            # 4: compact Systems, in a file of its own (STATE_PATH, "-v4" before the extension)
STATE_PATH = os.environ.get("EC_CHECK_STATE", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "check-state.pickle"))



def v4_path(path):
    """The compact state's file, next to the old one: code that does not know version 4 never reads
    it and starts empty, which is its rollback."""
    root, ext = os.path.splitext(path)
    return root + "-v4" + ext


ADDRESS_TYPES = {0: "random", 1: "icao", 2: "flarm", 3: "ogn"}


def band_of(km):
    for i, edge in enumerate(BANDS):
        if km < edge:
            return i
    return len(BANDS)


def band_label(i):
    lo = 0 if i == 0 else BANDS[i - 1]
    return f"{lo}-{BANDS[i]} km" if i < len(BANDS) else f"over {BANDS[-1]} km"


def _push(lst, value, cap):
    lst.append(value)
    if len(lst) > cap:
        del lst[0]


def _median(lst):
    return statistics.median(lst) if lst else None


class Samples:
    """A series of fixed-size records packed in one bytearray, read back as
    tuples: append, len, del [i] and iteration, which is all _push and the
    readers use. A subclass names the struct format and how a tuple is
    packed and unpacked. Every record starts with its epoch, as an unsigned
    32-bit integer."""
    __slots__ = ("buf",)
    fmt = None

    def __init__(self, data=b""):
        self.buf = bytearray(data)

    @classmethod
    def of(cls, items):
        out = cls()
        for x in items:
            out.append(x)
        return out

    def __reduce__(self):
        # The bytearray itself: a bytes copy per series would stay alive in the pickler's memo
        # until the whole state is written.
        return (type(self), (self.buf,))

    def __len__(self):
        return len(self.buf) // self.fmt.size

    def __iter__(self):
        # Over a copy: an iterator left open on the bytearray itself (any()
        # stopping early) would make the next append raise BufferError.
        return map(self.unpack, self.fmt.iter_unpack(bytes(self.buf)))

    def __delitem__(self, i):
        n = len(self)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError(i)
        size = self.fmt.size
        del self.buf[i * size:(i + 1) * size]

    def append(self, x):
        self.buf += self.fmt.pack(*self.pack(x))

    def since(self, cutoff):
        """The records from cutoff on, as a new series."""
        size = self.fmt.size
        out = type(self)()
        for i in range(0, len(self.buf), size):
            if _EPOCH.unpack_from(self.buf, i)[0] >= cutoff:
                out.buf += self.buf[i:i + size]
        return out

    @staticmethod
    def pack(x):
        return x

    @staticmethod
    def unpack(rec):
        return rec


_EPOCH = struct.Struct("<I")


def _int16(v):
    return max(-32768, min(32767, v))


# The signal of one device at one receiver and distance band (epoch, dB), packed in a bytes object
# held directly as the dict's value: an immutable series of fixed-size records, newest last, and
# the key tuples and receiver names shared between systems (31 October 2026: 58,698 keys at 334 B
# each at 1x, of which 167 B were the Signal object, a name and a tuple of its own, and slack).
_SIG = struct.Struct("<Ih")
_SIG_CAP = N_SIGNAL * _SIG.size
_KEYS = {}


def sig_key(station, band):
    """The (station, band) key, one tuple and one string for every system that has it."""
    k = _KEYS.get((station, band))
    if k is None:
        if len(_KEYS) > 200000:
            _KEYS.clear()
        k = _KEYS[(station, band)] = (sys.intern(station), band)
    return k


def sig_push(buf, epoch, db):
    buf += _SIG.pack(epoch, _int16(db))
    return buf[-_SIG_CAP:] if len(buf) > _SIG_CAP else buf


def sig_len(buf):
    return len(buf) // _SIG.size


def sig_since(buf, cutoff):
    """The records from cutoff on, in order."""
    if all(t >= cutoff for t, _ in _SIG.iter_unpack(buf)):
        return buf
    return b"".join(_SIG.pack(t, v) for t, v in _SIG.iter_unpack(buf) if t >= cutoff)


class Signal(Samples):
    """(epoch, dB) as the version 3 state kept it: only read from an old state and converted."""
    __slots__ = ()
    fmt = struct.Struct("<Ih")


class Khz(Samples):
    """(epoch, kHz to one decimal), kept as tenths so that reading back gives
    the same float round(x, 1) did."""
    __slots__ = ()
    fmt = struct.Struct("<Ii")

    @staticmethod
    def pack(x):
        return x[0], round(x[1] * 10)

    @staticmethod
    def unpack(rec):
        return rec[0], rec[1] / 10


class Ref(Samples):
    """(epoch, address, dB) of every device at one receiver, system and band.
    The address is the six-character string the feed gives (ASCII)."""
    __slots__ = ()
    fmt = struct.Struct("<I6sh")

    @staticmethod
    def pack(x):
        return x[0], x[1].encode("ascii", "replace"), _int16(x[2])

    @staticmethod
    def unpack(rec):
        return rec[0], rec[1].rstrip(b"\0").decode("ascii"), rec[2]


def pattern_days(rows, days):
    """The 30-day pattern of one system, from daily_device_pattern rows summed over the days:
    [(sector, receptions, sum of dB scaled to 10 km)]. As DeviceCheck._pattern, with a mean
    where it has a median, since the table holds counts and sums: per sector the mean signal
    minus the mean of every reception of the system, and the same PATTERN_MIN_N and
    PATTERN_MIN_SECTORS. `days` is how many of the PATTERN_DAYS days have a row."""
    n, total = [0] * 12, [0.0] * 12
    for sector, k, s in rows:
        n[sector] += k
        total[sector] += s
    count = sum(n)
    full = sum(1 for k in n if k >= PATTERN_MIN_N)
    out = {"n": count, "min_km": PATTERN_MIN_KM, "min_n": PATTERN_MIN_N, "min_sectors": PATTERN_MIN_SECTORS,
           "sectors_full": full, "sectors": None, "days": days, "window_days": PATTERN_DAYS}
    if full >= PATTERN_MIN_SECTORS:
        mid = sum(total) / count
        out["sectors"] = [{"deg": i * 30, "n": n[i],
                           "db": round(total[i] / n[i] - mid, 1) if n[i] >= PATTERN_MIN_N else None}
                          for i in range(12)]
    return out


class Pattern(Samples):
    """(epoch, sector 0-11, dB scaled to 10 km in tenths) of one system's far receptions."""
    __slots__ = ()
    fmt = struct.Struct("<IBh")

    @staticmethod
    def pack(x):
        return x[0], x[1], _int16(round(x[2] * 10))

    @staticmethod
    def unpack(rec):
        return rec[0], rec[1], rec[2] / 10


class Gap(Samples):
    """(from, to) epochs of one silence."""
    __slots__ = ()
    fmt = struct.Struct("<II")


class Hours(Samples):
    """(UTC hour start, packets) of one system, in the order the hours were first heard: a
    dict of hour -> packets, packed."""
    __slots__ = ()
    fmt = struct.Struct("<II")

    def bump(self, h):
        size, buf = self.fmt.size, self.buf
        for i in range(len(buf) - size, -1, -size):
            if _EPOCH.unpack_from(buf, i)[0] == h:
                n = _EPOCH.unpack_from(buf, i + 4)[0]
                buf[i + 4:i + 8] = _EPOCH.pack(n + 1)
                return
        buf += self.fmt.pack(h, 1)

    def first(self):
        """The earliest hour, or None."""
        return min((_EPOCH.unpack_from(self.buf, i)[0] for i in range(0, len(self.buf), self.fmt.size)),
                   default=None)


class System:
    """What one system of one address sent: plain attributes, so it pickles."""
    pattern = None          # a System saved before 25 October 2026 has none
    # Set by DeviceCheck.feed_break: the next fix starts again, with no interval or
    # silence to the one before. A class attribute, so that a state saved without it reads back.
    cut = False

    def __init__(self, label, kind, via, tocall):
        self.label, self.kind, self.via, self.tocall = label, kind, via, tocall
        self.src = None
        self.first = self.last = None
        self.hours = Hours()    # UTC hour start -> packets
        self.cats = {}          # declared category -> packets
        self.atype = None
        self.stealth = False
        self.fix = None         # (t_fix, lat, lon, alt_m, course, speed_kt, station, snr, err)
        self.intervals = []
        self.signal = {}        # sig_key(station, band) -> packed (epoch, dB) records, sig_push
        self.khz = None         # Khz of (epoch, offset minus the receiver's own median, kHz), from the first sample
        self.delay = []         # seconds between the fix and its arrival
        self.err_packets = 0    # packets with corrected bit errors
        self.radio_packets = 0
        self.gaps = Gap()       # (from, to) epochs
        self.jumps = []         # epochs of impossible jumps
        self.stations = {}      # receiver -> last epoch it relayed this system
        self.pattern = None     # Pattern of far airborne receptions by sector (PATTERN_MIN_KM), from the first one


def _from_v3(s):
    """A System of the version 3 state, in place, as the compact layout keeps it."""
    s.stations = {sys.intern(k): v[1] for k, v in s.stations.items()}
    s.signal = {sig_key(*k): bytes(v.buf) for k, v in s.signal.items()}
    s.gaps = Gap.of(s.gaps)
    s.hours = Hours.of(s.hours.items())
    s.khz = s.khz or None
    s.pattern = getattr(s, "pattern", None) or None


class DeviceCheck:
    def __init__(self, stations, terrain=None, ddb=None, path=STATE_PATH):
        self.stations = stations    # SourceTracker.stations: receiver -> (lat, lon)
        self.terrain = terrain
        self.ddb = ddb or (lambda address: {})
        self.path = path                # the version 3 file, read once when there is no version 4 one
        self.path4 = v4_path(path)
        self.devices = {}           # address -> {label: System}
        self.active = {}            # receiver -> last epoch it relayed a packet
        self.ref = {}               # (receiver, label, band) -> Ref of (epoch, address, dB), every device
        self.ref_khz = {}           # receiver -> Khz of (epoch, kHz), every device
        self.cache = {}
        self.lock = threading.Lock()
        self.load()
        atexit.register(self.save)

    # --- per packet (listener thread) ---------------------------------------

    def feed(self, src, tocall, label, kind, via, body, station, category, tick, now):
        if src.startswith("RND"):
            return
        fix = sources.parse_fix(body, now)
        if fix is None:
            return
        t_fix, lat, lon, course, speed, _rot, _sym, alt_m = fix
        address = src[-6:].upper()
        if station is not None:
            station = sys.intern(station)    # one string for every table that holds it
        if kind == "platform":
            # A platform (PureTrack and the like) forwards positions it got
            # from elsewhere, some under pseudo-receivers with made-up signal
            # figures, and mixes sources, so its jumps say nothing either.
            via = "net"
        meta = sources._radio_meta.search(body) if via == "radio" else None
        snr = err = khz = None
        if meta:
            for tok in body[meta.start():meta.end() + 1].split():
                if tok.endswith("dB"):
                    snr = float(tok[:-2])
                elif tok.endswith("kHz"):
                    khz = float(tok[:-3])
                elif tok.endswith("e") and tok[:-1].isdigit():
                    err = int(tok[:-1])
        with self.lock:
            systems = self.devices.get(address)
            if category in sources.GROUND_CATEGORIES and not (
                    systems and any(c in sources.AIRCRAFT_CATEGORIES
                                    for x in systems.values() for c in x.cats)):
                # Static objects and ground stations (a FANET base station
                # sends category 15 all day): not an aircraft, not kept. An
                # address that has declared an aircraft category keeps its
                # ground packets, as a pilot who has landed.
                return
            if systems is None:
                systems = self.devices[address] = {}
            s = systems.get(label)
            if s is None:
                s = systems[label] = System(label, kind, via, tocall)
            if s.last is not None and tick - s.last > SESSION_S:
                s.intervals, s.signal, s.khz, s.delay = [], {}, None, []
                s.err_packets = s.radio_packets = 0
            s.src = src
            if via == "radio":
                s.via = "radio"     # a system relayed once over the net is still radio
            if s.first is None:
                s.first = tick
            s.last = tick
            h = tick - tick % 3600
            s.hours.bump(h)
            if category is not None:
                s.cats[category] = s.cats.get(category, 0) + 1
            m = sources._id_field.search(body)
            if m and len(m.group(1)) == 8:
                b = int(m.group(1)[:2], 16)
                s.atype, s.stealth = b & 0x03, bool(b & 0x80)
            _push(s.delay, int(round(tick - t_fix)), N_SAMPLES)
            prev = s.fix
            if prev is not None and t_fix <= prev[0]:
                return          # a duplicate, or a late copy of an older fix
            cut, s.cut = s.cut, False
            if prev is not None:
                dt = t_fix - prev[0]
                cadence = sources.cadence_of(tocall, kind, via)
                if cut:
                    pass        # the feed was cut in between (24 October 2026): not a silence of the device
                elif dt > SESSION_S:
                    floor = sources.FLYING_KT.get(category, sources.AIRBORNE_KT)
                    if dt <= FLIGHT_MAX_S and min(prev[5] or 0, speed or 0) >= floor:
                        _push(s.gaps, (prev[0], t_fix), N_GAPS)
                elif dt > max(GAP_S, 3 * cadence[2] if cadence else 0):
                    _push(s.gaps, (prev[0], t_fix), N_GAPS)
                else:
                    _push(s.intervals, int(dt), N_INTERVALS)
                metres = sources._distance(prev[1], prev[2], lat, lon)
                if kind != "platform" and sources.impossible_jump(category, metres, dt):
                    _push(s.jumps, t_fix, N_GAPS)
            s.fix = (t_fix, lat, lon, alt_m, course, speed, station, snr, err)
            if station is None:
                return
            self.active[station] = tick
            if via != "radio":
                return
            s.radio_packets += 1
            if err:
                s.err_packets += 1
            s.stations[station] = tick
            where = self.stations.get(station)
            km = sources._distance(lat, lon, *where) / 1000 if where else None
            if khz is not None:
                ref = self.ref_khz.get(station)
                if ref is None:
                    ref = self.ref_khz[station] = Khz()
                _push(ref, (tick, round(khz, 1)), N_REF)
                base = statistics.median(v for t, v in ref)
                if s.khz is None:
                    s.khz = Khz()
                _push(s.khz, (tick, round(khz - base, 1)), N_SAMPLES)
            if snr is not None and km is not None:
                b = band_of(km)
                key = sig_key(station, b)
                s.signal[key] = sig_push(s.signal.get(key, b""), tick, round(snr))
                ref = self.ref.get((station, label, b))
                if ref is None:
                    ref = self.ref[(station, label, b)] = Ref()
                _push(ref, (tick, address, round(snr)), N_REF)
                if (km > PATTERN_MIN_KM and course is not None
                        and (speed or 0) >= sources.FLYING_KT.get(category, sources.AIRBORNE_KT)):
                    rel = (sources._bearing(lat, lon, *where) - course) % 360
                    sector = int(((rel + 15) % 360) // 30)
                    pat = s.pattern
                    if pat is None:
                        pat = s.pattern = Pattern()
                    _push(pat, (tick, sector, snr + sources.SNR_SLOPE_DB * math.log10(km / 10)), N_PATTERN)

    def feed_break(self, epoch=None):
        """The feed was cut (SourceTracker.feed_break): the time between the last
        fix of each system and its next is not a silence of the device, so it
        is neither an interval nor a listed gap."""
        with self.lock:
            for systems in self.devices.values():
                for s in systems.values():
                    s.cut = True

    # --- housekeeping (prune thread) ----------------------------------------

    def prune(self):
        cutoff = time.time() - WINDOW
        with self.lock:
            for address in list(self.devices):
                systems = self.devices[address]
                for label in list(systems):
                    s = systems[label]
                    if s.last < cutoff:
                        del systems[label]
                        continue
                    s.hours = s.hours.since(cutoff)
                    s.gaps = s.gaps.since(cutoff)
                    if s.khz is not None:
                        s.khz = s.khz.since(cutoff) or None
                    s.signal = {k: sig_since(v, cutoff) for k, v in s.signal.items()}
                    s.signal = {k: v for k, v in s.signal.items() if v}
                    if s.pattern is not None:
                        s.pattern = s.pattern.since(cutoff) or None
                    s.jumps = [j for j in s.jumps if j > cutoff]
                    s.stations = {k: v for k, v in s.stations.items() if v > cutoff}
                    first_hour = s.hours.first()
                    s.first = max(s.first, first_hour if first_hour is not None else s.last)
                if not systems:
                    del self.devices[address]
            self.active = {k: v for k, v in self.active.items() if v > cutoff}
            for k in list(self.ref):
                v = self.ref[k].since(cutoff)
                if v:
                    self.ref[k] = v
                else:
                    del self.ref[k]
            for k in list(self.ref_khz):
                v = self.ref_khz[k].since(cutoff)
                if v:
                    self.ref_khz[k] = v
                else:
                    del self.ref_khz[k]
            now = time.time()
            self.cache = {k: v for k, v in self.cache.items() if now - v[0] < CACHE_S}

    def loop(self):
        saved = time.time()
        while True:
            time.sleep(60)
            try:
                self.prune()
                if time.time() - saved >= SAVE_EVERY:
                    self.save()
                    saved = time.time()
            except Exception as e:
                logger.error("Device check housekeeping: %s", e)

    def save(self):
        try:
            tmp = self.path4 + ".tmp"
            # Straight to the file, under the lock, so the state is never held twice in memory.
            with open(tmp, "wb") as f, self.lock:
                pickle.dump((time.time(), self.devices, self.active, self.ref, self.ref_khz,
                             dict(self.stations), STATE_VERSION), f, protocol=pickle.HIGHEST_PROTOCOL)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path4)
        except Exception as e:
            logger.error("Device check: could not save the state: %s", e)

    def load(self):
        try:
            with open(self.path4, "rb") as f:
                saved = pickle.load(f)
            if len(saved) < 7 or saved[6] != STATE_VERSION:
                logger.error("Device check: the saved state is of version %s, not read", saved[6:7])
                return
        except FileNotFoundError:
            self.load_v3()
            return
        except Exception as e:
            logger.error("Device check: could not read the saved state: %s", e)
            return
        saved_at, devices, active, ref, ref_khz = saved[:5]
        if time.time() - saved_at > WINDOW:
            return
        for name, where in saved[5].items():
            self.stations.setdefault(name, where)
        for systems in devices.values():
            for s in systems.values():
                # Names and keys shared again, as in a running service.
                s.signal = {sig_key(*k): v for k, v in s.signal.items()}
                s.stations = {sys.intern(k): v for k, v in s.stations.items()}
        self.devices, self.active, self.ref, self.ref_khz = devices, active, ref, ref_khz
        self.prune()
        logger.info("Device check: %d addresses read back", len(self.devices))

    def load_v3(self):
        """The state of the code before version 4: read, converted exactly, kept in memory until the
        next save writes it in the new file. The old file is left as it is."""
        try:
            with open(self.path, "rb") as f:
                saved = pickle.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.error("Device check: could not read the saved state: %s", e)
            return
        saved_at, devices, active, ref, ref_khz = saved[:5]
        if time.time() - saved_at > WINDOW:
            return
        # Receiver positions come only from their beacons, every few minutes;
        # without these, distances are missing for a while after each reload.
        if len(saved) > 5:
            for name, where in saved[5].items():
                self.stations.setdefault(name, where)
        if len(saved) < 7 or saved[6] < 2:
            # Saved before samples carried their time (10 October 2026): the
            # samples are dropped, the rest is kept.
            ref, ref_khz = {}, {}
            for systems in devices.values():
                for s in systems.values():
                    s.signal, s.khz = {}, Khz()
        elif saved[6] < 3:
            # Saved as lists of tuples (before 24 October 2026): packed here.
            for systems in devices.values():
                for s in systems.values():
                    s.signal = {k: Signal.of(v) for k, v in s.signal.items()}
                    s.khz = Khz.of(s.khz)
            ref = {k: Ref.of(v) for k, v in ref.items()}
            ref_khz = {k: Khz.of(v) for k, v in ref_khz.items()}
        for systems in devices.values():
            for s in systems.values():
                _from_v3(s)
        self.devices, self.active, self.ref, self.ref_khz = devices, active, ref, ref_khz
        self.prune()
        logger.info("Device check: %d addresses read back (version 3 state converted)", len(self.devices))

    # --- the answer ---------------------------------------------------------

    def report(self, address):
        address = address.upper()
        now = time.time()
        with self.lock:
            hit = self.cache.get(address)
            if hit and now - hit[0] < CACHE_S:
                out = hit[1]
            else:
                out = self._report(address, now)
                self.cache[address] = (now, out)
            return dict(out, live=self._live(address, now))

    def _live(self, address, now):
        systems = []
        for s in sorted(self.devices.get(address, {}).values(), key=lambda s: -s.last):
            if s.last < now - WINDOW or (s.cats and all(c in sources.GROUND_CATEGORIES for c in s.cats)):
                continue
            station = s.fix[6] if s.fix and s.via == "radio" else None
            systems.append({"system": s.label, "via": s.via, "last_heard": s.last,
                            "receiver": station})
        return {"now": int(now), "systems": systems}

    def _report(self, address, now):
        out = {"address": address, "now": int(now), "window_s": WINDOW, "cache_s": CACHE_S,
               "ddb": self.ddb(address), "systems": []}
        systems = self.devices.get(address, {})
        latest = None
        for s in sorted(systems.values(), key=lambda s: -s.last):
            if s.last < now - WINDOW:
                continue
            if s.cats and all(c in sources.GROUND_CATEGORIES for c in s.cats):
                continue    # state saved before static objects were left out
            out["systems"].append(self._system(s, now, address))
            if latest is None or s.fix[0] > latest.fix[0]:
                latest = s
        out["heard"] = latest is not None
        if latest is not None:
            out["last_system"] = latest.label
            out["nearest_receiver"] = self._nearest(latest.fix[1], latest.fix[2], now)
        return out

    def _system(self, s, now, address):
        t_fix, lat, lon, alt_m, course, speed, station, snr, err = s.fix
        last = {"t": t_fix, "lat": round(lat, 5), "lon": round(lon, 5),
                "alt_m": round(alt_m) if alt_m is not None else None,
                "speed_kmh": round(speed * 1.852) if speed is not None else None,
                "course": course, "via_name": station}
        if self.terrain is not None and alt_m is not None:
            ground = self.terrain.elevation(lat, lon)
            if ground is not None:
                last["agl_m"] = round(alt_m - ground)
        if s.via == "radio" and station:
            where = self.stations.get(station)
            last.update({"receiver": station, "snr_db": snr})
            if where:
                last["receiver_lat"], last["receiver_lon"] = round(where[0], 4), round(where[1], 4)
                last["receiver_km"] = round(sources._distance(lat, lon, *where) / 1000, 1)
        cadence = sources.cadence_of(s.tocall, s.kind, s.via)
        cat = max(s.cats, key=s.cats.get) if s.cats else None
        sig = []
        cutoff = now - WINDOW
        for (station, b), samples in sorted(s.signal.items(), key=lambda kv: -sig_len(kv[1]))[:4]:
            values = [v for t, v in _SIG.iter_unpack(samples) if t >= cutoff]
            if not values:
                continue
            # Other devices only: near a receiver with little traffic the
            # device itself would otherwise fill its own reference.
            others = [(a, v) for t, a, v in self.ref.get((station, s.label, b), []) if a != address and t >= cutoff]
            ref = [v for a, v in others]
            sig.append({"receiver": station, "band": band_label(b), "n": len(values),
                        "snr_db": _median(values), "others_db": _median(ref), "others_n": len({a for a, v in others}),
                        "others_packets": len(ref)})
        return {
            "system": s.label, "kind": s.kind, "via": s.via, "tocall": s.tocall, "callsign": s.src,
            "first": max(s.first, now - WINDOW), "last_heard": s.last,
            "packets": sum(n for h, n in s.hours if h >= cutoff),
            "category": cat, "category_name": sources.CATEGORY_NAMES.get(cat) if cat is not None else None,
            "categories": {sources.CATEGORY_NAMES.get(c, str(c)): n for c, n in s.cats.items()},
            "address_type": ADDRESS_TYPES.get(s.atype), "stealth": s.stealth,
            "interval_s": _median(s.intervals),
            "nominal_s": cadence[2] if cadence and cadence[0] is None else None,
            "delay_s": _median(s.delay),
            "khz_offset": round(_median([v for t, v in s.khz or () if t >= cutoff]), 1)
                          if any(t >= cutoff for t, v in s.khz or ()) else None,
            "error_share": round(s.err_packets / s.radio_packets, 2) if s.radio_packets else None,
            "receivers": len(s.stations),
            "signal": sig,
            "gaps": [{"from": a, "to": b, "s": int(b - a)} for a, b in s.gaps if a >= cutoff],
            "jumps": len(s.jumps),
            "pattern": self._pattern(s, cutoff),
            "last": last,
        }

    def _pattern(self, s, cutoff):
        """Per sector (0 = nose, clockwise) the median signal minus the system's
        own median, in dB, and the receptions; None where a sector has fewer than
        PATTERN_MIN_N, and shown at all only with PATTERN_MIN_SECTORS such sectors."""
        recs = [(sec, v) for t, sec, v in (s.pattern or ()) if t >= cutoff]
        by = [[] for _ in range(12)]
        for sec, v in recs:
            by[sec].append(v)
        full = sum(1 for x in by if len(x) >= PATTERN_MIN_N)
        out = {"n": len(recs), "min_km": PATTERN_MIN_KM, "min_n": PATTERN_MIN_N,
               "min_sectors": PATTERN_MIN_SECTORS, "sectors_full": full, "sectors": None}
        if full >= PATTERN_MIN_SECTORS:
            mid = statistics.median(v for sec, v in recs)
            out["sectors"] = [{"deg": i * 30, "n": len(x),
                               "db": round(statistics.median(x) - mid, 1) if len(x) >= PATTERN_MIN_N else None}
                              for i, x in enumerate(by)]
        return out

    def _nearest(self, lat, lon, now):
        best = None
        for name, last in self.active.items():
            if now - last > ACTIVE_S:
                continue
            where = self.stations.get(name)
            if not where:
                continue
            km = sources._distance(lat, lon, *where) / 1000
            if best is None or km < best[1]:
                best = (name, km, where)
        if best is None:
            return None
        return {"name": best[0], "km": round(best[1], 1),
                "lat": round(best[2][0], 4), "lon": round(best[2][1], 4)}
