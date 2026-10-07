-- Which devices declaring a drone are drones (nightly.py, METHOD.md 10.1 since 7 October 2026,
-- 7 October 2026). Run once as root before deploying the code that uses it:
--   mysql < schema/2026-10-07-drone-evidence.sql
-- The drone tables gain `evidence` in their key: 1 confirmed, 2 uncertain.
-- Addresses with crewed evidence write no rows there; daily_drone_classes
-- counts every class: 3 crewed by ADS-B emitter category, 4 crewed by device
-- database type and a thermal, 5 stray (13 is the day's majority on none of
-- the address's systems), 6 crewed by a climbing thermal on a day of more
-- than 100 km. No other schema change is needed for the majority category,
-- the new quality checks or ADS-B's 0: the UPDATE (category) grant on
-- monthly_sources from 2026-10-07-nightly.sql covers the last. The tables hold only 2026-10-06 when
-- this runs; its existing rows become "uncertain" and that day is recomputed
-- afterwards with `nightly.py --day 2026-10-06`, which replaces them.

USE ads_l;

ALTER TABLE `daily_drones`
  ADD COLUMN `evidence` tinyint(3) unsigned NOT NULL DEFAULT 2 AFTER `day`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`lat_idx`,`lon_idx`,`height_band`,`speed_band`,`systems`);

ALTER TABLE `daily_drone_cells`
  ADD COLUMN `evidence` tinyint(3) unsigned NOT NULL DEFAULT 2 AFTER `day`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`lat_idx`,`lon_idx`);

ALTER TABLE `daily_drone_extent`
  ADD COLUMN `evidence` tinyint(3) unsigned NOT NULL DEFAULT 2 AFTER `day`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`extent_band`);

ALTER TABLE `daily_drone_encounters`
  ADD COLUMN `evidence` tinyint(3) unsigned NOT NULL DEFAULT 2 AFTER `day`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`drone_systems`,`other_category`,`other_systems`,`distance_band`);

-- Addresses declaring a drone (category 13 or Remote ID) per class, with
-- their airborne seconds, so that the page can say how many were set aside.
CREATE TABLE IF NOT EXISTS `daily_drone_classes` (
  `day` date NOT NULL,
  `evidence` tinyint(3) unsigned NOT NULL,
  `addresses` int(10) unsigned NOT NULL DEFAULT 0,
  `air_seconds` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`evidence`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_drone_classes TO 'ads_user'@'localhost';
