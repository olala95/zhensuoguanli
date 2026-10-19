-- 0018 M13 数据安全与恢复（第五阶段：F91 增量 / F93 恢复 / F95 异常 / F96 崩溃 / F97-F98 看门狗）
-- 契约：03 §6（M13 异常码 E3093-E3095）、04 §2 表所有权（M13）、M13 MODULE.md R-M13-1..8

-- ============ F91 增量：挂所属全量 + 记录备份时点的写事务序号 ============
ALTER TABLE backup_job ADD COLUMN parent_job_no TEXT;   -- INCR 指向其所属 FULL 的 job_no
ALTER TABLE backup_job ADD COLUMN write_seq     INTEGER; -- 备份时 db.write_seq()，用于"无变化则跳过"

-- ============ F93 恢复：恢复单号（可追溯，每次恢复独立留痕）============
ALTER TABLE restore_log ADD COLUMN restore_no TEXT;
CREATE INDEX IF NOT EXISTS ix_restore_no ON restore_log(restore_no);

-- ============ F97/F98 看门狗动作记录（监控性质，可轮转，非审计）============
CREATE TABLE IF NOT EXISTS watchdog_log (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,           -- AI / HARDWARE / CRASH
    target      TEXT,                    -- task_no / driver_code / device_code / 任务标识
    action      TEXT NOT NULL,           -- RETRY / ABANDON / RESTART / DEGRADE / RECOVER
    result      TEXT NOT NULL,           -- SUCCESS / FAIL / SKIPPED
    detail_json TEXT,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_watchdog_kind ON watchdog_log(kind, occurred_at);

-- ============ 硬约束：崩溃恢复记录属审计性质，禁止改删（03 §4.2）============
CREATE TRIGGER IF NOT EXISTS trg_crash_log_no_update
BEFORE UPDATE ON crash_recovery_log
BEGIN SELECT RAISE(ABORT, 'E2006: crash_recovery_log 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_crash_log_no_delete
BEFORE DELETE ON crash_recovery_log
BEGIN SELECT RAISE(ABORT, 'E2006: crash_recovery_log 禁止删除'); END;

-- ============ 配置 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('backup.incremental_minutes', '60', 'INT', 'M13', '增量备份周期（分钟），距上次不足一周期且非强制则跳过', datetime('now','localtime')),
    ('backup.keep_days',           '30', 'INT', 'M13', '全量备份保留天数', datetime('now','localtime')),
    ('backup.auto_on_startup',     '1',  'BOOL','M13', '每日首次启动自动全量备份（F90 R1）', datetime('now','localtime')),
    ('backup.auto_on_migration',   '1',  'BOOL','M13', '迁移前自动备份，失败中止迁移（F90 R3）', datetime('now','localtime')),
    ('ai.watchdog_seconds',        '120','INT', 'M13', 'AI 看门狗扫描周期（秒）', datetime('now','localtime')),
    ('ai.watchdog_grace_factor',   '3',  'INT', 'M13', 'AI 超时判定的宽放倍数（timeout_sec × 该值）', datetime('now','localtime')),
    ('print.job_timeout_sec',      '120','INT', 'M13', '打印任务卡在 PRINTING 的超時秒数（F98 R5）', datetime('now','localtime')),
    ('hardware.watchdog_degrade_rounds', '2', 'INT', 'M13', '重启后仍连续失败多少轮则设备降级 OFFLINE（F98 R4）', datetime('now','localtime'));
