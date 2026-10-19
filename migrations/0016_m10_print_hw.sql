-- 0016 M10 打印与硬件（F66-F75）
-- 契约：03 §1.2（PJ{yyyyMMddHHmmss}{3位}）、§2.7（打印任务状态机）、§6（E4xxx/E7xxx retryable）
--       04 §2 表所有权：M10

-- ============ 打印模板 ============
CREATE TABLE IF NOT EXISTS print_template (
    id            INTEGER PRIMARY KEY,
    template_code TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    biz_type      TEXT NOT NULL,          -- PRESCRIPTION / RECEIPT / LABEL
    variant       TEXT,                   -- WEST / HERB / GRANULE / REFUND
    content       TEXT NOT NULL,          -- 文本模板，占位符 {{field}}
    paper_width   INTEGER NOT NULL DEFAULT 80,
    is_default    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    remark        TEXT,
    created_at    TEXT,
    created_by    INTEGER,
    updated_at    TEXT,
    updated_by    INTEGER,
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ptpl_biz ON print_template(biz_type, variant);

-- ============ F66 打印任务 ============
CREATE TABLE IF NOT EXISTS print_job (
    id             INTEGER PRIMARY KEY,
    print_job_no   TEXT NOT NULL UNIQUE,   -- PJ{yyyyMMddHHmmss}{3位}
    template_code  TEXT NOT NULL,
    biz_type       TEXT NOT NULL,
    biz_id         INTEGER,
    biz_no         TEXT,                    -- 业务单据号（便于人读）
    driver_code    TEXT NOT NULL DEFAULT 'mock.printer',
    device_id      INTEGER,
    status         TEXT NOT NULL DEFAULT 'PENDING',
                   -- PENDING/PRINTING/SUCCESS/FAILED/RETRYING/ABANDONED（03 §2.7）
    rendered_text  TEXT NOT NULL,           -- 创建时固化，重打不再取数（R-M10-2）
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    reprint_count  INTEGER NOT NULL DEFAULT 0,
    reprint_reason TEXT,
    last_reprint_at TEXT,
    printed_at     TEXT,
    output_path    TEXT,
    patient_id     INTEGER,
    visit_id       INTEGER,
    idempotent_key TEXT UNIQUE,
    remark         TEXT,
    created_at     TEXT,
    created_by     INTEGER,
    updated_at     TEXT,
    updated_by     INTEGER,
    deleted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pjob_biz ON print_job(biz_type, biz_id);
CREATE INDEX IF NOT EXISTS idx_pjob_status ON print_job(status);
CREATE INDEX IF NOT EXISTS idx_pjob_time ON print_job(created_at);

-- 状态流转日志（03 §2.1）
CREATE TABLE IF NOT EXISTS print_status_log (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    operator_id INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pjoblog ON print_status_log(job_id);

-- ============ F71 打印失败 ============
CREATE TABLE IF NOT EXISTS print_failure (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL,
    attempt_no  INTEGER NOT NULL DEFAULT 1,
    error_code  TEXT NOT NULL,
    message     TEXT,
    retryable   INTEGER NOT NULL DEFAULT 1,
    occurred_at TEXT NOT NULL,
    operator_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pfail_job ON print_failure(job_id);

-- ============ F72 硬件设备 ============
CREATE TABLE IF NOT EXISTS hardware_device (
    id          INTEGER PRIMARY KEY,
    device_code TEXT NOT NULL UNIQUE,
    device_type TEXT NOT NULL,            -- PRINTER / ID_READER / SCANNER
    name        TEXT NOT NULL,
    vendor      TEXT,
    model       TEXT,
    driver_code TEXT NOT NULL,
    conn_json   TEXT,                     -- 连接参数（不存密钥明文）
    status      TEXT NOT NULL DEFAULT 'ONLINE',   -- ONLINE / OFFLINE / DEGRADED
    is_default  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    remark      TEXT,
    created_at  TEXT,
    created_by  INTEGER,
    updated_at  TEXT,
    updated_by  INTEGER,
    deleted_at  TEXT
);

-- ============ F73/F74 硬件事件（不含隐私原文）============
CREATE TABLE IF NOT EXISTS hardware_event (
    id          INTEGER PRIMARY KEY,
    device_id   INTEGER,
    driver_code TEXT,
    event_type  TEXT NOT NULL,            -- PRINT / READ_ID / SCAN / STATUS / ERROR / RESTART
    result      TEXT NOT NULL,            -- SUCCESS / FAIL
    summary     TEXT,                     -- 摘要，**禁止写入隐私原文**（R5/R6）
    error_code  TEXT,
    elapsed_ms  INTEGER,
    occurred_at TEXT NOT NULL,
    operator_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_hwevent_time ON hardware_event(occurred_at);
CREATE INDEX IF NOT EXISTS idx_hwevent_type ON hardware_event(event_type, result);

-- ============ F75 硬件心跳（监控数据，可轮转，非审计）============
CREATE TABLE IF NOT EXISTS hardware_health (
    id          INTEGER PRIMARY KEY,
    device_id   INTEGER,
    driver_code TEXT,
    result      TEXT NOT NULL,            -- OK / WARN / FAIL
    online      INTEGER NOT NULL DEFAULT 1,
    elapsed_ms  INTEGER,
    detail      TEXT,
    checked_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hwhealth_time ON hardware_health(checked_at);

-- ============ 硬约束：打印任务禁止物理删除（04 §3）============
CREATE TRIGGER IF NOT EXISTS trg_print_job_no_delete
BEFORE DELETE ON print_job
BEGIN SELECT RAISE(ABORT, 'E2006: print_job 禁止物理删除，请使用软删除'); END;

-- 扫码（F74）需要按条码反查品目：为 drug / herb 补 barcode 列（002/… 只增不改）
ALTER TABLE drug ADD COLUMN barcode TEXT;
ALTER TABLE herb ADD COLUMN barcode TEXT;
CREATE INDEX IF NOT EXISTS idx_drug_barcode ON drug(barcode);
CREATE INDEX IF NOT EXISTS idx_herb_barcode ON herb(barcode);

-- ============ 默认模板（占位符 {{field}}，渲染器做替换）============
INSERT OR IGNORE INTO print_template (template_code, name, biz_type, variant, content,
                                      is_default, created_at) VALUES
('RX_WEST', '处方笺（成药）', 'PRESCRIPTION', 'WEST',
'{{clinic_name}}  处方笺
------------------------------
处方号：{{prescription_no}}
患者：{{patient_name}} {{gender_text}} {{age_text}}
科室：{{department}}   医生：{{doctor_name}}
开方时间：{{occurred_at}}
------------------------------
诊断：{{diag_summary}}
------------------------------
{{#items}}
{{seq}}. {{name}} {{spec}}
   数量 {{quantity}}{{unit}}  单价 {{price_yuan}}  小计 {{line_amount_yuan}}
   用法：{{dosage}} {{frequency}} {{route}}{{days_text}}
{{/items}}
------------------------------
合计：{{total_yuan}} 元
------------------------------
医师签名：________  药师：________
打印时间：{{printed_at}}  任务号：{{print_job_no}}{{reprint_text}}',
1, datetime('now','localtime')),

('RX_HERB', '中药饮片方', 'PRESCRIPTION', 'HERB',
'{{clinic_name}}  中药饮片处方
------------------------------
处方号：{{prescription_no}}
患者：{{patient_name}} {{gender_text}} {{age_text}}
医生：{{doctor_name}}   开方时间：{{occurred_at}}
------------------------------
中医诊断：{{tcm_syndrome}}
------------------------------
{{#items}}
{{seq}}. {{name}} {{dose_g}}g  【{{foot_note_text}}】
{{/items}}
------------------------------
共 {{herb_count}} 味   付数：{{doses}} 付
煎法：{{decoction_method}}   服法：{{administration}}
饮片小计：{{herb_subtotal_yuan}} 元
代煎费：{{decoction_fee_yuan}} 元   包装费：{{package_fee_yuan}} 元
------------------------------
合计：{{total_yuan}} 元
------------------------------
医师签名：________  药师：________
打印时间：{{printed_at}}  任务号：{{print_job_no}}{{reprint_text}}',
1, datetime('now','localtime')),

('RECEIPT', '收费凭条', 'RECEIPT', 'NORMAL',
'{{clinic_name}}  收费凭条
------------------------------
账单号：{{bill_no}}   时间：{{paid_at}}
患者：{{patient_name}}   收费员：{{operator_name}}
------------------------------
{{#items}}
{{seq}}. {{name}} x{{quantity}}  {{amount_yuan}}
{{/items}}
------------------------------
应收合计：{{total_yuan}} 元
{{#payments}}
支付：{{channel_text}} {{amount_yuan}} 元{{change_text}}
{{/payments}}
已退：{{refunded_yuan}} 元
------------------------------
谢谢惠顾，请妥善保管凭条
任务号：{{print_job_no}}{{reprint_text}}',
1, datetime('now','localtime')),

('LABEL', '药袋标签', 'LABEL', 'NORMAL',
'{{clinic_name}}
------------------------------
患者：{{patient_name}} {{gender_text}} {{age_text}}
处方号：{{prescription_no}}
------------------------------
{{#items}}
{{name}}{{dose_text}} {{usage_text}}
{{/items}}
{{herb_text}}
------------------------------
{{allergy_text}}
发药时间：{{printed_at}}',
1, datetime('now','localtime'));

-- ============ 默认设备（mock 驱动）============
INSERT OR IGNORE INTO hardware_device (device_code, device_type, name, driver_code,
                                       status, is_default, created_at) VALUES
('PRN-01', 'PRINTER',   '默认打印机（mock）', 'mock.printer',   'ONLINE', 1, datetime('now','localtime')),
('IDC-01', 'ID_READER', '身份证读卡器（mock）', 'mock.id_reader', 'ONLINE', 1, datetime('now','localtime')),
('SCN-01', 'SCANNER',   '扫码枪（mock）',      'mock.scanner',   'ONLINE', 1, datetime('now','localtime'));

-- ============ 配置 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('print.max_retry',      '3', 'INT', 'M10', '打印自动重试上限（超限转 ABANDONED）', datetime('now','localtime')),
    ('print.max_reprint',    '5', 'INT', 'M10', '重打次数告警阈值（超过仍允许但写高风险审计）', datetime('now','localtime')),
    ('hardware.heartbeat_seconds', '60', 'INT', 'M10', '硬件监控心跳周期（秒）', datetime('now','localtime')),
    ('hardware.watchdog_fail_threshold', '3', 'INT', 'M13', '连续失败达到该次数触发看门狗重启（F98）', datetime('now','localtime'));
