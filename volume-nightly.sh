#!/bin/bash
# The measured-volume step of the EC nightly (METHOD.md section 10.9), run after nightly.py has finished its day.
# Cron, as the service user: 20 3 * * * EC_VOLUME_OUT=<web dir of the volume tiles> /home/ecmon/ec-monitor/volume-nightly.sh
# Like ec-encounters/screen-after-nightly.sh it waits, by an anchored process match, while the nightly (01:30 UTC), the
# ads_l dump that follows it (02:45 UTC) or the encounter screening (03:15 UTC) is running, and it never takes or tests
# ~/ec-nightly/lock, since the nightly's own cron uses flock -n and would skip the night. Then it runs `volume.py nightly`
# on yesterday (UTC) under the heavy-work cap of the host (MemoryHigh 600M, MemoryMax 800M; the replay peaked at
# 240 MB on 28 October): a systemd user scope when the user manager is up, otherwise prlimit on the data segment.
# Outcome: one row in volume_runs, one line in ~/ec-nightly/volume.log, the exit status of volume.py.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
LOG=$HOME/ec-nightly/volume.log
STORE=${EC_VOLUME_STORE:-$HOME/ec-volume/store}
OUT=${EC_VOLUME_OUT:?set EC_VOLUME_OUT to the web directory the coverage page reads the volume tiles from}
DAY=$(date -u -d yesterday +%F)
stamp() { date -u +%FT%TZ; }
mkdir -p "$STORE" "$(dirname "$LOG")"

deadline=$(( $(date +%s) + 3 * 3600 ))
while pgrep -f '^/usr/bin/python3 nightly[.]py' >/dev/null || pgrep -f '^mysqldump .*ads_l' >/dev/null \
      || pgrep -f 'python3 screen[.]py' >/dev/null; do
  if [ "$(date +%s)" -gt "$deadline" ]; then
    echo "$(stamp) $DAY: the nightly, the dump or the screening still running after 3 h, volume not computed" >> "$LOG"
    exit 1
  fi
  sleep 60
done

cd "$HERE" || exit 1
export XDG_RUNTIME_DIR=/run/user/$(id -u)
args=(python3 volume.py nightly --store "$STORE" --out "$OUT" --day "$DAY")
if systemctl --user is-system-running >/dev/null 2>&1 || [ "$(systemctl --user is-system-running 2>/dev/null)" = degraded ]; then
  how=scope
  systemd-run --user --scope -q -p MemoryHigh=600M -p MemoryMax=800M nice -n 19 ionice -c3 "${args[@]}" >> "$LOG" 2>&1
else
  how=prlimit
  prlimit --data=838860800 nice -n 19 ionice -c3 "${args[@]}" >> "$LOG" 2>&1
fi
rc=$?
echo "$(stamp) $DAY: exit $rc ($how)" >> "$LOG"
exit $rc
