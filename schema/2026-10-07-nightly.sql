-- Tables of the nightly measures (nightly.py, METHOD.md section 10 and, from
-- the evening of 7 October 2026, PATTERNS.md), and two
-- grants for the fixes of 7 October 2026 in sources.py. Run once as root:
--   mysql < schema/2026-10-07-nightly.sql
-- before deploying the code that uses them. Every daily table is keyed by
-- `day`, the UTC day of the recording it was computed from; nightly.py
-- deletes that day's rows and inserts them again, so a rerun replaces a day.
-- Only daily_circling_pilot holds device addresses, and archive_loop reduces
-- it to monthly_circling_pilot_summary and monthly_circling_preference once a
-- month is older than the previous one (METHOD.md, section 9). The same pass
-- now also keeps systems per aircraft and the month-to-month return of
-- monthly_sources before deleting its addresses, and nightly.py stores a
-- snapshot of every statistics endpoint after the last day of each month.

USE ads_l;

-- 1. Flying time per local solar hour (UTC + longitude/15). A UTC day yields
-- rows for up to three local dates near midnight, hence local_date beside day.
-- aircraft: distinct addresses in that hour; summed over hours it counts
-- aircraft-hours, not aircraft.
CREATE TABLE IF NOT EXISTS `daily_hours_solar` (
  `day` date NOT NULL,
  `local_date` date NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `solar_hour` tinyint(3) unsigned NOT NULL,
  `air_seconds` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`local_date`,`category`,`solar_hour`),
  KEY `idx_local_date` (`local_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 2. Circling direction: thermals, degrees turned and seconds circling, by side.
CREATE TABLE IF NOT EXISTS `daily_circling` (
  `day` date NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `thermals_right` int(10) unsigned NOT NULL DEFAULT 0,
  `thermals_left` int(10) unsigned NOT NULL DEFAULT 0,
  `degrees_right` double NOT NULL DEFAULT 0,
  `degrees_left` double NOT NULL DEFAULT 0,
  `seconds_right` double NOT NULL DEFAULT 0,
  `seconds_left` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Thermals per address and day, for the per-pilot preference test computed at
-- read time. Holds addresses: kept for the current and the previous month only.
CREATE TABLE IF NOT EXISTS `daily_circling_pilot` (
  `day` date NOT NULL,
  `address` varchar(16) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `right_hand` int(10) unsigned NOT NULL DEFAULT 0,
  `left_hand` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`address`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- What is left of daily_circling_pilot for an archived month (archive_circling):
-- pilots by thermal-count band (0: 1-4, 1: 5-9, 2: 10-19, 3: 20 or more) and
-- right-hand share decile (0: under 10%, ..., 9: 90% or more) ...
CREATE TABLE IF NOT EXISTS `monthly_circling_pilot_summary` (
  `month` char(7) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `thermal_band` tinyint(3) unsigned NOT NULL,
  `share_decile` tinyint(3) unsigned NOT NULL,
  `pilots` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`category`,`thermal_band`,`share_decile`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- ... and the sums of the per-pilot preference test for pilots with at least
-- min_thermals thermals (5, 10), from which the published figures follow:
-- chance variance = chance_var_sum / pilots, observed variance =
-- share_sq_sum / pilots - (share_sum / pilots)^2, p = right_hand / thermals.
CREATE TABLE IF NOT EXISTS `monthly_circling_preference` (
  `month` char(7) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `min_thermals` tinyint(3) unsigned NOT NULL,
  `pilots` int(10) unsigned NOT NULL,
  `thermals` int(10) unsigned NOT NULL,
  `right_hand` int(10) unsigned NOT NULL,
  `chance_var_sum` double NOT NULL,
  `share_sum` double NOT NULL,
  `share_sq_sum` double NOT NULL,
  `right_80` int(10) unsigned NOT NULL,
  `left_80` int(10) unsigned NOT NULL,
  `expected_80` double NOT NULL,
  PRIMARY KEY (`month`,`category`,`min_thermals`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Pairs of thermals flown together by two aircraft (category_a <= category_b).
CREATE TABLE IF NOT EXISTS `daily_gaggles` (
  `day` date NOT NULL,
  `category_a` tinyint(3) unsigned NOT NULL,
  `category_b` tinyint(3) unsigned NOT NULL,
  `same_side` int(10) unsigned NOT NULL DEFAULT 0,
  `opposite_side` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`category_a`,`category_b`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 3. Parked aircraft transmitting, per system (source label) and category.
CREATE TABLE IF NOT EXISTS `daily_parked` (
  `day` date NOT NULL,
  `system_name` varchar(32) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  `seconds` double NOT NULL DEFAULT 0,
  `packets` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`system_name`,`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 4. Drones: flying time per 1-degree cell, height band above ground (0-50,
-- 50-120, 120-300, over 300 m; 255 unknown), speed band (under 20, 20-50,
-- 50-100, over 100 km/h) and the systems the drone's address was heard on.
CREATE TABLE IF NOT EXISTS `daily_drones` (
  `day` date NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `height_band` tinyint(3) unsigned NOT NULL,
  `speed_band` tinyint(3) unsigned NOT NULL,
  `systems` varchar(255) NOT NULL,
  `air_seconds` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`lat_idx`,`lon_idx`,`height_band`,`speed_band`,`systems`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- The same per cell only, so that distinct drones per cell can be read.
CREATE TABLE IF NOT EXISTS `daily_drone_cells` (
  `day` date NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `air_seconds` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`lat_idx`,`lon_idx`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Drones by how far they went from the first fix of a session (under 1, 1-3,
-- 3-10, over 10 km), the largest of the day's sessions.
CREATE TABLE IF NOT EXISTS `daily_drone_extent` (
  `day` date NOT NULL,
  `extent_band` tinyint(3) unsigned NOT NULL,
  `drones` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`extent_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- A drone and a crewed aircraft in flight within 10 s, 1 km and 150 m;
-- distance_band of the closest approach (under 300, 300-600, 600-1000 m).
CREATE TABLE IF NOT EXISTS `daily_drone_encounters` (
  `day` date NOT NULL,
  `drone_systems` varchar(255) NOT NULL,
  `other_category` tinyint(3) unsigned NOT NULL,
  `other_systems` varchar(255) NOT NULL,
  `distance_band` tinyint(3) unsigned NOT NULL,
  `shared_system` tinyint(1) NOT NULL,
  `encounters` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`drone_systems`,`other_category`,`other_systems`,`distance_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- 5. Data quality per system (source label) or OGN receiver (by name):
-- `count` of `total` for each check; `value` is the measured figure where
-- there is one (a receiver's offset in m, its distance moved, a share).
-- Receivers have a row only when flagged, plus one row per check under the
-- name '*' with the receivers flagged (count) among those judged (total).
CREATE TABLE IF NOT EXISTS `daily_quality` (
  `day` date NOT NULL,
  `scope` enum('system','receiver') NOT NULL,
  `name` varchar(64) NOT NULL,
  `check_name` varchar(32) NOT NULL,
  `count` int(10) unsigned NOT NULL DEFAULT 0,
  `total` int(10) unsigned NOT NULL DEFAULT 0,
  `value` double DEFAULT NULL,
  PRIMARY KEY (`day`,`scope`,`name`,`check_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- One row per day computed: when, from how many hourly files, which were missing.
CREATE TABLE IF NOT EXISTS `nightly_runs` (
  `day` date NOT NULL,
  `finished_at` datetime NOT NULL,
  `hours_read` tinyint(3) unsigned NOT NULL,
  `hours_missing` varchar(80) NOT NULL DEFAULT '',
  `line_count` int(10) unsigned NOT NULL,
  `seconds` double NOT NULL,
  PRIMARY KEY (`day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Figures that need device addresses, kept for archived months (archive_loop,
-- from 7 October 2026). Systems per aircraft as /api/systems publishes them,
-- category 255 for none; combinations shared by fewer than 5 aircraft are
-- only in other_combinations, as on the endpoint.
CREATE TABLE IF NOT EXISTS `monthly_systems_summary` (
  `month` char(7) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `one` int(10) unsigned NOT NULL,
  `two` int(10) unsigned NOT NULL,
  `three_or_more` int(10) unsigned NOT NULL,
  `radio_and_phone` int(10) unsigned NOT NULL,
  `phone_only` int(10) unsigned NOT NULL,
  `other_combinations` int(10) unsigned NOT NULL,
  `adsl` int(10) unsigned NOT NULL,
  `adsl_with_flarm` int(10) unsigned NOT NULL,
  `adsl_with_fanet` int(10) unsigned NOT NULL,
  `adsl_only` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `monthly_systems_combo_summary` (
  `month` char(7) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `systems` varchar(255) NOT NULL,
  `aircraft` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`category`,`systems`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Of the devices of `month` on a source (channels together), how many were
-- heard again on it the month after; computed while both months still hold
-- their addresses.
CREATE TABLE IF NOT EXISTS `monthly_return_summary` (
  `month` char(7) NOT NULL,
  `source` varchar(9) NOT NULL,
  `devices` int(10) unsigned NOT NULL,
  `returned` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- What every statistics endpoint published for a month, taken by nightly.py
-- the night the month's last day is computed; body is zlib-compressed JSON,
-- bytes its size uncompressed. A rerun replaces the month.
CREATE TABLE IF NOT EXISTS `monthly_snapshot` (
  `month` char(7) NOT NULL,
  `endpoint` varchar(32) NOT NULL,
  `method_commit` char(40) DEFAULT NULL,
  `deployed_commit` char(40) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `bytes` int(10) unsigned NOT NULL,
  `body` mediumblob NOT NULL,
  PRIMARY KEY (`month`,`endpoint`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- nightly.py runs as ads_user (.env). SELECT and INSERT it has on ads_l.*
-- already; they are repeated so the file says everything the tables need.
GRANT SELECT, INSERT, DELETE ON ads_l.daily_hours_solar TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_circling TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_circling_pilot TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_gaggles TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_parked TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_drones TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_drone_cells TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_drone_extent TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_drone_encounters TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_quality TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.nightly_runs TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.monthly_snapshot TO 'ads_user'@'localhost';
-- archive_loop (the service, as ads_user) writes the summaries; DELETE on
-- daily_circling_pilot, above, is also what lets it remove the addresses.
GRANT SELECT, INSERT ON ads_l.monthly_circling_pilot_summary TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_circling_preference TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_systems_summary TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_systems_combo_summary TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_return_summary TO 'ads_user'@'localhost';

-- sources.py, 7 October 2026: an aircraft category replaces a stored ground
-- category (14, 15) in monthly_sources. Column privileges add up, so this
-- keeps UPDATE (last_seen). monthly_devices already has UPDATE (category).
GRANT UPDATE (category) ON ads_l.monthly_sources TO 'ads_user'@'localhost';
