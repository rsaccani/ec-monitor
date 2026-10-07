-- One nightly run, two families of tables, and the powered-aircraft
-- measures kept on 7 October 2026 (PATTERNS.md sections 9 to 11). Run once
-- as root before deploying the code that uses it (the statistics read
-- nightly_runs.notes):
--   mysql < schema/2026-10-07-nightly-families.sql
-- A two-run version drafted the same day (a `part` column in nightly_runs)
-- was dropped before it reached the repository.

USE ads_l;

-- No DDL for two changes of the same evening, recorded here so this file
-- tells the whole story: daily_launches.method gains the value
-- 'start_unseen' (a glider or hang glider first heard more than 150 m above
-- the ground, out of the launch split), and daily_thermals.kind has
-- 'paraglider' and 'hang_glider' in place of 'free_flight'.

-- nightly.py writes the conspicuity tables (METHOD.md) and the patterns
-- tables (PATTERNS.md) in two transactions; when one family fails, notes says
-- which and why, and the other's rows of the day are written all the same.
ALTER TABLE `nightly_runs`
  ADD COLUMN `notes` varchar(255) NOT NULL DEFAULT '' AFTER `seconds`;

-- Level segments of powered aircraft and helicopters (2 minutes within
-- 150 ft, above 300 m over the ground). emitter: the ADS-B emitter category
-- if A1, B4 or A7, "other", or "none" without ADS-B. altitude_ref: pressure
-- (ADS-B's FL) or gps (the altitude every system sends). altitude_band in ft
-- (0 under 3,000, 1 3-5,000, 2 5-7,000, 3 7-9,000, 4 9-11,000, 5 11-15,000,
-- 6 over 15,000); speed_band of the mean ground speed (0 under 100 km/h,
-- 1 100-150, 2 150-200, 3 200-250, 4 250-300, 5 over 300).
CREATE TABLE IF NOT EXISTS `daily_cruise` (
  `day` date NOT NULL,
  `kind` varchar(12) NOT NULL,
  `emitter` varchar(6) NOT NULL,
  `altitude_ref` varchar(8) NOT NULL,
  `altitude_band` tinyint(3) unsigned NOT NULL,
  `speed_band` tinyint(3) unsigned NOT NULL,
  `segments` int(10) unsigned NOT NULL DEFAULT 0,
  `seconds` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`emitter`,`altitude_ref`,`altitude_band`,`speed_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Helicopter flying time by night (sun more than 6 degrees below the
-- horizon) or day, height band above ground (as daily_agl_hours) and cell.
CREATE TABLE IF NOT EXISTS `daily_helicopter_night` (
  `day` date NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `night` tinyint(1) NOT NULL,
  `agl_band` tinyint(3) unsigned NOT NULL,
  `air_seconds` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`lat_idx`,`lon_idx`,`night`,`agl_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Tugs by the share of their flying time spent towing (0 under 25%, 1 25-50%,
-- 2 50-75%, 3 over 75%); never the tug's address.
CREATE TABLE IF NOT EXISTS `daily_tug_time` (
  `day` date NOT NULL,
  `share_band` tinyint(3) unsigned NOT NULL,
  `tugs` int(10) unsigned NOT NULL DEFAULT 0,
  `tow_seconds` double NOT NULL DEFAULT 0,
  `air_seconds` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`share_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_cruise TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_helicopter_night TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_tug_time TO 'ads_user'@'localhost';
