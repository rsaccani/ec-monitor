-- Sensitivity of a ground station against its neighbours (nightly.py, METHOD.md 10.5,
-- "Sensitivity against the neighbouring stations", 17 October 2026). Run once as root,
-- after schema/2026-10-17-stations.sql and before deploying the code that uses it:
--   mysql < schema/2026-10-17-stations-sensitivity.sql
--
-- daily_stations gains four columns, all NULL for a station the method did not judge
-- that day (fewer than 3 partners, or fewer than 200 shared fixes in all):
--   sensitivity_db        the station's signal against the network's, in dB, after the
--                         signals are scaled to 10 km with the day's fitted k; 0 is the
--                         median station, negative is deaf
--   sensitivity_partners  stations it was compared with
--   sensitivity_fixes     FLARM fixes it shared with them
--   sensitivity_low_share share of those partners it is more than 3 dB below, 0 to 1
--
-- The fitted k is a figure of the network, one per day, so it gets a small table of its
-- own instead of a row repeated in 2,000 stations: the quality table's `scope` is an
-- enum without a place for it. The table is in the stations family (nightly.FAMILIES),
-- so a rerun of a day deletes its row first and the service user needs no UPDATE.

USE ads_l;

ALTER TABLE `daily_stations`
  ADD COLUMN `sensitivity_db` float DEFAULT NULL AFTER `relay_offset_m`,
  ADD COLUMN `sensitivity_partners` smallint(5) unsigned DEFAULT NULL AFTER `sensitivity_db`,
  ADD COLUMN `sensitivity_fixes` int(10) unsigned DEFAULT NULL AFTER `sensitivity_partners`,
  ADD COLUMN `sensitivity_low_share` float DEFAULT NULL AFTER `sensitivity_fixes`;

CREATE TABLE IF NOT EXISTS `daily_station_sensitivity` (
  `day` date NOT NULL,
  `k` float DEFAULT NULL,
  `pairs` int(10) unsigned NOT NULL DEFAULT 0,
  `observations` int(10) unsigned NOT NULL DEFAULT 0,
  `stations_judged` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_station_sensitivity TO 'ads_user'@'localhost';
