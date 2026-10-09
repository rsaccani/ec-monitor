# Running the nightly measures

`nightly.py` computes the measures of METHOD.md section 10 and PATTERNS.md from the raw
recording of the previous UTC day and writes aggregate tables. It runs as
`rsa`, from the service checkout `~/ads-l-map`, at 01:30 UTC: the recorder
closes the last hour of the day at midnight and the first hour of the next
day at 01:00, and that file holds the day's last fixes, which arrive up to
5 minutes late. The database backup of 01:00 (`~/bin/backup_db.sh`) is over
by 01:00:20.

Before the first run, as root: the tables and grants, then the log directory.

```sh
mysql < ~rsa/ads-l-map/schema/2026-10-07-nightly.sql
install -d -o rsa -g rsa -m 700 ~rsa/ec-nightly
```

Credentials come from `~/ads-l-map/.env` (`DB_USER`, `DB_PASSWORD`,
`EC_RAW_DIR`), read by the script from its own directory, so neither the
working directory nor `HOME` decides which file is used. A day can be
recomputed at any time while its files exist (seven days): its rows are
replaced, never added to.

```sh
cd ~/ads-l-map && nice -n 19 python3 nightly.py --day 2026-10-07 --dry-run   # prints, writes nothing
cd ~/ads-l-map && nice -n 19 python3 nightly.py --day 2026-10-07             # replaces that day
```

The night the last day of a month is computed (the 1st, at 01:30), the same
run also stores the month's snapshot of every statistics endpoint
(METHOD.md section 9), after the day's rows. `--snapshot` takes it on any
day, `--snapshot-only` without reading the recording; with `--dry-run` both
print the size of each endpoint's JSON instead of storing it.

Either of the two ways below, not both.

**Cron**, as `rsa` (`crontab -e`). Cron sets `HOME` from the password file
and mails nothing here, since the output goes to the log; `flock` keeps an
overrunning night from overlapping the next.

```
30 1 * * * cd /home/rsa/ads-l-map && /usr/bin/flock -n /home/rsa/ec-nightly/lock /usr/bin/nice -n 19 /usr/bin/ionice -c3 /usr/bin/python3 nightly.py >> /home/rsa/ec-nightly/nightly.log 2>&1
```

**systemd**, as root: `ec-monitor-nightly.service` and `.timer` in this
directory, with the install commands in the unit's header. Start the service
once by hand and read the log before relying on the timer; a timer job that
had never been tried that way is what left the service stopped on the night
of 4 October 2026.

Each run logs one line with the rows written, the hours read and the peak
memory. The tables come in two families written in two transactions,
conspicuity (METHOD.md) first and patterns (PATTERNS.md) second (from
7 October 2026): if one family fails, while it is computed or written, the
traceback is logged, `nightly_runs.notes` says which family and why, the
other family's rows are written all the same, and the run exits with status
1. An error while the day's files are read stops both, and leaves the
previous rows of that day as they were. The log grows by a few lines a night.
