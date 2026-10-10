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
every second still yields one fix a minute.

Random addresses (FLARM's privacy mode, callsign prefix RND) change every few
seconds and can never be looked up, so they are not kept at all.

The state is pickled every SAVE_EVERY seconds and at exit, and read back at
start, because a deploy reloads the worker and would otherwise empty it. The
receivers' positions (SourceTracker.stations) are saved with it.
"""

import atexit
import logging
import os
import pickle
import statistics
import threading
import time

import sources

logger = logging.getLogger("main")

WINDOW = 24 * 3600          # the OGN terms: nothing older than 24 hours is shown
CACHE_S = 60                # one answer per address per minute
SAVE_EVERY = 300
GAP_S = 60                  # a silence longer than this (or 3 heartbeats) is listed
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
STATE_PATH = os.environ.get("EC_CHECK_STATE", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "check-state.pickle"))

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


class System:
    """What one system of one address sent: plain attributes, so it pickles."""

    def __init__(self, label, kind, via, tocall):
        self.label, self.kind, self.via, self.tocall = label, kind, via, tocall
        self.src = None
        self.first = self.last = None
        self.hours = {}         # UTC hour start -> packets
        self.cats = {}          # declared category -> packets
        self.atype = None
        self.stealth = False
        self.fix = None         # (t_fix, lat, lon, alt_m, course, speed_kt, station, snr, err)
        self.intervals = []
        self.signal = {}        # (station, band) -> [dB]
        self.khz = []           # offset minus the receiver's own median, kHz
        self.delay = []         # seconds between the fix and its arrival
        self.err_packets = 0    # packets with corrected bit errors
        self.radio_packets = 0
        self.gaps = []          # (from, to) epochs
        self.jumps = []         # epochs of impossible jumps
        self.stations = {}      # receiver -> [packets, last epoch, min km, max km]


class DeviceCheck:
    def __init__(self, stations, terrain=None, ddb=None, path=STATE_PATH):
        self.stations = stations    # SourceTracker.stations: receiver -> (lat, lon)
        self.terrain = terrain
        self.ddb = ddb or (lambda address: {})
        self.path = path
        self.devices = {}           # address -> {label: System}
        self.active = {}            # receiver -> last epoch it relayed a packet
        self.ref = {}               # (receiver, label, band) -> [dB], every device
        self.ref_khz = {}           # receiver -> [kHz], every device
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
            if systems is None:
                systems = self.devices[address] = {}
            s = systems.get(label)
            if s is None:
                s = systems[label] = System(label, kind, via, tocall)
            s.src = src
            if via == "radio":
                s.via = "radio"     # a system relayed once over the net is still radio
            if s.first is None:
                s.first = tick
            s.last = tick
            h = tick - tick % 3600
            s.hours[h] = s.hours.get(h, 0) + 1
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
            if prev is not None:
                dt = t_fix - prev[0]
                cadence = sources.cadence_of(tocall, kind, via)
                if dt > max(GAP_S, 3 * cadence[2] if cadence else 0):
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
            st = s.stations.get(station)
            where = self.stations.get(station)
            km = sources._distance(lat, lon, *where) / 1000 if where else None
            if st is None:
                st = s.stations[station] = [0, tick, km, km]
            st[0] += 1
            st[1] = tick
            if km is not None:
                st[2] = km if st[2] is None else min(st[2], km)
                st[3] = km if st[3] is None else max(st[3], km)
            if khz is not None:
                ref = self.ref_khz.setdefault(station, [])
                _push(ref, round(khz, 1), N_REF)
                _push(s.khz, round(khz - statistics.median(ref), 1), N_SAMPLES)
            if snr is not None and km is not None:
                b = band_of(km)
                _push(s.signal.setdefault((station, b), []), round(snr), N_SIGNAL)
                _push(self.ref.setdefault((station, label, b), []), (address, round(snr)), N_REF)

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
                    s.hours = {h: n for h, n in s.hours.items() if h + 3600 > cutoff}
                    s.gaps = [g for g in s.gaps if g[1] > cutoff]
                    s.jumps = [j for j in s.jumps if j > cutoff]
                    s.stations = {k: v for k, v in s.stations.items() if v[1] > cutoff}
                    s.first = max(s.first, min(s.hours) if s.hours else s.last)
                if not systems:
                    del self.devices[address]
            self.active = {k: v for k, v in self.active.items() if v > cutoff}
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
            with self.lock:
                blob = pickle.dumps((time.time(), self.devices, self.active, self.ref, self.ref_khz,
                                     dict(self.stations)),
                                    protocol=pickle.HIGHEST_PROTOCOL)
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except Exception as e:
            logger.error("Device check: could not save the state: %s", e)

    def load(self):
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
        # Before 10 October 2026, 21:00 UTC the samples carried no address.
        ref = {k: [x for x in v if isinstance(x, tuple)] for k, v in ref.items()}
        self.devices, self.active, self.ref, self.ref_khz = devices, active, ref, ref_khz
        self.prune()
        logger.info("Device check: %d addresses read back", len(self.devices))

    # --- the answer ---------------------------------------------------------

    def report(self, address):
        address = address.upper()
        now = time.time()
        with self.lock:
            hit = self.cache.get(address)
            if hit and now - hit[0] < CACHE_S:
                return hit[1]
            out = self._report(address, now)
            self.cache[address] = (now, out)
            return out

    def _report(self, address, now):
        out = {"address": address, "now": int(now), "window_s": WINDOW, "cache_s": CACHE_S,
               "ddb": self.ddb(address), "systems": []}
        systems = self.devices.get(address, {})
        latest = None
        for s in sorted(systems.values(), key=lambda s: -s.last):
            if s.last < now - WINDOW:
                continue
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
        for (station, b), values in sorted(s.signal.items(), key=lambda kv: -len(kv[1]))[:4]:
            # Other devices only: near a receiver with little traffic the
            # device itself would otherwise fill its own reference.
            ref = [v for a, v in self.ref.get((station, s.label, b), []) if a != address]
            sig.append({"receiver": station, "band": band_label(b), "n": len(values),
                        "snr_db": _median(values), "others_db": _median(ref), "others_n": len(ref)})
        return {
            "system": s.label, "kind": s.kind, "via": s.via, "tocall": s.tocall, "callsign": s.src,
            "first": max(s.first, now - WINDOW), "last_heard": s.last,
            "packets": sum(s.hours.values()),
            "category": cat, "category_name": sources.CATEGORY_NAMES.get(cat) if cat is not None else None,
            "categories": {sources.CATEGORY_NAMES.get(c, str(c)): n for c, n in s.cats.items()},
            "address_type": ADDRESS_TYPES.get(s.atype), "stealth": s.stealth,
            "interval_s": _median(s.intervals),
            "nominal_s": cadence[2] if cadence and cadence[0] is None else None,
            "delay_s": _median(s.delay),
            "khz_offset": round(_median(s.khz), 1) if s.khz else None,
            "error_share": round(s.err_packets / s.radio_packets, 2) if s.radio_packets else None,
            "receivers": len(s.stations),
            "signal": sig,
            "gaps": [{"from": a, "to": b, "s": int(b - a)} for a, b in s.gaps if b > now - WINDOW],
            "jumps": len(s.jumps),
            "last": last,
        }

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
