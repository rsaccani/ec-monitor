-- Station reception rose (rose.py, tools/station_rose_backfill.py, METHOD.md 10.7, 25 October
-- 2026). Run once as root, before deploying the code that uses it:
--   mysql < schema/2026-10-25-station-rose.sql
--
-- daily_station_rose: one row per UTC day, station and source, with the direct airborne
-- receptions of that station in 36 sectors of 10 degrees by bearing from the station (north
-- up, clockwise). `station` is compared byte for byte, as daily_stations.name; `source` is the
-- tocall, as in daily_device_pattern. `n_total` is the receptions of the day. `payload` is a
-- JSON list with one entry per sector that heard anything, [sector, receptions with a signal,
-- sum of dB scaled to 10 km, [16 counts by distance]]; the bins are under 2 km, then 2 km
-- times 1.5 per step, the last one open at 584 km. No aircraft address is kept. A pair with
-- no reception in the day has no row. The rows of the last 30 days are kept: the
-- writer deletes the older ones. A rerun of a day deletes its rows first.

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_station_rose` (
  `day` date NOT NULL,
  `station` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `source` varchar(9) NOT NULL,
  `n_total` int(10) unsigned NOT NULL,
  `payload` text NOT NULL,
  PRIMARY KEY (`day`,`station`,`source`),
  KEY `station_day` (`station`,`day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_station_rose TO 'ads_user'@'localhost';
