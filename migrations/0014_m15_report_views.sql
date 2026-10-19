-- 0014 M15 经营统计 · 只读视图层 + 报表缓存表
-- 契约：03 §7（统计口径）、04 §4（视图层：M15 只查视图，不查原始表）
-- 口径要点：
--   营收   = Σ payment.amount(SUCCESS) − Σ refund.amount(SUCCEEDED)，归集时间 = payment.paid_at
--   门诊量 = COUNT(visit) WHERE status='COMPLETED'（不含 VOID / SUSPENDED）
--   初复诊 = 该患者在此次 Visit 之前是否存在 COMPLETED 的 Visit
--   进销存 = 期初 + 入库 − 出库 + 退药 − 报损 ± 盘点调整（按批次维度）
--   处方   = 仅 CONFIRMED/DISPENSED/PARTIAL_RETURNED/FULLY_RETURNED，作废单列

-- ============ F119 日营收（收款为正、退款为负，报表只需 SUM）============
CREATE VIEW IF NOT EXISTS v_revenue_daily AS
SELECT substr(p.paid_at, 1, 10) AS biz_date,
       p.channel                AS channel,
       'PAY'                    AS kind,
       p.amount_fen             AS amount_fen,
       p.bill_id                AS bill_id,
       p.id                     AS ref_id,
       p.paid_at                AS occurred_at
FROM payment p
WHERE p.status = 'SUCCESS' AND p.paid_at IS NOT NULL
UNION ALL
SELECT substr(r.occurred_at, 1, 10),
       'REFUND',
       'REFUND',
       -r.total_amount_fen,
       r.bill_id,
       r.id,
       r.occurred_at
FROM refund r
WHERE r.status = 'SUCCEEDED' AND r.deleted_at IS NULL AND r.occurred_at IS NOT NULL;

-- ============ F120 门诊量与医生工作量 ============
CREATE VIEW IF NOT EXISTS v_visit_stats AS
SELECT v.id                     AS visit_id,
       v.visit_no               AS visit_no,
       v.patient_id             AS patient_id,
       v.doctor_id              AS doctor_id,
       u.display_name           AS doctor_name,
       v.visit_type             AS visit_type,
       v.status                 AS status,
       COALESCE(substr(v.completed_at, 1, 10), substr(v.registered_at, 1, 10)) AS biz_date,
       v.registered_at          AS registered_at,
       v.completed_at           AS completed_at,
       (SELECT COUNT(*) FROM visit v2
         WHERE v2.patient_id = v.patient_id AND v2.deleted_at IS NULL
           AND v2.status = 'COMPLETED' AND v2.id < v.id)     AS prior_completed,
       (SELECT COUNT(*) FROM prescription p
         WHERE p.visit_id = v.id AND p.deleted_at IS NULL
           AND p.status IN ('CONFIRMED','DISPENSED','PARTIAL_RETURNED','FULLY_RETURNED'))
                                                              AS rx_count,
       (SELECT COUNT(*) FROM treatment_execution t
         WHERE t.visit_id = v.id AND t.deleted_at IS NULL
           AND t.status = 'COMPLETED')                        AS treatment_count
FROM visit v
LEFT JOIN sys_user u ON u.id = v.doctor_id
WHERE v.deleted_at IS NULL;

-- ============ F121 药品进销存流水（含品目名称与成本）============
CREATE VIEW IF NOT EXISTS v_stock_ledger AS
SELECT t.id                  AS trans_id,
       t.item_kind           AS item_kind,
       t.drug_id             AS drug_id,
       t.herb_id             AS herb_id,
       t.batch_id            AS batch_id,
       t.trans_type          AS trans_type,
       t.ref_type            AS ref_type,
       t.ref_id              AS ref_id,
       t.qty_change          AS qty_change,
       t.unit_cost_milli     AS unit_cost_milli,
       t.amount_fen          AS amount_fen,
       t.occurred_at         AS occurred_at,
       substr(t.occurred_at, 1, 10) AS biz_date,
       t.operator_id         AS operator_id,
       b.batch_no            AS batch_no,
       b.supplier_batch_no   AS supplier_batch_no,
       b.expiry_date         AS expiry_date,
       CASE WHEN t.item_kind = 'DRUG' THEN d.generic_name ELSE h.name END AS item_name,
       CASE WHEN t.item_kind = 'DRUG' THEN d.spec        ELSE NULL   END AS spec,
       CASE WHEN t.item_kind = 'DRUG' THEN d.unit        ELSE 'g'    END AS unit,
       CASE WHEN t.item_kind = 'DRUG' THEN d.drug_class  ELSE NULL   END AS item_class
FROM stock_transaction t
LEFT JOIN stock_batch b ON b.id = t.batch_id
LEFT JOIN drug d        ON d.id = t.drug_id
LEFT JOIN herb h        ON h.id = t.herb_id;

-- ============ F122 处方统计 ============
CREATE VIEW IF NOT EXISTS v_prescription_stats AS
SELECT p.id                       AS prescription_id,
       p.prescription_no          AS prescription_no,
       p.visit_id                 AS visit_id,
       p.patient_id               AS patient_id,
       p.prescription_type        AS prescription_type,
       p.status                   AS status,
       p.doses                    AS doses,
       p.created_by               AS doctor_id,
       u.display_name             AS doctor_name,
       substr(p.occurred_at, 1, 10) AS biz_date,
       p.occurred_at              AS occurred_at,
       COALESCE(pr.total_amount_fen, p.total_amount, 0) AS amount_fen,
       (SELECT COUNT(*) FROM prescription_item i
         WHERE i.prescription_id = p.id AND i.deleted_at IS NULL)      AS drug_line_count,
       (SELECT COUNT(*) FROM herb_prescription_item hi
         WHERE hi.prescription_id = p.id AND hi.deleted_at IS NULL)    AS herb_line_count,
       EXISTS (SELECT 1 FROM prescription_item i
                WHERE i.prescription_id = p.id AND i.deleted_at IS NULL
                  AND i.is_antibiotic = 1)                             AS has_antibiotic,
       EXISTS (SELECT 1 FROM prescription_item i
                WHERE i.prescription_id = p.id AND i.deleted_at IS NULL
                  AND i.route IN ('IV','IM','SC'))                     AS has_injection
FROM prescription p
LEFT JOIN prescription_pricing pr ON pr.prescription_id = p.id
LEFT JOIN sys_user u              ON u.id = p.created_by
WHERE p.deleted_at IS NULL;

-- ============ F123 患者统计 ============
CREATE VIEW IF NOT EXISTS v_patient_stats AS
SELECT pt.id                AS patient_id,
       pt.patient_no        AS patient_no,
       pt.name              AS name,
       pt.gender            AS gender,
       pt.birth_date        AS birth_date,
       pt.created_at        AS created_at,
       substr(pt.created_at, 1, 10) AS biz_date,
       (SELECT COUNT(*) FROM visit v
         WHERE v.patient_id = pt.id AND v.deleted_at IS NULL
           AND v.status = 'COMPLETED')                     AS visit_count,
       (SELECT MAX(v.registered_at) FROM visit v
         WHERE v.patient_id = pt.id AND v.deleted_at IS NULL) AS last_visit_at
FROM patient pt
WHERE pt.deleted_at IS NULL;

-- ============ F123 疾病谱（新增视图：diagnosis + record + visit）============
CREATE VIEW IF NOT EXISTS v_diagnosis_stats AS
SELECT dx.id                      AS diagnosis_id,
       dx.record_id               AS record_id,
       rc.visit_id                AS visit_id,
       rc.patient_id              AS patient_id,
       dx.diag_type               AS diag_type,
       dx.icd10_code              AS icd10_code,
       dx.icd10_name              AS icd10_name,
       dx.tcm_code                AS tcm_code,
       dx.tcm_name                AS tcm_name,
       dx.is_primary              AS is_primary,
       dx.confidence              AS confidence,
       substr(COALESCE(dx.created_at, rc.created_at), 1, 10) AS biz_date,
       COALESCE(dx.created_at, rc.created_at)                AS occurred_at
FROM diagnosis dx
LEFT JOIN record rc ON rc.id = dx.record_id
WHERE dx.deleted_at IS NULL;

-- ============ 报表缓存（可选，P2）============
CREATE TABLE IF NOT EXISTS report_definition (
    id          INTEGER PRIMARY KEY,
    report_code TEXT NOT NULL UNIQUE,     -- REVENUE_DAILY / VISIT_STATS / STOCK_LEDGER ...
    name        TEXT NOT NULL,
    description TEXT,
    view_name   TEXT,                     -- 依赖的视图（便于校验）
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS report_snapshot (
    id           INTEGER PRIMARY KEY,
    report_code  TEXT NOT NULL,
    period_from  TEXT,
    period_to    TEXT,
    status       TEXT NOT NULL DEFAULT 'VALID',   -- VALID / EXPIRED（纯缓存标记）
    row_count    INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT,
    built_at     TEXT,
    built_by     INTEGER,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_rpt_snap ON report_snapshot(report_code, period_from, status);

INSERT OR IGNORE INTO report_definition (report_code, name, description, view_name, created_at)
VALUES
    ('REVENUE_DAILY',     '日营收与班结',   'F119 营收、退款、渠道分项、客单价', 'v_revenue_daily',
     datetime('now','localtime')),
    ('VISIT_STATS',       '门诊量与工作量', 'F120 门诊量、初复诊、医生工作量',   'v_visit_stats',
     datetime('now','localtime')),
    ('STOCK_LEDGER',      '药品进销存',     'F121 期初/入库/出库/退药/报损/盘点/期末', 'v_stock_ledger',
     datetime('now','localtime')),
    ('PRESCRIPTION_STATS','处方统计',       'F122 中西药占比、抗生素与注射剂使用率', 'v_prescription_stats',
     datetime('now','localtime')),
    ('PATIENT_STATS',     '患者与疾病谱',   'F123 新增患者、复诊率、疾病谱 TOP',  'v_patient_stats',
     datetime('now','localtime'));

-- 报表导出目录配置（F124 R6：与日志同分区，可轮转）
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at)
VALUES ('report.export_max_rows', '50000', 'INT', 'M15', '单次导出行数上限', datetime('now','localtime'));
