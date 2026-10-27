-- Maker and firmware per device (nightly.py, METHOD.md 10.8, 27 October 2026). Run once as
-- root, before deploying the code that uses it:
--   mysql < schema/2026-10-27-makers.sql
--
-- daily_device_makers: one row per UTC day, device and source (FANET, FLARM or ADS-L) heard that day.
-- `device_id` and `source` are as in monthly_sources (the address with its prefix, and the tocall).
-- `maker` is the manufacturer the first byte of the device's FANET address names in the FANET
-- specification, applied to its FLARM and ADS-L rows when the same address sent FANET the same day;
-- NULL when no FANET was heard, or the byte names nobody (0A, FLARM's own, counts as unknown).
-- `firmware` and `hardware` are the "sX.YY" and "hXX" of the last FLARM packet of the day (NULL for
-- the other systems and for FLARM devices that send neither); the hardware code has no public table
-- and no maker is ever named from it. Holds addresses: reduced at month end by archive_loop and
-- deleted, like daily_device_regions. A rerun of a day deletes its rows first (the makers family of
-- nightly.FAMILIES).
--
-- monthly_maker_summary: distinct device addresses per month, source, maker and firmware, what is
-- left once the addresses are deleted. maker '' and firmware '' are unknown. A device is in one row
-- of its source and month, so the rows add up to the source's devices per maker or per firmware.

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_device_makers` (
  `day` date NOT NULL,
  `device_id` varchar(16) NOT NULL,
  `source` varchar(9) NOT NULL,
  `maker` varchar(32) DEFAULT NULL,
  `firmware` varchar(8) DEFAULT NULL,
  `hardware` char(2) DEFAULT NULL,
  PRIMARY KEY (`day`,`device_id`,`source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `monthly_maker_summary` (
  `month` char(7) NOT NULL,
  `source` varchar(9) NOT NULL,
  `maker` varchar(32) NOT NULL DEFAULT '',
  `firmware` varchar(8) NOT NULL DEFAULT '',
  `devices` int(10) unsigned NOT NULL,
  PRIMARY KEY (`month`,`source`,`maker`,`firmware`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_device_makers TO 'ads_user'@'localhost';
GRANT SELECT, INSERT ON ads_l.monthly_maker_summary TO 'ads_user'@'localhost';
