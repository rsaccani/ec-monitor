"""APRS position parsing of sources.py, on synthetic lines in the format of the OGN feed."""
import calendar
import datetime

import pytest

import sources

NOW = datetime.datetime(2026, 11, 1, 12, 0, 5)
LINE = ("ICA4B0001>OGFLR,qAS,TESTRX:/115959h5134.67N/00145.76W'307/103/A=003652 !W20! id054B0001 +079fpm +1.5rot")


def split(line):
    """The body of a packet and its path, as SourceTracker.handle splits them."""
    src, rest = line.split(">", 1)
    head, body = rest.split(":", 1)
    return src, head.split(","), body


def test_parse_fix_full_line():
    _, _, body = split(LINE)
    t, lat, lon, course, kt, rot, symbol, alt_m = sources.parse_fix(body, NOW)
    assert t == calendar.timegm((2026, 11, 1, 11, 59, 59))
    # 51 deg 34.67 min plus the !W2 extra digit of 0.002 min
    assert lat == pytest.approx(51 + 34.672 / 60)
    assert lon == pytest.approx(-(1 + 45.760 / 60))
    assert (course, kt, rot, symbol) == (307, 103, 1.5, "'")
    assert alt_m == pytest.approx(3652 * 0.3048)


def test_parse_fix_without_extras():
    t, lat, lon, course, kt, rot, symbol, alt_m = sources.parse_fix("/120000h4600.00N/00900.00E^090/050", NOW)
    assert (lat, lon, course, kt, rot, alt_m) == (46.0, 9.0, 90, 50, None, None)


def test_parse_fix_southern_and_course_zero_is_unknown():
    fix = sources.parse_fix("/120000h3354.00S/15112.00E^000/000", NOW)
    assert fix[1] == pytest.approx(-33.9) and fix[2] == pytest.approx(151.2)
    assert fix[3] is None and fix[4] == 0


def test_parse_fix_without_course_and_speed():
    fix = sources.parse_fix("/120000h4600.00N/00900.00E&", NOW)
    assert fix[3] is None and fix[4] is None


def test_parse_fix_negative_altitude():
    fix = sources.parse_fix("/120000h4600.00N/00900.00E'090/050/A=-00100", NOW)
    assert fix[7] == pytest.approx(-100 * 0.3048)


def test_parse_fix_just_before_midnight_belongs_to_yesterday():
    now = datetime.datetime(2026, 11, 2, 0, 0, 30)
    fix = sources.parse_fix("/235958h4600.00N/00900.00E'090/050", now)
    assert fix[0] == calendar.timegm((2026, 11, 1, 23, 59, 58))


def test_parse_fix_rejects_what_is_not_a_position():
    assert sources.parse_fix("/093560h4600.00N/00900.00E'090/050", NOW) is None     # second 60
    assert sources.parse_fix(">status text", NOW) is None
    assert sources.parse_fix("", NOW) is None


@pytest.mark.parametrize("text,expected", [
    ("id054B0001", (1, False)),        # glider, tracked
    ("id1E4B0001", (7, False)),        # paraglider
    ("id454B0001", (1, True)),         # no-track bit
    ("id854B0001", (1, False)),        # stealth bit only: category unchanged
    ("id05", (None, False)),           # too short
    ("no id here", (None, False)),
])
def test_id_info_eight_digits(text, expected):
    assert sources.id_info(text) == expected


def test_id_info_inside_a_packet():
    _, _, body = split(LINE)
    assert sources.id_info(body) == (1, False)


def test_id_info_ten_digits_naviter():
    # 40 bits: category in bits 34-37, no-track bit 38
    assert sources.id_info("id0400000001") == (1, False)
    assert sources.id_info("id4400000001") == (1, True)


def test_id_field_must_stand_alone():
    assert sources.id_info("xid054B0001") == (None, False)


def test_packet_system_and_source_info():
    src, path, _ = split(LINE)
    assert src == "ICA4B0001"
    assert path[0] == "OGFLR"
    system = sources.packet_system(path, "4B0001")
    assert sources.source_info(system) == ("FLARM", "flarm")
    assert sources.source_info("OGADSL") == ("ADS-L", "adsl")
    assert sources.source_info("OGNFNT") == ("FANET", "fanet")


def test_source_info_unknown_tocall_is_other():
    assert sources.source_info("APXYZ1") == ("APXYZ1", "other")


def test_remote_id_tocall_is_a_drone_source():
    assert sources.packet_system(["OGNMAV", "qAS", "TESTRX"], "AAAAAA") == sources.REMOTE_ID
    assert sources.packet_system(["OGNDSC", "qAS", "TESTRX"], "AAAAAA") == sources.REMOTE_ID


def test_distance_and_move_agree():
    lat, lon = sources._move(46.0, 9.0, 90, 1000)
    assert sources._distance(46.0, 9.0, lat, lon) == pytest.approx(1000, rel=1e-3)
    lat, lon = sources._move(46.0, 9.0, 0, 1000)
    assert lon == pytest.approx(9.0) and lat > 46.0
