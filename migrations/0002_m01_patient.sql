-- 0002_m01_patient · M01 患者中心
-- 契约：03 §4.2（软删除）、03 §5（审计字段）、04 §2（表所有权 M01）

CREATE TABLE IF NOT EXISTS patient (
    id             INTEGER PRIMARY KEY,
    patient_no     TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    name_pinyin    TEXT,
    gender         TEXT NOT NULL DEFAULT 'U',   -- M/F/U
    birth_date     TEXT,
    phone          TEXT,
    address        TEXT,
    occupation     TEXT,
    marital_status TEXT,
    blood_type     TEXT,
    note           TEXT,
    first_visit_at TEXT,
    last_visit_at  TEXT,
    version        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT,
    created_by     INTEGER,
    updated_at     TEXT,
    updated_by     INTEGER,
    deleted_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_patient_name ON patient(name);
CREATE INDEX IF NOT EXISTS ix_patient_phone ON patient(phone);
CREATE INDEX IF NOT EXISTS ix_patient_birth ON patient(birth_date);

CREATE TABLE IF NOT EXISTS patient_identifier (
    id         INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patient(id),
    id_type    TEXT NOT NULL,          -- ID_CARD/MEDICAL_CARD/OTHER
    id_no      TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    created_by INTEGER,
    deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_patient_idno ON patient_identifier(id_type, id_no);

CREATE TABLE IF NOT EXISTS patient_contact (
    id           INTEGER PRIMARY KEY,
    patient_id   INTEGER NOT NULL REFERENCES patient(id),
    contact_name TEXT,
    relation     TEXT,
    phone        TEXT,
    is_primary   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT,
    created_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_patient_contact ON patient_contact(patient_id);

CREATE TABLE IF NOT EXISTS patient_merge_log (
    id                INTEGER PRIMARY KEY,
    primary_patient_id INTEGER NOT NULL REFERENCES patient(id),
    merged_patient_id  INTEGER NOT NULL REFERENCES patient(id),
    merged_by         INTEGER,
    merged_at         TEXT,
    reason            TEXT,
    detail_json       TEXT
);
CREATE INDEX IF NOT EXISTS ix_merge_primary ON patient_merge_log(primary_patient_id);

-- 业务主表禁止物理删除（04 §3）：一律走软删除 deleted_at
CREATE TRIGGER IF NOT EXISTS trg_patient_no_delete
BEFORE DELETE ON patient
BEGIN SELECT RAISE(ABORT, 'E2006: patient 禁止物理删除，请使用软删除'); END;
