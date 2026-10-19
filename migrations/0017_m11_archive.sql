-- 0017 M11 病历归档（F76-F83）
-- 契约：03 §1.2（AR{yyyyMM}{4位}）、§2.3（病历状态机）、§2.3/§9（管理员无权改内容、解封高风险）
--       04 §2 表所有权：M11；04 §3 只增不改表
-- 边界：本模块**不写 M03 的表**；状态推进一律经 m03_record.transition()

-- ============ F76 病历版本（快照自包含 + 只增不改）============
CREATE TABLE IF NOT EXISTS record_version (
    id            INTEGER PRIMARY KEY,
    record_id     INTEGER NOT NULL REFERENCES record(id),
    version_no    INTEGER NOT NULL,          -- 同一病历从 1 递增，不跳号
    snapshot_json TEXT NOT NULL,             -- 完整快照（R-M11-7，不引用实时内容）
    source        TEXT NOT NULL DEFAULT 'CONFIRM',  -- CONFIRM 确认 / AMEND 修改
    change_note   TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    created_role  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rver ON record_version(record_id, version_no);
CREATE INDEX IF NOT EXISTS idx_rver_record ON record_version(record_id);

-- ============ F77 病历修改留痕（一次留痕可多行 field_path，按 version_no 归组）============
CREATE TABLE IF NOT EXISTS record_amendment (
    id           INTEGER PRIMARY KEY,
    record_id    INTEGER NOT NULL REFERENCES record(id),
    version_no   INTEGER NOT NULL,           -- 同一次修改产生的版本号（归组用）
    field_path   TEXT NOT NULL,              -- 如 chief_complaint.content
    before_value TEXT,
    after_value  TEXT,
    reason       TEXT NOT NULL,              -- 改了什么 + 为什么（R2 必填）
    operator_id  INTEGER,
    operator_role TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ramend_record ON record_amendment(record_id, version_no);

-- ============ F78 病历关闭（标记，不新增契约状态）============
CREATE TABLE IF NOT EXISTS record_closure (
    id             INTEGER PRIMARY KEY,
    record_id      INTEGER NOT NULL REFERENCES record(id),
    summary        TEXT,                     -- 关闭小结（如"疗程结束"）
    closed_by      INTEGER,
    closed_role    TEXT,
    closed_at      TEXT NOT NULL,
    idempotent_key TEXT UNIQUE,
    created_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rclose ON record_closure(record_id);

-- ============ F79 病历归档 ============
CREATE TABLE IF NOT EXISTS record_archive (
    id             INTEGER PRIMARY KEY,
    archive_no     TEXT NOT NULL UNIQUE,     -- AR{yyyyMM}{4位}（03 §1.2）
    record_id      INTEGER NOT NULL REFERENCES record(id),
    archive_month  TEXT NOT NULL,            -- yyyyMM
    version_no     INTEGER NOT NULL,         -- 归档时固化的版本号（R4）
    closed_flag    INTEGER NOT NULL DEFAULT 0,  -- 归档时是否已关闭（R3 提醒标记）
    hash_verified  INTEGER NOT NULL DEFAULT 0,
    archived_by    INTEGER,
    archived_role  TEXT,
    archived_at    TEXT NOT NULL,
    idempotent_key TEXT UNIQUE,
    remark         TEXT,
    created_at     TEXT
);
-- 解封后允许**重新归档**（开新归档号，S3 闭环），故 record_id 不唯一
DROP INDEX IF EXISTS ux_rarch;
CREATE INDEX IF NOT EXISTS idx_rarch_record ON record_archive(record_id);
CREATE INDEX IF NOT EXISTS idx_rarch_month ON record_archive(archive_month);

-- ============ F80 Hash 链（只增不改，修补即掩盖篡改）============
CREATE TABLE IF NOT EXISTS record_hash (
    id          INTEGER PRIMARY KEY,
    record_id   INTEGER NOT NULL REFERENCES record(id),
    version_no  INTEGER NOT NULL,
    algorithm   TEXT NOT NULL DEFAULT 'SHA256',
    prev_hash   TEXT NOT NULL DEFAULT '',    -- 链首为空串（R3）
    hash        TEXT NOT NULL,
    created_at  TEXT,
    created_by  INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rhash ON record_hash(record_id, version_no);

-- ============ F81 封存 ============
CREATE TABLE IF NOT EXISTS record_seal (
    id             INTEGER PRIMARY KEY,
    record_id      INTEGER NOT NULL REFERENCES record(id),
    archive_no     TEXT,
    reason         TEXT,
    sealed_by      INTEGER,
    sealed_role    TEXT,
    sealed_at      TEXT NOT NULL,
    idempotent_key TEXT UNIQUE,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_rseal_record ON record_seal(record_id);

-- ============ F82 解封（高风险，与封存成对留存）============
CREATE TABLE IF NOT EXISTS record_unseal (
    id             INTEGER PRIMARY KEY,
    record_id      INTEGER NOT NULL REFERENCES record(id),
    archive_no     TEXT,
    reason         TEXT NOT NULL,            -- R2 必填
    unsealed_by    INTEGER,
    unsealed_role  TEXT,
    unsealed_at    TEXT NOT NULL,
    idempotent_key TEXT UNIQUE,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runseal_record ON record_unseal(record_id);

-- ============ F83 导出（只记事件，不落内容副本 R-M11-8）============
CREATE TABLE IF NOT EXISTS record_export (
    id             INTEGER PRIMARY KEY,
    record_id      INTEGER NOT NULL REFERENCES record(id),
    version_no     INTEGER NOT NULL,         -- 内容取自该版本快照（R3）
    format         TEXT NOT NULL,            -- PDF / HTML / JSON
    target_path    TEXT NOT NULL,
    file_size      INTEGER,
    hash_verified  INTEGER NOT NULL DEFAULT 0,
    exported_by    INTEGER,
    exported_role  TEXT,
    exported_at    TEXT NOT NULL,
    idempotent_key TEXT UNIQUE,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_rexp_record ON record_export(record_id);

-- ============ 硬约束：04 §3 只增不改（篡改路径一律封死）============
CREATE TRIGGER IF NOT EXISTS trg_record_version_no_update
BEFORE UPDATE ON record_version
BEGIN SELECT RAISE(ABORT, 'E2006: record_version 只增不改，更正请新建版本'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_version_no_delete
BEFORE DELETE ON record_version
BEGIN SELECT RAISE(ABORT, 'E2006: record_version 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_hash_no_update
BEFORE UPDATE ON record_hash
BEGIN SELECT RAISE(ABORT, 'E2006: record_hash 只增不改，禁止修补历史 Hash'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_hash_no_delete
BEFORE DELETE ON record_hash
BEGIN SELECT RAISE(ABORT, 'E2006: record_hash 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_export_no_update
BEFORE UPDATE ON record_export
BEGIN SELECT RAISE(ABORT, 'E2006: record_export 只增不改'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_export_no_delete
BEFORE DELETE ON record_export
BEGIN SELECT RAISE(ABORT, 'E2006: record_export 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_amendment_no_update
BEFORE UPDATE ON record_amendment
BEGIN SELECT RAISE(ABORT, 'E2006: record_amendment 只增不改'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_amendment_no_delete
BEFORE DELETE ON record_amendment
BEGIN SELECT RAISE(ABORT, 'E2006: record_amendment 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_closure_no_update
BEFORE UPDATE ON record_closure
BEGIN SELECT RAISE(ABORT, 'E2006: record_closure 只增不改（关闭不可撤销）'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_closure_no_delete
BEFORE DELETE ON record_closure
BEGIN SELECT RAISE(ABORT, 'E2006: record_closure 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_archive_no_update
BEFORE UPDATE ON record_archive
BEGIN SELECT RAISE(ABORT, 'E2006: record_archive 只增不改'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_archive_no_delete
BEFORE DELETE ON record_archive
BEGIN SELECT RAISE(ABORT, 'E2006: record_archive 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_seal_no_update
BEFORE UPDATE ON record_seal
BEGIN SELECT RAISE(ABORT, 'E2006: record_seal 只增不改（封存痕迹永久保留）'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_seal_no_delete
BEFORE DELETE ON record_seal
BEGIN SELECT RAISE(ABORT, 'E2006: record_seal 禁止删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_unseal_no_update
BEFORE UPDATE ON record_unseal
BEGIN SELECT RAISE(ABORT, 'E2006: record_unseal 只增不改'); END;

CREATE TRIGGER IF NOT EXISTS trg_record_unseal_no_delete
BEFORE DELETE ON record_unseal
BEGIN SELECT RAISE(ABORT, 'E2006: record_unseal 禁止删除'); END;

-- ============ 配置 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('record.export_dir', 'data/exports', 'STRING', 'M11', '病历导出目录（相对项目根，禁止路径穿越）', datetime('now','localtime')),
    ('record.hash_algorithm', 'SHA256', 'STRING', 'M11', '病历 Hash 链算法（不可变更）', datetime('now','localtime'));
