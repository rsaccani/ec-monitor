"""Runs the nightly's dry run on a raw-feed directory and normalises its output for comparison.

Used by test_golden.py and, to write a new reference, from the command line:

    EC_GOLDEN_RAW=<raw dir> EC_GOLDEN_DAY=YYYY-MM-DD python tests/golden_run.py --make-ref <reference file>

The raw data and the reference stay outside the repository.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LOG_TIME = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} ")
ELAPSED = re.compile(r", \d+ s$")
WAVE_WIND = re.compile(r"Wave: the wind request|Wave: .*wind.*(failed|retry)", re.I)
DDB_COUNT = re.compile(r"OGN device database: [\d,]+ types, [\d,]+ not to be tracked")
PEAK = re.compile(r"Peak memory \d+ MB .*anonymous \S+ MB")
HASH8 = re.compile(r"^(\s+)[0-9a-f]{8}\b", re.M)
HASHED_SECTIONS = ("== declared drones that circled", "== garbage pairs")


def normalise_log(text):
    out = []
    for line in text.splitlines():
        line = LOG_TIME.sub("", line)
        if WAVE_WIND.search(line) or DDB_COUNT.search(line):
            continue
        out.append(PEAK.sub("Peak memory N MB", line))
    return "\n".join(out) + "\n"


def normalise_stdout(text):
    """The elapsed seconds are masked, and so are the salted hashes of addresses: the rows
    that carry them are sorted afterwards, since the order of equal keys follows the hash."""
    lines = text.splitlines()
    out, block, hashed = [], [], False

    def flush():
        out.extend(sorted(block))
        block.clear()

    for line in lines:
        if line.startswith("day ") and ELAPSED.search(line):
            line = ELAPSED.sub(", N s", line)
        if line.startswith("\n==") or line.startswith("=="):
            flush()
            hashed = line.startswith(HASHED_SECTIONS)
            out.append(line)
        elif hashed and line.startswith("   "):
            block.append(HASH8.sub(r"\1HASH", line))
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out) + "\n"


def run_dry(raw_dir, day):
    env = dict(os.environ, EC_RAW_DIR=raw_dir)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "nightly.py"), "--dry-run", "--day", day],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"nightly.py exited {p.returncode}:\n{p.stderr[-2000:]}")
    return "%s\n=== log ===\n%s" % (normalise_stdout(p.stdout), normalise_log(p.stderr))


def main(argv):
    raw, day = os.getenv("EC_GOLDEN_RAW"), os.getenv("EC_GOLDEN_DAY")
    if len(argv) != 3 or argv[1] != "--make-ref" or not raw or not day:
        print(__doc__)
        return 2
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write(run_dry(raw, day))
    print("reference written to", argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
