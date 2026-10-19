-- 0007 M06 处方与用药（含中药）
-- 契约：03 §1.2 处方号 R{yyyyMMdd}{4位}（成药/饮片/颗粒共用序列）
--       03 §2.4 处方状态机 / §3.1 成药计价 / §3.2 饮片计价 / §8 中药特殊规则
--       04 §2  表所有权：M06

-- ============ F32 药品资料 ============
CREATE TABLE IF NOT EXISTS drug (
    id              INTEGER PRIMARY KEY,
    drug_code       TEXT NOT NULL UNIQUE,
    generic_name    TEXT NOT NULL,          -- 通用名
    trade_name      TEXT,                   -- 商品名
    alias           TEXT,                   -- 别名/拼音，检索用
    pinyin          TEXT,
    spec            TEXT,                   -- 规格，如 0.25g*24粒
    dosage_form     TEXT,                   -- 剂型：片剂/胶囊/颗粒/注射剂
    unit            TEXT NOT NULL DEFAULT '盒',
    pack_qty        INTEGER NOT NULL DEFAULT 1,   -- 包装含量（拆零用）
    manufacturer    TEXT,
    approval_no     TEXT,
    price_fen       INTEGER NOT NULL DEFAULT 0,   -- 零售价（分）
    drug_class      TEXT,                   -- 药理分类（重复用药/同类判断）
    allergy_class   TEXT,                   -- 过敏类别（PENICILLIN/CEPHALOSPORIN/SULFA/NSAID...）
    is_antibiotic   INTEGER NOT NULL DEFAULT 0,   -- F122 统计用
    is_injection    INTEGER NOT NULL DEFAULT 0,   -- F122 统计用
    need_skin_test  INTEGER NOT NULL DEFAULT 0,   -- F36 皮试前置
    skin_test_type  TEXT,
    max_single_dose REAL,                   -- 单次剂量上限
    max_daily_dose  REAL,                   -- 日剂量上限
    dose_unit       TEXT,
    default_usage   TEXT,                   -- 默认用法
    default_freq    TEXT,                   -- 默认频次
    default_route   TEXT,                   -- 默认给药途径
    is_active       INTEGER NOT NULL DEFAULT 1,
    remark          TEXT,
    created_at      TEXT,
    created_by      INTEGER,
    updated_at      TEXT,
    updated_by      INTEGER,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_drug_name ON drug(generic_name);
CREATE INDEX IF NOT EXISTS idx_drug_alias ON drug(alias, pinyin);

-- ============ F110 中药饮片资料 ============
CREATE TABLE IF NOT EXISTS herb (
    id              INTEGER PRIMARY KEY,
    herb_code       TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,          -- 正名，如 黄芪
    alias           TEXT,                   -- 别名，如 黄耆
    pinyin          TEXT,                   -- huangqi
    nature          TEXT,                   -- 性：寒/热/温/凉/平
    flavor          TEXT,                   -- 味：辛/甘/酸/苦/咸
    meridian        TEXT,                   -- 归经
    efficacy        TEXT,                   -- 功效
    indication      TEXT,                   -- 主治
    price_milli_per_g INTEGER NOT NULL DEFAULT 0,  -- 单价（毫元/g），1/1000 元
    usual_dose_g    REAL,                   -- 常用量(g)
    max_dose_g      REAL,                   -- 剂量上限(g)，F113
    is_toxic        INTEGER NOT NULL DEFAULT 0,    -- 毒性饮片：超量强制二次确认
    toxic_limit_g   REAL,                   -- 毒性限量(g)
    pregnancy_flag  TEXT,                   -- 妊娠：CONTRAINDICATED/CAUTION/空
    stock_unit      TEXT NOT NULL DEFAULT 'g',     -- 库存单位（契约 §8 固定为克）
    is_active       INTEGER NOT NULL DEFAULT 1,
    remark          TEXT,
    created_at      TEXT,
    created_by      INTEGER,
    updated_at      TEXT,
    updated_by      INTEGER,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_herb_name ON herb(name);
CREATE INDEX IF NOT EXISTS idx_herb_pinyin ON herb(pinyin, alias);

-- ============ 配伍禁忌（十八反十九畏）F113 ============
CREATE TABLE IF NOT EXISTS herb_compatibility (
    id          INTEGER PRIMARY KEY,
    herb_a      TEXT NOT NULL,
    herb_b      TEXT NOT NULL,
    taboo_type  TEXT NOT NULL DEFAULT 'EIGHTEEN',  -- EIGHTEEN 十八反 / NINETEEN 十九畏
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_compat ON herb_compatibility(herb_a, herb_b, taboo_type);

-- ============ 处方主表 ============
CREATE TABLE IF NOT EXISTS prescription (
    id                 INTEGER PRIMARY KEY,
    prescription_no    TEXT NOT NULL UNIQUE,      -- R{yyyyMMdd}{4位}
    visit_id           INTEGER NOT NULL,
    patient_id         INTEGER NOT NULL,
    record_id          INTEGER,
    prescription_type  TEXT NOT NULL DEFAULT 'WEST',  -- WEST / HERB / GRANULE
    status             TEXT NOT NULL DEFAULT 'DRAFT',
    version            INTEGER NOT NULL DEFAULT 1,     -- 乐观锁
    -- 患者快照（R-M06-9：不随患者主数据变更）
    patient_name_snap  TEXT,
    gender_snap        TEXT,
    birth_date_snap    TEXT,
    age_text_snap      TEXT,
    -- 临床摘要
    diag_summary       TEXT,
    tcm_syndrome       TEXT,
    -- 中药专有（契约 §8）
    doses              INTEGER,                    -- 付数
    is_decoction       INTEGER NOT NULL DEFAULT 0, -- 代煎
    decoction_method   TEXT,                       -- 煎法
    administration     TEXT,                       -- 服法
    -- 金额（整数分）
    total_amount       INTEGER NOT NULL DEFAULT 0,
    -- 来源与放行
    source             TEXT NOT NULL DEFAULT 'DOCTOR',  -- DOCTOR / AI
    ai_task_id         INTEGER,
    override_flag      INTEGER NOT NULL DEFAULT 0,      -- 强制放行（过敏/毒性超量）
    override_reason    TEXT,
    -- 签名与发药
    confirmed_by       INTEGER,
    confirmed_at       TEXT,
    sign_method        TEXT,
    dispensed_at       TEXT,
    void_reason        TEXT,
    remark             TEXT,
    occurred_at        TEXT,                       -- 业务发生时间（开方时间）
    created_at         TEXT,
    created_by         INTEGER,
    updated_at         TEXT,
    updated_by         INTEGER,
    deleted_at         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_prescription_no ON prescription(prescription_no);
CREATE INDEX IF NOT EXISTS idx_presc_visit ON prescription(visit_id);
CREATE INDEX IF NOT EXISTS idx_presc_patient ON prescription(patient_id);
CREATE INDEX IF NOT EXISTS idx_presc_status ON prescription(status);
CREATE INDEX IF NOT EXISTS idx_presc_type ON prescription(prescription_type);

-- 状态流转日志（03 §2.1：每次转换写入 *_status_log）
CREATE TABLE IF NOT EXISTS prescription_status_log (
    id              INTEGER PRIMARY KEY,
    prescription_id INTEGER NOT NULL,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    reason          TEXT,
    operator_id     INTEGER,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_presc_log ON prescription_status_log(prescription_id);

-- ============ F34 成药明细 ============
CREATE TABLE IF NOT EXISTS prescription_item (
    id                INTEGER PRIMARY KEY,
    prescription_id   INTEGER NOT NULL,
    line_no           INTEGER NOT NULL DEFAULT 1,
    drug_id           INTEGER NOT NULL,
    drug_name_snap    TEXT NOT NULL,
    spec_snap         TEXT,
    dosage_form_snap  TEXT,
    unit              TEXT,
    unit_price_fen    INTEGER NOT NULL DEFAULT 0,   -- 开方时价（F112 R7）
    quantity          REAL NOT NULL DEFAULT 1,
    line_amount_fen   INTEGER NOT NULL DEFAULT 0,   -- 行小计（分）
    dosage            TEXT,                         -- 单次用量
    frequency         TEXT,                         -- 频次
    route             TEXT,                         -- 给药途径
    days              INTEGER,                      -- 天数
    is_antibiotic     INTEGER NOT NULL DEFAULT 0,   -- 快照，F122 统计
    need_skin_test    INTEGER NOT NULL DEFAULT 0,   -- 快照，F36 前置
    remark            TEXT,
    created_at        TEXT,
    created_by        INTEGER,
    updated_at        TEXT,
    updated_by        INTEGER,
    deleted_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_pitem_presc ON prescription_item(prescription_id);

-- ============ F111 饮片明细 ============
CREATE TABLE IF NOT EXISTS herb_prescription_item (
    id                  INTEGER PRIMARY KEY,
    prescription_id     INTEGER NOT NULL,
    line_no             INTEGER NOT NULL DEFAULT 1,
    herb_id             INTEGER NOT NULL,
    herb_name_snap      TEXT NOT NULL,
    dose_g              REAL NOT NULL,              -- 每味剂量(g)
    price_milli_per_g   INTEGER NOT NULL DEFAULT 0, -- 开方时价（毫元/g）
    foot_note           TEXT NOT NULL DEFAULT 'NORMAL',  -- 脚注：先煎/后下/包煎/烊化/冲服/另煎
    line_amount_fen     INTEGER NOT NULL DEFAULT 0, -- 每味小计（分）
    is_toxic            INTEGER NOT NULL DEFAULT 0, -- 快照
    remark              TEXT,
    created_at          TEXT,
    created_by          INTEGER,
    updated_at          TEXT,
    updated_by          INTEGER,
    deleted_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_hitem_presc ON herb_prescription_item(prescription_id);
CREATE INDEX IF NOT EXISTS idx_hitem_herb ON herb_prescription_item(herb_id);

-- ============ F112 计价结果 ============
CREATE TABLE IF NOT EXISTS prescription_pricing (
    id                INTEGER PRIMARY KEY,
    prescription_id   INTEGER NOT NULL UNIQUE,
    drug_subtotal_fen INTEGER NOT NULL DEFAULT 0,
    herb_subtotal_fen INTEGER NOT NULL DEFAULT 0,
    decoction_fee_fen INTEGER NOT NULL DEFAULT 0,
    package_fee_fen   INTEGER NOT NULL DEFAULT 0,
    total_amount_fen  INTEGER NOT NULL DEFAULT 0,
    tail_diff_fen     INTEGER NOT NULL DEFAULT 0,   -- 尾差显式记录（F112 R6）
    doses_snapshot    INTEGER,
    decoction_mode    TEXT,                         -- PER_DOSE / PER_RX / NONE
    priced_at         TEXT,
    priced_by         INTEGER,
    created_at        TEXT,
    created_by        INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pricing ON prescription_pricing(prescription_id);

-- ============ 规则命中记录（F35 / F113 共用）============
CREATE TABLE IF NOT EXISTS prescription_rule_hit (
    id             INTEGER PRIMARY KEY,
    prescription_id INTEGER,
    rule_type      TEXT NOT NULL,   -- ALLERGY/SKIN_TEST/DOSE/INTERACTION/DUPLICATE/
                                    -- TOXIC/COMPATIBILITY/PREGNANCY/HERB_COUNT/REQUIRED
    severity       TEXT NOT NULL,   -- BLOCK / FORCE_CONFIRM / WARN
    entity_type    TEXT,            -- DRUG / HERB
    entity_id      INTEGER,
    entity_name    TEXT,
    limit_value    TEXT,
    actual_value   TEXT,
    detail_json    TEXT,
    doctor_action  TEXT,            -- ACCEPTED / IGNORED / FORCED
    reason         TEXT,
    created_at     TEXT,
    created_by     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rulehit_presc ON prescription_rule_hit(prescription_id);
CREATE INDEX IF NOT EXISTS idx_rulehit_type ON prescription_rule_hit(rule_type);

-- ============ F36 皮试 ============
CREATE TABLE IF NOT EXISTS skin_test (
    id            INTEGER PRIMARY KEY,
    patient_id    INTEGER NOT NULL,
    visit_id      INTEGER,
    drug_id       INTEGER,
    drug_name     TEXT NOT NULL,
    batch_no      TEXT,             -- 批号
    result        TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/NEGATIVE/POSITIVE
    performed_at  TEXT,
    performed_by  INTEGER,
    observe_min   INTEGER,          -- 观察分钟数
    note          TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    updated_at    TEXT,
    updated_by    INTEGER,
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_skintest_patient ON skin_test(patient_id, drug_id);

-- ============ F127 处方模板与经验方 ============
CREATE TABLE IF NOT EXISTS prescription_template (
    id                  INTEGER PRIMARY KEY,
    template_type       TEXT NOT NULL DEFAULT 'HERB',  -- WEST / HERB / GRANULE
    scope               TEXT NOT NULL DEFAULT 'USER',  -- SYSTEM / USER / CLINIC
    owner_id            INTEGER,
    category            TEXT,
    name                TEXT NOT NULL,
    syndrome_code       TEXT,           -- 非空即为经验方
    syndrome_name       TEXT,
    indication          TEXT,
    doses_default       INTEGER,        -- 默认付数
    decoction_method    TEXT,
    administration      TEXT,
    usage_count         INTEGER NOT NULL DEFAULT 0,
    source_prescription_no TEXT,        -- 由处方另存时记录
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT,
    created_by          INTEGER,
    updated_at          TEXT,
    updated_by          INTEGER,
    deleted_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ptpl_owner ON prescription_template(owner_id, scope);
CREATE INDEX IF NOT EXISTS idx_ptpl_syndrome ON prescription_template(syndrome_name);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ptpl ON prescription_template(scope, owner_id, name);

CREATE TABLE IF NOT EXISTS prescription_template_item (
    id           INTEGER PRIMARY KEY,
    template_id  INTEGER NOT NULL,
    item_kind    TEXT NOT NULL DEFAULT 'HERB',   -- DRUG / HERB
    ref_id       INTEGER,                        -- drug_id / herb_id
    name_snap    TEXT NOT NULL,
    dose_g       REAL,                           -- 饮片：每味剂量
    quantity     REAL,                           -- 成药：数量
    dosage       TEXT,
    frequency    TEXT,
    route        TEXT,
    foot_note    TEXT,
    seq_no       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT,
    created_by   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ptpl_item ON prescription_template_item(template_id);

-- ============ F39 用药指导 ============
CREATE TABLE IF NOT EXISTS medication_guide (
    id              INTEGER PRIMARY KEY,
    prescription_id INTEGER NOT NULL,
    content         TEXT NOT NULL,
    is_printed      INTEGER NOT NULL DEFAULT 0,
    printed_at      TEXT,
    created_at      TEXT,
    created_by      INTEGER,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_guide_presc ON medication_guide(prescription_id);

-- ============ 硬约束：禁止物理删除（04 §3）============
CREATE TRIGGER IF NOT EXISTS trg_prescription_no_delete
BEFORE DELETE ON prescription
BEGIN SELECT RAISE(ABORT, 'E2006: prescription 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_prescription_item_no_delete
BEFORE DELETE ON prescription_item
BEGIN SELECT RAISE(ABORT, 'E2006: prescription_item 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_herb_item_no_delete
BEFORE DELETE ON herb_prescription_item
BEGIN SELECT RAISE(ABORT, 'E2006: herb_prescription_item 禁止物理删除，请使用软删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_skin_test_no_delete
BEFORE DELETE ON skin_test
BEGIN SELECT RAISE(ABORT, 'E2006: skin_test 禁止物理删除，请使用软删除'); END;

-- ============ 配置：味数阈值与代煎费默认值 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('herb.max_herb_count',   '20', 'INT', 'M06', '饮片味数提醒阈值（超过仅提醒）', datetime('now','localtime')),
    ('presc.require_diagnosis','1', 'BOOL','M06', '无诊断时开方是否提示（提示不阻断）', datetime('now','localtime'));

-- ============ 字典：脚注与给药途径 ============
INSERT OR IGNORE INTO sys_dict (dict_type, dict_code, dict_label, sort_order, created_at) VALUES
    ('HERB_FOOTNOTE', 'NORMAL',   '常规',   1, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'FIRST',    '先煎',   2, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'LATER',    '后下',   3, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'WRAP',     '包煎',   4, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'MELT',     '烊化',   5, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'DISSOLVE', '冲服',   6, datetime('now','localtime')),
    ('HERB_FOOTNOTE', 'SEPARATE', '另煎',   7, datetime('now','localtime')),
    ('DRUG_ROUTE',    'PO',       '口服',   1, datetime('now','localtime')),
    ('DRUG_ROUTE',    'IV',       '静注',   2, datetime('now','localtime')),
    ('DRUG_ROUTE',    'IM',       '肌注',   3, datetime('now','localtime')),
    ('DRUG_ROUTE',    'SC',       '皮下',   4, datetime('now','localtime')),
    ('DRUG_ROUTE',    'TOPICAL',  '外用',   5, datetime('now','localtime')),
    ('DRUG_ROUTE',    'INHAL',    '吸入',   6, datetime('now','localtime'));

-- ============ 示例配伍禁忌（十八反十九畏典型组）============
INSERT OR IGNORE INTO herb_compatibility (herb_a, herb_b, taboo_type, description, is_active, created_at) VALUES
    ('甘草', '甘遂', 'EIGHTEEN', '十八反：甘草反甘遂、大戟、海藻、芫花', 1, datetime('now','localtime')),
    ('甘草', '大戟', 'EIGHTEEN', '十八反：甘草反甘遂、大戟、海藻、芫花', 1, datetime('now','localtime')),
    ('甘草', '海藻', 'EIGHTEEN', '十八反：甘草反甘遂、大戟、海藻、芫花', 1, datetime('now','localtime')),
    ('甘草', '芫花', 'EIGHTEEN', '十八反：甘草反甘遂、大戟、海藻、芫花', 1, datetime('now','localtime')),
    ('乌头', '半夏', 'EIGHTEEN', '十八反：乌头反半夏、瓜蒌、贝母、白蔹、白及', 1, datetime('now','localtime')),
    ('乌头', '瓜蒌', 'EIGHTEEN', '十八反：乌头反半夏、瓜蒌、贝母、白蔹、白及', 1, datetime('now','localtime')),
    ('乌头', '贝母', 'EIGHTEEN', '十八反：乌头反半夏、瓜蒌、贝母、白蔹、白及', 1, datetime('now','localtime')),
    ('藜芦', '人参', 'EIGHTEEN', '十八反：藜芦反人参、南沙参、丹参、玄参、苦参、细辛、芍药', 1, datetime('now','localtime')),
    ('藜芦', '丹参', 'EIGHTEEN', '十八反：藜芦反人参、南沙参、丹参、玄参、苦参、细辛、芍药', 1, datetime('now','localtime')),
    ('藜芦', '细辛', 'EIGHTEEN', '十八反：藜芦反人参、南沙参、丹参、玄参、苦参、细辛、芍药', 1, datetime('now','localtime')),
    ('硫黄', '朴硝', 'NINETEEN', '十九畏：硫黄畏朴硝', 1, datetime('now','localtime')),
    ('水银', '砒霜', 'NINETEEN', '十九畏：水银畏砒霜', 1, datetime('now','localtime')),
    ('狼毒', '密陀僧','NINETEEN', '十九畏：狼毒畏密陀僧', 1, datetime('now','localtime')),
    ('巴豆', '牵牛', 'NINETEEN', '十九畏：巴豆畏牵牛', 1, datetime('now','localtime')),
    ('丁香', '郁金', 'NINETEEN', '十九畏：丁香畏郁金', 1, datetime('now','localtime')),
    ('人参', '五灵脂','NINETEEN', '十九畏：人参畏五灵脂', 1, datetime('now','localtime')),
    ('官桂', '石脂', 'NINETEEN', '十九畏：官桂畏石脂', 1, datetime('now','localtime'));
