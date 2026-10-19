-- 0012 M09 收费财务
-- 契约：03 §1.2（B/RF/RV/DS 编号）、§2.5（账单状态机）、§2.6（支付状态机）
--       §3.4（退款指定明细行、冲正走负数行）、§7（营收口径：paid_at 归集、收款−退款）
--       04 §2 表所有权：M09
-- 明确不做：医保结算、欠费挂账、应收账款、储值卡（01 §3）——因此无"部分支付"中间态

-- ============ F57 收费项目 ============
CREATE TABLE IF NOT EXISTS charge_item (
    id            INTEGER PRIMARY KEY,
    item_code     TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    alias         TEXT,
    pinyin        TEXT,
    category      TEXT NOT NULL DEFAULT 'OTHER',
                  -- REGISTRATION/CONSULT/MATERIAL/SERVICE/OTHER
    unit          TEXT NOT NULL DEFAULT '次',
    price_fen     INTEGER NOT NULL DEFAULT 0,
    auto_add_on_visit INTEGER NOT NULL DEFAULT 0,  -- 生成账单时自动并入（如挂号费）
    is_active     INTEGER NOT NULL DEFAULT 1,
    remark        TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    updated_at    TEXT,
    updated_by    INTEGER,
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_citem_name ON charge_item(name);
CREATE INDEX IF NOT EXISTS idx_citem_cat ON charge_item(category);

-- ============ F58 账单 ============
CREATE TABLE IF NOT EXISTS bill (
    id                INTEGER PRIMARY KEY,
    bill_no           TEXT NOT NULL UNIQUE,   -- B{yyyyMMdd}{4位}
    visit_id          INTEGER NOT NULL,
    patient_id        INTEGER,
    patient_name_snap TEXT,
    status            TEXT NOT NULL DEFAULT 'PENDING',
                      -- PENDING/PAID/PARTIAL_REFUNDED/FULLY_REFUNDED/VOID（03 §2.5）
    total_amount_fen  INTEGER NOT NULL DEFAULT 0,   -- Σ bill_item.amount_fen（含负数冲正行）
    paid_amount_fen   INTEGER NOT NULL DEFAULT 0,
    refunded_amount_fen INTEGER NOT NULL DEFAULT 0,
    line_count        INTEGER NOT NULL DEFAULT 0,
    biz_date          TEXT,                    -- 营业日（日结归集用）
    settlement_id     INTEGER,                 -- 所属日结单（非空=已日结）
    settled_at        TEXT,
    void_reason       TEXT,
    occurred_at       TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT,
    created_by        INTEGER,
    updated_at        TEXT,
    updated_by        INTEGER,
    deleted_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_bill_visit ON bill(visit_id);
CREATE INDEX IF NOT EXISTS idx_bill_patient ON bill(patient_id);
CREATE INDEX IF NOT EXISTS idx_bill_status ON bill(status);
CREATE INDEX IF NOT EXISTS idx_bill_date ON bill(biz_date);
-- 一个 Visit 最多一张有效账单（F58 R3）
CREATE UNIQUE INDEX IF NOT EXISTS ux_bill_visit_active
    ON bill(visit_id) WHERE status <> 'VOID' AND deleted_at IS NULL;

-- 账单状态流转日志（03 §2.1）
CREATE TABLE IF NOT EXISTS bill_status_log (
    id          INTEGER PRIMARY KEY,
    bill_id     INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_billlog ON bill_status_log(bill_id);

-- ============ F60 账单明细（只允许 INSERT，冲正走负数行）============
CREATE TABLE IF NOT EXISTS bill_item (
    id                  INTEGER PRIMARY KEY,
    bill_id             INTEGER NOT NULL,
    line_no             INTEGER NOT NULL DEFAULT 1,
    source_type         TEXT NOT NULL DEFAULT 'CHARGE',
                        -- DRUG/HERB/TREATMENT/LAB/CHARGE
    source_id           INTEGER,
    name_snap           TEXT NOT NULL,
    unit                TEXT,
    unit_price_fen      INTEGER NOT NULL DEFAULT 0,
    quantity            REAL NOT NULL DEFAULT 1,
    amount_fen          INTEGER NOT NULL DEFAULT 0,
    refunded_amount_fen INTEGER NOT NULL DEFAULT 0,
    reversed_amount_fen INTEGER NOT NULL DEFAULT 0,
    is_reversal         INTEGER NOT NULL DEFAULT 0,
    reversal_id         INTEGER,
    remark              TEXT,
    created_at          TEXT,
    created_by          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_billitem_bill ON bill_item(bill_id);
CREATE INDEX IF NOT EXISTS idx_billitem_src ON bill_item(source_type, source_id);

-- ============ F59 支付 ============
CREATE TABLE IF NOT EXISTS payment (
    id                  INTEGER PRIMARY KEY,
    bill_id             INTEGER NOT NULL,
    channel             TEXT NOT NULL DEFAULT 'CASH',
                        -- CASH/WECHAT/ALIPAY/CARD/OTHER（01 §5）
    amount_fen          INTEGER NOT NULL DEFAULT 0,   -- 本笔应收金额
    received_amount_fen INTEGER NOT NULL DEFAULT 0,   -- 实收（现金场景，用于找零）
    change_amount_fen   INTEGER NOT NULL DEFAULT 0,   -- 找零
    trade_no            TEXT,                         -- 第三方渠道凭证号
    status              TEXT NOT NULL DEFAULT 'INIT',
                        -- INIT/SUCCESS/FAILED/REFUNDED/REVERSED（03 §2.6）
    paid_at             TEXT,                         -- 营收归集时间（03 §7）
    refunded_amount_fen INTEGER NOT NULL DEFAULT 0,
    idempotent_key      TEXT UNIQUE,
    operator_id         INTEGER,
    remark              TEXT,
    created_at          TEXT,
    created_by          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pay_bill ON payment(bill_id);
CREATE INDEX IF NOT EXISTS idx_pay_time ON payment(paid_at);
CREATE INDEX IF NOT EXISTS idx_pay_status ON payment(status);

CREATE TABLE IF NOT EXISTS payment_status_log (
    id          INTEGER PRIMARY KEY,
    payment_id  INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_paylog ON payment_status_log(payment_id);

-- ============ F61 退款 ============
CREATE TABLE IF NOT EXISTS refund (
    id               INTEGER PRIMARY KEY,
    refund_no        TEXT NOT NULL UNIQUE,   -- RF{yyyyMMdd}{4位}
    bill_id          INTEGER NOT NULL,
    visit_id         INTEGER,
    patient_id       INTEGER,
    drug_return_id   INTEGER,                -- F63 退药退款来源（可空）
    refund_type      TEXT NOT NULL DEFAULT 'NORMAL',  -- NORMAL/DRUG_RETURN
    status           TEXT NOT NULL DEFAULT 'DRAFT',   -- DRAFT/SUCCEEDED/FAILED/VOID
    total_amount_fen INTEGER NOT NULL DEFAULT 0,
    line_count       INTEGER NOT NULL DEFAULT 0,
    reason           TEXT,
    occurred_at      TEXT,
    operator_id      INTEGER,
    idempotent_key   TEXT UNIQUE,
    remark           TEXT,
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT,
    created_by       INTEGER,
    updated_at       TEXT,
    updated_by       INTEGER,
    deleted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_refund_bill ON refund(bill_id);
CREATE INDEX IF NOT EXISTS idx_refund_return ON refund(drug_return_id);
-- 一张退药单最多一次退款（F63 R2）
CREATE UNIQUE INDEX IF NOT EXISTS ux_refund_drug_return
    ON refund(drug_return_id) WHERE drug_return_id IS NOT NULL AND status <> 'VOID'
       AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS refund_item (
    id             INTEGER PRIMARY KEY,
    refund_id      INTEGER NOT NULL,
    line_no        INTEGER NOT NULL DEFAULT 1,
    bill_item_id   INTEGER NOT NULL,
    source_type    TEXT,
    source_id      INTEGER,
    name_snap      TEXT,
    quantity       REAL NOT NULL DEFAULT 0,
    amount_fen     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT,
    created_by     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_refunditem ON refund_item(refund_id);
CREATE INDEX IF NOT EXISTS idx_refunditem_bi ON refund_item(bill_item_id);

-- ============ F62 红字冲正 ============
CREATE TABLE IF NOT EXISTS reversal (
    id               INTEGER PRIMARY KEY,
    reversal_no      TEXT NOT NULL UNIQUE,   -- RV{yyyyMMdd}{4位}
    bill_id          INTEGER NOT NULL,
    total_amount_fen INTEGER NOT NULL DEFAULT 0,   -- 冲正总额（正数表示冲掉多少）
    line_count       INTEGER NOT NULL DEFAULT 0,
    reason           TEXT,
    occurred_at      TEXT,
    operator_id      INTEGER,
    idempotent_key   TEXT UNIQUE,
    remark           TEXT,
    created_at       TEXT,
    created_by       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reversal_bill ON reversal(bill_id);

-- ============ F64 日结 ============
CREATE TABLE IF NOT EXISTS day_settlement (
    id                   INTEGER PRIMARY KEY,
    settlement_no        TEXT NOT NULL UNIQUE,   -- DS{yyyyMMdd}{2位}
    biz_date             TEXT NOT NULL,
    from_settlement_id   INTEGER,                -- 增量日结：上次日结 ID
    bill_count           INTEGER NOT NULL DEFAULT 0,
    pending_count        INTEGER NOT NULL DEFAULT 0,
    pending_amount_fen   INTEGER NOT NULL DEFAULT 0,
    receivable_amount_fen INTEGER NOT NULL DEFAULT 0,
    paid_amount_fen      INTEGER NOT NULL DEFAULT 0,
    refund_amount_fen    INTEGER NOT NULL DEFAULT 0,
    net_amount_fen       INTEGER NOT NULL DEFAULT 0,
    channel_json         TEXT,                   -- 分渠道汇总快照
    occurred_at          TEXT,
    operator_id          INTEGER,
    idempotent_key       TEXT UNIQUE,
    remark               TEXT,
    created_at           TEXT,
    created_by           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_settle_date ON day_settlement(biz_date);

-- ============ 硬约束：业务主表禁止物理删除（04 §3）============
CREATE TRIGGER IF NOT EXISTS trg_bill_no_delete
BEFORE DELETE ON bill
BEGIN SELECT RAISE(ABORT, 'E2006: bill 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_bill_item_no_delete
BEFORE DELETE ON bill_item
BEGIN SELECT RAISE(ABORT, 'E2006: bill_item 禁止物理删除，冲正走负数行'); END;

CREATE TRIGGER IF NOT EXISTS trg_refund_no_delete
BEFORE DELETE ON refund
BEGIN SELECT RAISE(ABORT, 'E2006: refund 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_payment_no_delete
BEFORE DELETE ON payment
BEGIN SELECT RAISE(ABORT, 'E2006: payment 禁止物理删除'); END;

-- 账单明细 INSERT-ONLY（04 §3：与审计表、库存流水同级）
CREATE TRIGGER IF NOT EXISTS trg_bill_item_no_update
BEFORE UPDATE ON bill_item
BEGIN SELECT RAISE(ABORT, 'E2006: bill_item 只允许 INSERT'); END;

-- ============ 字典：支付渠道与收费类别 ============
INSERT OR IGNORE INTO sys_dict (dict_type, dict_code, dict_label, sort_order, created_at) VALUES
    ('PAY_CHANNEL', 'CASH',   '现金',   1, datetime('now','localtime')),
    ('PAY_CHANNEL', 'WECHAT', '微信',   2, datetime('now','localtime')),
    ('PAY_CHANNEL', 'ALIPAY', '支付宝', 3, datetime('now','localtime')),
    ('PAY_CHANNEL', 'CARD',   '银行卡', 4, datetime('now','localtime')),
    ('PAY_CHANNEL', 'OTHER',  '其他',   5, datetime('now','localtime')),
    ('CHARGE_CATEGORY', 'REGISTRATION', '挂号',  1, datetime('now','localtime')),
    ('CHARGE_CATEGORY', 'CONSULT',      '诊查',  2, datetime('now','localtime')),
    ('CHARGE_CATEGORY', 'MATERIAL',     '耗材',  3, datetime('now','localtime')),
    ('CHARGE_CATEGORY', 'SERVICE',      '服务',  4, datetime('now','localtime')),
    ('CHARGE_CATEGORY', 'OTHER',        '其他',  5, datetime('now','localtime')),
    ('BILL_SOURCE', 'DRUG',      '成药处方', 1, datetime('now','localtime')),
    ('BILL_SOURCE', 'HERB',      '中药处方', 2, datetime('now','localtime')),
    ('BILL_SOURCE', 'TREATMENT', '处置',     3, datetime('now','localtime')),
    ('BILL_SOURCE', 'LAB',       '检验',     4, datetime('now','localtime')),
    ('BILL_SOURCE', 'CHARGE',    '收费项目', 5, datetime('now','localtime'));

-- ============ 配置：挂号费自动并入 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('finance.auto_registration', '1', 'BOOL', 'M09', '生成账单时自动并入挂号费', datetime('now','localtime')),
    ('finance.allow_pending_settle', '1', 'BOOL', 'M09', '存在未收费账单时允许日结（仅提示）', datetime('now','localtime'));
