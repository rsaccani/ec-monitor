-- Where encounters happen, how, and the flying time to divide them by (nightly.py,
-- METHOD.md 10.1 and 10.2, 9 October 2026). Run once as root before deploying
-- the code that uses it:
--   mysql < schema/2026-10-09-encounter-cells.sql
-- An encounter is filed at the midpoint of the two aircraft at its closest
-- approach: lat_idx = floor(lat * 4), lon_idx = floor(lon * 4) for crewed
-- encounters (0.25 degree, as daily_thermals), floor(lat) and floor(lon) for
-- drone encounters (1 degree, as daily_drone_cells: drones are few). Rows
-- recorded before have no place and become -32768 (sources.NO_CELL), which
-- the endpoints leave out of the lists by cell; recompute the days still in
-- the raw recording (seven days) with `nightly.py --day`, which replaces them.
-- daily_crewed_encounters also gains pairs of the same kind from the same
-- day, free flight and gliders excepted, which needs no change here.
-- Both encounter tables gain `geometry`, the angle between the two ground
-- tracks at the closest approach: 0 under 45 degrees (overtaking or the same
-- way), 1 45-135 (crossing), 2 over 135 (head on), 255 without a velocity for
-- both. daily_drone_encounters gains `closing_band` too, with the edges of the
-- crewed encounters (0 under 50 km/h, 1 50-100, 2 100-200, 3 200-400, 4 over
-- 400, 255 unknown). Rows recorded before become 255 in both.

USE ads_l;

ALTER TABLE `daily_crewed_encounters`
  ADD COLUMN `lat_idx` smallint(6) NOT NULL DEFAULT -32768 AFTER `threshold`,
  ADD COLUMN `lon_idx` smallint(6) NOT NULL DEFAULT -32768 AFTER `lat_idx`,
  ADD COLUMN `geometry` tinyint(3) unsigned NOT NULL DEFAULT 255 AFTER `closing_band`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`kind_a`,`kind_b`,`threshold`,`lat_idx`,`lon_idx`,`distance_band`,`closing_band`,
                   `geometry`,`systems_a`,`systems_b`);

ALTER TABLE `daily_drone_encounters`
  ADD COLUMN `lat_idx` smallint(6) NOT NULL DEFAULT -32768 AFTER `flight_class`,
  ADD COLUMN `lon_idx` smallint(6) NOT NULL DEFAULT -32768 AFTER `lat_idx`,
  ADD COLUMN `closing_band` tinyint(3) unsigned NOT NULL DEFAULT 255 AFTER `distance_band`,
  ADD COLUMN `geometry` tinyint(3) unsigned NOT NULL DEFAULT 255 AFTER `closing_band`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`flight_class`,`lat_idx`,`lon_idx`,`drone_systems`,`other_category`,
                   `other_systems`,`distance_band`,`closing_band`,`geometry`);

-- The denominator of the crewed encounters: flying time per kind (free_flight,
-- glider, powered, helicopter, as kind_a and kind_b) and 0.25-degree cell,
-- over the same fixes the encounters are looked for in (every system, ADS-B
-- included, one per address and second, airborne at the speed of the kind,
-- the kind the majority category of the address that day), counted between
-- consecutive fixes at most 120 s apart and filed by the first. `aircraft`
-- is the distinct addresses of the kind in the cell that day.
CREATE TABLE IF NOT EXISTS `daily_air_cells` (
  `day` date NOT NULL,
  `kind` varchar(12) NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `air_seconds` double NOT NULL DEFAULT 0,
  `aircraft` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind`,`lat_idx`,`lon_idx`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_air_cells TO 'ads_user'@'localhost';

-- Encounters left out as probably one aircraft under two addresses (nightly.py
-- SAME_AIRCRAFT_*, METHOD.md 10.2): closest approach the same way (geometry
-- 0), under 150 m and under 20 km/h relative. Per kind pair as in
-- daily_crewed_encounters, kind_a 'drone' for drone encounters (threshold
-- 'wide', the only one they have); `pairs` distinct address pairs that day.
CREATE TABLE IF NOT EXISTS `daily_same_aircraft_pairs` (
  `day` date NOT NULL,
  `kind_a` varchar(12) NOT NULL,
  `kind_b` varchar(12) NOT NULL,
  `threshold` varchar(5) NOT NULL,
  `pairs` int(10) unsigned NOT NULL DEFAULT 0,
  `encounters` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`kind_a`,`kind_b`,`threshold`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_same_aircraft_pairs TO 'ads_user'@'localhost';
