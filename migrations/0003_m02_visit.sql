-- 0003_m02_visit · M02 就诊中心
-- 契约：03 §2.2（Visit 状态机）、03 §2.1（状态转换留痕）、04 §2（表所有权 M02）

CREATE TABLE IF NOT EXISTS visit (
    id              INTEGER PRIMARY KEY,
    visit_no        TEXT NOT NULL UNIQUE,
    patient_id      INTEGER NOT NULL REFERENCES patient(id),
    doctor_id       INTEGER REFERENCES sys_user(id),
    visit_type      TEXT NOT NULL DEFAULT 'NORMAL',   -- NORMAL/TCM/EMERG
    status          TEXT NOT NULL DEFAULT 'WAITING',  -- WAITING/IN_PROGRESS/SUSPENDED/COMPLETED/VOID
    is_first_visit  INTEGER NOT NULL DEFAULT 0,
    registered_at   TEXT,
    registered_by   INTEGER,
    started_at      TEXT,
    completed_at    TEXT,
    voided_at       TEXT,
    department      TEXT,
    note            TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT,
    created_by      INTEGER,
    updated_at      TEXT,
    updated_by      INTEGER,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_visit_patient ON visit(patient_id);
CREATE INDEX IF NOT EXISTS ix_visit_status ON visit(status);
CREATE INDEX IF NOT EXISTS ix_visit_date ON visit(registered_at);
CREATE INDEX IF NOT EXISTS ix_visit_doctor ON visit(doctor_id, registered_at);

-- 状态流转日志（03 §2.1）
CREATE TABLE IF NOT EXISTS visit_status_log (
    id           INTEGER PRIMARY KEY,
    entity_type  TEXT NOT NULL DEFAULT 'visit',
    visit_id     INTEGER NOT NULL REFERENCES visit(id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    operator_id  INTEGER,
    role_code    TEXT,
    occurred_at  TEXT NOT NULL,
    reason       TEXT,
    operation_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_visit_status_log ON visit_status_log(visit_id);

CREATE TABLE IF NOT EXISTS visit_queue (
    id          INTEGER PRIMARY KEY,
    visit_id    INTEGER NOT NULL UNIQUE REFERENCES visit(id),
    queue_date  TEXT NOT NULL,
    queue_no    INTEGER NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,      -- 0 普通；越大越优先
    status      TEXT NOT NULL DEFAULT 'WAITING', -- WAITING/CALLED/SKIPPED/DONE/CANCELLED
    window_no   TEXT,
    called_at   TEXT,
    called_by   INTEGER,
    finished_at TEXT,
    note        TEXT,
    created_at  TEXT,
    created_by  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_queue_date ON visit_queue(queue_date, status);

CREATE TABLE IF NOT EXISTS visit_suspension (
    id                 INTEGER PRIMARY KEY,
    visit_id           INTEGER NOT NULL REFERENCES visit(id),
    suspend_type       TEXT NOT NULL,   -- OBSERVATION 留观 / HOLD 挂单
    reason             TEXT NOT NULL,
    suspended_at       TEXT NOT NULL,
    suspended_by       INTEGER,
    expected_resume_at TEXT,
    resumed_at         TEXT,
    resumed_by         INTEGER,
    note               TEXT,
    created_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_suspension_visit ON visit_suspension(visit_id);

CREATE TABLE IF NOT EXISTS visit_void (
    id              INTEGER PRIMARY KEY,
    visit_id        INTEGER NOT NULL UNIQUE REFERENCES visit(id),
    reason          TEXT NOT NULL,
    voided_by       INTEGER,
    voided_at       TEXT NOT NULL,
    refund_required INTEGER NOT NULL DEFAULT 0,
    detail_json     TEXT,
    created_at      TEXT
);

CREATE TRIGGER IF NOT EXISTS trg_visit_no_delete
BEFORE DELETE ON visit
BEGIN SELECT RAISE(ABORT, 'E2006: visit 禁止物理删除，请使用软删除'); END;
