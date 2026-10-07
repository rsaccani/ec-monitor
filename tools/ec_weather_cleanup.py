#!/usr/bin/env python3
"""One-off cleanup after 41a073c: FANET weather stations counted as devices.

Listens to the OGN APRS feed for FANET packets for LISTEN seconds and collects
the addresses that send weather reports (APRS symbol "_"; a station reports
about once a minute). Then, for October 2026 only:
  - deletes those addresses from monthly_sources (source OGNFNT, no category);
  - deletes the unknown-category row (255) of monthly_hours, which their wind
    readings had inflated.
Backup taken before: ~/fivl-backups/ads_l-weather-cleanup-20261005-081909.sql.gz
"""
import re
import socket
import subprocess
import time

LISTEN = 600
MONTH = "2026-10"
weather = re.compile(r"^(FNT[0-9A-F]{6})>OGNFNT,[^:]*:/\d{6}h\d{4}\.\d\d[NS].\d{5}\.\d\d[EW]_")

ids = set()
s = socket.create_connection(("aprs.glidernet.org", 14580), timeout=30)
s.sendall(b"user N0CALL pass -1 vers ec-cleanup 0.1 filter p/FNT\r\n")
buf, end = b"", time.time() + LISTEN
while time.time() < end:
    try:
        chunk = s.recv(65536)
    except socket.timeout:
        continue
    if not chunk:
        break
    buf += chunk
    *lines, buf = buf.split(b"\n")
    for raw in lines:
        m = weather.match(raw.decode("latin-1").strip())
        if m:
            ids.add(m.group(1))
s.close()
print(f"weather stations heard in {LISTEN} s: {len(ids)}")
if not ids:
    raise SystemExit("nothing collected, nothing deleted")

in_list = ",".join(f"'{i}'" for i in sorted(ids))
sql = f"""
SELECT via, COUNT(*) AS rows_to_delete FROM monthly_sources
 WHERE month='{MONTH}' AND source='OGNFNT' AND category IS NULL AND device_id IN ({in_list})
 GROUP BY via;
DELETE FROM monthly_sources
 WHERE month='{MONTH}' AND source='OGNFNT' AND category IS NULL AND device_id IN ({in_list});
SELECT ROW_COUNT() AS sources_deleted;
SELECT category, segments, ROUND(air_seconds/3600, 1) AS hours FROM monthly_hours
 WHERE month='{MONTH}' AND category=255;
DELETE FROM monthly_hours WHERE month='{MONTH}' AND category=255;
SELECT ROW_COUNT() AS hours_rows_deleted, UTC_TIMESTAMP() AS at_utc;
"""
subprocess.run(["mysql", "-t", "ads_l"], input=sql, text=True, check=True)
