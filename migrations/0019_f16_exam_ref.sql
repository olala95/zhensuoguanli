-- 0019 F16 辅助检查记录（M03）——补全即时检查、附件与"源数据已更新"提示所需字段
-- 契约：M03/F16 R1（只存引用不复制）/ R3（源变更提示）/ R4（即时检查不走 M05）/ R5（待回报显示）
-- 说明：原 0004 建表时只考虑了"引用 M05"，缺即时检查与溯源字段，故补齐

-- 即时检查（血糖/心电图/尿常规试纸等）直接记录，不走 M05（R4）
ALTER TABLE record_exam_ref ADD COLUMN exam_name       TEXT;   -- 项目名称，如"即时血糖"
ALTER TABLE record_exam_ref ADD COLUMN exam_value      TEXT;   -- 结果值文本，如"8.5"
ALTER TABLE record_exam_ref ADD COLUMN unit            TEXT;   -- 单位，如 mmol/L
ALTER TABLE record_exam_ref ADD COLUMN abnormal_flag   TEXT;   -- NORMAL/HIGH/LOW/CRITICAL/ABNORMAL
ALTER TABLE record_exam_ref ADD COLUMN attachment_path TEXT;   -- 报告/图片附件路径（只存路径）

-- 溯源（R3）：引用时刻记下源数据的 updated_at 快照，用于提示"引用的结果已于 X 更新"
ALTER TABLE record_exam_ref ADD COLUMN source             TEXT;  -- M05 / POINT
ALTER TABLE record_exam_ref ADD COLUMN source_updated_at  TEXT;
ALTER TABLE record_exam_ref ADD COLUMN updated_at         TEXT;
ALTER TABLE record_exam_ref ADD COLUMN updated_by         INTEGER;

CREATE INDEX IF NOT EXISTS ix_record_exam_ref_record ON record_exam_ref(record_id);
CREATE INDEX IF NOT EXISTS ix_record_exam_ref_result ON record_exam_ref(lab_result_id);
