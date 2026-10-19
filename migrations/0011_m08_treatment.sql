-- 0011 M08 处置执行
-- 契约：03 §1.2（TE{yyyyMMdd}{4位}）、§2.11（处置执行状态机）、§3.1（行小计舍入）
--       04 §2 表所有权：M08
-- 与 M06 边界：药品只走处方（F34/F111），本模块只承载非药品操作

-- ============ F52 处置项目 ============
CREATE TABLE IF NOT EXISTS treatment_item (
    id          INTEGER PRIMARY KEY,
    item_code   TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    alias       TEXT,
    pinyin      TEXT,
    category    TEXT NOT NULL DEFAULT 'OTHER',
                -- INJECTION/DRESSING/NEBULIZE/PHYSIO/TCM/EXAM/OTHER
    unit        TEXT NOT NULL DEFAULT '次',
    price_fen   INTEGER NOT NULL DEFAULT 0,     -- 单价（分）
    need_record INTEGER NOT NULL DEFAULT 0,     -- 完成时是否必须填写执行记录
    is_tcm      INTEGER NOT NULL DEFAULT 0,     -- 中医操作：需 F116 结构化记录
    duration_min INTEGER,                       -- 参考耗时（分钟）
    remark      TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_titem_name ON treatment_item(name);
CREATE INDEX IF NOT EXISTS idx_titem_cat ON treatment_item(category);

-- ============ F53 处置组套 ============
CREATE TABLE IF NOT EXISTS treatment_package (
    id          INTEGER PRIMARY KEY,
    package_code TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT,
    scope       TEXT NOT NULL DEFAULT 'CLINIC',   -- SYSTEM/USER/CLINIC
    owner_id    INTEGER,
    usage_count INTEGER NOT NULL DEFAULT 0,
    remark      TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS treatment_package_item (
    id            INTEGER PRIMARY KEY,
    package_id    INTEGER NOT NULL,
    item_id       INTEGER NOT NULL,
    item_name_snap TEXT NOT NULL,
    quantity      REAL NOT NULL DEFAULT 1,
    unit_snap     TEXT,
    price_fen_snap INTEGER NOT NULL DEFAULT 0,
    seq_no        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT,
    created_by    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tpkg_item ON treatment_package_item(package_id);

-- ============ F54/F55 处置执行 ============
CREATE TABLE IF NOT EXISTS treatment_execution (
    id                INTEGER PRIMARY KEY,
    exec_no           TEXT NOT NULL UNIQUE,   -- TE{yyyyMMdd}{4位}
    visit_id          INTEGER NOT NULL,
    patient_id        INTEGER,
    item_id           INTEGER NOT NULL,
    item_name_snap    TEXT NOT NULL,
    category_snap     TEXT,
    unit_snap         TEXT,
    price_fen         INTEGER NOT NULL DEFAULT 0,   -- 开单时价快照（调价不追溯）
    quantity          REAL NOT NULL DEFAULT 1,
    amount_fen        INTEGER NOT NULL DEFAULT 0,
    package_id        INTEGER,                -- 由组套展开时共享
    status            TEXT NOT NULL DEFAULT 'SCHEDULED',
                      -- SCHEDULED/IN_PROGRESS/COMPLETED/CANCELLED/INTERRUPTED（03 §2.11）
    -- 患者快照
    patient_name_snap TEXT,
    gender_snap       TEXT,
    age_text_snap     TEXT,
    -- 开立与执行
    ordered_by        INTEGER,
    ordered_at        TEXT,
    executor_id       INTEGER,
    executed_at       TEXT,
    interrupt_reason  TEXT,
    cancel_reason     TEXT,
    charge_flag       INTEGER NOT NULL DEFAULT 0,   -- 是否已进入账单（M15 埋点）
    occurred_at       TEXT,                    -- 业务发生时间（报表归集口径）
    remark            TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT,
    created_by        INTEGER,
    updated_at        TEXT,
    updated_by        INTEGER,
    deleted_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_texec_visit ON treatment_execution(visit_id);
CREATE INDEX IF NOT EXISTS idx_texec_patient ON treatment_execution(patient_id);
CREATE INDEX IF NOT EXISTS idx_texec_status ON treatment_execution(status);
CREATE INDEX IF NOT EXISTS idx_texec_time ON treatment_execution(occurred_at);

-- 状态流转日志（03 §2.1）
CREATE TABLE IF NOT EXISTS treatment_status_log (
    id          INTEGER PRIMARY KEY,
    exec_id     INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tlog_exec ON treatment_status_log(exec_id);

-- ============ F56 治疗过程（只追加）============
CREATE TABLE IF NOT EXISTS treatment_process (
    id          INTEGER PRIMARY KEY,
    exec_id     INTEGER NOT NULL,
    seq_no      INTEGER NOT NULL DEFAULT 1,
    phase       TEXT NOT NULL DEFAULT 'STEP',
                -- PRE/STEP/OBSERVE/REACTION/AFTER
    content     TEXT NOT NULL,
    is_abnormal INTEGER NOT NULL DEFAULT 0,   -- 不良反应：写高风险审计
    attachment  TEXT,                          -- 附件路径（本体不入库）
    checksum    TEXT,
    occurred_at TEXT NOT NULL,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tproc_exec ON treatment_process(exec_id);

-- ============ F116 中医治疗执行记录 ============
CREATE TABLE IF NOT EXISTS tcm_treatment_record (
    id              INTEGER PRIMARY KEY,
    exec_id         INTEGER NOT NULL,
    visit_id        INTEGER,
    patient_id      INTEGER,
    therapy_type    TEXT NOT NULL DEFAULT 'ACUP',
                    -- ACUP/TUINA/CUPPING/MOXA/GUASHA/PATCH/OTHER
    points          TEXT,          -- 穴位，逗号分隔（针灸必填）
    manipulation    TEXT,          -- 手法：补/泻/平补平泻/电针/揉法...
    retain_minutes  INTEGER,       -- 留针时长（针灸必填）
    body_part       TEXT,          -- 部位
    posture         TEXT,          -- 体位
    syndrome_snap   TEXT,          -- 证型快照（源自 M03 F109，只读）
    reaction        TEXT,          -- 治疗反应
    is_abnormal     INTEGER NOT NULL DEFAULT 0,
    performed_at    TEXT,
    operator_id     INTEGER,
    created_at      TEXT,
    created_by      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tcm_exec ON tcm_treatment_record(exec_id);
CREATE INDEX IF NOT EXISTS idx_tcm_patient ON tcm_treatment_record(patient_id);

-- ============ 硬约束：业务主表禁止物理删除（04 §3）============
CREATE TRIGGER IF NOT EXISTS trg_treatment_item_no_delete
BEFORE DELETE ON treatment_item
BEGIN SELECT RAISE(ABORT, 'E2006: treatment_item 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_treatment_exec_no_delete
BEFORE DELETE ON treatment_execution
BEGIN SELECT RAISE(ABORT, 'E2006: treatment_execution 禁止物理删除，请使用软删除'); END;

-- 治疗过程只追加不改（F56 R3）
CREATE TRIGGER IF NOT EXISTS trg_treatment_process_no_update
BEFORE UPDATE ON treatment_process
BEGIN SELECT RAISE(ABORT, 'E2006: treatment_process 只追加，不允许修改'); END;

CREATE TRIGGER IF NOT EXISTS trg_treatment_process_no_delete
BEFORE DELETE ON treatment_process
BEGIN SELECT RAISE(ABORT, 'E2006: treatment_process 只追加，不允许删除'); END;

-- ============ 字典：处置类别与中医治疗类别 ============
INSERT OR IGNORE INTO sys_dict (dict_type, dict_code, dict_label, sort_order, created_at) VALUES
    ('TREATMENT_CATEGORY', 'INJECTION', '注射', 1, datetime('now','localtime')),
    ('TREATMENT_CATEGORY', 'DRESSING',  '换药', 2, datetime('now','localtime')),
    ('TREATMENT_CATEGORY', 'NEBULIZE',  '雾化', 3, datetime('now','localtime')),
    ('TREATMENT_CATEGORY', 'PHYSIO',    '理疗', 4, datetime('now','localtime')),
    ('TREATMENT_CATEGORY', 'TCM',       '中医', 5, datetime('now','localtime')),
    ('TREATMENT_CATEGORY', 'EXAM',      '检查操作', 6, datetime('now','localtime')),
    ('TREATMENT_CATEGORY', 'OTHER',     '其他', 7, datetime('now','localtime')),
    ('TCM_THERAPY', 'ACUP',    '针灸',     1, datetime('now','localtime')),
    ('TCM_THERAPY', 'TUINA',   '推拿',     2, datetime('now','localtime')),
    ('TCM_THERAPY', 'CUPPING', '拔罐',     3, datetime('now','localtime')),
    ('TCM_THERAPY', 'MOXA',    '艾灸',     4, datetime('now','localtime')),
    ('TCM_THERAPY', 'GUASHA',  '刮痧',     5, datetime('now','localtime')),
    ('TCM_THERAPY', 'PATCH',   '穴位贴敷', 6, datetime('now','localtime')),
    ('TCM_THERAPY', 'OTHER',   '其他',     7, datetime('now','localtime')),
    ('PROCESS_PHASE', 'PRE',      '治疗前',   1, datetime('now','localtime')),
    ('PROCESS_PHASE', 'STEP',     '操作步骤', 2, datetime('now','localtime')),
    ('PROCESS_PHASE', 'OBSERVE',  '巡视观察', 3, datetime('now','localtime')),
    ('PROCESS_PHASE', 'REACTION', '反应',     4, datetime('now','localtime')),
    ('PROCESS_PHASE', 'AFTER',    '治疗后交代', 5, datetime('now','localtime'));
