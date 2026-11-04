"""CircleTrack and CrewedEncounters, fed with synthetic tracks the way Nightly feeds them."""
import math
import pytest

import nightly
import sources

T0 = 1_800_000_000
LAT0, LON0 = 46.0, 9.0


class Feeder:
    """Nightly.circle needs only the dictionary of tracks, so it is called on a stand-in."""
    def __init__(self):
        self.circles = {}

    def feed(self, address, category, t, course, kt, lat, lon, alt_m):
        nightly.Nightly.circle(self, address, "OGFLR", category, t, course, kt, lat, lon, alt_m)

    def finish(self):
        for tr in self.circles.values():
            tr.finish()


def circling(feeder, address, turn_s, climb_ms, seconds, right=True, kt=30.0, category=1, t0=T0):
    """One fix a second on a circle of the given period, climbing at climb_ms."""
    sign = 1 if right else -1
    rate = 360.0 / turn_s
    radius = kt * 0.514444 * turn_s / (2 * math.pi)
    for i in range(seconds):
        course = (sign * rate * i) % 360
        # position on the circle: the aircraft flies along its course
        heading = math.radians(course)
        cx, cy = radius * math.sin(heading - sign * math.pi / 2), radius * math.cos(heading - sign * math.pi / 2)
        lat, lon = sources._move(LAT0, LON0, math.degrees(math.atan2(cx, cy)) % 360, math.hypot(cx, cy))
        feeder.feed(address, category, t0 + i, course or 360, kt, lat, lon, 1000 + climb_ms * i)


def test_steady_right_hand_climb_is_one_thermal():
    f = Feeder()
    circling(f, "5E0001", turn_s=30, climb_ms=1.5, seconds=300)
    f.finish()
    tr = f.circles[("5E0001", "OGFLR")]
    assert len(tr.thermals) == 1
    th = tr.thermals[0]
    assert th[0] == 1                                   # clockwise seen from above
    assert th[9] > 300 and th[10] > 150                 # gained metres and the seconds it took
    assert th[5] >= nightly.CIRCLE_MIN_TURN


def test_left_hand_climb_reports_left():
    f = Feeder()
    circling(f, "5E0002", turn_s=30, climb_ms=1.5, seconds=300, right=False)
    f.finish()
    ths = f.circles[("5E0002", "OGFLR")].thermals
    assert [th[0] for th in ths] == [-1]


def test_circling_without_gain_is_not_a_thermal():
    nightly.CircleTrack.outcomes.clear()
    f = Feeder()
    circling(f, "5E0003", turn_s=30, climb_ms=0.0, seconds=300)
    f.finish()
    assert f.circles[("5E0003", "OGFLR")].thermals == []
    assert nightly.CircleTrack.outcomes[(1, "no_gain")] == 1


def test_straight_glide_reports_nothing():
    nightly.CircleTrack.outcomes.clear()
    f = Feeder()
    for i in range(600):
        lat, lon = sources._move(LAT0, LON0, 90, 25 * i)
        f.feed("5E0004", 1, T0 + i, 90, 48.0, lat, lon, 2000 - 0.8 * i)
    f.finish()
    assert f.circles[("5E0004", "OGFLR")].thermals == []
    assert sum(nightly.CircleTrack.outcomes.values()) == 0


def test_gap_between_runs_on_one_side_merges_into_one_thermal():
    f = Feeder()
    circling(f, "5E0005", turn_s=30, climb_ms=1.5, seconds=150)
    circling(f, "5E0005", turn_s=30, climb_ms=1.5, seconds=150, t0=T0 + 200)
    f.finish()
    # the second run starts 50 s after the first ends, at the same place: one thermal
    assert len(f.circles[("5E0005", "OGFLR")].thermals) == 1


def crossing_pair(ce, offset_m, alt_a=1500, alt_b=1500, seconds=120, kt=100):
    """Two powered aircraft flying head on, east and west, passing offset_m to the side."""
    ms = kt * 0.514444
    for i in range(seconds):
        t = T0 + i
        la, lo = sources._move(LAT0, LON0, 90, -3000 + ms * i)
        ce.fix("5E0010", "powered", t, la, lo, alt_a, 90, kt, 2)
        lb, lob = sources._move(LAT0 + offset_m / 111195.0, LON0, 90, 3000 - ms * i)
        ce.fix("5E0011", "powered", t, lb, lob, alt_b, 270, kt, 2)
    ce.finish()


def test_two_aircraft_converging_record_one_encounter():
    ce = nightly.CrewedEncounters()
    crossing_pair(ce, 200)
    assert {pair for (pair, _), *_ in ce.done} == {("5E0010", "5E0011")}
    names = sorted(name for (_, name), *_ in ce.done)
    assert names == ["close", "wide"]                    # one per threshold
    for key, t0, d, rel, kinds, lat, lon, geom, info in ce.closed():
        assert 190 < d < 215
        assert rel == pytest.approx(370.4, rel=0.01)    # closing speed in km/h
        assert kinds == ("powered", "powered")
        assert geom == 2                                # head on
        assert LAT0 - 0.01 < lat < LAT0 + 0.01


def test_aircraft_far_apart_record_nothing():
    ce = nightly.CrewedEncounters()
    crossing_pair(ce, 5000)
    assert list(ce.done) == []


def test_aircraft_far_apart_vertically_record_nothing():
    ce = nightly.CrewedEncounters()
    crossing_pair(ce, 100, alt_b=1500 + 600)
    assert list(ce.done) == []


def test_encounter_at_wide_distance_only():
    ce = nightly.CrewedEncounters()
    crossing_pair(ce, 600)
    assert [name for (_, name), *_ in ce.done] == ["wide"]


def test_one_fix_per_address_and_second():
    ce = nightly.CrewedEncounters()
    ce.fix("5E0012", "powered", T0, LAT0, LON0, 1500, 90, 100, 2)
    ce.fix("5E0012", "powered", T0, LAT0 + 0.5, LON0, 1500, 90, 100, 2)
    assert ce.last["5E0012"] == (T0, LAT0, LON0)


def test_parked_run_starts_with_one_fix():
    r = nightly.ParkedRun(2, T0, LAT0, LON0)
    assert (r.packets, r.t0, r.t1, r.la0, r.lo1) == (1, T0, T0, LAT0, LON0)
    assert r.agl == []
