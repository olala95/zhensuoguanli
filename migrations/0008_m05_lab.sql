-- 0008 M05 检验检查（全部外送）
-- 契约：03 §1.2 检验申请号 L{yyyyMMdd}{4位}、标本条码 BC{yyyyMMdd}{6位}
--       03 §2.9 状态机 DRAFT→ORDERED→SAMPLED→DELIVERED→REPORTED→REVIEWED
--       04 §2  表所有权：M05
-- 注：lab_order_item 与 lab_order_status_log 为本次新增（04 §2 清单回补见 04_DATA_MODEL）

-- ============ 外送机构 ============
CREATE TABLE IF NOT EXISTS lab_agency (
    id               INTEGER PRIMARY KEY,
    agency_code      TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    contact          TEXT,
    phone            TEXT,
    address          TEXT,
    settlement_cycle TEXT NOT NULL DEFAULT 'MONTHLY',  -- MONTHLY 月结 / PER_ORDER 单次
    is_active        INTEGER NOT NULL DEFAULT 1,
    remark           TEXT,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);

-- ============ F27 外送项目目录 ============
CREATE TABLE IF NOT EXISTS lab_item (
    id               INTEGER PRIMARY KEY,
    agency_id        INTEGER,
    item_code        TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    short_name       TEXT,
    category         TEXT,                    -- 检验/病理/影像/功能
    specimen_type    TEXT,                    -- 血/尿/便/分泌物/组织
    container        TEXT,                    -- 采血管类型
    price_fen        INTEGER NOT NULL DEFAULT 0,   -- 对患者收费价（分）
    settle_price_fen INTEGER NOT NULL DEFAULT 0,   -- 与机构结算价（分）
    report_days      INTEGER,                 -- 报告周期（天）
    is_active        INTEGER NOT NULL DEFAULT 1,
    remark           TEXT,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_item_name ON lab_item(name, short_name);
CREATE INDEX IF NOT EXISTS idx_lab_item_agency ON lab_item(agency_id);

-- ============ F28 检查申请单 ============
CREATE TABLE IF NOT EXISTS lab_order (
    id            INTEGER PRIMARY KEY,
    order_no      TEXT NOT NULL UNIQUE,        -- L{yyyyMMdd}{4位}
    visit_id      INTEGER NOT NULL,
    patient_id    INTEGER NOT NULL,
    record_id     INTEGER,
    agency_id     INTEGER,
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    version       INTEGER NOT NULL DEFAULT 1,
    patient_name_snap TEXT,
    gender_snap   TEXT,
    birth_date_snap TEXT,
    age_text_snap TEXT,
    diag_summary  TEXT,
    clinical_note TEXT,                        -- 临床诊断/送检目的
    total_amount  INTEGER NOT NULL DEFAULT 0,  -- 收费合计（分）
    settle_amount INTEGER NOT NULL DEFAULT 0,  -- 结算合计（分）
    source        TEXT NOT NULL DEFAULT 'DOCTOR',
    occurred_at   TEXT,
    cancelled_reason TEXT,
    reviewed_by   INTEGER,
    reviewed_at   TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    updated_at    TEXT,
    updated_by    INTEGER,
    deleted_at    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_lab_order_no ON lab_order(order_no);
CREATE INDEX IF NOT EXISTS idx_lab_order_visit ON lab_order(visit_id);
CREATE INDEX IF NOT EXISTS idx_lab_order_patient ON lab_order(patient_id);
CREATE INDEX IF NOT EXISTS idx_lab_order_status ON lab_order(status);

CREATE TABLE IF NOT EXISTS lab_order_status_log (
    id          INTEGER PRIMARY KEY,
    lab_order_id INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_order_log ON lab_order_status_log(lab_order_id);

-- 申请明细（一个申请单多个项目）
CREATE TABLE IF NOT EXISTS lab_order_item (
    id              INTEGER PRIMARY KEY,
    lab_order_id    INTEGER NOT NULL,
    line_no         INTEGER NOT NULL DEFAULT 1,
    lab_item_id     INTEGER NOT NULL,
    item_name_snap  TEXT NOT NULL,
    specimen_type   TEXT,
    price_fen       INTEGER NOT NULL DEFAULT 0,
    settle_price_fen INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT,
    created_by      INTEGER,
    updated_at      TEXT,
    updated_by      INTEGER,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_order_item ON lab_order_item(lab_order_id);

-- ============ F29 标本与条码 ============
CREATE TABLE IF NOT EXISTS lab_specimen (
    id             INTEGER PRIMARY KEY,
    lab_order_id   INTEGER NOT NULL,
    lab_order_item_id INTEGER,
    barcode        TEXT NOT NULL UNIQUE,       -- BC{yyyyMMdd}{6位}
    specimen_type  TEXT,
    container      TEXT,
    collected_at   TEXT,
    collected_by   INTEGER,
    status         TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/COLLECTED/DELIVERED/REJECTED
    reject_reason  TEXT,
    remark         TEXT,
    created_at     TEXT,
    created_by     INTEGER,
    updated_at     TEXT,
    updated_by     INTEGER,
    deleted_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_specimen_barcode ON lab_specimen(barcode);
CREATE INDEX IF NOT EXISTS idx_specimen_order ON lab_specimen(lab_order_id);

-- ============ F30 结果回录 ============
CREATE TABLE IF NOT EXISTS lab_result (
    id                INTEGER PRIMARY KEY,
    lab_order_id      INTEGER NOT NULL,
    lab_order_item_id INTEGER,
    lab_item_id       INTEGER,
    item_name         TEXT NOT NULL,
    result_text       TEXT,                    -- 结果值文本
    unit              TEXT,
    ref_range         TEXT,
    abnormal_flag     TEXT,                    -- NORMAL/HIGH/LOW/CRITICAL/ABNORMAL
    result_summary    TEXT,                    -- 结论/提示
    source            TEXT NOT NULL DEFAULT 'MANUAL',  -- MANUAL 手工回录 / AGENCY 机构回传
    entered_by        INTEGER,
    entered_at        TEXT,
    reported_at       TEXT,                    -- 机构出报告时间
    created_at        TEXT,
    created_by        INTEGER,
    updated_at        TEXT,
    updated_by        INTEGER,
    deleted_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_result_order ON lab_result(lab_order_id);

CREATE TABLE IF NOT EXISTS lab_result_attachment (
    id           INTEGER PRIMARY KEY,
    lab_result_id INTEGER,
    lab_order_id INTEGER NOT NULL,
    file_path    TEXT NOT NULL,
    file_name    TEXT,
    file_size    INTEGER,
    mime_type    TEXT,
    uploaded_by  INTEGER,
    created_at   TEXT,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_attach_order ON lab_result_attachment(lab_order_id);

-- ============ F117 外送送检与回报登记 ============
CREATE TABLE IF NOT EXISTS lab_delivery (
    id               INTEGER PRIMARY KEY,
    lab_order_id     INTEGER NOT NULL,
    agency_id        INTEGER,
    batch_no         TEXT,                     -- 机构交接批次号
    delivered_at     TEXT,
    delivered_by     INTEGER,
    courier          TEXT,
    expected_report_at TEXT,
    returned_at      TEXT,
    returned_by      INTEGER,
    receiver         TEXT,
    status           TEXT NOT NULL DEFAULT 'DELIVERED',  -- DELIVERED/RETURNED/LOST
    remark           TEXT,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_delivery_order ON lab_delivery(lab_order_id);
CREATE INDEX IF NOT EXISTS idx_lab_delivery_agency ON lab_delivery(agency_id);

-- ============ F118 机构月度对账 ============
CREATE TABLE IF NOT EXISTS lab_reconciliation (
    id            INTEGER PRIMARY KEY,
    agency_id     INTEGER NOT NULL,
    period        TEXT NOT NULL,               -- YYYY-MM
    order_count   INTEGER NOT NULL DEFAULT 0,
    charged_fen   INTEGER NOT NULL DEFAULT 0,  -- 向患者收费合计
    settle_fen    INTEGER NOT NULL DEFAULT 0,  -- 应付机构合计
    diff_fen      INTEGER NOT NULL DEFAULT 0,  -- 差额（毛利）
    status        TEXT NOT NULL DEFAULT 'DRAFT',   -- DRAFT/CONFIRMED/SETTLED
    settled_at    TEXT,
    settled_by    INTEGER,
    note          TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    updated_at    TEXT,
    updated_by    INTEGER,
    deleted_at    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_recon ON lab_reconciliation(agency_id, period);

-- ============ 硬约束：禁止物理删除 ============
CREATE TRIGGER IF NOT EXISTS trg_lab_order_no_delete
BEFORE DELETE ON lab_order
BEGIN SELECT RAISE(ABORT, 'E2006: lab_order 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_lab_result_no_delete
BEFORE DELETE ON lab_result
BEGIN SELECT RAISE(ABORT, 'E2006: lab_result 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_lab_specimen_no_delete
BEFORE DELETE ON lab_specimen
BEGIN SELECT RAISE(ABORT, 'E2006: lab_specimen 禁止物理删除，请使用软删除'); END;

-- ============ 初始数据：示例外送机构 ============
INSERT OR IGNORE INTO lab_agency (agency_code, name, settlement_cycle, is_active, created_at)
VALUES ('DEFAULT', '默认外送机构（待维护）', 'MONTHLY', 1, datetime('now','localtime'));
