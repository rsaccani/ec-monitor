#!/bin/bash
# Deploy origin/main to the running service, from inside the server's checkout.
#
#   ~/ads-l-map/deploy.sh            # deploy if origin/main moved
#   ~/ads-l-map/deploy.sh --force    # reload even if nothing changed
#
# Needs no root: the service runs as the same user, and gunicorn reloads its
# worker (re-importing app.py) on SIGHUP. If the new worker does not answer
# within a minute, or logs a database write error in its first 30 seconds, the
# previous commit is checked out and reloaded again. The second check exists
# because a worker can answer HTTP perfectly while every write fails (a missing
# privilege did exactly that on 2026-10-03).
#
# Only app.py and templates/ are picked up this way. A change to
# requirements.txt, to the systemd unit or to the database schema has to be
# applied separately, and a schema change has to land BEFORE the code that
# uses it.

set -euo pipefail
cd "$(dirname "$0")"

SERVICE=ads-l-map
URL=http://127.0.0.1:5000/conspicuity-monitor/api/adsl/monthly
SETTLE=30

prev=$(git rev-parse HEAD)
git fetch -q --tags origin main
next=$(git rev-parse origin/main)

if [ "$next" = "$prev" ] && [ "${1:-}" != "--force" ]; then
    echo "Already at $(git log -1 --format='%h %s')"
    exit 0
fi

master=$(systemctl show -p MainPID --value "$SERVICE")
if [ -z "$master" ] || [ "$master" = 0 ]; then
    echo "$SERVICE is not running" >&2
    exit 1
fi

reload() {
    local old since
    old=$(pgrep -P "$master" | sort | tr '\n' ' ')
    since=$(date '+%Y-%m-%d %H:%M:%S')
    kill -HUP "$master"
    for _ in $(seq 1 30); do
        sleep 2
        local now
        now=$(pgrep -P "$master" | sort | tr '\n' ' ')
        if [ -n "$now" ] && [ "$now" != "$old" ] && curl -fsS -o /dev/null "$URL"; then
            sleep "$SETTLE"
            # Only the new worker's lines: the old one keeps logging while it
            # drains, and its errors are the very thing being replaced.
            local pids errors
            pids=$(echo $now | sed 's/ /|/g')
            errors=$(journalctl -u "$SERVICE" --since "$since" --no-pager -q |
                grep -E "gunicorn\[($pids)\]" | grep "Error writing to database" || true)
            if [ -n "$errors" ]; then
                echo "The new worker logged database write errors:" >&2
                echo "$errors" | tail -3 >&2
                return 1
            fi
            return 0
        fi
    done
    return 1
}

git checkout -q --detach "$next"
if ! python3 -m py_compile app.py; then
    echo "app.py does not compile at $(git rev-parse --short HEAD), staying on $(git rev-parse --short "$prev")" >&2
    git checkout -q --detach "$prev"
    exit 1
fi

if reload; then
    echo "Deployed $(git log -1 --format='%h %s')"
else
    echo "New worker did not answer, rolling back to $(git rev-parse --short "$prev")" >&2
    git checkout -q --detach "$prev"
    reload || echo "Rollback reload failed too: check journalctl -u $SERVICE" >&2
    exit 1
fi
