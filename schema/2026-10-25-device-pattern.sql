-- The reception pattern of each device over 30 days (nightly.py, METHOD.md 7, 25 October 2026).
-- Run once as root, before deploying the code that uses it:
--   mysql < schema/2026-10-25-device-pattern.sql
--
-- daily_device_pattern: one row per UTC day, device address, source (the tocall) and sector,
-- for the airborne receptions from receivers farther than check.PATTERN_MIN_KM, chosen as the
-- live device check chooses them. `sector` 0-11 is the side of the aircraft the receiver sees,
-- in 30-degree steps centred on the nose, clockwise; `n` the receptions, `snr_sum` the sum of
-- their signal in dB scaled to 10 km (sources.SNR_SLOPE_DB). Nothing else is kept: no
-- position, time or receiver. Holds addresses, so the nightly deletes the rows older than
-- nightly.PATTERN_KEEP_DAYS (30) days after each run. A rerun of a day deletes its rows first
-- (the device_pattern family of nightly.FAMILIES).

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_device_pattern` (
  `day` date NOT NULL,
  `address` char(6) NOT NULL,
  `source` varchar(9) NOT NULL,
  `sector` tinyint(3) unsigned NOT NULL,
  `n` int(10) unsigned NOT NULL,
  `snr_sum` float NOT NULL,
  PRIMARY KEY (`day`,`address`,`source`,`sector`),
  KEY `address` (`address`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_device_pattern TO 'ads_user'@'localhost';
