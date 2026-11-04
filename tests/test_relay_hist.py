"""The relay pairs keep a histogram and the far differences; the median must be the one of the sorted list."""
import collections
import random

import nightly


class Stub:
    pass


def median_by_hist(diffs):
    n = Stub()
    n._relay_offsets = None
    n.relay_pairs = collections.defaultdict(
        lambda: nightly.array.array("I", bytes(4 * (2 * nightly.RELAY_HIST_M + 1))))
    n.relay_far = collections.defaultdict(lambda: nightly.array.array("h"))
    for d in diffs:
        h = n.relay_pairs[("A", "B")]
        if -nightly.RELAY_HIST_M <= d <= nightly.RELAY_HIST_M:
            h[d + nightly.RELAY_HIST_M] += 1
        else:
            n.relay_far[("A", "B")].append(d)
    captured = {}
    orig = nightly.weighted_median
    try:
        nightly.weighted_median = lambda pairs: captured.setdefault(len(captured), pairs) and orig(pairs)
        nightly.Nightly.relay_offsets(n)
    finally:
        nightly.weighted_median = orig
    return captured[0][0]


def test_relay_median_matches_sorted_list():
    rnd = random.Random(7)
    for size in (1, 2, 3, 10, 51, 400):
        for spread in (5, 60, 500):
            diffs = [round(rnd.gauss(0, spread)) for _ in range(size)]
            diffs = [max(-32000, min(32000, d)) for d in diffs]
            v = sorted(diffs)
            assert median_by_hist(diffs) == (v[len(v) // 2], len(v))
