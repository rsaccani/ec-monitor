-- EC monitor item 0l, 27 October 2026: the FLARM hardware code in the monthly maker summary, so the
-- firmware spread can be read per hardware group (METHOD.md 10.8). The table is empty until October
-- 2026 is archived, so the new key costs nothing.
USE ads_l;

ALTER TABLE `monthly_maker_summary`
  ADD COLUMN IF NOT EXISTS `hardware` char(2) NOT NULL DEFAULT '' AFTER `firmware`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`month`,`source`,`maker`,`firmware`,`hardware`);
