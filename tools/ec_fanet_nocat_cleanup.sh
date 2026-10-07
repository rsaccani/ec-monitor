#!/bin/bash
# One-off cleanup after 9bd6f89: October 2026 FANET rows with no category.
# Since the filters went live no FANET device without a category has been
# written, so the rows left are weather stations, ground-station beacons and
# devices without a fix. Backup first, then delete, then show what is left.
set -euo pipefail
ts=$(date +%Y%m%d-%H%M%S)
f=~/fivl-backups/ads_l-fanet-nocat-$ts.sql.gz
mysqldump --single-transaction --quick ads_l monthly_sources \
  --where="month='2026-10' AND source='OGNFNT' AND category IS NULL" | gzip > "$f"
zcat "$f" | tail -1
mysql -t ads_l <<'SQL'
DELETE FROM monthly_sources WHERE month='2026-10' AND source='OGNFNT' AND category IS NULL;
SELECT ROW_COUNT() AS deleted, UTC_TIMESTAMP() AS at_utc;
SQL
