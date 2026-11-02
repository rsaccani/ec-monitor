"""How much of what a ground station delivers nobody else delivers (METHOD.md 10.5, nightly measure).

For one UTC day and every OGN receiver, per band of height above the ground, the aircraft-seconds the
receiver delivered (`heard`) and those no other receiver delivered (`unique`). The unit is a key: a
device address and a 10-second bin of the packet's own time. A key is delivered by every receiver that
forwarded at least one packet of that address inside the bin, and is unique to a receiver when it was the
only one. Keys are read in the order of the feed and held open for HOLD_S seconds of arrival time, the
longest a copy of a packet is accepted late (sources.STALE_SECONDS), then counted and forgotten, so
memory follows the aircraft airborne in five minutes and not the length of the day.

Only radio systems whose packets a receiver hears over the air count (FLARM, FANET, ADS-L, the OGN
tracker, PilotAware): a packet that reaches OGN by a phone app has no receiver to credit. Packets of
a speed under MIN_KT, of ground vehicles and static objects (category 14 and 15) and of an unknown
height above the ground are not counted. The band of a key is read from the first packet of the key
that arrived, 0 below 300 m, 1 from 300 m to 1,000 m, 2 from 1,000 m up.

Memory per open key is one dict entry holding two small integers: the address as an int, and a value
that packs the band in its two low bits and, above them, one 16-bit group per receiver that delivered
the key (the receiver's number plus one). A key of one receiver is unique, a key of several is shared.
"""
import collections
import time

SYSTEMS = frozenset({"OGFLR", "OGNFNT", "OGADSL", "OGNTRK", "OGPAW"})   # after sources.packet_system
NOT_AIRCRAFT = (14, 15)          # the OGN categories of ground support and of static objects
MIN_KT = 3                       # knots: the least that is 5 km/h or more
BIN_S = 10
HOLD_S = 300                     # sources.STALE_SECONDS: a packet is taken up to this long after its time stamp
BANDS_M = (300, 1000)
BAND_NAMES = ("low", "mid", "high")
UNKNOWN = 3                      # the band of a key whose first packet has no known ground
MAX_RECEIVERS = 65534            # 16 bits per receiver in a packed value, 0 meaning none


class Unique:
    def __init__(self, terrain):
        self.terrain = terrain
        self.ids = {}                       # receiver name -> its number
        self.names = []                     # number -> receiver name
        self.open = {}                      # bin -> {address as int: packed value}
        self.tally = {}                     # receiver number -> [heard x 3, unique x 3], in keys
        self.latest = 0                     # the latest bin of arrival time seen
        self.keys = self.no_ground = self.overflow = 0

    def packet(self, epoch, t, tocall, category, kt, course, alt_m, address, receiver, lat, lon):
        """One packet that arrived at `epoch` with the time stamp `t`; counts it when it is one of the
        measure's. `category` is the one in the packet's own id, `address` six hex digits."""
        if (tocall not in SYSTEMS or category is None or category in NOT_AIRCRAFT or kt is None or kt < MIN_KT
                or course is None or alt_m is None or receiver is None):
            return
        try:
            a = int(address, 16)
        except ValueError:
            return
        self.feed(epoch, t, a, receiver, lat, lon, alt_m)

    def feed(self, epoch, t, a, receiver, lat, lon, alt_m):
        r = self.ids.get(receiver)
        if r is None:
            if len(self.names) >= MAX_RECEIVERS:
                self.overflow += 1
                return
            r = self.ids[receiver] = len(self.names)
            self.names.append(receiver)
        g = r + 1
        b = int(t) // BIN_S
        d = self.open.get(b)
        if d is None:
            d = self.open[b] = {}
        v = d.get(a)
        if v is None:
            ground = self.terrain.elevation(lat, lon)
            if ground is None:
                self.no_ground += 1
                band = UNKNOWN
            else:
                agl = alt_m - ground
                band = 0 if agl < BANDS_M[0] else 1 if agl < BANDS_M[1] else 2
            d[a] = band | (g << 2)
        else:
            x = v >> 2
            sh = 2
            while x:
                if x & 0xFFFF == g:
                    break
                x >>= 16
                sh += 16
            else:
                d[a] = v | (g << sh)
        e = int(epoch) // BIN_S
        if e > self.latest:
            self.latest = e
            self.flush(e - HOLD_S // BIN_S)

    def flush(self, upto=None):
        """Count and forget the bins before `upto` (all of them without it)."""
        old = [b for b in self.open if upto is None or b < upto]
        tally = self.tally
        for b in old:
            for v in self.open.pop(b).values():
                band = v & 3
                if band == UNKNOWN:
                    continue
                self.keys += 1
                x = v >> 2
                first = x & 0xFFFF
                if x >> 16 == 0:
                    row = tally.get(first - 1)
                    if row is None:
                        row = tally[first - 1] = [0] * 6
                    row[band] += 1
                    row[3 + band] += 1
                    continue
                while x:
                    r = (x & 0xFFFF) - 1
                    row = tally.get(r)
                    if row is None:
                        row = tally[r] = [0] * 6
                    row[band] += 1
                    x >>= 16

    def open_keys(self):
        return sum(len(d) for d in self.open.values())

    def rows(self, day):
        """(day, station, heard low, mid, high, unique low, mid, high), by station name, after counting
        what is still open."""
        self.flush()
        return [(day, self.names[r]) + tuple(v) for r, v in sorted(self.tally.items(), key=lambda kv: self.names[kv[0]])]
