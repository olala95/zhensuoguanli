-- 0005_m13_backup · M13 数据安全与恢复（核心部分第一阶段落地）
-- 契约：05 §5（启动自检/崩溃恢复）、05 §6（备份策略）

CREATE TABLE IF NOT EXISTS backup_job (
    id            INTEGER PRIMARY KEY,
    job_no        TEXT NOT NULL UNIQUE,
    job_type      TEXT NOT NULL,        -- FULL/INCR
    status        TEXT NOT NULL,        -- PENDING/RUNNING/SUCCEEDED/FAILED/ABANDONED
    started_at    TEXT,
    finished_at   TEXT,
    duration_ms   INTEGER,
    file_path     TEXT,
    file_size     INTEGER,
    checksum      TEXT,
    reason        TEXT,                 -- SCHEDULED/MANUAL/BEFORE_MIGRATION/ON_EXIT
    error_message TEXT,
    created_by    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_backup_job_time ON backup_job(started_at);

CREATE TABLE IF NOT EXISTS backup_file (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES backup_job(id),
    file_name  TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    file_size  INTEGER,
    checksum   TEXT,
    status     TEXT NOT NULL,           -- VALID/INVALID/DELETED
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_backup_file_job ON backup_file(job_id);

CREATE TABLE IF NOT EXISTS restore_log (
    id             INTEGER PRIMARY KEY,
    restored_at    TEXT NOT NULL,
    source_file    TEXT NOT NULL,
    target_path    TEXT,
    result         TEXT NOT NULL,       -- SUCCEEDED/FAILED
    before_backup  TEXT,
    detail_json    TEXT,
    restored_by    INTEGER
);

CREATE TABLE IF NOT EXISTS integrity_check (
    id          INTEGER PRIMARY KEY,
    checked_at  TEXT NOT NULL,
    check_type  TEXT NOT NULL,          -- DB/BACKUP_VERIFY/SCHEDULED
    target      TEXT,
    result      TEXT NOT NULL,          -- OK/WARN/FAIL
    detail_json TEXT,
    duration_ms INTEGER,
    checked_by  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_integrity_time ON integrity_check(checked_at);

CREATE TABLE IF NOT EXISTS crash_recovery_log (
    id           INTEGER PRIMARY KEY,
    detected_at  TEXT NOT NULL,
    issue        TEXT,
    action_taken TEXT,
    result       TEXT,                  -- RECOVERED/NEED_MANUAL_REVIEW
    detail_json  TEXT
);

-- 备份记录属审计性质：禁止物理删除（03 §4.2）
CREATE TRIGGER IF NOT EXISTS trg_backup_job_no_delete
BEFORE DELETE ON backup_job
BEGIN SELECT RAISE(ABORT, 'E2006: backup_job 禁止删除'); END;
CREATE TRIGGER IF NOT EXISTS trg_integrity_no_delete
BEFORE DELETE ON integrity_check
BEGIN SELECT RAISE(ABORT, 'E2006: integrity_check 禁止删除'); END;
