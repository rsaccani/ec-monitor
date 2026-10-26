"""The station reception rose (METHOD.md 10.7, 25 October 2026).

Per ground station and per source (the tocall, as daily_device_pattern keys it), the airborne
receptions the station made directly, in 36 sectors of 10 degrees by bearing from the station to
the aircraft (north up, clockwise). Per sector the accumulator keeps the reception count by
distance (16 bins, log-spaced from 2 km with ratio 1.5), and the sum of the signal scaled to
10 km. The 95th-percentile reach is read from the histogram, so no distance is stored.

One (station, source) takes 36 x 16 unsigned ints and 36 x 2 doubles: 2.9 kB, whatever the
traffic. A day is written as one row per station and source (`daily_station_rose`), the payload
holding only the sectors that heard something. `combine` adds the days of a window back up.

The nightly writes the table from 26 October 2026 (`nightly.Nightly.rose_fix`, family `station_rose`);
`tools/station_rose_backfill.py` is only for gaps and for the days recorded before that.

This module imports `sources` only inside `Rose.__init__`, since sources imports it for `combine`.
"""
import array
import json
import math

SECTORS = 36                    # of 10 degrees, from north, clockwise; the bearing is from the station
BINS = 16
BIN0_KM = 2.0                   # bin 0 is under 2 km; bin i (1..15) starts at 2 * 1.5^(i-1) km
RATIO = 1.5
MAX_KM = 1000                   # a farther "reception" is a wrong position, not a signal
SNR_MIN_KM = 2.0                # the signal is scaled to 10 km only from here: closer in, 1 dB of error is a large share of the slope
MIN_DAY_N = 1                   # a small station adds up over 30 days, so every day with a reception counts
MIN_SECTOR_N = 20               # receptions of a sector, over the window, under which the API gives no figure
P95 = 0.95
_LOG_RATIO = math.log(RATIO)


def bin_of(km):
    if km < BIN0_KM:
        return 0
    return min(BINS - 1, 1 + int(math.log(km / BIN0_KM) / _LOG_RATIO))


def bin_edges(i):
    """(low, high) km of a bin; the last one is closed at the edge it would have."""
    if i == 0:
        return 0.0, BIN0_KM
    return BIN0_KM * RATIO ** (i - 1), BIN0_KM * RATIO ** i


def percentile_km(hist, q=P95):
    """The distance under which a share `q` of the receptions lie, interpolated inside its bin
    (linearly in the first bin, in the logarithm elsewhere). None for an empty histogram."""
    total = sum(hist)
    if not total:
        return None
    goal = q * total
    run = 0
    for i, c in enumerate(hist):
        if c and run + c >= goal:
            lo, hi = bin_edges(i)
            f = (goal - run) / c
            if i == 0:
                return lo + f * (hi - lo)
            return lo * (hi / lo) ** f
        run += c
    return bin_edges(BINS - 1)[1]


class Rose:
    """feed() each direct airborne reception; rows(day) at the end of the day."""

    def __init__(self):
        import sources
        self.slope = sources.SNR_SLOPE_DB
        self.distance = sources._distance
        self.bearing = sources._bearing
        self.blocks = {}            # (station, source) -> (histogram, [n with a signal, sum] per sector)

    def feed(self, station, source, st_lat, st_lon, lat, lon, snr):
        km = self.distance(st_lat, st_lon, lat, lon) / 1000
        if km > MAX_KM:
            return
        sector = int(self.bearing(st_lat, st_lon, lat, lon) // 10) % SECTORS
        block = self.blocks.get((station, source))
        if block is None:
            block = self.blocks[(station, source)] = (array.array("I", bytes(4 * SECTORS * BINS)),
                                                      array.array("d", bytes(8 * SECTORS * 2)))
        block[0][sector * BINS + bin_of(km)] += 1
        if snr is not None and km >= SNR_MIN_KM:
            s = block[1]
            s[sector * 2] += 1
            s[sector * 2 + 1] += snr + self.slope * math.log10(km / 10)

    def rows(self, day):
        """(day, station, source, n_total, payload) for each pair with at least MIN_DAY_N receptions.
        The payload is a JSON list of [sector, n with a signal, sum of dB at 10 km, [16 counts]], for the
        sectors that heard anything."""
        out = []
        for (station, source), (hist, snr) in sorted(self.blocks.items()):
            total = sum(hist)
            if total < MIN_DAY_N:
                continue
            sectors = []
            for s in range(SECTORS):
                h = list(hist[s * BINS:(s + 1) * BINS])
                if any(h):
                    sectors.append([s, int(snr[s * 2]), round(snr[s * 2 + 1], 1), h])
            out.append((day, station, source, total, json.dumps(sectors, separators=(",", ":"))))
        return out


def combine(payloads):
    """Add the payloads of several days of one (station, source): 36 sector dicts with `n` (receptions),
    `snr10` (mean dB at 10 km, 0.1 dB, None under MIN_SECTOR_N signal readings) and `p95_km` (None under
    MIN_SECTOR_N receptions)."""
    hist = [[0] * BINS for _ in range(SECTORS)]
    ns = [0] * SECTORS
    sums = [0.0] * SECTORS
    for text in payloads:
        for s, n_snr, total, h in json.loads(text):
            ns[s] += n_snr
            sums[s] += total
            row = hist[s]
            for i, c in enumerate(h):
                row[i] += c
    out = []
    for s in range(SECTORS):
        n = sum(hist[s])
        p95 = percentile_km(hist[s]) if n >= MIN_SECTOR_N else None
        out.append({"sector": s, "n": n,
                    "snr10": round(sums[s] / ns[s], 1) if ns[s] >= MIN_SECTOR_N else None,
                    "p95_km": round(p95, 1) if p95 is not None else None})
    return out
