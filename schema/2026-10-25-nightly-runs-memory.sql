-- The nightly's peak anonymous memory per run (nightly.py anon_mb, RESOURCES.md,
-- 25 October 2026). The RSS peak of the log counts the pages of the memory-mapped
-- terrain file too, which are cache. Run once as root before deploying the code
-- that writes it:
--   mysql < schema/2026-10-25-nightly-runs-memory.sql

USE ads_l;

ALTER TABLE `nightly_runs`
  ADD COLUMN IF NOT EXISTS `peak_anon_mb` smallint(5) unsigned DEFAULT NULL AFTER `seconds`;
