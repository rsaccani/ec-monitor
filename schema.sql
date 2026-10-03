-- Schema of the Electronic Conspicuity Monitor, as it runs in production
-- (taken from SHOW CREATE TABLE on 2026-10-03). Load it with
--   mysql < schema.sql
-- then create the service user and its grants as README.md describes.

CREATE DATABASE IF NOT EXISTS ads_l CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE ads_l;

-- Distinct ADS-L, ADS-B and FLARM devices per month (the ADS-L adoption chart).
CREATE TABLE IF NOT EXISTS `monthly_devices` (
  `month` char(7) NOT NULL,
  `device_id` varchar(16) NOT NULL,
  `device_type` enum('ADSL','ADSB','FLARM','OTHER') NOT NULL,
  `category` tinyint(3) unsigned DEFAULT NULL,
  `first_seen` datetime NOT NULL,
  PRIMARY KEY (`month`,`device_id`),
  KEY `idx_month_type` (`month`,`device_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Distinct devices per month, OGN source and channel (radio or net).
CREATE TABLE IF NOT EXISTS `monthly_sources` (
  `month` char(7) NOT NULL,
  `source` varchar(9) NOT NULL,
  `via` enum('radio','net') NOT NULL,
  `device_id` varchar(16) NOT NULL,
  `category` tinyint(3) unsigned DEFAULT NULL,
  `first_seen` datetime NOT NULL,
  `last_seen` datetime NOT NULL,
  PRIMARY KEY (`month`,`source`,`via`,`device_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Append-only: one row per day, source, channel and category at every 15-minute write; readers sum them.
CREATE TABLE IF NOT EXISTS `daily_visibility` (
  `day` date NOT NULL,
  `source` varchar(9) NOT NULL,
  `via` enum('radio','net') NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `flushed_at` datetime NOT NULL,
  `packets` int(10) unsigned NOT NULL,
  `rot_packets` int(10) unsigned NOT NULL,
  `segments` int(10) unsigned NOT NULL,
  `sessions` int(10) unsigned NOT NULL,
  `implausible` int(10) unsigned NOT NULL,
  `air_seconds` double NOT NULL,
  `p0_300` double NOT NULL,
  `p0_1000` double NOT NULL,
  `p0_3000` double NOT NULL,
  `p1_300` double NOT NULL,
  `p1_1000` double NOT NULL,
  `p1_3000` double NOT NULL,
  PRIMARY KEY (`day`,`source`,`via`,`category`,`flushed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- The tables below are updated in place with INSERT ... ON DUPLICATE KEY UPDATE.
-- Visibility per month, source, channel, category and height bands.
CREATE TABLE IF NOT EXISTS `monthly_visibility_detail` (
  `month` char(7) NOT NULL,
  `source` varchar(9) NOT NULL,
  `via` enum('radio','net') NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `msl_band` tinyint(3) unsigned NOT NULL,
  `agl_band` tinyint(3) unsigned NOT NULL,
  `segments` double NOT NULL DEFAULT 0,
  `rot_segments` double NOT NULL DEFAULT 0,
  `circling_segments` double NOT NULL DEFAULT 0,
  `air_seconds` double NOT NULL DEFAULT 0,
  `p0_300` double NOT NULL DEFAULT 0,
  `p0_1000` double NOT NULL DEFAULT 0,
  `p0_3000` double NOT NULL DEFAULT 0,
  `p1_300` double NOT NULL DEFAULT 0,
  `p1_1000` double NOT NULL DEFAULT 0,
  `p1_3000` double NOT NULL DEFAULT 0,
  `p2_300` double NOT NULL DEFAULT 0,
  `p2_1000` double NOT NULL DEFAULT 0,
  `p2_3000` double NOT NULL DEFAULT 0,
  `rot_air` double NOT NULL DEFAULT 0,
  `rot_p1_300` double NOT NULL DEFAULT 0,
  `rot_p1_1000` double NOT NULL DEFAULT 0,
  `rot_p2_300` double NOT NULL DEFAULT 0,
  `rot_p2_1000` double NOT NULL DEFAULT 0,
  `circ_air` double NOT NULL DEFAULT 0,
  `circ_p1_300` double NOT NULL DEFAULT 0,
  `circ_p1_1000` double NOT NULL DEFAULT 0,
  `circ_p2_300` double NOT NULL DEFAULT 0,
  `circ_p2_1000` double NOT NULL DEFAULT 0,
  `age_le3` double NOT NULL DEFAULT 0,
  `age_le6` double NOT NULL DEFAULT 0,
  `age_le15` double NOT NULL DEFAULT 0,
  `age_le30` double NOT NULL DEFAULT 0,
  `seg_le3` double NOT NULL DEFAULT 0,
  `seg_le6` double NOT NULL DEFAULT 0,
  `vanish_2` double NOT NULL DEFAULT 0,
  `vanish_5` double NOT NULL DEFAULT 0,
  `vanish_20` double NOT NULL DEFAULT 0,
  `int_le2` double NOT NULL DEFAULT 0,
  `int_le4` double NOT NULL DEFAULT 0,
  `int_le8` double NOT NULL DEFAULT 0,
  `int_le16` double NOT NULL DEFAULT 0,
  `int_le32` double NOT NULL DEFAULT 0,
  `int_le64` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`month`,`source`,`via`,`category`,`msl_band`,`agl_band`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Visibility per month, 0.25-degree cell, group of sources and channel.
CREATE TABLE IF NOT EXISTS `monthly_visibility_grid` (
  `month` char(7) NOT NULL,
  `lat_idx` smallint(6) NOT NULL,
  `lon_idx` smallint(6) NOT NULL,
  `grp` varchar(8) NOT NULL,
  `via` enum('radio','net') NOT NULL,
  `segments` double NOT NULL DEFAULT 0,
  `air_seconds` double NOT NULL DEFAULT 0,
  `p0_300` double NOT NULL DEFAULT 0,
  `p1_300` double NOT NULL DEFAULT 0,
  `p1_1000` double NOT NULL DEFAULT 0,
  `p0_1000` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`month`,`lat_idx`,`lon_idx`,`grp`,`via`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Radio packets received while circling, by distance band and 30-degree sector.
CREATE TABLE IF NOT EXISTS `monthly_reception_pattern` (
  `month` char(7) NOT NULL,
  `source` varchar(9) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `dist_band` tinyint(3) unsigned NOT NULL,
  `sector` tinyint(3) unsigned NOT NULL,
  `packets` double NOT NULL DEFAULT 0,
  `snr_sum` double NOT NULL DEFAULT 0,
  `snr_n` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`month`,`source`,`category`,`dist_band`,`sector`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Prediction errors in bins b0..b13 (edges in /api/prediction, bins_m).
CREATE TABLE IF NOT EXISTS `monthly_prediction` (
  `month` char(7) NOT NULL,
  `source` varchar(9) NOT NULL,
  `category` tinyint(3) unsigned NOT NULL,
  `horizon` tinyint(3) unsigned NOT NULL,
  `circling` tinyint(3) unsigned NOT NULL,
  `predictor` varchar(20) NOT NULL,
  `b0` double NOT NULL DEFAULT 0,
  `b1` double NOT NULL DEFAULT 0,
  `b2` double NOT NULL DEFAULT 0,
  `b3` double NOT NULL DEFAULT 0,
  `b4` double NOT NULL DEFAULT 0,
  `b5` double NOT NULL DEFAULT 0,
  `b6` double NOT NULL DEFAULT 0,
  `b7` double NOT NULL DEFAULT 0,
  `b8` double NOT NULL DEFAULT 0,
  `b9` double NOT NULL DEFAULT 0,
  `b10` double NOT NULL DEFAULT 0,
  `b11` double NOT NULL DEFAULT 0,
  `b12` double NOT NULL DEFAULT 0,
  `b13` double NOT NULL DEFAULT 0,
  `n` double NOT NULL DEFAULT 0,
  `error_sum` double NOT NULL DEFAULT 0,
  PRIMARY KEY (`month`,`source`,`category`,`horizon`,`circling`,`predictor`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
