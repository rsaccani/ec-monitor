-- What only one ground station delivers (nightly.py, station_unique.py, METHOD.md 10.5, "What only this
-- station delivers", 2 November 2026). One row per station and day; the counts are 10-second keys (a device
-- address and a 10-second slice of packet time), 360 to the hour, per band of height above the ground:
-- low below 300 m, mid from 300 m to 1,000 m, high from 1,000 m. heard counts the keys the station
-- delivered, unique those no other station delivered. Run once as root, before deploying the code that
-- writes it:
--   mysql < schema/2026-11-02-station-unique.sql

USE ads_l;

CREATE TABLE IF NOT EXISTS `daily_station_unique` (
  `day` date NOT NULL,
  `station` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `heard_low` int(10) unsigned NOT NULL,
  `heard_mid` int(10) unsigned NOT NULL,
  `heard_high` int(10) unsigned NOT NULL,
  `unique_low` int(10) unsigned NOT NULL,
  `unique_mid` int(10) unsigned NOT NULL,
  `unique_high` int(10) unsigned NOT NULL,
  PRIMARY KEY (`day`, `station`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT, DELETE ON ads_l.daily_station_unique TO 'ads_user'@'localhost';
