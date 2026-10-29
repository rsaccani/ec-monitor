-- One row per run of the nightly measured-volume step (volume.py nightly, METHOD.md section 10.9), the counterpart of
-- nightly_runs for the part that runs after the main nightly. The service user may INSERT but not UPDATE or DELETE
-- here, so the key is (day, finished_at) and a rerun of a day adds a row; read the newest per day. Run once as root
-- before the first run:
--   mysql < schema/2026-10-30-volume-runs.sql

USE ads_l;

CREATE TABLE IF NOT EXISTS `volume_runs` (
  `day` date NOT NULL,
  `finished_at` datetime NOT NULL,
  `seconds` double NOT NULL,
  `peak_rss_mb` smallint(5) unsigned DEFAULT NULL,
  `status` varchar(12) NOT NULL,
  `hours_read` tinyint(3) unsigned DEFAULT NULL,
  `voxels_fine` int(10) unsigned DEFAULT NULL,
  `voxels_coarse` int(10) unsigned DEFAULT NULL,
  `day_bytes` int(10) unsigned DEFAULT NULL,
  `tiles` smallint(5) unsigned DEFAULT NULL,
  `notes` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`day`, `finished_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

GRANT SELECT, INSERT ON ads_l.volume_runs TO 'ads_user'@'localhost';
