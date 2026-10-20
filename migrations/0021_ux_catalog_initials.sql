-- 0021 易用性改造：目录检索支持拼音首字母
-- 背景：开方时医生习惯敲首字母（"hq" 找黄芪），但 herb 表只有全拼 pinyin，
-- LIKE '%hq%' 匹配不到 "huangqi"。新增首字母列，随目录一起检索。
-- 只增不改：新增列 + 索引，不动既有数据。

ALTER TABLE herb ADD COLUMN pinyin_initials TEXT;
CREATE INDEX IF NOT EXISTS idx_herb_initials ON herb(pinyin_initials);

ALTER TABLE drug ADD COLUMN pinyin_initials TEXT;
CREATE INDEX IF NOT EXISTS idx_drug_initials ON drug(pinyin_initials);
