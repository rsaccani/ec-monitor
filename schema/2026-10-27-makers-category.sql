-- EC monitor item 0l, 27 October 2026: the aircraft category in the makers tables, so each FLARM hardware
-- group can be told by the aircraft it flies in (METHOD.md 10.8). monthly_maker_summary is empty until
-- October 2026 is archived, so its new key costs nothing.
USE ads_l;

ALTER TABLE `daily_device_makers`
  ADD COLUMN IF NOT EXISTS `category` tinyint(3) unsigned DEFAULT NULL AFTER `hardware`;

ALTER TABLE `monthly_maker_summary`
  ADD COLUMN IF NOT EXISTS `category` tinyint(3) unsigned NOT NULL DEFAULT 255 AFTER `hardware`,
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (`month`,`source`,`maker`,`firmware`,`hardware`,`category`);
