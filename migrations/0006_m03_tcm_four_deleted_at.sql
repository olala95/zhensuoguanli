-- 0006_m03_tcm_four_deleted_at · 补齐软删除字段
-- 契约：03 §4.2（业务主表一律软删除）—— 0004 中 tcm_four_diagnosis 漏了 deleted_at，
-- 按「迁移脚本只增不改」原则新增本脚本补齐，不回改 0004。

ALTER TABLE tcm_four_diagnosis ADD COLUMN deleted_at TEXT;
