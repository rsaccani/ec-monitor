-- Devices by country and region (nightly.py, METHOD.md 10.6, 25 October 2026). Run once as
-- root, before deploying the code that uses it:
--   mysql < schema/2026-10-25-device-regions.sql
--
-- daily_device_regions: one row per UTC day, device, source, country and region where the
-- device had at least one airborne position. `device_id` and `source` are as in
-- monthly_sources (the address with its prefix, and the tocall), so that COUNTED_SQL and
-- SAME_SYSTEM_SQL apply to it unchanged. `country` is the ISO code of Natural Earth admin-1,
-- `region` its first-level region as grouped in sources.py (`-` when it has none, since
-- '' is the country total in the summary);
-- `category` is the day's majority category of the device on that source. Holds addresses:
-- reduced at month end by archive_loop and deleted, like daily_routes. A rerun of a day
-- deletes its rows first (the regions family of nightly.FAMILIES).
--
-- monthly_region_summary: distinct devices per month, country, region, source and
-- category, what is left once the addresses are deleted. `region` = '' is the whole
-- country and `source` = '' every source together, each counted as distinct devices at its
-- own level, never as a sum.

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_device_regions` (
  `day` date NOT NULL,
  `device_id` varchar(16) NOT NULL,
  `source` varchar(9) NOT NULL,
  `category` tinyint(3) unsigned DEFAULT NULL,
  `country` char(2) NOT NULL,
  `region` varchar(64) NOT NULL DEFAULT '',
  PRIMARY KEY (`day`,`device_id`,`source`,`country`,`region`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `monthly_region_summary` (
  `month` char(7) NOT NULL,
  `country` char(2) NOT NULL,
  `region` varchar(64) NOT NULL DEFAULT '',
  `source` varchar(9) NOT NULL DEFAULT '',
  `category` tinyint(3) unsigned NOT NULL,
  `devices` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`country`,`region`,`source`,`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_device_regions TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_region_summary TO 'ads_user'@'localhost';
