"""Records spilled to a temporary file come back in order, whatever is appended or read in between."""
import nightly


def test_spill_keeps_order_across_batches(monkeypatch):
    monkeypatch.setattr(nightly.Spill, "BATCH", 7)
    s = nightly.Spill()
    for i in range(30):
        s.append((i, "a" * (i % 3), nightly.array.array("d", [i / 3])))
    assert len(s) == 30
    assert [r[0] for r in s] == list(range(30))
    assert [r[0] for r in s] == list(range(30))       # a second pass reads the same


def test_spill_survives_an_abandoned_iterator_and_later_appends(monkeypatch):
    monkeypatch.setattr(nightly.Spill, "BATCH", 4)
    s = nightly.Spill()
    for i in range(10):
        s.append(i)
    assert any(r == 5 for r in s)                     # stops mid-file
    it = iter(s)
    assert next(it) == 0
    for i in range(10, 20):
        s.append(i)
    assert list(s) == list(range(20))
    assert next(it) == 1


def test_empty_spill():
    assert list(nightly.Spill()) == []
