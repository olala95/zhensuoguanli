-- 0009 M04 AI 与语音
-- 契约：03 §1.2 AI 任务号 AI{yyyyMMddHHmmss}{6位}
--       03 §2.10 状态机 CREATED→RUNNING→SUCCEEDED|FAILED→RETRYING→ABANDONED
--              **AI 结果不得直接写入病历**，只能作为草稿，经医生确认后落库并标记来源
--       05    单写者原则：AI Worker 不直连业务库，走 IPC 调主程序

-- ============ AI 任务主表 ============
CREATE TABLE IF NOT EXISTS ai_task (
    id           INTEGER PRIMARY KEY,
    task_no      TEXT NOT NULL UNIQUE,
    task_type    TEXT NOT NULL,          -- ASR / EXTRACT / DRAFT / SUGGEST / RISK
    visit_id     INTEGER,
    patient_id   INTEGER,
    record_id    INTEGER,
    status       TEXT NOT NULL DEFAULT 'CREATED',
    retry_count  INTEGER NOT NULL DEFAULT 0,
    max_retry    INTEGER NOT NULL DEFAULT 3,
    engine       TEXT,                   -- ASR/LLM 引擎标识
    model        TEXT,
    input_json   TEXT,
    output_json  TEXT,
    error_code   TEXT,
    error_msg    TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    duration_ms  INTEGER,
    operator_id  INTEGER,                -- 发起任务的医生
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_task_no ON ai_task(task_no);
CREATE INDEX IF NOT EXISTS idx_ai_task_visit ON ai_task(visit_id);
CREATE INDEX IF NOT EXISTS idx_ai_task_status ON ai_task(status);

-- ============ F20 录音元信息 ============
CREATE TABLE IF NOT EXISTS ai_audio (
    id           INTEGER PRIMARY KEY,
    ai_task_id   INTEGER,
    visit_id     INTEGER,
    file_path    TEXT NOT NULL,
    file_size    INTEGER,
    duration_sec REAL,
    sample_rate  INTEGER,
    channel      INTEGER,
    format       TEXT,                   -- wav / mp3 / pcm
    device       TEXT,
    recorded_at  TEXT,
    checksum     TEXT,
    created_at   TEXT,
    created_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_audio_task ON ai_audio(ai_task_id);

-- ============ F21 ASR 转写 ============
CREATE TABLE IF NOT EXISTS ai_transcript (
    id          INTEGER PRIMARY KEY,
    ai_task_id  INTEGER NOT NULL,
    ai_audio_id INTEGER,
    seq_no      INTEGER NOT NULL DEFAULT 1,
    text        TEXT NOT NULL,
    confidence  REAL,
    language    TEXT NOT NULL DEFAULT 'zh-CN',
    engine      TEXT,
    is_edited   INTEGER NOT NULL DEFAULT 0,   -- 医生是否手工修改过
    edited_by   INTEGER,
    created_at  TEXT,
    created_by  INTEGER,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_trans_task ON ai_transcript(ai_task_id);

-- ============ F22 医疗事实提取 ============
CREATE TABLE IF NOT EXISTS ai_fact (
    id          INTEGER PRIMARY KEY,
    ai_task_id  INTEGER NOT NULL,
    visit_id    INTEGER,
    patient_id  INTEGER,
    fact_type   TEXT NOT NULL,   -- SYMPTOM / DURATION / VITAL / HISTORY / ALLERGY /
                                 -- DIAGNOSIS / TCM_SYNDROME / MEDICATION
    fact_key    TEXT,
    fact_value  TEXT,
    confidence  REAL,
    source_text TEXT,            -- 依据的原话，便于医生核对
    created_at  TEXT,
    created_by  INTEGER,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_fact_task ON ai_fact(ai_task_id);
CREATE INDEX IF NOT EXISTS idx_ai_fact_visit ON ai_fact(visit_id);

-- ============ F23 病历草稿（**未确认前不得写入 record**）============
CREATE TABLE IF NOT EXISTS ai_draft (
    id           INTEGER PRIMARY KEY,
    ai_task_id   INTEGER,
    visit_id     INTEGER NOT NULL,
    record_id    INTEGER,
    patient_id   INTEGER,
    draft_json   TEXT NOT NULL,          -- 结构化草稿内容
    status       TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/ACCEPTED/REJECTED/EXPIRED
    accepted_by  INTEGER,
    accepted_at  TEXT,
    reject_reason TEXT,
    model        TEXT,
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_draft_visit ON ai_draft(visit_id);
CREATE INDEX IF NOT EXISTS idx_ai_draft_status ON ai_draft(status);

-- ============ F24 处方建议 ============
CREATE TABLE IF NOT EXISTS ai_suggestion (
    id           INTEGER PRIMARY KEY,
    ai_task_id   INTEGER,
    visit_id     INTEGER NOT NULL,
    prescription_id INTEGER,             -- 医生采纳后生成的处方
    suggest_type TEXT NOT NULL DEFAULT 'DRUG',   -- DRUG / HERB / GRANULE
    suggest_json TEXT NOT NULL,
    rationale    TEXT,                   -- 建议依据（证型/诊断）
    status       TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/ADOPTED/REJECTED
    adopted_by   INTEGER,
    adopted_at   TEXT,
    model        TEXT,
    created_at   TEXT,
    created_by   INTEGER,
    updated_at   TEXT,
    updated_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_sugg_visit ON ai_suggestion(visit_id);

-- ============ F25 风险提醒 ============
CREATE TABLE IF NOT EXISTS ai_risk_alert (
    id           INTEGER PRIMARY KEY,
    ai_task_id   INTEGER,
    visit_id     INTEGER,
    patient_id   INTEGER,
    risk_type    TEXT NOT NULL,   -- ALLERGY / INTERACTION / DOSE / PREGNANCY /
                                  -- ABNORMAL_RESULT / TCM_TABOO / CHRONIC
    severity     TEXT NOT NULL DEFAULT 'WARN',   -- INFO / WARN / HIGH
    title        TEXT NOT NULL,
    detail       TEXT,
    source_ref   TEXT,            -- 依据：过敏史/诊断/检验结果
    ack_by       INTEGER,
    ack_at       TEXT,
    ack_action   TEXT,            -- ACCEPTED / IGNORED / ESCALATED
    created_at   TEXT,
    created_by   INTEGER,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_risk_visit ON ai_risk_alert(visit_id);

-- ============ F26 失败与恢复日志 ============
CREATE TABLE IF NOT EXISTS ai_failure_log (
    id          INTEGER PRIMARY KEY,
    ai_task_id  INTEGER,
    task_no     TEXT,
    stage       TEXT NOT NULL,     -- ASR / EXTRACT / DRAFT / SUGGEST / RISK / APPLY
    attempt     INTEGER NOT NULL DEFAULT 1,
    error_code  TEXT,
    error_msg   TEXT,
    recoverable INTEGER NOT NULL DEFAULT 1,
    action      TEXT,              -- RETRY / ABANDON / FALLBACK
    created_at  TEXT,
    created_by  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ai_fail_task ON ai_failure_log(ai_task_id);

-- ============ 硬约束 ============
CREATE TRIGGER IF NOT EXISTS trg_ai_failure_no_update
BEFORE UPDATE ON ai_failure_log
BEGIN SELECT RAISE(ABORT, 'E2006: ai_failure_log 只允许 INSERT'); END;
CREATE TRIGGER IF NOT EXISTS trg_ai_failure_no_delete
BEFORE DELETE ON ai_failure_log
BEGIN SELECT RAISE(ABORT, 'E2006: ai_failure_log 禁止删除'); END;

-- ============ 配置 ============
INSERT OR IGNORE INTO sys_config (cfg_key, cfg_value, cfg_type, module, description, created_at) VALUES
    ('ai.enabled',       '1',  'BOOL',   'M04', 'AI 功能总开关（关闭后语音与草稿不可用）', datetime('now','localtime')),
    ('ai.max_retry',     '3',  'INT',    'M04', '任务失败重试上限，超过置 ABANDONED', datetime('now','localtime')),
    ('ai.timeout_sec',   '30', 'INT',    'M04', '单次 AI 任务超时秒数', datetime('now','localtime')),
    ('ai.engine',        'LOCAL_RULE', 'STRING', 'M04', 'ASR/LLM 引擎标识（LOCAL_RULE=本地规则兜底）', datetime('now','localtime')),
    ('ai.require_confirm','1', 'BOOL',   'M04', 'AI 草稿必须医生确认后才可落库（恒为 1，不可关）', datetime('now','localtime')),
    ('ai.audio_dir',     '',   'STRING', 'M04', '录音文件存放目录（空=data/audio）', datetime('now','localtime'));
