-- Ground stations, one row per station and UTC day (nightly.py, METHOD.md 10.5,
-- 17 October 2026). Run once as root before deploying the code that uses it:
--   mysql < schema/2026-10-17-stations.sql
-- A station is a name that sends receiver beacons (OGN receivers, FANET ground
-- stations such as GXAirCom). The name is compared byte for byte, as in
-- daily_quality (schema/2026-10-16-quality-name-binary.sql): StChamas and
-- STCHAMAS are two stations and must not collide on the primary key.
-- `kind` is OGN, FANET or other, told from the software version of the status
-- beacon. NULL means the beacon did not carry the figure (a FANET station has
-- no NTP offset); `*_last` is the last value of the day, `*_median` the median.
-- `availability` is the share of the day outside a silence of more than 15
-- minutes between beacons, NULL when hours of the recording are missing.
-- `moved_m`, `altitude_m`, `late_share`, `stray_share` and `relay_offset_m`
-- are the five receiver checks of METHOD.md 10.4 for the day, NULL where the
-- station was not judged by that check. `notes` lists the codes of the
-- checks that flagged the station (comma separated, see sources.STATION_NOTES).
-- The service user needs no UPDATE: a rerun of a day deletes its rows first.

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_stations` (
  `day` date NOT NULL,
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `kind` varchar(8) NOT NULL,
  `version` varchar(32) DEFAULT NULL,
  `lat` decimal(6,4) DEFAULT NULL,
  `lon` decimal(7,4) DEFAULT NULL,
  `availability` float DEFAULT NULL,
  `beacons` int(10) unsigned NOT NULL DEFAULT 0,
  `status_beacons` int(10) unsigned NOT NULL DEFAULT 0,
  `ntp_ms_last` float DEFAULT NULL,
  `ntp_ms_median` float DEFAULT NULL,
  `temp_c_last` float DEFAULT NULL,
  `temp_c_median` float DEFAULT NULL,
  `noise_db_last` float DEFAULT NULL,
  `noise_db_median` float DEFAULT NULL,
  `ppm_manual_last` float DEFAULT NULL,
  `ppm_manual_median` float DEFAULT NULL,
  `ppm_auto_last` float DEFAULT NULL,
  `ppm_auto_median` float DEFAULT NULL,
  `signal_10km_db_last` float DEFAULT NULL,
  `signal_10km_db_median` float DEFAULT NULL,
  `geoid_declared` tinyint(1) DEFAULT NULL,
  `aircraft_heard` int(10) unsigned NOT NULL DEFAULT 0,
  `packets_forwarded` int(10) unsigned NOT NULL DEFAULT 0,
  `moved_m` float DEFAULT NULL,
  `altitude_m` float DEFAULT NULL,
  `late_share` float DEFAULT NULL,
  `stray_share` float DEFAULT NULL,
  `relay_offset_m` float DEFAULT NULL,
  `notes` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`day`,`name`),
  KEY `name_day` (`name`,`day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_stations TO 'ads_user'@'localhost';
