# Tests

Set up once, from the checkout:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt -r requirements-dev.txt

## Fast tests

    .venv/bin/python -m pytest tests

They need no database, network, terrain model or raw recording, run in about a second and use
little memory. `app.py` is never imported, since importing it starts the listener. The inputs are
synthetic: made-up addresses, made-up tracks. Reading a compressed hour needs `zstd` on the path,
and the tests that use it are skipped without it.

## Golden regression test

`tests/test_golden.py` runs `nightly.py --dry-run` on a raw-feed directory and compares the
output with a reference written earlier, after removing what changes from run to run (log
timestamps, elapsed seconds, the wave wind request failing or retrying, the device database count,
peak memory, and the salted hashes of addresses). The raw data and the reference are never kept in
the repository. The test is skipped unless these are set:

    EC_GOLDEN_RAW   directory holding the day's hourly files (and the next day's first)
    EC_GOLDEN_DAY   the UTC day, YYYY-MM-DD
    EC_GOLDEN_REF   the reference file

Write the reference with the code known to be good, then run the test on the change:

    EC_GOLDEN_RAW=... EC_GOLDEN_DAY=2026-11-01 tests/make_golden.sh /path/to/reference.txt
    EC_GOLDEN_RAW=... EC_GOLDEN_DAY=2026-11-01 EC_GOLDEN_REF=/path/to/reference.txt \
        .venv/bin/python -m pytest tests/test_golden.py

Run on a busy day this takes as long and as much memory as the nightly itself (see "Memory" in
AGENTS.md). The dry run downloads the OGN device database and keeps a copy beside the raw files, so
a device that changed its opt-out status between the two runs can differ in the output. Use the
same day's reference within a day or two of writing it.
