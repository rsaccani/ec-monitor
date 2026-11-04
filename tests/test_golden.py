"""Regression check of the nightly's dry run against a reference output kept outside the repository.

Skipped unless EC_GOLDEN_RAW (a raw-feed directory), EC_GOLDEN_DAY (YYYY-MM-DD) and EC_GOLDEN_REF
(the reference file) are set. See tests/README.md.
"""
import difflib
import os

import pytest

import golden_run

RAW, DAY, REF = (os.getenv(k) for k in ("EC_GOLDEN_RAW", "EC_GOLDEN_DAY", "EC_GOLDEN_REF"))


@pytest.mark.skipif(not (RAW and DAY and REF and os.path.isfile(REF)),
                    reason="EC_GOLDEN_RAW, EC_GOLDEN_DAY and an existing EC_GOLDEN_REF are not all set")
def test_dry_run_matches_reference():
    got = golden_run.run_dry(RAW, DAY)
    with open(REF, encoding="utf-8") as f:
        want = f.read()
    diff = list(difflib.unified_diff(want.splitlines(), got.splitlines(), "reference", "this run", lineterm="", n=1))
    assert not diff, "dry run differs from the reference:\n" + "\n".join(diff[:80])


# The normalisation itself is tested on synthetic output, whether or not the golden data is present.

def test_normalise_log_strips_noise():
    log = ("2026-11-03 01:02:03,456 INFO: OGN device database: 34,567 types, 89 not to be tracked\n"
           "2026-11-03 01:02:04,000 WARNING: Wave: the wind request failed (URLError: x); retrying once\n"
           "2026-11-03 01:02:05,000 INFO: 12 stations\n"
           "2026-11-03 01:02:06,000 INFO: Peak memory 812 MB (RSS, terrain cache included), anonymous 640 MB\n")
    assert golden_run.normalise_log(log) == "INFO: 12 stations\nINFO: Peak memory N MB\n"


def test_normalise_stdout_masks_hashes_and_elapsed():
    one = ("day 2026-11-02: hours read 24, missing none, lines 1,000, 301 s\n"
           "\n== declared drones that circled: class, climb\n"
           "   aaaaaaaa evidence      12   1.0\n"
           "   11111111 evidence      12   1.0\n"
           "\n== daily_flights: 2 rows\n"
           "   ('x', 1)\n")
    two = one.replace("301 s", "12 s").replace("aaaaaaaa", "ffffffff").replace("11111111", "00000000")
    assert golden_run.normalise_stdout(one) == golden_run.normalise_stdout(two)
    assert "HASH evidence" in golden_run.normalise_stdout(one)
    assert "('x', 1)" in golden_run.normalise_stdout(one)
