"""Records packed into arrays to save memory come back exactly as they were built."""
import nightly


def test_closed_encounter_round_trip():
    ce = nightly.CrewedEncounters()
    info = {"first": 1_800_000_000, "last": 1_800_000_012.5, "secs": {1_800_000_000, 1_800_000_001},
            "event": (1_800_000_005.25, "5E0010", "5E0011", (46.1, 9.2, 1500.0, 2), (46.1001, 9.2002, 1480, 4.5),
                      20.0, 1_799_999_990, None, 3.25),
            "spd": (None, 92.6), "ang": None, "gap": 4.5, "over": True, "miss": None}
    enc = [1_800_000_000, 312.5, -4.0, ("glider", "glider"), 46.10005, 9.2001, "head-on", info]
    ce.close_encounter((("5E0010", "5E0011"), "close"), enc)
    (key, t0, d, rel, kinds, la, lo, geom, back), = list(ce.closed())
    assert key == (("5E0010", "5E0011"), "close")
    assert (t0, d, rel, kinds, la, lo, geom) == tuple(enc[:7])
    assert type(t0) is int and type(back["first"]) is int and type(back["last"]) is float
    want = dict(info, secs=2)
    assert back == want
    assert type(back["event"][3][3]) is int and type(back["event"][4][2]) is int


class Days:
    flights = None


def test_flight_round_trip():
    n = Days()
    n.flights = []
    f = [1_800_000_000, 46.0, 9.0, 1_800_003_600, 25_000.5, 80_000.25, 1, 9.0, 46.2, 9.3, 15.0,
         (46.2, 9.3, None), 812, None, [], (900.0, 10.0), [1650.5, True]]
    nightly.Nightly.end_flight(n, "5E0020", f, infer=False)
    f2 = list(f)
    f2[11] = None
    f2[16] = [None, False]
    nightly.Nightly.end_flight(n, "5E0021", f2, infer=False)
    got = list(nightly.Nightly.flight_records(n))
    assert got[0] == ("5E0020", 1, 1_800_000_000, 9.0, 3600, 25_000.5, 80_000.25, 46.0, 812, None,
                      (46.2, 9.3, None), "seen", 1650.5)
    assert got[1][10:] == (None, "none", None)
    assert type(got[0][4]) is int
