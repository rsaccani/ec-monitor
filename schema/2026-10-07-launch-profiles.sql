-- Glider launches read by their climb, flights by launch method, the terrain
-- class, and flights of at least 2 minutes (nightly.py, PATTERNS.md sections
-- 6, 7 and 12, evening of 7 October 2026).
-- Run once as root before deploying the code that writes it:
--   mysql < schema/2026-10-07-launch-profiles.sql
-- daily_launches.method gains the values tow_or_self, self_launch and other
-- for gliders (no DDL: varchar(12) holds them; no_tow_seen stays for hang
-- gliders). daily_flights gains `launch`, the launch method of the glider
-- flight (aerotow, winch, tow_or_self, self_launch, other, start_unseen),
-- empty for every other kind. Existing rows get the empty value; the day is
-- recomputed after the deploy.

USE ads_l;

ALTER TABLE `daily_flights`
  ADD COLUMN `terrain` varchar(8) NOT NULL DEFAULT '' AFTER `kind`,
  ADD COLUMN `launch` varchar(12) NOT NULL DEFAULT '' AFTER `terrain`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`kind`,`terrain`,`launch`,`start_hour`,`duration_band`,`extent_band`,`path_band`);

-- The terrain class (PATTERNS.md, section 12, 7 October 2026): "plain" where
-- the highest minus the lowest ground within 5 km is under 600 m,
-- "mountain" from 600 m, "unknown" outside the terrain model; empty on rows
-- written before. Flights take it at their start, thermals where they end,
-- height above ground and circling time at each segment, launches where they
-- begin.
ALTER TABLE `daily_thermals`
  ADD COLUMN `terrain` varchar(8) NOT NULL DEFAULT '' AFTER `kind`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`kind`,`terrain`,`solar_hour`,`lat_idx`,`lon_idx`,`climb_band`,`radius_band`);

ALTER TABLE `daily_agl_hours`
  ADD COLUMN `terrain` varchar(8) NOT NULL DEFAULT '' AFTER `kind`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`kind`,`terrain`,`solar_hour`,`agl_band`);

ALTER TABLE `daily_circling_time`
  ADD COLUMN `terrain` varchar(8) NOT NULL DEFAULT '' AFTER `kind`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`kind`,`terrain`,`solar_hour`);

ALTER TABLE `daily_launches`
  ADD COLUMN `terrain` varchar(8) NOT NULL DEFAULT '' AFTER `towed_kind`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`method`,`towed_kind`,`terrain`,`lat_idx`,`lon_idx`,`height_band`,`duration_band`);
