-- Encounters between crewed aircraft of different kinds ("Sharing the air",
-- METHOD.md), launches and the patterns of flight (PATTERNS.md), all from
-- nightly.py, 7 October 2026: one root script for the lot. Run once as root
-- before deploying the code that writes them:
--   mysql < schema/2026-10-07-crewed-encounters.sql
-- kind_a <= kind_b alphabetically (free_flight, glider, helicopter, powered);
-- systems_a belongs to kind_a. threshold 'wide' is 1 km and 150 m, 'close'
-- 300 m and 100 m, both within 10 s. distance_band of the closest approach:
-- wide 0 under 300 m, 1 300-600, 2 600-1,000; close 0 under 100 m, 1 100-200,
-- 2 200-300. closing_band of the relative speed at that moment: 0 under
-- 50 km/h, 1 50-100, 2 100-200, 3 200-400, 4 over 400, 255 unknown (a fix
-- without a course). A rerun of a day replaces its rows.

USE ads_l;

-- 7 October 2026: daily_quality.check_name was varchar(32), and four of the
-- drone checks of nightly.py (declared_drone_crewed_adsb_emitter and the
-- like) are 33 to 35 characters, so the recompute of 2026-10-06 failed with
-- "Data too long" and rolled back. Widened by Rodolfo as root the same day;
-- recorded here, and harmless to run again.
ALTER TABLE `daily_quality` MODIFY `check_name` varchar(64) NOT NULL;

CREATE TABLE IF NOT EXISTS `daily_crewed_encounters` (
  `day` date NOT NULL,
  `kind_a` varchar(12) NOT NULL,
  `kind_b` varchar(12) NOT NULL,
  `threshold` varchar(5) NOT NULL,
  `distance_band` tinyint(3) unsigned NOT NULL,
  `closing_band` tinyint(3) unsigned NOT NULL,
  `systems_a` varchar(255) NOT NULL,
  `systems_b` varchar(255) NOT NULL,
  `shares_radio` tinyint(1) NOT NULL,
  `shares_any` tinyint(1) NOT NULL,
  `encounters` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind_a`,`kind_b`,`threshold`,`distance_band`,`closing_band`,`systems_a`,`systems_b`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_crewed_encounters TO 'ads_user'@'localhost';

-- Launches of gliders and hang gliders (nightly.py, PATTERNS.md section 7).
-- method: aerotow, winch or no_tow_seen; towed_kind: glider or hang_glider;
-- cell of the start, 1 degree. height_band: for an aerotow the height above
-- ground at release (0 under 300 m, 1 300-450, 2 450-600, 3 600-900, 4 over
-- 900), for a winch the top of the launch (0 under 300, 1 300-400, 2 400-500,
-- 3 500-700, 4 over 700), 255 otherwise or unknown. duration_band of a tow:
-- 0 under 3 minutes, 1 3-5, 2 5-8, 3 8-12, 4 over 12; 255 otherwise.
CREATE TABLE IF NOT EXISTS `daily_launches` (
  `day` date NOT NULL,
  `method` varchar(12) NOT NULL,
  `towed_kind` varchar(12) NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `height_band` tinyint(3) unsigned NOT NULL,
  `duration_band` tinyint(3) unsigned NOT NULL,
  `launches` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`method`,`towed_kind`,`lat_idx`,`lon_idx`,`height_band`,`duration_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- How many tugs towed how often that day (tows_band 0: 1 tow, 1: 2-5,
-- 2: 6-10, 3: more than 10); the tug's address is never stored.
CREATE TABLE IF NOT EXISTS `daily_tug_tows` (
  `day` date NOT NULL,
  `tows_band` tinyint(3) unsigned NOT NULL,
  `tugs` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`tows_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_launches TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_tug_tows TO 'ads_user'@'localhost';

-- Patterns of flight (nightly.py, PATTERNS.md, 7 October 2026). kind is
-- free_flight, glider, powered, helicopter, other, or for drones
-- drone_confirmed / drone_uncertain; solar_hour is local solar time.
-- Thermals of gliders and free flight, one system per aircraft: climb_band
-- of the thermal's average climb (0 under 0.5 m/s, 1 0.5-1, 2 1-1.5, 3 1.5-2,
-- 4 2-3, 5 3-4, 6 over 4, 255 unknown) and radius_band (0 under 30 m, 1 30-50,
-- 2 50-80, 3 80-120, 4 120-200, 5 over 200, 255 unknown); the sums give means.
CREATE TABLE IF NOT EXISTS `daily_thermals` (
  `day` date NOT NULL,
  `kind` varchar(16) NOT NULL,
  `solar_hour` tinyint(3) unsigned NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `climb_band` tinyint(3) unsigned NOT NULL,
  `radius_band` tinyint(3) unsigned NOT NULL,
  `thermals` int(10) unsigned NOT NULL DEFAULT 0,
  `climb_sum` double NOT NULL DEFAULT 0,
  `climbs` int(10) unsigned NOT NULL DEFAULT 0,
  `radius_sum` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`solar_hour`,`lat_idx`,`lon_idx`,`climb_band`,`radius_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Thermals shared by two aircraft of different kinds, by the median vertical
-- separation while together (0 under 50 m, 1 50-100, 2 100-200, 3 200-300).
CREATE TABLE IF NOT EXISTS `daily_mixed_thermals` (
  `day` date NOT NULL,
  `kind_a` varchar(16) NOT NULL,
  `kind_b` varchar(16) NOT NULL,
  `vsep_band` tinyint(3) unsigned NOT NULL,
  `thermals` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind_a`,`kind_b`,`vsep_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Airborne time by kind, solar hour and height above ground (0 under 50 m,
-- 1 50-120, 2 120-300, 3 300-600, 4 600-1,200, 5 1,200-2,000, 6 over 2,000,
-- 255 unknown).
CREATE TABLE IF NOT EXISTS `daily_agl_hours` (
  `day` date NOT NULL,
  `kind` varchar(16) NOT NULL,
  `solar_hour` tinyint(3) unsigned NOT NULL,
  `agl_band` tinyint(3) unsigned NOT NULL,
  `air_seconds` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`solar_hour`,`agl_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Seconds circling in thermals against airborne seconds, by kind and solar hour.
CREATE TABLE IF NOT EXISTS `daily_circling_time` (
  `day` date NOT NULL,
  `kind` varchar(16) NOT NULL,
  `solar_hour` tinyint(3) unsigned NOT NULL,
  `circling_seconds` double NOT NULL DEFAULT 0,
  `air_seconds` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`solar_hour`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Flights: start hour (solar), duration band (0 under 10 min, 1 10-30, 2 30-60,
-- 3 1-2 h, 4 2-4 h, 5 4-8 h, 6 over 8 h), largest distance from the start
-- (0 under 1 km, 1 1-5, 2 5-20, 3 20-50, 4 50-100, 5 100-300, 6 over 300) and
-- path length (0 under 5 km, 1 5-20, 2 20-50, 3 50-100, 4 100-300, 5 300-500,
-- 6 over 500).
CREATE TABLE IF NOT EXISTS `daily_flights` (
  `day` date NOT NULL,
  `kind` varchar(16) NOT NULL,
  `start_hour` tinyint(3) unsigned NOT NULL,
  `duration_band` tinyint(3) unsigned NOT NULL,
  `extent_band` tinyint(3) unsigned NOT NULL,
  `path_band` tinyint(3) unsigned NOT NULL,
  `flights` int(10) unsigned NOT NULL DEFAULT 0,
  `seconds` double NOT NULL DEFAULT 0,
  `path_m` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`start_hour`,`duration_band`,`extent_band`,`path_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Glider climbs that are probably wave, per 1-degree cell.
CREATE TABLE IF NOT EXISTS `daily_wave` (
  `day` date NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `climbs` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`lat_idx`,`lon_idx`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_thermals TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_mixed_thermals TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_agl_hours TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_circling_time TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_flights TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_wave TO 'ads_user'@'localhost';
