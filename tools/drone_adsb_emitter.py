#!/usr/bin/env python3
"""ADS-B emitter category of addresses that declare themselves drones (7 October 2026).

An address that declares category 13 (drone) on FLARM or another radio system
and is also heard by ADS-B carries a second, independent declaration: the
emitter category set in its transponder (A1-A7 aircraft and rotorcraft, B6
unmanned aerial vehicle), in the OGN ADS-B comment as "<cat>:<flight id>".
Counts per address only; whether the flight id has the shape of a
registration is reported as a yes/no, never the id itself.

Usage: (cd ~/ads-l-map && nice -n 19 python3 tools/drone_adsb_emitter.py <files>)
"""
import collections
import re
import subprocess
import sys

sys.path.insert(0, ".")
import sources  # noqa: E402

EMIT = re.compile(r" ([A-D][0-7]):(\S*)")
REG = re.compile(r"^[A-Z]{1,2}[A-Z0-9]{2,5}$")


def lines(paths):
    for p in paths:
        cmd = ["zstdcat", p] if p.endswith(".zst") else ["cat", p]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")


def main():
    drone = set()
    emit = collections.defaultdict(collections.Counter)
    regshape = collections.defaultdict(set)
    for line in lines(sys.argv[1:]):
        try:
            _, rest = line.split(" ", 1)
            src, rest = rest.split(">", 1)
            head, body = rest.split(":", 1)
        except ValueError:
            continue
        tc = head.split(",")[0]
        addr = src[-6:]
        if tc == "OGADSB":
            m = EMIT.search(body)
            if m:
                emit[addr][m.group(1)] += 1
                fid = m.group(2)
                if fid:
                    regshape[addr].add(bool(REG.match(fid)) and not re.match(r"^[A-Z]{3}\d", fid))
            continue
        cat, no_track = sources.id_info(body)
        if cat == 13 and not no_track and tc not in sources.EXCLUDED:
            drone.add(addr)
    both = [a for a in drone if a in emit]
    print(f"addresses declaring drone: {len(drone)}; also on ADS-B with an emitter category: {len(both)}")
    c = collections.Counter(emit[a].most_common(1)[0][0] for a in both)
    print("main emitter category:", dict(c.most_common()))
    print("flight id shaped like a registration (not an airline flight number):",
          dict(collections.Counter("yes" if True in regshape[a] else "no" if regshape[a] else "none" for a in both)))
    mixed = sum(1 for a in both if len(emit[a]) > 1)
    print(f"addresses with more than one emitter category: {mixed}")


if __name__ == "__main__":
    main()
