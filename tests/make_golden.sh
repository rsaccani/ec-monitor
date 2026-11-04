#!/bin/sh
# Writes a new reference output for tests/test_golden.py.
# Usage: EC_GOLDEN_RAW=<raw dir> EC_GOLDEN_DAY=YYYY-MM-DD tests/make_golden.sh <reference file>
# Run it on code known to be good (main), then run the suite on the change with EC_GOLDEN_REF set to the file.
set -e
[ $# -eq 1 ] || { echo "usage: EC_GOLDEN_RAW=<dir> EC_GOLDEN_DAY=YYYY-MM-DD $0 <reference file>" >&2; exit 2; }
cd "$(dirname "$0")/.."
exec "${PYTHON:-python3}" tests/golden_run.py --make-ref "$1"
