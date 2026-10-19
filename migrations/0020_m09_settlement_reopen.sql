-- 0020 F64 日结撤销（受限）
-- 背景：单机诊所没有财务复核岗，「执行日结」误点一次，当天就无法再建账单收费（E3006），
--       等于诊所停业一天。这里不删除日结记录，只打作废标记，保留完整痕迹。
ALTER TABLE day_settlement ADD COLUMN voided_at   TEXT;
ALTER TABLE day_settlement ADD COLUMN voided_by   INTEGER;
ALTER TABLE day_settlement ADD COLUMN void_reason TEXT;

CREATE INDEX IF NOT EXISTS ix_day_settle_date ON day_settlement(biz_date, voided_at);
