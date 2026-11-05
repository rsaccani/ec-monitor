"""The device check comes back from a restart with every answer as it was, also from the state of the
code before version 4, and a new worker waits for the old one's last save."""
import datetime
import json
import pickle
import threading
import time

import check

STATIONS = {"RXONE": (46.00, 9.00), "RXTWO": (46.30, 9.40), "RXTHREE": (45.80, 8.70)}
ADDRESSES = ("DD1001", "DD1002", "3E0003")


def body(t, lat, lon, snr, khz):
    hhmmss = time.strftime("%H%M%S", time.gmtime(t))
    la = f"{int(lat):02d}{(lat % 1) * 60:05.2f}N"
    lo = f"{int(lon):03d}{(lon % 1) * 60:05.2f}E"
    return f"/{hhmmss}h{la}/{lo}'090/040/A=004500 !W00! id06{ADDRESSES[0]} +100fpm {snr:.1f}dB 0e {khz:+.1f}kHz"


def feed(dc, start, n):
    """n packets of each address, every 4 s from start, heard by the three stations in turn."""
    for i in range(n):
        for j, address in enumerate(ADDRESSES):
            t = start + 4 * i + j
            station = list(STATIONS)[(i + j) % 3]
            lat, lon = 46.1 + 0.002 * i, 9.1 + 0.001 * j
            snr, khz = 5 + (i * 7 + j) % 20, -2 + ((i + j) % 9) / 10
            dc.feed(f"FLR{address}", "OGFLR", "FLARM", "radio", "radio", body(t, lat, lon, snr, khz),
                    station, 7, t, datetime.datetime.utcfromtimestamp(t))


def answers(dc):
    return {a: json.dumps(dc.report(a), sort_keys=True) for a in ADDRESSES}


def worker(path):
    return check.DeviceCheck(dict(STATIONS), path=str(path))


def test_restart_keeps_every_answer(tmp_path):
    path = tmp_path / "check-state.pickle"
    start = int(time.time()) - 6 * 3600
    old = worker(path)
    feed(old, start, 600)
    feed(old, start + 4 * 3600, 300)            # a second session, after a silence of hours
    want = answers(old)
    old.close()
    new = worker(path)
    assert answers(new) == want
    new.close()


def as_v3(s):
    """A System put back in the layout of the code before version 4."""
    s.stations = {k: [1, v, None, None] for k, v in s.stations.items()}
    s.signal = {k: check.Signal.of(check._SIG.iter_unpack(v)) for k, v in s.signal.items()}
    s.gaps = list(s.gaps)
    s.hours = dict(s.hours)
    s.khz = s.khz or check.Khz()
    s.pattern = s.pattern or check.Pattern()


def test_version_3_state_reads_back_the_same(tmp_path):
    path = tmp_path / "check-state.pickle"
    start = int(time.time()) - 3 * 3600
    old = worker(path)
    feed(old, start, 600)
    want = answers(old)
    for systems in old.devices.values():
        for s in systems.values():
            as_v3(s)
    with open(path, "wb") as f:
        pickle.dump((time.time(), old.devices, old.active, old.ref, old.ref_khz, dict(STATIONS), 3), f)
    old.owner.close()
    old.owner = None
    before = path.read_bytes()
    new = worker(path)
    assert answers(new) == want
    assert path.read_bytes() == before          # the old file is left for a rollback
    new.close()
    assert worker(path).devices.keys() == new.devices.keys()   # and the next start reads version 4


def test_new_worker_waits_for_the_old_ones_last_save(tmp_path, monkeypatch):
    path = tmp_path / "check-state.pickle"
    start = int(time.time()) - 3600
    old = worker(path)
    feed(old, start, 100)
    old.save()                                  # the periodic save
    box = {}
    reader = threading.Thread(target=lambda: box.update(new=worker(path)))
    reader.start()
    time.sleep(0.3)
    assert reader.is_alive()                    # waiting for the old worker
    feed(old, start + 400, 100)                 # what arrives after the periodic save
    want = answers(old)
    old.close()                                 # the old worker's exit
    reader.join(5)
    assert answers(box["new"]) == want
    box["new"].close()
