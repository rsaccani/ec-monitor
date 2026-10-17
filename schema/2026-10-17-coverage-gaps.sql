-- Coverage gaps, one row per UTC day, 0.25-degree cell, family and band of height above the
-- ground (nightly.py, METHOD.md 10.5, "Where a new station would help most", 17 October
-- 2026). Run once as root, after schema/2026-10-17-stations.sql and before deploying the
-- code that uses it:
--   mysql < schema/2026-10-17-coverage-gaps.sql
--
-- `lat_idx` and `lon_idx` are floor(degrees / 0.25), as in daily_air_cells. `family` is
-- free_flight (categories 6 and 7) or glider (category 1). `band` is the height above the
-- ground at the start of the segment: 0 below 300 m, 1 from 300 to 600, 2 from 600 to
-- 1,200, 3 from 1,200 m up. Flying of unknown height is left out. FLARM random addresses
-- are left out.
--   probe_s         seconds of radio signal judged for the probe: free-flight aircraft that
--                   sent two or more radio systems that day, or gliders
--   probe_late_s    the part of probe_s without signal
--   probe_aircraft  probe aircraft with any judged time in the row; summed over days it
--                   counts aircraft-days
--   air_s           airborne seconds of every aircraft of the family, whatever it carries
-- The page adds up the last 28 days. The service user needs no UPDATE: a rerun of a day
-- deletes its rows first (the table is in the stations family of nightly.FAMILIES).

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_coverage_gaps` (
  `day` date NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `family` varchar(12) NOT NULL,
  `band` tinyint(3) unsigned NOT NULL,
  `probe_s` int(10) unsigned NOT NULL DEFAULT 0,
  `probe_late_s` int(10) unsigned NOT NULL DEFAULT 0,
  `probe_aircraft` smallint(5) unsigned NOT NULL DEFAULT 0,
  `air_s` int(10) unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`,`lat_idx`,`lon_idx`,`family`,`band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_coverage_gaps TO 'ads_user'@'localhost';
