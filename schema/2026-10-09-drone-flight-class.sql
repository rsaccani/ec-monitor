-- How an uncertain drone flies (nightly.py, METHOD.md 10.1, 9 October 2026).
-- Run once as root before deploying the code that uses it:
--   mysql < schema/2026-10-09-drone-flight-class.sql
-- The drone tables gain `flight_class` after `evidence`, in their key:
-- 0 not judged (direct evidence decided, or a day computed before this),
-- 2 never above 30 m, 3 probably crewed, 4 probably a multirotor,
-- 5 probably a fixed-wing drone, 6 still uncertain. Existing rows become 0;
-- recompute the days still in the raw recording with `nightly.py --day`,
-- which replaces them. The impossible-track checks of the same day are new
-- rows of daily_quality and need no change.

USE ads_l;

ALTER TABLE `daily_drones`
  ADD COLUMN `flight_class` tinyint(3) unsigned NOT NULL DEFAULT 0 AFTER `evidence`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`flight_class`,`lat_idx`,`lon_idx`,`height_band`,`speed_band`,`systems`);

ALTER TABLE `daily_drone_cells`
  ADD COLUMN `flight_class` tinyint(3) unsigned NOT NULL DEFAULT 0 AFTER `evidence`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`flight_class`,`lat_idx`,`lon_idx`);

ALTER TABLE `daily_drone_extent`
  ADD COLUMN `flight_class` tinyint(3) unsigned NOT NULL DEFAULT 0 AFTER `evidence`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`flight_class`,`extent_band`);

ALTER TABLE `daily_drone_encounters`
  ADD COLUMN `flight_class` tinyint(3) unsigned NOT NULL DEFAULT 0 AFTER `evidence`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`flight_class`,`drone_systems`,`other_category`,`other_systems`,`distance_band`);

ALTER TABLE `daily_drone_classes`
  ADD COLUMN `flight_class` tinyint(3) unsigned NOT NULL DEFAULT 0 AFTER `evidence`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`evidence`,`flight_class`);
