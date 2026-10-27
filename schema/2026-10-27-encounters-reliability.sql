-- How an encounter's placement was judged, and a drone's evidence, in the key of the crewed
-- encounters (METHOD.md 10.2, 27 October 2026). `reliability` is dense (fixes under 10 s apart),
-- reliable or uncertain (placed between fixes 10 s or more apart, by projection or beyond the
-- limit of the kind); `drone_evidence` is '' for two crewed aircraft and the drone's evidence
-- (confirmed, uncertain) for a drone and a crewed aircraft. Existing rows take the defaults
-- ('dense', ''), which is right for the days before: their encounters were all placed on single
-- fixes. One ALTER, so that the table is never without a key. Run once as root before deploying
-- the code that writes them:
--   mysql < schema/2026-10-27-encounters-reliability.sql

USE ads_l;

ALTER TABLE `daily_crewed_encounters`
  ADD COLUMN IF NOT EXISTS `reliability` varchar(9) NOT NULL DEFAULT 'dense' AFTER `shares_any`,
  ADD COLUMN IF NOT EXISTS `drone_evidence` varchar(12) NOT NULL DEFAULT '' AFTER `reliability`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`day`,`kind_a`,`kind_b`,`threshold`,`lat_idx`,`lon_idx`,`distance_band`,`closing_band`,`geometry`,`systems_a`,`systems_b`,`reliability`,`drone_evidence`);
