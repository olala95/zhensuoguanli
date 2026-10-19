-- 0010 M07 药房与库存
-- 契约：03 §1.2（IN/BT/CK/LS/DR 编号）、§2.8（批次状态机）、§2.12（盘点状态机）
--       §3.3（拆零尾差）、§3（金额整数分）、§8（饮片单位克、出库量=剂量×付数）
--       04 §2 表所有权：M07
-- 两条链路：成药（stock，按 drug+batch）/ 饮片（herb_stock，按 herb+batch，单位 g）

-- ============ F40 供应商 ============
CREATE TABLE IF NOT EXISTS supplier (
    id           INTEGER PRIMARY KEY,
    supplier_code TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    contact_name TEXT,
    phone        TEXT,
    address      TEXT,
    license_no   TEXT,
    note         TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);

-- ============ F40 入库单 ============
CREATE TABLE IF NOT EXISTS stock_in (
    id              INTEGER PRIMARY KEY,
    stock_in_no     TEXT NOT NULL UNIQUE,        -- IN{yyyyMMdd}{4位}
    supplier_id     INTEGER REFERENCES supplier(id),
    invoice_no      TEXT,
    in_type         TEXT NOT NULL DEFAULT 'PURCHASE',  -- PURCHASE/RETURN_IN/INITIAL/ADJUST
    status          TEXT NOT NULL DEFAULT 'DRAFT',     -- DRAFT/CONFIRMED/VOID
    line_count      INTEGER NOT NULL DEFAULT 0,
    total_amount_fen INTEGER NOT NULL DEFAULT 0,
    occurred_at     TEXT,
    confirmed_by    INTEGER,
    confirmed_at    TEXT,
    void_reason     TEXT,
    remark          TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT,
    created_by      INTEGER,
    updated_at      TEXT,
    updated_by      INTEGER,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_stockin_status ON stock_in(status);
CREATE INDEX IF NOT EXISTS idx_stockin_time ON stock_in(occurred_at);

-- ============ F41 批次（一个入库明细行 = 一个批次）============
CREATE TABLE IF NOT EXISTS stock_batch (
    id               INTEGER PRIMARY KEY,
    batch_no         TEXT NOT NULL UNIQUE,       -- BT{yyyyMMdd}{6位}（系统内码）
    stock_in_id      INTEGER REFERENCES stock_in(id),
    line_no          INTEGER NOT NULL DEFAULT 1, -- 明细行定位（03 §1.3）
    item_kind        TEXT NOT NULL DEFAULT 'DRUG',  -- DRUG / HERB
    drug_id          INTEGER,
    herb_id          INTEGER,
    supplier_batch_no TEXT,                      -- 供应商原批号，原样保存
    produce_date     TEXT,
    expiry_date      TEXT NOT NULL,
    unit             TEXT NOT NULL DEFAULT 'UNIT',  -- 成药单位；饮片固定 'g'
    pack_qty         INTEGER NOT NULL DEFAULT 1,    -- 包装含量（拆零用）
    unit_cost_milli  INTEGER NOT NULL DEFAULT 0,    -- 单位成本（毫元）
    unit_price_milli INTEGER NOT NULL DEFAULT 0,    -- 单位零售价（毫元）
    qty_in           REAL NOT NULL DEFAULT 0,
    qty_remain       REAL NOT NULL DEFAULT 0,
    is_split         INTEGER NOT NULL DEFAULT 0,    -- 是否拆零产生的零头批次
    parent_batch_id  INTEGER REFERENCES stock_batch(id),
    status           TEXT NOT NULL DEFAULT 'NORMAL',  -- NORMAL/DEPLETED/EXPIRED/LOCKED
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_line ON stock_batch(stock_in_id, line_no);
CREATE INDEX IF NOT EXISTS idx_batch_drug ON stock_batch(item_kind, drug_id);
CREATE INDEX IF NOT EXISTS idx_batch_herb ON stock_batch(item_kind, herb_id);
CREATE INDEX IF NOT EXISTS idx_batch_expiry ON stock_batch(expiry_date);
CREATE INDEX IF NOT EXISTS idx_batch_status ON stock_batch(status);

-- 批次状态流转日志（03 §2.1）
CREATE TABLE IF NOT EXISTS stock_batch_status_log (
    id          INTEGER PRIMARY KEY,
    batch_id    INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_batchlog_batch ON stock_batch_status_log(batch_id);

-- ============ F42 成药库存（药品 + 批次）============
CREATE TABLE IF NOT EXISTS stock (
    id         INTEGER PRIMARY KEY,
    drug_id    INTEGER NOT NULL,
    batch_id   INTEGER NOT NULL,
    qty        REAL NOT NULL DEFAULT 0,
    unit       TEXT,
    version    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    created_by INTEGER,
    updated_at TEXT,
    updated_by INTEGER,
    deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_stock ON stock(drug_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_stock_drug ON stock(drug_id);

-- ============ F114 饮片库存（饮片 + 批次，单位 g）============
CREATE TABLE IF NOT EXISTS herb_stock (
    id         INTEGER PRIMARY KEY,
    herb_id    INTEGER NOT NULL,
    batch_id   INTEGER NOT NULL,
    qty_g      REAL NOT NULL DEFAULT 0,     -- 契约 §8：恒为克
    version    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    created_by INTEGER,
    updated_at TEXT,
    updated_by INTEGER,
    deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_herb_stock ON herb_stock(herb_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_herb_stock_herb ON herb_stock(herb_id);

-- ============ F43 库存流水（只允许 INSERT）============
CREATE TABLE IF NOT EXISTS stock_transaction (
    id              INTEGER PRIMARY KEY,
    trans_type      TEXT NOT NULL,   -- IN/OUT/RETURN_IN/LOSS/CHECK_ADJUST/SPLIT_OUT/SPLIT_IN
    item_kind       TEXT NOT NULL,   -- DRUG / HERB
    drug_id         INTEGER,
    herb_id         INTEGER,
    batch_id        INTEGER NOT NULL,
    qty_change      REAL NOT NULL,   -- 带符号：入库为正，出库为负
    balance_after   REAL NOT NULL,
    unit            TEXT,
    unit_cost_milli INTEGER NOT NULL DEFAULT 0,
    amount_fen      INTEGER NOT NULL DEFAULT 0,   -- 报表埋点（03 §7）
    ref_type        TEXT,            -- STOCK_IN/DISPENSE/HERB_DISPENSE/DRUG_RETURN/
                                     -- STOCK_LOSS/STOCK_CHECK/SPLIT
    ref_id          INTEGER,
    ref_line_id     INTEGER,
    is_toxic        INTEGER NOT NULL DEFAULT 0,
    occurred_at     TEXT NOT NULL,
    operator_id     INTEGER,
    remark          TEXT,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_st_batch ON stock_transaction(batch_id);
CREATE INDEX IF NOT EXISTS idx_st_ref ON stock_transaction(ref_type, ref_id);
CREATE INDEX IF NOT EXISTS idx_st_time ON stock_transaction(occurred_at);
CREATE INDEX IF NOT EXISTS idx_st_item ON stock_transaction(item_kind, drug_id, herb_id);

-- ============ F44/F45 拆零与尾差 ============
CREATE TABLE IF NOT EXISTS stock_split (
    id                INTEGER PRIMARY KEY,
    batch_id          INTEGER NOT NULL,   -- 原批次
    new_batch_id      INTEGER,            -- 拆出的零头批次
    pack_qty          INTEGER NOT NULL,   -- 包装含量
    pack_price_milli  INTEGER NOT NULL DEFAULT 0,
    unit_price_milli  INTEGER NOT NULL DEFAULT 0,
    tail_diff_milli   INTEGER NOT NULL DEFAULT 0,   -- 尾差显式记录（03 §3.3）
    pack_cost_milli   INTEGER NOT NULL DEFAULT 0,
    unit_cost_milli   INTEGER NOT NULL DEFAULT 0,
    tail_cost_diff_milli INTEGER NOT NULL DEFAULT 0,
    idempotent_key    TEXT UNIQUE,
    occurred_at       TEXT,
    operator_id       INTEGER,
    created_at        TEXT,
    created_by        INTEGER
);

-- ============ F46 退药 ============
CREATE TABLE IF NOT EXISTS drug_return (
    id               INTEGER PRIMARY KEY,
    return_no        TEXT NOT NULL UNIQUE,   -- DR{yyyyMMdd}{4位}
    prescription_id  INTEGER NOT NULL,
    visit_id         INTEGER,
    patient_id       INTEGER,
    status           TEXT NOT NULL DEFAULT 'DRAFT',  -- DRAFT/CONFIRMED/VOID
    line_count       INTEGER NOT NULL DEFAULT 0,
    total_amount_fen INTEGER NOT NULL DEFAULT 0,
    reason           TEXT,
    occurred_at      TEXT,
    confirmed_by     INTEGER,
    confirmed_at     TEXT,
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_dr_presc ON drug_return(prescription_id);

CREATE TABLE IF NOT EXISTS drug_return_item (
    id                      INTEGER PRIMARY KEY,
    return_id               INTEGER NOT NULL,
    line_no                 INTEGER NOT NULL DEFAULT 1,
    item_kind               TEXT NOT NULL DEFAULT 'DRUG',
    drug_id                 INTEGER,
    herb_id                 INTEGER,
    prescription_item_id    INTEGER,          -- 来源：成药明细
    herb_prescription_item_id INTEGER,        -- 来源：饮片明细
    batch_id                INTEGER,          -- 回库批次
    qty                     REAL NOT NULL DEFAULT 0,   -- 成药单位数 / 饮片克数
    unit                    TEXT,
    unit_price_milli        INTEGER NOT NULL DEFAULT 0,
    amount_fen              INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT,
    created_by              INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dri_return ON drug_return_item(return_id);

-- ============ F47 报损 ============
CREATE TABLE IF NOT EXISTS stock_loss (
    id               INTEGER PRIMARY KEY,
    loss_no          TEXT NOT NULL UNIQUE,   -- LS{yyyyMMdd}{4位}
    status           TEXT NOT NULL DEFAULT 'DRAFT',
    loss_reason      TEXT NOT NULL DEFAULT 'OTHER',  -- DAMAGE/DETERIORATE/EXPIRED/LOST/OTHER
    line_count       INTEGER NOT NULL DEFAULT 0,
    total_amount_fen INTEGER NOT NULL DEFAULT 0,
    reason           TEXT,
    occurred_at      TEXT,
    confirmed_by     INTEGER,
    confirmed_at     TEXT,
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);

CREATE TABLE IF NOT EXISTS stock_loss_item (
    id              INTEGER PRIMARY KEY,
    loss_id         INTEGER NOT NULL,
    line_no         INTEGER NOT NULL DEFAULT 1,
    item_kind       TEXT NOT NULL DEFAULT 'DRUG',
    drug_id         INTEGER,
    herb_id         INTEGER,
    batch_id        INTEGER NOT NULL,
    qty             REAL NOT NULL DEFAULT 0,
    unit            TEXT,
    unit_cost_milli INTEGER NOT NULL DEFAULT 0,
    amount_fen      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT,
    created_by      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sli_loss ON stock_loss_item(loss_id);

-- ============ F48 盘点 ============
CREATE TABLE IF NOT EXISTS stock_check (
    id                  INTEGER PRIMARY KEY,
    check_no            TEXT NOT NULL UNIQUE,   -- CK{yyyyMMdd}{4位}
    scope               TEXT NOT NULL DEFAULT 'ALL',  -- ALL/DRUG/HERB/CATEGORY
    scope_value         TEXT,
    status              TEXT NOT NULL DEFAULT 'DRAFT',
                        -- DRAFT/COUNTING/ADJUSTING/APPROVED/CANCELLED（03 §2.12）
    item_count          INTEGER NOT NULL DEFAULT 0,
    diff_count          INTEGER NOT NULL DEFAULT 0,
    total_diff_amount_fen INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT,
    started_by          INTEGER,
    approved_at         TEXT,
    approved_by         INTEGER,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT,
    created_by          INTEGER,
    updated_at          TEXT,
    updated_by          INTEGER,
    deleted_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_check_status ON stock_check(status);

CREATE TABLE IF NOT EXISTS stock_check_item (
    id              INTEGER PRIMARY KEY,
    check_id        INTEGER NOT NULL,
    line_no         INTEGER NOT NULL DEFAULT 1,
    item_kind       TEXT NOT NULL DEFAULT 'DRUG',
    drug_id         INTEGER,
    herb_id         INTEGER,
    batch_id        INTEGER NOT NULL,
    book_qty        REAL NOT NULL DEFAULT 0,   -- 账面量快照（F48 R4）
    counted_qty     REAL,
    diff_qty        REAL,                      -- 实盘 − 账面
    unit            TEXT,
    unit_cost_milli INTEGER NOT NULL DEFAULT 0,
    diff_amount_fen INTEGER NOT NULL DEFAULT 0,
    is_adjusted     INTEGER NOT NULL DEFAULT 1,
    reason          TEXT,
    created_at      TEXT,
    created_by      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sci_check ON stock_check_item(check_id);

CREATE TABLE IF NOT EXISTS stock_check_status_log (
    id          INTEGER PRIMARY KEY,
    check_id    INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);

-- ============ F49/F50 效期与库存预警 ============
CREATE TABLE IF NOT EXISTS stock_expiry_alert (
    id             INTEGER PRIMARY KEY,
    alert_type     TEXT NOT NULL,   -- NEAR_EXPIRY/EXPIRED/LOW_STOCK/OUT_OF_STOCK
    item_kind      TEXT NOT NULL DEFAULT 'DRUG',
    drug_id        INTEGER,
    herb_id        INTEGER,
    batch_id       INTEGER,
    expiry_date    TEXT,
    days_left      INTEGER,
    available_qty  REAL,
    threshold_days INTEGER,
    threshold_qty  REAL,
    status         TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN/RESOLVED/IGNORED
    reason         TEXT,
    resolved_at    TEXT,
    created_at     TEXT,
    created_by     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alert_status ON stock_expiry_alert(status, alert_type);
-- 幂等：同一对象同一类型只保留一条 OPEN 预警（重复扫描不产生重复行）
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_open ON stock_expiry_alert(
    alert_type, item_kind, drug_id, herb_id, batch_id) WHERE status='OPEN';

-- ============ F51 成药发药 ============
CREATE TABLE IF NOT EXISTS dispense (
    id               INTEGER PRIMARY KEY,
    prescription_id  INTEGER NOT NULL,
    visit_id         INTEGER,
    patient_id       INTEGER,
    line_count       INTEGER NOT NULL DEFAULT 0,
    total_amount_fen INTEGER NOT NULL DEFAULT 0,
    occurred_at      TEXT,
    operator_id      INTEGER,
    idempotent_key   TEXT UNIQUE,
    remark           TEXT,
    created_at       TEXT,
    created_by       INTEGER,
    deleted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_disp_presc ON dispense(prescription_id);

-- ============ F115 中药调配与发药 ============
CREATE TABLE IF NOT EXISTS herb_dispense (
    id               INTEGER PRIMARY KEY,
    prescription_id  INTEGER NOT NULL,
    visit_id         INTEGER,
    patient_id       INTEGER,
    doses            INTEGER NOT NULL DEFAULT 1,     -- 付数
    decoction_type   TEXT NOT NULL DEFAULT 'ZIJIAN', -- ZIJIAN/DAIJIAN/KELI
    packs            INTEGER,                        -- 代煎分袋数
    herb_count       INTEGER NOT NULL DEFAULT 0,     -- 味数
    decoction_fee_fen INTEGER NOT NULL DEFAULT 0,
    package_fee_fen  INTEGER NOT NULL DEFAULT 0,
    loss_rate_permille INTEGER NOT NULL DEFAULT 0,   -- 调剂损耗率（千分比）
    ready_at         TEXT,                           -- 代煎预计可取时间
    occurred_at      TEXT,
    operator_id      INTEGER,
    idempotent_key   TEXT UNIQUE,
    remark           TEXT,
    created_at       TEXT,
    created_by       INTEGER,
    deleted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_hdisp_presc ON herb_dispense(prescription_id);

-- ============ 硬约束：业务主表禁止物理删除（04 §3）============
CREATE TRIGGER IF NOT EXISTS trg_stock_in_no_delete
BEFORE DELETE ON stock_in
BEGIN SELECT RAISE(ABORT, 'E2006: stock_in 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_stock_batch_no_delete
BEFORE DELETE ON stock_batch
BEGIN SELECT RAISE(ABORT, 'E2006: stock_batch 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_drug_return_no_delete
BEFORE DELETE ON drug_return
BEGIN SELECT RAISE(ABORT, 'E2006: drug_return 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_stock_loss_no_delete
BEFORE DELETE ON stock_loss
BEGIN SELECT RAISE(ABORT, 'E2006: stock_loss 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_stock_check_no_delete
BEFORE DELETE ON stock_check
BEGIN SELECT RAISE(ABORT, 'E2006: stock_check 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_dispense_no_delete
BEFORE DELETE ON dispense
BEGIN SELECT RAISE(ABORT, 'E2006: dispense 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_herb_dispense_no_delete
BEFORE DELETE ON herb_dispense
BEGIN SELECT RAISE(ABORT, 'E2006: herb_dispense 禁止物理删除，请使用软删除'); END;

-- ============ 库存流水 INSERT-ONLY（04 §3，与审计表同级）============
CREATE TRIGGER IF NOT EXISTS trg_stock_transaction_no_update
BEFORE UPDATE ON stock_transaction
BEGIN SELECT RAISE(ABORT, 'E2006: stock_transaction 只允许 INSERT'); END;

CREATE TRIGGER IF NOT EXISTS trg_stock_transaction_no_delete
BEFORE DELETE ON stock_transaction
BEGIN SELECT RAISE(ABORT, 'E2006: stock_transaction 只允许 INSERT'); END;

-- ============ 配置：效期与安全库存阈值 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('stock.expiry_warn_days', '90', 'INT', 'M07', '近效期预警阈值（天）', datetime('now','localtime')),
    ('stock.safety_stock_default', '0', 'INT', 'M07', '默认安全库存（0 表示不预警）', datetime('now','localtime')),
    ('stock.allow_negative', '0', 'BOOL', 'M07', '是否允许负库存出库（默认否）', datetime('now','localtime'));

-- ============ 字典：库存相关枚举 ============
INSERT OR IGNORE INTO sys_dict (dict_type, dict_code, dict_label, sort_order, created_at) VALUES
    ('STOCK_TRANS_TYPE', 'IN', '入库', 1, datetime('now','localtime')),
    ('STOCK_TRANS_TYPE', 'OUT', '出库', 2, datetime('now','localtime')),
    ('STOCK_TRANS_TYPE', 'RETURN_IN', '退药回库', 3, datetime('now','localtime')),
    ('STOCK_TRANS_TYPE', 'LOSS', '报损', 4, datetime('now','localtime')),
    ('STOCK_TRANS_TYPE', 'CHECK_ADJUST', '盘点调整', 5, datetime('now','localtime')),
    ('STOCK_TRANS_TYPE', 'SPLIT_OUT', '拆零出库', 6, datetime('now','localtime')),
    ('STOCK_TRANS_TYPE', 'SPLIT_IN', '拆零入库', 7, datetime('now','localtime')),
    ('LOSS_REASON', 'DAMAGE', '破损', 1, datetime('now','localtime')),
    ('LOSS_REASON', 'DETERIORATE', '变质', 2, datetime('now','localtime')),
    ('LOSS_REASON', 'EXPIRED', '过期', 3, datetime('now','localtime')),
    ('LOSS_REASON', 'LOST', '丢失', 4, datetime('now','localtime')),
    ('LOSS_REASON', 'OTHER', '其他', 5, datetime('now','localtime'));
