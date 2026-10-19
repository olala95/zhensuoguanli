-- 0004_m03_record · M03 临床病历（含中医四诊与辨证论治）
-- 契约：03 §2.3（病历状态机）、03 §8（中药规则）、04 §2（表所有权 M03）
-- 边界：药品只能走处方（F34/F111），medical_order 只承载非药品处置/检验/说明

CREATE TABLE IF NOT EXISTS record (
    id          INTEGER PRIMARY KEY,
    visit_id    INTEGER NOT NULL UNIQUE REFERENCES visit(id),
    patient_id  INTEGER NOT NULL REFERENCES patient(id),
    record_type TEXT NOT NULL DEFAULT 'TCM',     -- TCM/WEST
    status      TEXT NOT NULL DEFAULT 'DRAFT',   -- DRAFT/CONFIRMED/ARCHIVED/SEALED/UNSEALED
    version     INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'DOCTOR',  -- DOCTOR/AI（AI 结果仅作草稿，确认后落库）
    confirmed_by INTEGER,
    confirmed_at TEXT,
    closed_at   TEXT,
    note        TEXT,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_record_visit ON record(visit_id);
CREATE INDEX IF NOT EXISTS ix_record_patient ON record(patient_id);
CREATE INDEX IF NOT EXISTS ix_record_status ON record(status);

CREATE TABLE IF NOT EXISTS record_status_log (
    id           INTEGER PRIMARY KEY,
    entity_type  TEXT NOT NULL DEFAULT 'record',
    record_id    INTEGER NOT NULL REFERENCES record(id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    operator_id  INTEGER,
    role_code    TEXT,
    occurred_at  TEXT NOT NULL,
    reason       TEXT,
    operation_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_record_status_log ON record_status_log(record_id);

-- F10 主诉
CREATE TABLE IF NOT EXISTS record_chief_complaint (
    id         INTEGER PRIMARY KEY,
    record_id  INTEGER NOT NULL REFERENCES record(id),
    content    TEXT NOT NULL,
    duration   TEXT,
    source     TEXT NOT NULL DEFAULT 'DOCTOR',   -- DOCTOR/AI
    created_at TEXT,
    created_by INTEGER,
    updated_at TEXT,
    updated_by INTEGER,
    deleted_at TEXT
);

-- F11 现病史
CREATE TABLE IF NOT EXISTS record_present_illness (
    id           INTEGER PRIMARY KEY,
    record_id    INTEGER NOT NULL REFERENCES record(id),
    content      TEXT NOT NULL,
    onset_time   TEXT,
    onset_type   TEXT,     -- 急性/亚急性/慢性
    aggravate    TEXT,
    relieve      TEXT,
    treatment_hx TEXT,     -- 诊治经过
    general      TEXT,     -- 一般情况（纳眠二便）
    source       TEXT NOT NULL DEFAULT 'DOCTOR',
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);

-- F12 既往史 / 个人史 / 家族史
CREATE TABLE IF NOT EXISTS record_history (
    id          INTEGER PRIMARY KEY,
    record_id   INTEGER NOT NULL REFERENCES record(id),
    hist_type   TEXT NOT NULL,   -- PAST 既往史 / PERSONAL 个人史 / FAMILY 家族史 / MENSTRUAL 月经婚育史
    disease     TEXT,
    diagnosed_at TEXT,
    treatment   TEXT,
    outcome     TEXT,            -- 痊愈/好转/未愈
    content     TEXT,
    is_abnormal INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'DOCTOR',
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_history_record ON record_history(record_id, hist_type);

-- F13 过敏史（患者级别留痕 + 就诊级别引用）
CREATE TABLE IF NOT EXISTS record_allergy (
    id           INTEGER PRIMARY KEY,
    record_id    INTEGER REFERENCES record(id),
    patient_id   INTEGER NOT NULL REFERENCES patient(id),
    allergen     TEXT NOT NULL,
    allergen_type TEXT,          -- DRUG/FOOD/OTHER
    manifestation TEXT,
    severity     TEXT,           -- MILD/MODERATE/SEVERE
    source       TEXT NOT NULL DEFAULT 'DOCTOR',
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_allergy_patient ON record_allergy(patient_id);

-- F14 生命体征
CREATE TABLE IF NOT EXISTS vital_sign (
    id            INTEGER PRIMARY KEY,
    record_id     INTEGER NOT NULL REFERENCES record(id),
    temperature_c REAL,
    pulse         INTEGER,
    respiration   INTEGER,
    systolic      INTEGER,
    diastolic     INTEGER,
    spo2          INTEGER,
    height_cm     REAL,
    weight_kg     REAL,
    bmi           REAL,
    pain_score    INTEGER,
    measured_at   TEXT,
    measured_by   INTEGER,
    note          TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_vital_record ON vital_sign(record_id);

-- F15 查体
CREATE TABLE IF NOT EXISTS physical_exam (
    id          INTEGER PRIMARY KEY,
    record_id   INTEGER NOT NULL REFERENCES record(id),
    section     TEXT,      -- GENERAL/HEAD_NECK/CHEST/ABDOMEN/NERVOUS/LIMBS/TCM_TONGUE/TCM_PULSE
    content     TEXT NOT NULL,
    is_abnormal INTEGER NOT NULL DEFAULT 0,
    seq_no      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_exam_record ON physical_exam(record_id);

-- F16 辅助检查引用（只读引用 M05 的 lab_order / lab_result，不复制结果正文）
CREATE TABLE IF NOT EXISTS record_exam_ref (
    id           INTEGER PRIMARY KEY,
    record_id    INTEGER NOT NULL REFERENCES record(id),
    ref_type     TEXT NOT NULL,     -- LAB/IMAGING/OTHER
    lab_order_id INTEGER,
    lab_result_id INTEGER,
    summary      TEXT,
    occurred_at  TEXT,
    created_at   TEXT,
    created_by   INTEGER,
    deleted_at   TEXT
);

-- F17 诊断（西医 ICD-10 + 中医证型）
CREATE TABLE IF NOT EXISTS diagnosis (
    id                INTEGER PRIMARY KEY,
    record_id         INTEGER NOT NULL REFERENCES record(id),
    diag_type         TEXT NOT NULL,   -- WEST/TCM
    icd10_code        TEXT,
    icd10_name        TEXT,
    tcm_code          TEXT,
    tcm_name          TEXT,
    is_primary        INTEGER NOT NULL DEFAULT 0,
    seq_no            INTEGER NOT NULL DEFAULT 0,
    confidence        TEXT,            -- CONFIRMED/SUSPECTED/EXCLUDED
    source            TEXT NOT NULL DEFAULT 'DOCTOR',
    note              TEXT,
    created_at        TEXT,
    created_by        INTEGER,
    updated_at        TEXT,
    updated_by        INTEGER,
    deleted_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_diag_record ON diagnosis(record_id);
CREATE INDEX IF NOT EXISTS ix_diag_icd ON diagnosis(icd10_code);

-- F18 医嘱（非药品）
CREATE TABLE IF NOT EXISTS medical_order (
    id           INTEGER PRIMARY KEY,
    record_id    INTEGER NOT NULL REFERENCES record(id),
    order_type   TEXT NOT NULL,   -- TREATMENT/LAB/ADVICE/REFERRAL/FOLLOWUP
    content      TEXT NOT NULL,
    item_ref_type TEXT,           -- TREATMENT_ITEM/LAB_ITEM/CHARGE_ITEM
    item_ref_id  INTEGER,
    dosage       TEXT,
    frequency    TEXT,
    days         INTEGER,
    seq_no       INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE/CANCELLED/EXECUTED
    source       TEXT NOT NULL DEFAULT 'DOCTOR',
    note         TEXT,
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_order_record ON medical_order(record_id);

-- F108 中医四诊（望闻问切、舌脉）
CREATE TABLE IF NOT EXISTS tcm_four_diagnosis (
    id            INTEGER PRIMARY KEY,
    record_id     INTEGER NOT NULL UNIQUE REFERENCES record(id),
    inspection    TEXT,     -- 望：神色形态
    auscultation  TEXT,     -- 闻：声息气味
    interrogation TEXT,     -- 问：寒热汗出、饮食二便等
    palpation     TEXT,     -- 切：脉象与按诊
    tongue_body   TEXT,     -- 舌质
    tongue_coating TEXT,    -- 舌苔
    pulse_desc    TEXT,     -- 脉象描述
    constitution  TEXT,     -- 体质辨识
    note          TEXT,
    source        TEXT NOT NULL DEFAULT 'DOCTOR',
    created_at    TEXT,
    created_by    INTEGER,
    updated_at    TEXT,
    updated_by    INTEGER
);

-- F109 辨证论治
CREATE TABLE IF NOT EXISTS tcm_syndrome (
    id                 INTEGER PRIMARY KEY,
    record_id          INTEGER NOT NULL REFERENCES record(id),
    syndrome_name      TEXT NOT NULL,
    syndrome_code      TEXT,
    syndrome_category  TEXT,   -- 八纲/脏腑/气血津液/病因/六经/卫气营血
    basis              TEXT,   -- 辨证依据
    treatment_principle TEXT,  -- 治则
    treatment_method   TEXT,   -- 治法
    formula_ref        TEXT,   -- 参考方
    seq_no             INTEGER NOT NULL DEFAULT 0,
    source             TEXT NOT NULL DEFAULT 'DOCTOR',
    created_at         TEXT,
    created_by         INTEGER,
    updated_at         TEXT,
    updated_by         INTEGER,
    deleted_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_syndrome_record ON tcm_syndrome(record_id);

-- F126 病历模板与常用语
CREATE TABLE IF NOT EXISTS record_template (
    id           INTEGER PRIMARY KEY,
    template_type TEXT NOT NULL,   -- RECORD/PHRASE/TCM_RECORD
    scope        TEXT NOT NULL DEFAULT 'USER',  -- SYSTEM/USER/DEPT
    owner_id     INTEGER,
    category     TEXT,
    name         TEXT NOT NULL,
    content_json TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,
    usage_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_tpl_owner ON record_template(scope, owner_id, template_type);

-- F19 病历确认签名
CREATE TABLE IF NOT EXISTS record_signature (
    id          INTEGER PRIMARY KEY,
    record_id   INTEGER NOT NULL REFERENCES record(id),
    signer_id   INTEGER NOT NULL,
    signer_name TEXT,
    role_code   TEXT,
    signed_at   TEXT NOT NULL,
    sign_method TEXT NOT NULL DEFAULT 'PASSWORD',  -- PASSWORD/CARD
    sign_hash   TEXT,
    version_no  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_sign_record ON record_signature(record_id);

CREATE TRIGGER IF NOT EXISTS trg_record_no_delete
BEFORE DELETE ON record
BEGIN SELECT RAISE(ABORT, 'E2006: record 禁止物理删除，请使用软删除'); END;
