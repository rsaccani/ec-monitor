-- The figures of the stations' notes clock_fault and garbage_pairs (item 0i, 26 October
-- 2026): the highest share of a UTC hour's fixes (hours of 100 or more) that arrived more
-- than 5 minutes off their time stamp, and the addresses whose fixes from the station the
-- garbage rule ignored. Run once as root before deploying the code that writes them:
--   mysql < schema/2026-10-26-station-garbage.sql

USE ads_l;

ALTER TABLE `daily_stations`
  ADD COLUMN IF NOT EXISTS `clock_fault_share` float DEFAULT NULL AFTER `relay_offset_m`,
  ADD COLUMN IF NOT EXISTS `garbage_addresses` smallint(5) unsigned NOT NULL DEFAULT 0 AFTER `clock_fault_share`;
