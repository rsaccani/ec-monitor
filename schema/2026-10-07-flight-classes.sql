-- Thermal places named by take-off site, flight classes and routes
-- (nightly.py, PATTERNS.md sections 3 and 6, evening of 7 October 2026).
-- Run once as root before deploying the code that uses it:
--   mysql < schema/2026-10-07-flight-classes.sql
-- daily_routes holds device addresses: it falls under the two-month retention
-- of METHOD.md section 9, and archive_loop reduces it to
-- monthly_routes_summary. The other tables hold counts only; site names are
-- public places (FIVL sites, OpenStreetMap take-offs and airfields).

USE ads_l;

-- Thermals per 0.25-degree cell and kind, by the take-off site of the flight
-- they were flown in, for naming thermal places.
CREATE TABLE IF NOT EXISTS `daily_thermal_sites` (
  `day` date NOT NULL,
  `kind` varchar(16) NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `site` varchar(255) NOT NULL,
  `thermals` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`lat_idx`,`lon_idx`,`site`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Flights of at least 2 minutes by class. Unpowered (paraglider, hang_glider,
-- glider): local, out_and_return, cross, end_unseen, no_altitude. Powered:
-- local, out_and_return, one_way, end_unseen, no_airfield. distance_band:
-- the largest distance from the take-off (local, out_and_return, end_unseen,
-- no_*), or take-off to landing (cross, one_way), in the bands of
-- daily_flights.extent_band; path_band as daily_flights.path_band. landing:
-- seen (heard standing on the ground), inferred (low and descending before a
-- silence; for powered aircraft at an aerodrome) or none.
CREATE TABLE IF NOT EXISTS `daily_flight_classes` (
  `day` date NOT NULL,
  `kind` varchar(16) NOT NULL,
  `terrain` varchar(8) NOT NULL,
  `class` varchar(16) NOT NULL,
  `landing` varchar(8) NOT NULL,
  `distance_band` tinyint(3) unsigned NOT NULL,
  `path_band` tinyint(3) unsigned NOT NULL,
  `flights` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`terrain`,`class`,`landing`,`distance_band`,`path_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- One-way flights of powered aircraft between two airfields (airfield_a <
-- airfield_b), per day and address. Published only per month, and a route
-- by name only with at least 5 distinct aircraft that month.
CREATE TABLE IF NOT EXISTS `daily_routes` (
  `day` date NOT NULL,
  `airfield_a` varchar(255) NOT NULL,
  `airfield_b` varchar(255) NOT NULL,
  `address` varchar(16) NOT NULL,
  `flights` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`airfield_a`,`airfield_b`,`address`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `monthly_routes_summary` (
  `month` char(7) NOT NULL,
  `airfield_a` varchar(255) NOT NULL,
  `airfield_b` varchar(255) NOT NULL,
  `flights` int(10) unsigned NOT NULL,
  `aircraft` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`airfield_a`,`airfield_b`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_thermal_sites TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_flight_classes TO 'ads_user'@'localhost';
GRANT SELECT, INSERT, DELETE ON ads_l.daily_routes TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_routes_summary TO 'ads_user'@'localhost';
