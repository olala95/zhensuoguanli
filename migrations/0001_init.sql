-- 0001_init · M14 系统管理 + M12 安全审计
-- 契约：03_GLOBAL_CONTRACTS §5（审计字段标准）、04_DATA_MODEL §2/§3
-- 第一阶段地基：编号、配置、用户角色、会话、迁移、字典、健康检查、诊断、导入任务 + 6 张审计表

-- ============ M14 系统管理 ============

CREATE TABLE IF NOT EXISTS sys_config (
    id          INTEGER PRIMARY KEY,
    cfg_key     TEXT NOT NULL UNIQUE,
    cfg_value   TEXT,
    cfg_type    TEXT NOT NULL DEFAULT 'STRING',  -- STRING/INT/BOOL/JSON
    module      TEXT,
    description TEXT,
    is_secret   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS sys_role (
    id          INTEGER PRIMARY KEY,
    role_code   TEXT NOT NULL UNIQUE,   -- DOCTOR/PHARMACIST/CASHIER/ADMIN
    role_name   TEXT NOT NULL,
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS sys_user (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    password_salt   TEXT NOT NULL,
    employee_no     TEXT,
    title           TEXT,               -- 职称（医师/药师等）
    is_active       INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    last_login_at   TEXT,
    pwd_changed_at  TEXT,
    created_at      TEXT,
    created_by      INTEGER,
    updated_at      TEXT,
    updated_by      INTEGER,
    deleted_at      TEXT
);

CREATE TABLE IF NOT EXISTS sys_user_role (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES sys_user(id),
    role_code  TEXT NOT NULL REFERENCES sys_role(role_code),
    created_at TEXT,
    created_by INTEGER,
    deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_role ON sys_user_role(user_id, role_code);

CREATE TABLE IF NOT EXISTS sys_session (
    id           INTEGER PRIMARY KEY,
    token        TEXT NOT NULL UNIQUE,
    user_id      INTEGER NOT NULL REFERENCES sys_user(id),
    role_code    TEXT NOT NULL,        -- 登录时选择的当前角色
    status       TEXT NOT NULL,        -- ACTIVE/LOCKED/LOGGED_OUT/EXPIRED
    login_at     TEXT NOT NULL,
    last_seen_at TEXT,
    locked_at    TEXT,
    expires_at   TEXT,
    idle_timeout INTEGER NOT NULL DEFAULT 900,
    client_info  TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_session_user ON sys_session(user_id, status);

CREATE TABLE IF NOT EXISTS sys_sequence (
    id          INTEGER PRIMARY KEY,
    biz_date    TEXT NOT NULL,
    seq_type    TEXT NOT NULL,
    current_val INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sequence ON sys_sequence(biz_date, seq_type);

CREATE TABLE IF NOT EXISTS sys_migration (
    id           INTEGER PRIMARY KEY,
    seq_no       TEXT NOT NULL UNIQUE,
    filename     TEXT NOT NULL,
    checksum     TEXT NOT NULL,
    executed_at  TEXT NOT NULL,
    execution_ms INTEGER,
    executed_by  INTEGER
);

CREATE TABLE IF NOT EXISTS sys_dict (
    id         INTEGER PRIMARY KEY,
    dict_type  TEXT NOT NULL,
    dict_code  TEXT NOT NULL,
    dict_label TEXT NOT NULL,
    parent_code TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1,
    remark     TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dict ON sys_dict(dict_type, dict_code);

CREATE TABLE IF NOT EXISTS sys_health_check (
    id          INTEGER PRIMARY KEY,
    check_item  TEXT NOT NULL,
    result      TEXT NOT NULL,          -- OK/WARN/FAIL
    detail      TEXT,
    duration_ms INTEGER,
    checked_at  TEXT NOT NULL,
    checked_by  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_health_time ON sys_health_check(checked_at);

CREATE TABLE IF NOT EXISTS sys_diagnostic (
    id          INTEGER PRIMARY KEY,
    diag_type   TEXT NOT NULL,          -- DB/DISK/BACKUP/PERMISSION/RUNTIME
    target      TEXT,
    result      TEXT NOT NULL,          -- OK/WARN/FAIL
    detail_json TEXT,
    created_at  TEXT NOT NULL,
    created_by  INTEGER
);

CREATE TABLE IF NOT EXISTS sys_import_job (
    id           INTEGER PRIMARY KEY,
    job_no       TEXT NOT NULL UNIQUE,
    data_type    TEXT NOT NULL,         -- DRUG/HERB/CHARGE_ITEM/ICD10/TREATMENT
    source_file  TEXT,
    total_rows   INTEGER NOT NULL DEFAULT 0,
    success_rows INTEGER NOT NULL DEFAULT 0,
    failed_rows  INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,         -- PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED
    error_file   TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    created_at   TEXT,
    created_by   INTEGER
);

-- 通用状态流转日志：03 §2.1 要求每次状态转换留痕
CREATE TABLE IF NOT EXISTS sys_status_log (
    id          INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    operator_id INTEGER,
    role_code   TEXT,
    occurred_at TEXT NOT NULL,
    reason      TEXT,
    operation_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sys_status_entity ON sys_status_log(entity_type, entity_id);

-- ============ M12 安全审计（只允许 INSERT） ============

CREATE TABLE IF NOT EXISTS audit_operation (
    id           INTEGER PRIMARY KEY,
    occurred_at  TEXT NOT NULL,
    module       TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    TEXT,
    user_id      INTEGER NOT NULL,
    role_code    TEXT,
    source       TEXT NOT NULL,          -- UI/AI/HARDWARE/SYSTEM
    result       TEXT NOT NULL,          -- SUCCESS/FAIL
    error_code   TEXT,
    request_id   TEXT,
    session_id   INTEGER,
    client_info  TEXT,
    detail_json  TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_op_time ON audit_operation(occurred_at);
CREATE INDEX IF NOT EXISTS ix_audit_op_entity ON audit_operation(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_op_user ON audit_operation(user_id, occurred_at);

CREATE TABLE IF NOT EXISTS audit_data_change (
    id           INTEGER PRIMARY KEY,
    occurred_at  TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    record_id    TEXT,
    field_name   TEXT NOT NULL,
    before_value TEXT,
    after_value  TEXT,
    user_id      INTEGER NOT NULL,
    role_code    TEXT,
    source       TEXT,
    reason       TEXT,
    operation_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_audit_dc_table ON audit_data_change(table_name, record_id);

CREATE TABLE IF NOT EXISTS audit_high_risk (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    risk_type   TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    user_id     INTEGER NOT NULL,
    role_code   TEXT,
    source      TEXT,
    reason      TEXT,
    detail_json TEXT,
    operation_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_audit_hr_time ON audit_high_risk(occurred_at);

CREATE TABLE IF NOT EXISTS audit_login (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    event_type  TEXT NOT NULL,   -- LOGIN/LOGOUT/LOCK/UNLOCK/LOGIN_FAILED/SESSION_EXPIRED
    user_id     INTEGER,
    username    TEXT,
    role_code   TEXT,
    result      TEXT NOT NULL,
    fail_reason TEXT,
    client_info TEXT,
    session_id  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_audit_login_time ON audit_login(occurred_at);

CREATE TABLE IF NOT EXISTS audit_time_anomaly (
    id            INTEGER PRIMARY KEY,
    detected_at   TEXT NOT NULL,
    anomaly_type  TEXT NOT NULL,        -- BACKWARD/FORWARD_JUMP
    last_known_at TEXT,
    current_at    TEXT,
    diff_seconds  REAL,
    detail_json   TEXT
);

CREATE TABLE IF NOT EXISTS audit_permission (
    id                  INTEGER PRIMARY KEY,
    occurred_at         TEXT NOT NULL,
    user_id             INTEGER NOT NULL,
    role_code           TEXT,
    required_permission TEXT NOT NULL,
    resource            TEXT,
    action              TEXT,
    result              TEXT NOT NULL,   -- DENIED
    detail_json         TEXT,
    request_id          TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_perm_time ON audit_permission(occurred_at);

-- 审计表 INSERT-ONLY 触发器（04 §3 硬约束，应用层二次校验之外的数据库兜底）
CREATE TRIGGER IF NOT EXISTS trg_audit_operation_no_update
BEFORE UPDATE ON audit_operation BEGIN SELECT RAISE(ABORT, 'E2006: audit_operation 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_operation_no_delete
BEFORE DELETE ON audit_operation BEGIN SELECT RAISE(ABORT, 'E2006: audit_operation 只允许 INSERT'); END;

CREATE TRIGGER IF NOT EXISTS trg_audit_data_change_no_update
BEFORE UPDATE ON audit_data_change BEGIN SELECT RAISE(ABORT, 'E2006: audit_data_change 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_data_change_no_delete
BEFORE DELETE ON audit_data_change BEGIN SELECT RAISE(ABORT, 'E2006: audit_data_change 只允许 INSERT'); END;

CREATE TRIGGER IF NOT EXISTS trg_audit_high_risk_no_update
BEFORE UPDATE ON audit_high_risk BEGIN SELECT RAISE(ABORT, 'E2006: audit_high_risk 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_high_risk_no_delete
BEFORE DELETE ON audit_high_risk BEGIN SELECT RAISE(ABORT, 'E2006: audit_high_risk 只允许 INSERT'); END;

CREATE TRIGGER IF NOT EXISTS trg_audit_login_no_update
BEFORE UPDATE ON audit_login BEGIN SELECT RAISE(ABORT, 'E2006: audit_login 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_login_no_delete
BEFORE DELETE ON audit_login BEGIN SELECT RAISE(ABORT, 'E2006: audit_login 只允许 INSERT'); END;

CREATE TRIGGER IF NOT EXISTS trg_audit_time_anomaly_no_update
BEFORE UPDATE ON audit_time_anomaly BEGIN SELECT RAISE(ABORT, 'E2006: audit_time_anomaly 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_time_anomaly_no_delete
BEFORE DELETE ON audit_time_anomaly BEGIN SELECT RAISE(ABORT, 'E2006: audit_time_anomaly 只允许 INSERT'); END;

CREATE TRIGGER IF NOT EXISTS trg_audit_permission_no_update
BEFORE UPDATE ON audit_permission BEGIN SELECT RAISE(ABORT, 'E2006: audit_permission 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_permission_no_delete
BEFORE DELETE ON audit_permission BEGIN SELECT RAISE(ABORT, 'E2006: audit_permission 只允许 INSERT'); END;

-- ============ 初始数据 ============

INSERT OR IGNORE INTO sys_role (role_code, role_name, description, created_at) VALUES
    ('DOCTOR',     '医生',   '接诊、写病历、开处方（含中药）、开处置与检验', datetime('now','localtime')),
    ('PHARMACIST', '药师',   '发药、退药、库存管理、盘点、报损',           datetime('now','localtime')),
    ('CASHIER',    '收费员', '收费、退款、日结',                          datetime('now','localtime')),
    ('ADMIN',      '管理员', '用户角色、系统配置、备份恢复；不可修改已确认病历', datetime('now','localtime'));

-- 中药相关字典（03 §8 / F111）
INSERT OR IGNORE INTO sys_dict (dict_type, dict_code, dict_label, sort_order, created_at) VALUES
    ('HERB_FOOTNOTE', 'XIANJIAN', '先煎', 1, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'HOUXIA',   '后下', 2, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'BAOJIAN',  '包煎', 3, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'YANGHUA',  '烊化', 4, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'CHONGFU',  '冲服', 5, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'LINGJIAN', '另煎', 6, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'ZHIJIAN',  '炙煎', 7, datetime('now','localtime')),
    ('DECOCTION',     'ZIJIAN',   '自煎', 1, datetime('now','localtime')),
    ('DECOCTION',     'DAIJIAN',  '代煎', 2, datetime('now','localtime')),
    ('DECOCTION',     'KELI',     '颗粒', 3, datetime('now','localtime')),
    ('VISIT_TYPE',    'NORMAL',   '普通门诊', 1, datetime('now','localtime')),
    ('VISIT_TYPE',    'TCM',      '中医门诊', 2, datetime('now','localtime')),
    ('VISIT_TYPE',    'EMERG',    '急诊',    3, datetime('now','localtime')),
    ('GENDER',        'M',        '男', 1, datetime('now','localtime')),
    ('GENDER',        'F',        '女', 2, datetime('now','localtime')),
    ('GENDER',        'U',        '未知', 3, datetime('now','localtime'));

-- 默认配置（F101 / 03 §8 中药默认规则）
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('clinic.name',            '未命名诊所', 'STRING', 'M14', '诊所名称', datetime('now','localtime')),
    ('clinic.address',         '',           'STRING', 'M14', '诊所地址', datetime('now','localtime')),
    ('clinic.phone',           '',           'STRING', 'M14', '联系电话', datetime('now','localtime')),
    ('herb.default_doses',     '7',          'INT',    'M06', '中药默认付数', datetime('now','localtime')),
    ('herb.decoct_fee_per_dose','0',         'INT',    'M06', '代煎费（分/付），0 表示按处方计', datetime('now','localtime')),
    ('herb.decoct_fee_per_rx', '0',          'INT',    'M06', '代煎费（分/处方）', datetime('now','localtime')),
    ('herb.package_fee',       '0',          'INT',    'M06', '包装费（分/处方）', datetime('now','localtime')),
    ('herb.loss_rate',         '0',          'INT',    'M07', '饮片调剂损耗率（千分比）', datetime('now','localtime')),
    ('session.idle_timeout',   '900',        'INT',    'M14', '空闲锁屏秒数', datetime('now','localtime')),
    ('login.max_attempts',     '5',          'INT',    'M14', '登录失败锁定阈值', datetime('now','localtime')),
    ('backup.dir',             '',           'STRING', 'M13', '备份目录（空=默认 backups/）', datetime('now','localtime')),
    ('backup.retention_days',  '30',         'INT',    'M13', '全量备份保留天数', datetime('now','localtime')),
    ('backup.on_exit',         '1',          'BOOL',   'M13', '退出时自动全量备份', datetime('now','localtime')),
    ('ui.print_preview',       '1',          'BOOL',   'M10', '打印前预览', datetime('now','localtime'));
