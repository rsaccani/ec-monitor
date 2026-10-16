-- daily_quality.name compared byte for byte (nightly.py, METHOD.md 10.4,
-- 16 October 2026). Run once as root:
--   mysql ads_l < schema/2026-10-16-quality-name-binary.sql
-- OGN receiver names are case-sensitive: StChamas and STCHAMAS are two
-- stations, each sending its own beacons. Under utf8mb4_general_ci they made
-- one primary key, so when both were flagged by the same check on the same
-- day the insert failed with a duplicate entry and the whole conspicuity
-- family of that day was rolled back (2026-10-14, in the nightly run and in
-- its recompute). Recompute such a day with `nightly.py --day` afterwards.
ALTER TABLE daily_quality MODIFY `name` varchar(64) COLLATE utf8mb4_bin NOT NULL;
