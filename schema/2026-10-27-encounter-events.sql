-- One row per kept encounter of crewed aircraft (and of a drone against a crewed aircraft), written by
-- the nightly beside daily_crewed_encounters from the same encounter objects (nightly.py, METHOD.md
-- 10.2, 27 October 2026): summing a day's events per threshold and pair of kinds gives the `encounters`
-- of the aggregate. Run once as root, before deploying the code that writes it:
--   mysql < schema/2026-10-27-encounter-events.sql
--
-- The row holds no address, no hash and no registration, and nothing links two events of one aircraft.
-- What finds the scene again in the raw feed (kept 30 days) is the instant of the closest approach
-- (UTC, to the second) with the placed position and altitude of each aircraft. Placed means that the
-- aircraft with the later fix is interpolated at the instant of the earlier one (METHOD.md 10.2), so
-- the positions are those the detector compared, not necessarily a packet. Private: no endpoint serves
-- this table. A rerun of a day deletes its rows first (the conspicuity family of nightly.FAMILIES).
--
-- Side a and side b are filed as in daily_crewed_encounters (kinds in canonical order, and for equal
-- kinds the side whose systems sort first). `dup` numbers events that share the instant, the threshold
-- and both positions. alt_*_m is above mean sea level and agl_*_m above the terrain model (NULL
-- outside it). distance_m and vertical_m are the horizontal and vertical distance at the closest
-- approach; approach_s the seconds from the pair first being within 1 km (and 150 m vertically) to it;
-- under_s the seconds between the first and the last contact under the threshold; crossing_deg the
-- angle between the two tracks and relative_kmh the speed of one against the other. circling_* is 1
-- when the aircraft turned at 3 degrees a second or more, NULL without a course; above is 'a', 'b' or
-- '' when level within 0.5 m; gap_*_s the seconds between the two fixes each position was placed
-- from (0 when it is a fix). reliability, miss_m (the projection miss of sparse fixes, in metres),
-- drone_evidence, shares_radio and shares_any are as in daily_crewed_encounters.

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_encounter_events` (
  `day` date NOT NULL,
  `closest_at` datetime NOT NULL,
  `threshold` varchar(5) NOT NULL,
  `lat_a` decimal(7,5) NOT NULL,
  `lon_a` decimal(8,5) NOT NULL,
  `lat_b` decimal(7,5) NOT NULL,
  `lon_b` decimal(8,5) NOT NULL,
  `dup` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `kind_a` varchar(12) NOT NULL,
  `kind_b` varchar(12) NOT NULL,
  `systems_a` varchar(255) NOT NULL,
  `systems_b` varchar(255) NOT NULL,
  `alt_a_m` smallint(6) NOT NULL,
  `alt_b_m` smallint(6) NOT NULL,
  `agl_a_m` smallint(6) DEFAULT NULL,
  `agl_b_m` smallint(6) DEFAULT NULL,
  `distance_m` decimal(5,1) NOT NULL,
  `vertical_m` decimal(5,1) NOT NULL,
  `approach_s` smallint(5) unsigned NOT NULL,
  `under_s` smallint(5) unsigned NOT NULL,
  `crossing_deg` smallint(5) unsigned DEFAULT NULL,
  `relative_kmh` smallint(5) unsigned DEFAULT NULL,
  `geometry` tinyint(3) unsigned NOT NULL DEFAULT 255,
  `circling_a` tinyint(1) DEFAULT NULL,
  `circling_b` tinyint(1) DEFAULT NULL,
  `above` char(1) NOT NULL DEFAULT '',
  `gap_a_s` smallint(5) unsigned NOT NULL,
  `gap_b_s` smallint(5) unsigned NOT NULL,
  `reliability` varchar(9) NOT NULL DEFAULT 'dense',
  `miss_m` int(10) unsigned DEFAULT NULL,
  `drone_evidence` varchar(12) NOT NULL DEFAULT '',
  `shares_radio` tinyint(1) NOT NULL,
  `shares_any` tinyint(1) NOT NULL,
  PRIMARY KEY (`day`,`closest_at`,`threshold`,`lat_a`,`lon_a`,`lat_b`,`lon_b`,`dup`),
  KEY `kinds` (`day`,`kind_a`,`kind_b`,`threshold`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_encounter_events TO 'ads_user'@'localhost';
