"""Pure functions of nightly.py and the packed series of check.py."""
import calendar
import datetime
import random
import subprocess

import pytest

import check
import nightly
import sources


def test_band_counts_edges_reached():
    edges = (50, 120, 300)
    assert nightly.band(0, edges) == 0
    assert nightly.band(49.9, edges) == 0
    assert nightly.band(50, edges) == 1
    assert nightly.band(299, edges) == 2
    assert nightly.band(300, edges) == 3
    assert nightly.band(10 ** 6, edges) == 3


def test_band_none_is_unknown():
    assert nightly.band(None, (1, 2)) == nightly.UNKNOWN_BAND


def test_dist_known_distances():
    assert nightly.dist(46.0, 9.0, 46.0, 9.0) == 0
    # one degree of latitude is 111.2 km
    assert nightly.dist(46.0, 9.0, 47.0, 9.0) == pytest.approx(111195, rel=1e-3)
    # one degree of longitude at 60 degrees is half of that
    assert nightly.dist(60.0, 9.0, 60.0, 10.0) == pytest.approx(55597, rel=1e-3)


def test_dist_is_symmetric():
    a = nightly.dist(46.1, 9.2, 46.3, 9.6)
    b = nightly.dist(46.3, 9.6, 46.1, 9.2)
    assert a == pytest.approx(b)


def test_pack_unpack_round_trip_random():
    rng = random.Random(1)
    for _ in range(2000):
        lat, lon, alt = rng.uniform(-90, 90), rng.uniform(-180, 180), rng.randint(-900, 20000)
        la, lo, al = nightly.unpack(nightly.pack(lat, lon, alt))
        assert la == pytest.approx(lat, abs=1e-5)
        assert lo == pytest.approx(lon, abs=1e-5)
        assert al == alt


@pytest.mark.parametrize("lat,lon", [(-90, -180), (90, 180), (-90, 180), (90, -180), (0, 0), (-0.00001, -0.00001)])
def test_pack_unpack_extremes(lat, lon):
    la, lo, al = nightly.unpack(nightly.pack(lat, lon, 1234))
    assert (la, lo, al) == (pytest.approx(lat, abs=1e-5), pytest.approx(lon, abs=1e-5), 1234)


def test_pack_clamps_altitude():
    assert nightly.unpack(nightly.pack(46.0, 9.0, -5000))[2] == -1000
    assert nightly.unpack(nightly.pack(46.0, 9.0, 10 ** 6))[2] == 65535 - 1000


def test_weighted_median():
    assert nightly.weighted_median([(1, 1), (2, 1), (3, 1)]) == 2
    assert nightly.weighted_median([(10, 1), (1, 5)]) == 1
    assert nightly.weighted_median([(5, 3)]) == 5
    assert nightly.weighted_median([]) is None


def test_weighted_median_unsorted_input():
    assert nightly.weighted_median([(9, 1), (1, 1), (5, 1)]) == 5


def test_ci_fold_case_accents_and_trailing_spaces():
    assert nightly.ci_fold("StChamas") == nightly.ci_fold("STCHAMAS")
    assert nightly.ci_fold("Schänis") == "schanis"
    assert nightly.ci_fold("Name  ") == "name"
    assert nightly.ci_fold(" Name") == " name"
    assert nightly.ci_fold(None) is None
    assert nightly.ci_fold(7) == 7


def test_merge_ci_sums_and_files_under_larger_spelling():
    rows = [("StChamas", 3, 1.0), ("STCHAMAS", 5, 2.0), ("Other", 1, 0.5)]
    out, merged = nightly.merge_ci(rows, (0,), (1, 2))
    assert merged == 1
    assert sorted(out) == [("Other", 1, 0.5), ("STCHAMAS", 8, 3.0)]


def test_merge_ci_tie_takes_smaller_string():
    out, merged = nightly.merge_ci([("b", 2), ("B", 2)], (0,), (1,))
    assert merged == 1
    assert out == [("B", 4)]


def test_merge_ci_nothing_to_merge():
    rows = [("a", 1), ("b", 2)]
    assert nightly.merge_ci(rows, (0,), (1,)) == (rows, 0)


def ddb_text(n_ok=1200, extra=()):
    lines = ["#DEVICE_TYPE,DEVICE_ID,AIRCRAFT_MODEL,REGISTRATION,CN,TRACKED,IDENTIFIED,AIRCRAFT_TYPE"]
    for i in range(n_ok):
        lines.append(f"'F','{i:06X}','Model {i}','XX-{i}','{i}','Y','Y','1'")
    lines.extend(extra)
    return "\n".join(lines) + "\n"


def test_parse_ddb_types_notrack_models():
    extra = ["'F','FF0001','Hidden','','','N','Y','1'",
             "'F','FF0002','Anonymous','','','Y','N','1'",
             "'F','FF0003','Paraglider','','','Y','Y','7'"]
    types, notrack, models = nightly.parse_ddb(ddb_text(extra=extra))
    assert notrack == {"FF0001"}
    assert "FF0002" not in types and "FF0002" not in models
    assert types["FF0003"] == 7 and models["FF0003"] == "Paraglider"
    assert types["000005"] == 1 and models["000005"] == "Model 5"


def test_parse_ddb_skips_broken_rows():
    types, _, _ = nightly.parse_ddb(ddb_text(extra=["'F','FF0004'", "'F','FF0005','M','','','Y','Y','x'"]))
    assert "FF0004" not in types and "FF0005" not in types


def test_parse_ddb_rejects_bad_downloads():
    with pytest.raises(ValueError):
        nightly.parse_ddb("")
    with pytest.raises(ValueError):
        nightly.parse_ddb("<html>maintenance</html>\n")
    with pytest.raises(ValueError):
        nightly.parse_ddb(ddb_text(n_ok=10))


class FakeGate:
    """Skips the epochs from 10 up to but not including 20, with an edge marker at each end."""
    def __init__(self):
        self.inside = False

    def step(self, epoch):
        edges = []
        now = 10 <= epoch < 20
        if now != self.inside:
            edges.append(epoch)
            self.inside = now
        return now, edges


def test_gated_takes_window_out_and_marks_edges():
    lines = [(float(t), f"l{t}") for t in (5, 12, 15, 25)]
    out = list(nightly.gated(iter(lines), FakeGate()))
    assert out == [(5.0, "l5"), (12.0, sources.FEED_BREAK), (25.0, sources.FEED_BREAK), (25.0, "l25")]


RAW = (
    "1793448000.100 # aprsc server comment\n"
    "1793448001.200 AAA>OGFLR:/115959h5134.67N/00145.76W'307/103/A=003652 first\n"
    "not-a-number garbage\n"
    f"1793448002.300 {sources.FEED_BREAK}\n"
    "1793448003.400 BBB>OGFLR:/120000h5134.67N/00145.76W'307/103/A=003652 second\n"
)


def zstd_available():
    try:
        subprocess.run(["zstd", "-V"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def write_raw(tmp_path, zst):
    plain = tmp_path / "20261101-12.aprs"
    plain.write_text(RAW)
    if not zst:
        return str(plain)
    subprocess.run(["zstd", "-q", "-f", "--rm", str(plain)], check=True)
    return str(plain) + ".zst"


READ_CASES = [False, pytest.param(True, marks=pytest.mark.skipif(not zstd_available(), reason="no zstd"))]


@pytest.mark.parametrize("zst", READ_CASES)
def test_read_lines(tmp_path, zst):
    path = write_raw(tmp_path, zst)
    got = list(nightly.read_lines(path))
    assert [e for e, _ in got] == [1793448001.2, 1793448002.3, 1793448003.4]
    assert got[0][1].startswith("AAA>OGFLR:")
    assert got[1][1] == sources.FEED_BREAK


@pytest.mark.parametrize("zst", READ_CASES)
def test_read_lines_options(tmp_path, zst):
    path = write_raw(tmp_path, zst)
    assert [e for e, _ in nightly.read_lines(path, stop=1793448002.0)] == [1793448001.2]
    assert [e for e, _ in nightly.read_lines(path, contains=b"second")] == [1793448003.4]
    with_comments = list(nightly.read_lines(path, comments=True))
    assert with_comments[0] == (1793448000.1, "# aprsc server comment")


def test_hour_path_prefers_plain_file(tmp_path):
    day = datetime.date(2026, 11, 1)
    assert nightly.hour_path(str(tmp_path), day, 12) is None
    (tmp_path / "20261101-12.aprs.zst").write_bytes(b"")
    assert nightly.hour_path(str(tmp_path), day, 12).endswith("20261101-12.aprs.zst")
    (tmp_path / "20261101-12.aprs").write_bytes(b"")
    assert nightly.hour_path(str(tmp_path), day, 12).endswith("20261101-12.aprs")


def samples(*alts):
    """A circling episode's samples: 5-second buckets from 1000, altitudes in metres."""
    return {1000 + i: nightly.pack(46.0, 9.0, a) for i, a in enumerate(alts)}


def test_climb_span_simple_climb():
    gain, secs = nightly.climb_span(samples(1000, 1010, 1020, 1030))
    assert gain == 30 and secs == 15


def test_climb_span_uses_running_minimum():
    # climb 20, sink 50, climb 40: the largest rise is the last one
    gain, secs = nightly.climb_span(samples(1000, 1020, 970, 1010))
    assert gain == 40 and secs == 5


def test_climb_span_descent_and_empty():
    assert nightly.climb_span(samples(1000, 990, 980)) == (0, 0)
    assert nightly.climb_span({}) is None


def test_velocity_directions():
    e, n = nightly.velocity(90, 100)
    assert e == pytest.approx(185.2) and n == pytest.approx(0, abs=1e-9)
    e, n = nightly.velocity(180, 10)
    assert e == pytest.approx(0, abs=1e-9) and n == pytest.approx(-18.52)


def test_velocity_missing():
    assert nightly.velocity(None, 50) is None
    assert nightly.velocity(0, 50) is None      # course 0 is APRS for unknown
    assert nightly.velocity(90, None) is None


def test_geometry_bands():
    east = nightly.velocity(90, 50)
    assert nightly.geometry(east, nightly.velocity(100, 80)) == 0     # same way
    assert nightly.geometry(east, nightly.velocity(180, 50)) == 1     # crossing
    assert nightly.geometry(east, nightly.velocity(270, 50)) == 2     # head on
    assert nightly.geometry(east, None) == nightly.UNKNOWN_BAND
    assert nightly.geometry(east, (0, 0)) == nightly.UNKNOWN_BAND


def test_same_aircraft():
    assert nightly.same_aircraft(100, 5, 0)
    assert not nightly.same_aircraft(200, 5, 0)       # too far
    assert not nightly.same_aircraft(100, 25, 0)      # too fast apart
    assert not nightly.same_aircraft(100, 5, 1)       # crossing
    assert not nightly.same_aircraft(100, None, 0)    # no velocity: kept
    assert not nightly.same_aircraft(150, 5, 0)       # the limit itself is excluded


def epoch(y, m, d, hh, mm):
    return calendar.timegm((y, m, d, hh, mm, 0))


def test_sun_elevation_summer_noon_and_midnight():
    # 21 June, 46 N, 9 E: local solar noon is about 11:24 UTC
    noon = nightly.sun_elevation(epoch(2026, 6, 21, 11, 24), 46.0, 9.0)
    assert 66 < noon < 68.5            # 90 - 46 + 23.4
    assert nightly.sun_elevation(epoch(2026, 6, 21, 23, 24), 46.0, 9.0) < -20


def test_sun_elevation_winter_noon_lower():
    summer = nightly.sun_elevation(epoch(2026, 6, 21, 11, 24), 46.0, 9.0)
    winter = nightly.sun_elevation(epoch(2026, 12, 21, 11, 24), 46.0, 9.0)
    assert 19 < winter < 22 and winter < summer


def test_samples_series_round_trip():
    class Pair(check.Samples):
        fmt = check.struct.Struct("<Ih")

    s = Pair.of([(100, -5), (200, 7), (300, 32767)])
    assert len(s) == 3
    assert list(s) == [(100, -5), (200, 7), (300, 32767)]
    assert list(s.since(200)) == [(200, 7), (300, 32767)]
    del s[0]
    assert list(s) == [(200, 7), (300, 32767)]
    with pytest.raises(IndexError):
        del s[5]
