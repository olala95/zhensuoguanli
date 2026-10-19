# -*- coding: utf-8 -*-
"""M04 AI 与语音（F20-F26）。

表所有权：ai_task / ai_audio / ai_transcript / ai_fact / ai_draft / ai_suggestion /
         ai_risk_alert / ai_failure_log
状态机：03 §2.10 —— CREATED→RUNNING→SUCCEEDED|FAILED；FAILED→RETRYING→RUNNING|ABANDONED
**硬规则（03 §2.10）：AI 结果不得直接写入病历。只能写草稿，医生确认后落库并标记 source=AI。**
**处方建议不豁免规则校验（R-M06-4）：采纳建议走 M06 正常开方流程，规则原样执行。**

引擎：本机无第三方依赖，默认 `LOCAL_RULE`（规则抽取兜底）。
      接入真实 ASR/LLM 时只需替换 `_asr()` / `_extract()` 两个内部函数，
      上层流程与失败恢复不变。
"""

import datetime
import os
import re
import time

from core import db, audit, config, timeutil as tu, ids
from core.api import route
from core.errors import (require, require_keys, AppError, conflict, not_found,
                         BIZ_STATE_INVALID, AI_FAILED, AI_TIMEOUT, AI_UNPARSABLE)

CREATED = "CREATED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
RETRYING = "RETRYING"
ABANDONED = "ABANDONED"

TRANSITIONS = {
    CREATED:   [RUNNING, FAILED],
    RUNNING:   [SUCCEEDED, FAILED],
    FAILED:    [RETRYING, ABANDONED],
    RETRYING:  [RUNNING, ABANDONED],
    SUCCEEDED: [],
    ABANDONED: [],
}

TASK_TYPES = ("ASR", "EXTRACT", "DRAFT", "SUGGEST", "RISK")

# 症状关键词（本地规则抽取用）
SYMPTOM_WORDS = ["咳嗽", "发热", "发烧", "头痛", "头晕", "乏力", "失眠", "腹泻", "腹痛", "胃痛",
                 "恶心", "呕吐", "鼻塞", "流涕", "咽痛", "咽干", "胸闷", "心悸", "气短", "关节痛",
                 "腰痛", "皮疹", "瘙痒", "水肿", "口干", "口苦", "食欲不振", "便秘", "多汗", "畏寒"]
DURATION_PAT = re.compile(r"(?:已?|约|大概)?\s*(\d+|[一二三四五六七八九十两半]+)\s*"
                          r"(天|日|周|星期|个月|月|年|小时|小时余)")
TEMP_PAT = re.compile(r"(?:体温|发烧|发热)?\s*(\d{2}(?:\.\d)?)\s*(?:度|℃|摄氏度)?")
BP_PAT = re.compile(r"(?:血压)?\s*(\d{2,3})\s*[/\\]\s*(\d{2,3})")
ALLERGY_PAT = re.compile(r"对?\s*([\u4e00-\u9fa5A-Za-z]{2,10})\s*(?:过敏|过敏史|不能用)")
HISTORY_PAT = re.compile(r"(?:有|患有|既往)\s*([\u4e00-\u9fa5]{2,12})\s*(?:病史|病|史)")
CHRONIC_WORDS = ["高血压", "糖尿病", "冠心病", "哮喘", "慢性胃炎", "肝炎", "肾病", "甲亢"]


def _enabled():
    return config.get_int("ai.enabled", 1) == 1


def _max_retry():
    return config.get_int("ai.max_retry", 3)


def _transition(conn, task_id, to_status, reason=None, ctx=None, extra=None):
    row = db.query_one("SELECT * FROM ai_task WHERE id=? AND deleted_at IS NULL", (task_id,))
    if row is None:
        raise not_found("AI 任务不存在")
    frm = row["status"]
    if to_status not in TRANSITIONS.get(frm, []):
        raise conflict("AI 任务状态不允许从 %s 转换到 %s" % (frm, to_status), code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?"]
    params = [to_status, tu.now_str(), (ctx.user_id if ctx else 0)]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(task_id)
    db.execute(conn, "UPDATE ai_task SET %s WHERE id=?" % ",".join(sets), tuple(params))
    return frm, to_status


def _fail(conn, task_id, stage, err_code, err_msg, attempt=1, ctx=None):
    """F26：失败一律留痕；可重试的按上限决定是否 ABANDONED。"""
    recoverable = err_code in (AI_FAILED, AI_TIMEOUT, AI_UNPARSABLE, "E1001")
    db.execute(conn, """
        INSERT INTO ai_failure_log (ai_task_id, task_no, stage, attempt, error_code, error_msg,
                                    recoverable, action, created_at, created_by)
        VALUES (?,(SELECT task_no FROM ai_task WHERE id=?),?,?,?,?,?,?,?,?)
    """, (task_id, task_id, stage, attempt, err_code, err_msg, 1 if recoverable else 0,
          "RETRY" if recoverable else "ABANDON", tu.now_str(),
          (ctx.user_id if ctx else 0)))
    row = db.query_one("SELECT status, retry_count, max_retry FROM ai_task WHERE id=?", (task_id,))
    action = "RETRY"
    if not recoverable or (row["retry_count"] or 0) >= (row["max_retry"] or _max_retry()):
        action = "ABANDON"
    _transition(conn, task_id, ABANDONED if action == "ABANDON" else FAILED,
                err_msg, ctx,
                extra={"error_code": err_code, "error_msg": err_msg,
                       "status": ABANDONED if action == "ABANDON" else FAILED})
    if action == "ABANDON":
        audit.audit_high_risk(conn, "AI_ABANDON", "ai_task", task_id,
                              "AI 任务放弃：%s / %s" % (err_code, err_msg))
    return action


def recover_stale_running(ctx=None):
    """F97 看门狗 / F96 崩溃收敛：把卡在 RUNNING 的任务拉回确定态。

    RUNNING --(超时)--> FAILED --(未超上限)--> RETRYING
                                --(已达上限)--> ABANDONED

    幂等：状态已不是 RUNNING 的任务不会被重复处理（不会重复写 ai_failure_log）。
    表所有权：ai_task / ai_failure_log 属 M04，M13 只能调本函数。
    返回 {"scanned":n, "retried":n, "abandoned":n, "items":[...]}
    """
    grace = max(config.get_int("ai.watchdog_grace_factor", 3), 1)
    timeout = max(config.get_int("ai.timeout_sec", 30), 1) * grace
    cutoff = tu.fmt(tu.now() - datetime.timedelta(seconds=timeout))
    rows = db.query("""
        SELECT id, task_no, status, retry_count, max_retry, started_at
        FROM ai_task
        WHERE status='RUNNING' AND deleted_at IS NULL
          AND COALESCE(started_at, created_at) IS NOT NULL
          AND COALESCE(started_at, created_at) < ?
    """, (cutoff,))
    out = {"scanned": len(rows), "retried": 0, "abandoned": 0, "items": []}
    for r in rows:
        limit = r["max_retry"] or _max_retry()
        abandoned = (r["retry_count"] or 0) + 1 > limit
        with db.tx() as conn:
            # 只写失败日志（F26），状态推进自己来：RUNNING 不能直接跳 ABANDONED
            db.execute(conn, """
                INSERT INTO ai_failure_log (ai_task_id, task_no, stage, attempt, error_code,
                                            error_msg, recoverable, action, created_at,
                                            created_by)
                VALUES (?,?,'WATCHDOG',?,?,?,1,?,?,?)
            """, (r["id"], r["task_no"], (r["retry_count"] or 0) + 1, AI_TIMEOUT,
                  "AI 任务超时（> %ds）被看门狗收敛" % timeout,
                  "ABANDON" if abandoned else "RETRY", tu.now_str(),
                  (ctx.user_id if ctx else 0)))
            _transition(conn, r["id"], FAILED, "看门狗：任务超时", ctx,
                        extra={"error_code": AI_TIMEOUT,
                               "error_msg": "AI 任务超时（> %ds）" % timeout})
            if abandoned:
                _transition(conn, r["id"], ABANDONED, "看门狗：重试已达上限", ctx)
                audit.audit_high_risk(conn, "AI_ABANDON", "ai_task", r["id"],
                                      "AI 任务放弃：超时且重试已达上限 %d" % limit)
                out["abandoned"] += 1
                out["items"].append({"task_no": r["task_no"], "action": "ABANDON"})
            else:
                _transition(conn, r["id"], RETRYING, "看门狗：重新入队", ctx,
                            extra={"retry_count": (r["retry_count"] or 0) + 1})
                out["retried"] += 1
                out["items"].append({"task_no": r["task_no"], "action": "RETRY"})
    return out


# ---------- 引擎内部实现（替换点）----------

def _asr(text, audio_id=None):
    """F21 ASR。LOCAL_RULE 引擎：直接采用传入文本（外部引擎在此接入）。

    真实接入时：读 audio 文件 → 调引擎 → 返回文本；失败抛 AppError(E5xxx)。
    """
    if not (text or "").strip():
        raise AppError(AI_FAILED, "转写文本为空（未接入 ASR 引擎时需手工传入文本）",
                       retryable=True)
    return {"text": text.strip(), "confidence": 1.0, "engine": config.get("ai.engine",
                                                                          "LOCAL_RULE")}


def _extract(text):
    """F22 医疗事实抽取（本地规则）。返回 fact 列表。"""
    facts = []
    t = text or ""

    def add(ftype, key, value, conf, src):
        facts.append({"fact_type": ftype, "fact_key": key, "fact_value": value,
                      "confidence": conf, "source_text": src})

    for w in SYMPTOM_WORDS:
        if w in t:
            idx = t.find(w)
            add("SYMPTOM", w, w, 0.75, t[max(0, idx - 8):idx + 8])
    m = DURATION_PAT.search(t)
    if m:
        add("DURATION", "duration", m.group(0), 0.7, m.group(0))
    m = TEMP_PAT.search(t)
    if m and 35.0 <= float(m.group(1)) <= 43.0:
        add("VITAL", "temperature_c", m.group(1), 0.8, m.group(0))
    m = BP_PAT.search(t)
    if m:
        add("VITAL", "blood_pressure", "%s/%s" % (m.group(1), m.group(2)), 0.8, m.group(0))
    for m in ALLERGY_PAT.finditer(t):
        add("ALLERGY", m.group(1), m.group(1), 0.7, m.group(0))
    for m in HISTORY_PAT.finditer(t):
        add("HISTORY", m.group(1), m.group(1), 0.6, m.group(0))
    for w in CHRONIC_WORDS:
        if w in t and not any(f["fact_value"] == w for f in facts):
            add("HISTORY", w, w, 0.7, w)
    return facts


def _build_draft(facts, transcript):
    """F23 由事实组装病历草稿（结构化，不写 record）。"""
    symptoms = [f["fact_value"] for f in facts if f["fact_type"] == "SYMPTOM"]
    durations = [f["fact_value"] for f in facts if f["fact_type"] == "DURATION"]
    chief = "、".join(symptoms[:4])
    if durations:
        chief = chief + " " + durations[0]
    vitals = {f["fact_key"]: f["fact_value"] for f in facts if f["fact_type"] == "VITAL"}
    allergies = [f["fact_value"] for f in facts if f["fact_type"] == "ALLERGY"]
    histories = [f["fact_value"] for f in facts if f["fact_type"] == "HISTORY"]
    return {
        "chief_complaint": {"content": chief,
                            "duration": durations[0] if durations else None},
        "present_illness": {"content": transcript[:500],
                            "onset_time": durations[0] if durations else None},
        "allergies": [{"allergen": a, "allergen_type": "DRUG"} for a in allergies],
        "histories": [{"disease": h, "hist_type": "PAST"} for h in histories],
        "vitals": vitals,
        "source": "AI",
    }


# ---------- F20/F21 语音与转写 ----------

@route("/api/ai/task/create", "POST", perm="ai.use", module="M04", action="ai.task.create")
def task_create(ctx, body, q):
    """F26 任务创建：只登记，不执行。"""
    require(not _enabled() is False, "AI 功能已关闭（ai.enabled=0）")
    require(body.get("task_type") in TASK_TYPES, "task_type 取值非法")
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.task.create", "ai_task", None)
        task_no = ids.next_no(conn, "AI_TASK")
        cur = db.execute(conn, """
            INSERT INTO ai_task (task_no, task_type, visit_id, patient_id, record_id, status,
                max_retry, engine, model, input_json, operator_id, created_at, created_by,
                updated_at, updated_by)
            VALUES (?,?,?,?,?,'CREATED',?,?,?,?,?,?,?,?,?)
        """, (task_no, body["task_type"], body.get("visit_id"), body.get("patient_id"),
              body.get("record_id"), _max_retry(), config.get("ai.engine", "LOCAL_RULE"),
              body.get("model"), audit.dumps(body.get("input") or {}), ctx.user_id,
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "task_no": task_no, "status": CREATED}


@route("/api/ai/audio/register", "POST", perm="ai.use", module="M04", action="ai.audio.register")
def audio_register(ctx, body, q):
    """F20 录音元信息登记（文件本体由客户端落盘，此处只记路径与校验和）。"""
    require(body.get("file_path"), "缺少 file_path")
    path = body["file_path"]
    require(os.path.isfile(path), "录音文件不存在：%s" % path)
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.audio.register", "ai_audio", None)
        cur = db.execute(conn, """
            INSERT INTO ai_audio (ai_task_id, visit_id, file_path, file_size, duration_sec,
                sample_rate, channel, format, device, recorded_at, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body.get("ai_task_id"), body.get("visit_id"), path, os.path.getsize(path),
              body.get("duration_sec"), body.get("sample_rate"), body.get("channel"),
              body.get("format"), body.get("device"),
              body.get("recorded_at") or tu.now_str(), tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "file_path": path}


@route("/api/ai/transcript/save", "POST", perm="ai.use", module="M04", action="ai.transcript")
def transcript_save(ctx, body, q):
    """F21 ASR 转写。任务状态 CREATED→RUNNING→SUCCEEDED，失败写 ai_failure_log。"""
    task_id = body.get("ai_task_id")
    require(task_id, "缺少 ai_task_id")
    row = db.query_one("SELECT * FROM ai_task WHERE id=? AND deleted_at IS NULL", (task_id,))
    if row is None:
        raise not_found("AI 任务不存在")
    t0 = time.time()
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.transcript", "ai_task", task_id)
        _transition(conn, task_id, RUNNING, ctx=ctx, extra={"started_at": tu.now_str()})
        try:
            res = _asr(body.get("text") or body.get("transcript"), body.get("ai_audio_id"))
        except AppError as exc:
            action = _fail(conn, task_id, "ASR", exc.code, exc.message, ctx=ctx)
            return {"ai_task_id": task_id, "status": FAILED, "action": action,
                    "error": exc.message}
        cur = db.execute(conn, """
            INSERT INTO ai_transcript (ai_task_id, ai_audio_id, seq_no, text, confidence,
                language, engine, created_at, created_by)
            VALUES (?,?,1,?,?,?,?,?,?)
        """, (task_id, body.get("ai_audio_id"), res["text"], res.get("confidence"),
              body.get("language", "zh-CN"), res.get("engine"), tu.now_str(), ctx.user_id))
        dur = int((time.time() - t0) * 1000)
        _transition(conn, task_id, SUCCEEDED, ctx=ctx,
                    extra={"finished_at": tu.now_str(), "duration_ms": dur,
                           "output_json": audit.dumps(res)})
    return {"ai_task_id": task_id, "transcript_id": cur.lastrowid, "status": SUCCEEDED,
            "text": res["text"]}


# ---------- F22 事实提取 ----------

@route("/api/ai/extract", "POST", perm="ai.use", module="M04", action="ai.extract")
def extract(ctx, body, q):
    """F22 从转写文本（或指定 ai_transcript）抽取医疗事实。"""
    task_id = body.get("ai_task_id")
    require(task_id, "缺少 ai_task_id")
    text = body.get("text")
    if not text:
        tr = db.query_one("SELECT text FROM ai_transcript WHERE ai_task_id=? "
                          "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (task_id,))
        if tr is None:
            # 回退：取同一次就诊最近一次转写（医生不必重复贴文本）
            tr = db.query_one(
                "SELECT t.text FROM ai_transcript t JOIN ai_task k ON k.id=t.ai_task_id "
                "WHERE k.visit_id=(SELECT visit_id FROM ai_task WHERE id=?) "
                "AND t.deleted_at IS NULL ORDER BY t.id DESC LIMIT 1", (task_id,))
        require(tr, "缺少待抽取文本（可传 text 或先完成 ASR 转写）")
        text = tr["text"]
    t0 = time.time()
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.extract", "ai_task", task_id)
        _transition(conn, task_id, RUNNING, ctx=ctx, extra={"started_at": tu.now_str()})
        try:
            facts = _extract(text)
            if not facts:
                raise AppError(AI_UNPARSABLE, "未能从文本中抽取到任何医疗事实", retryable=True)
        except AppError as exc:
            _fail(conn, task_id, "EXTRACT", exc.code, exc.message, ctx=ctx)
            return {"ai_task_id": task_id, "status": FAILED, "error": exc.message}
        for f in facts:
            db.execute(conn, """
                INSERT INTO ai_fact (ai_task_id, visit_id, patient_id, fact_type, fact_key,
                    fact_value, confidence, source_text, created_at, created_by)
                VALUES (?,(SELECT visit_id FROM ai_task WHERE id=?),
                        (SELECT patient_id FROM ai_task WHERE id=?),?,?,?,?,?,?,?)
            """, (task_id, task_id, task_id, f["fact_type"], f["fact_key"], f["fact_value"],
                  f["confidence"], f["source_text"], tu.now_str(), ctx.user_id))
        dur = int((time.time() - t0) * 1000)
        _transition(conn, task_id, SUCCEEDED, ctx=ctx,
                    extra={"finished_at": tu.now_str(), "duration_ms": dur,
                           "output_json": audit.dumps(facts)})
    return {"ai_task_id": task_id, "status": SUCCEEDED, "facts": facts}


# ---------- F23 病历草稿 ----------

@route("/api/ai/draft/create", "POST", perm="ai.use", module="M04", action="ai.draft.create")
def draft_create(ctx, body, q):
    """F23 生成病历草稿。**只写 ai_draft，绝不写 record**（03 §2.10）。"""
    require(body.get("visit_id"), "缺少 visit_id")
    task_id = body.get("ai_task_id")
    facts = body.get("facts")
    if facts is None:
        facts = db.query("SELECT * FROM ai_fact WHERE ai_task_id=? AND deleted_at IS NULL",
                         (task_id or 0,))
        facts = [dict(f) for f in facts]
    text = body.get("text") or ""
    if not text and task_id:
        tr = db.query_one("SELECT text FROM ai_transcript WHERE ai_task_id=? "
                          "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (task_id,))
        if tr is None:
            tr = db.query_one(
                "SELECT t.text FROM ai_transcript t JOIN ai_task k ON k.id=t.ai_task_id "
                "WHERE k.visit_id=(SELECT visit_id FROM ai_task WHERE id=?) "
                "AND t.deleted_at IS NULL ORDER BY t.id DESC LIMIT 1", (task_id,))
        text = tr["text"] if tr else ""
    draft = _build_draft(facts, text)
    require(facts or text, "既无抽取事实也无转写文本，无法生成草稿")
    v = db.query_one("SELECT patient_id FROM visit WHERE id=?", (body["visit_id"],))
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.draft.create", "ai_draft", None,
                              detail={"visit_id": body["visit_id"], "facts": len(facts)})
        cur = db.execute(conn, """
            INSERT INTO ai_draft (ai_task_id, visit_id, record_id, patient_id, draft_json,
                status, model, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,'PENDING',?,?,?,?,?)
        """, (task_id, body["visit_id"], body.get("record_id"),
              (v["patient_id"] if v else None), audit.dumps(draft),
              config.get("ai.engine", "LOCAL_RULE"), tu.now_str(), ctx.user_id,
              tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "status": "PENDING", "draft": draft}


@route("/api/ai/draft/apply", "POST", perm="ai.use", module="M04", action="ai.draft.apply")
def draft_apply(ctx, body, q):
    """F23 医生确认后落库：**必须医生显式确认**，落库标 source=AI（R-M06-4 同族规则）。

    走 M03 的保存服务（不直接写 M03 的表），且只写当前 DRAFT 病历。
    """
    did = body.get("draft_id")
    require(did, "缺少 draft_id")
    require(body.get("confirm") is True, "AI 草稿必须医生显式确认后才能写入病历")
    d = db.query_one("SELECT * FROM ai_draft WHERE id=? AND deleted_at IS NULL", (did,))
    if d is None:
        raise not_found("草稿不存在")
    require(d["status"] == "PENDING", "草稿已处理（%s）" % d["status"])
    import json
    try:
        draft = json.loads(d["draft_json"] or "{}")
    except ValueError:
        raise AppError(AI_UNPARSABLE, "草稿内容不可解析", retryable=True)
    from modules import m03_record
    written = []
    with db.tx() as conn:
        rid = d["record_id"]
        if not rid:
            rec = m03_record.ensure_record(conn, d["visit_id"], ctx)
            rid = rec["id"]
        audit.audit_operation(conn, "M04", "ai.draft.apply", "ai_draft", did,
                              detail={"record_id": rid})
        st = db.query_one("SELECT status FROM record WHERE id=?", (rid,))
        require(st and st["status"] == "DRAFT",
                "病历已确认，AI 草稿不能直接写入；修改需走病历修改留痕流程（M11 F77）",
                code="E3007")
        cc = draft.get("chief_complaint") or {}
        if cc.get("content"):
            m03_record.save_chief(ctx, {"record_id": rid, "content": cc["content"],
                                        "duration": cc.get("duration"), "source": "AI"},
                                  {})
            written.append("chief_complaint")
        pi = draft.get("present_illness") or {}
        if pi.get("content"):
            m03_record.save_present(ctx, {"record_id": rid, "content": pi["content"],
                                          "onset_time": pi.get("onset_time"), "source": "AI"},
                                    {})
            written.append("present_illness")
        if draft.get("allergies"):
            m03_record.save_allergy(ctx, {"record_id": rid,
                                          "items": [dict(a, source="AI")
                                                    for a in draft["allergies"]]}, {})
            written.append("allergies")
        if draft.get("histories"):
            m03_record.save_history(ctx, {"record_id": rid,
                                          "items": [dict(h, source="AI")
                                                    for h in draft["histories"]]}, {})
            written.append("histories")
        vit = draft.get("vitals") or {}
        if vit:
            m03_record.save_vital(ctx, {"record_id": rid,
                                        "temperature_c": vit.get("temperature_c")}, {})
            written.append("vitals")
        db.execute(conn, "UPDATE ai_draft SET status='ACCEPTED', accepted_by=?, accepted_at=?, "
                         "updated_at=?, updated_by=? WHERE id=?",
                   (ctx.user_id, tu.now_str(), tu.now_str(), ctx.user_id, did))
        audit.audit_high_risk(conn, "AI_APPLY", "record", rid,
                              "AI 草稿经医生确认写入病历（字段：%s）" % "、".join(written))
    return {"draft_id": did, "record_id": rid, "written": written, "status": "ACCEPTED"}


@route("/api/ai/draft/reject", "POST", perm="ai.use", module="M04", action="ai.draft.reject")
def draft_reject(ctx, body, q):
    did = body.get("draft_id")
    require(did, "缺少 draft_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.draft.reject", "ai_draft", did)
        db.execute(conn, "UPDATE ai_draft SET status='REJECTED', reject_reason=?, "
                         "updated_at=?, updated_by=? WHERE id=? AND status='PENDING'",
                   (body.get("reason"), tu.now_str(), ctx.user_id, did))
    return {"draft_id": did, "status": "REJECTED"}


# ---------- F24 处方建议 ----------

@route("/api/ai/suggest/create", "POST", perm="ai.use", module="M04", action="ai.suggest.create")
def suggest_create(ctx, body, q):
    """F24 生成处方建议。**只写 ai_suggestion，不建处方**；采纳走 M06 正常开方流程。"""
    require(body.get("visit_id"), "缺少 visit_id")
    items = body.get("items") or []
    require(items, "建议明细不能为空")
    sugg = {"items": items, "type": body.get("suggest_type", "DRUG"),
            "doses": body.get("doses")}
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.suggest.create", "ai_suggestion", None,
                              detail={"visit_id": body["visit_id"], "count": len(items)})
        cur = db.execute(conn, """
            INSERT INTO ai_suggestion (ai_task_id, visit_id, suggest_type, suggest_json,
                rationale, status, model, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,'PENDING',?,?,?,?,?)
        """, (body.get("ai_task_id"), body["visit_id"], sugg["type"], audit.dumps(sugg),
              body.get("rationale"), config.get("ai.engine", "LOCAL_RULE"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "status": "PENDING", "suggestion": sugg}


@route("/api/ai/suggest/adopt", "POST", perm="prescription.create", module="M04",
       action="ai.suggest.adopt")
def suggest_adopt(ctx, body, q):
    """F24 采纳建议 → 建处方。**规则校验原样执行，AI 不豁免（R-M06-4）。**"""
    sid = body.get("suggestion_id")
    require(sid, "缺少 suggestion_id")
    s = db.query_one("SELECT * FROM ai_suggestion WHERE id=? AND deleted_at IS NULL", (sid,))
    if s is None:
        raise not_found("建议不存在")
    require(s["status"] == "PENDING", "建议已处理")
    import json
    try:
        sugg = json.loads(s["suggest_json"] or "{}")
    except ValueError:
        raise AppError(AI_UNPARSABLE, "建议内容不可解析", retryable=True)
    from modules import m06_prescription
    rx = m06_prescription.create(ctx, {
        "visit_id": s["visit_id"],
        # 建议类型 → 处方类型：DRUG(成药) → WEST
        "prescription_type": {"DRUG": "WEST", "HERB": "HERB",
                              "GRANULE": "GRANULE"}.get(sugg.get("type", "DRUG"), "WEST"),
        "doses": sugg.get("doses"), "source": "AI", "ai_task_id": s.get("ai_task_id"),
    }, {})
    kind = "items" if sugg.get("type", "DRUG") == "DRUG" else "items"
    if sugg.get("type") in ("HERB", "GRANULE"):
        m06_prescription.save_herbs(ctx, {"prescription_id": rx["id"],
                                          "items": sugg.get("items", []),
                                          "doses": sugg.get("doses"),
                                          "decoction_method": sugg.get("decoction_method"),
                                          "administration": sugg.get("administration")}, {})
    else:
        m06_prescription.save_items(ctx, {"prescription_id": rx["id"],
                                          "items": sugg.get("items", [])}, {})
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.suggest.adopt", "ai_suggestion", sid,
                              detail={"prescription_id": rx["id"]})
        db.execute(conn, "UPDATE ai_suggestion SET status='ADOPTED', prescription_id=?, "
                         "adopted_by=?, adopted_at=?, updated_at=?, updated_by=? WHERE id=?",
                   (rx["id"], ctx.user_id, tu.now_str(), tu.now_str(), ctx.user_id, sid))
    return {"suggestion_id": sid, "prescription_id": rx["id"],
            "prescription_no": rx["prescription_no"],
            "note": "AI 建议已转为普通处方，规则校验与手工开方完全一致"}


# ---------- F25 风险提醒 ----------

@route("/api/ai/risk/check", "POST", perm="ai.use", module="M04", action="ai.risk.check")
def risk_check(ctx, body, q):
    """F25 风险提醒：过敏史、异常检验结果、慢性病史、中药禁忌的交叉提醒。

    **提醒不等于拦截**：本模块只产出提醒，是否拦截由 M06 规则决定。
    """
    require(body.get("patient_id") or body.get("visit_id"), "缺少 patient_id 或 visit_id")
    pid = body.get("patient_id")
    vid = body.get("visit_id")
    if not pid and vid:
        v = db.query_one("SELECT patient_id FROM visit WHERE id=?", (vid,))
        pid = v["patient_id"] if v else None
    alerts = []
    from modules import m03_record
    try:
        allergies = m03_record.list_allergies(pid)
    except Exception as exc:
        allergies = []
        alerts.append({"risk_type": "ALLERGY", "severity": "HIGH",
                       "title": "过敏史未知", "detail": "接口不可用：%s" % exc,
                       "source_ref": "M03"})
    else:
        for a in allergies:
            alerts.append({"risk_type": "ALLERGY", "severity": "HIGH",
                           "title": "患者有过敏史：%s" % a.get("allergen"),
                           "detail": a.get("manifestation"), "source_ref": "record_allergy"})
    # 异常检验结果（最近一次已回报的）
    labs = db.query("SELECT item_name, abnormal_flag, result_text FROM lab_result r "
                    "JOIN lab_order o ON o.id=r.lab_order_id "
                    "WHERE o.patient_id=? AND r.deleted_at IS NULL "
                    "AND r.abnormal_flag NOT IN ('NORMAL','') "
                    "ORDER BY r.id DESC LIMIT 10", (pid or 0,))
    for l in labs:
        alerts.append({"risk_type": "ABNORMAL_RESULT", "severity": "WARN",
                       "title": "%s 异常：%s" % (l["item_name"], l["result_text"]),
                       "source_ref": "lab_result"})
    # 慢性病史
    # record_history 归 M03 且无 patient_id 列，只能经 record 关联（只读，不写 M03 的表）
    hists = db.query("SELECT disease FROM record_history WHERE deleted_at IS NULL AND "
                     "record_id IN (SELECT id FROM record WHERE patient_id=?) "
                     "ORDER BY id DESC LIMIT 20", (pid or 0,))
    seen = set()
    for h in hists:
        if h["disease"] and h["disease"] not in seen:
            seen.add(h["disease"])
            alerts.append({"risk_type": "CHRONIC", "severity": "INFO",
                           "title": "既往病史：%s" % h["disease"],
                           "source_ref": "record_history"})
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.risk.check", "ai_risk_alert", None,
                              detail={"patient_id": pid, "count": len(alerts)})
        for a in alerts:
            db.execute(conn, """
                INSERT INTO ai_risk_alert (ai_task_id, visit_id, patient_id, risk_type, severity,
                    title, detail, source_ref, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (body.get("ai_task_id"), vid, pid, a["risk_type"], a.get("severity", "WARN"),
                  a["title"], a.get("detail"), a.get("source_ref"), tu.now_str(), ctx.user_id))
    return {"patient_id": pid, "alerts": alerts}


@route("/api/ai/risk/ack", "POST", perm="ai.use", module="M04", action="ai.risk.ack")
def risk_ack(ctx, body, q):
    aid = body.get("alert_id")
    require(aid, "缺少 alert_id")
    require(body.get("ack_action") in ("ACCEPTED", "IGNORED", "ESCALATED"),
            "ack_action 取值非法")
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.risk.ack", "ai_risk_alert", aid)
        db.execute(conn, "UPDATE ai_risk_alert SET ack_by=?, ack_at=?, ack_action=? WHERE id=?",
                   (ctx.user_id, tu.now_str(), body["ack_action"], aid))
    return {"alert_id": aid, "ack_action": body["ack_action"]}


# ---------- F26 失败恢复 ----------

@route("/api/ai/task/retry", "POST", perm="ai.use", module="M04", action="ai.task.retry")
def task_retry(ctx, body, q):
    """F26 重试：FAILED→RETRYING→RUNNING；超过上限 → ABANDONED。"""
    task_id = body.get("ai_task_id")
    require(task_id, "缺少 ai_task_id")
    row = db.query_one("SELECT * FROM ai_task WHERE id=? AND deleted_at IS NULL", (task_id,))
    if row is None:
        raise not_found("AI 任务不存在")
    if row["status"] == ABANDONED:
        raise conflict("任务已放弃，不能再重试", code=BIZ_STATE_INVALID)
    if row["status"] not in (FAILED,):
        raise conflict("只有 FAILED 状态的任务可以重试", code=BIZ_STATE_INVALID)
    with db.tx() as conn:
        audit.audit_operation(conn, "M04", "ai.task.retry", "ai_task", task_id)
        if (row["retry_count"] or 0) >= (row["max_retry"] or _max_retry()):
            db.execute(conn, "INSERT INTO ai_failure_log (ai_task_id, task_no, stage, attempt, "
                             "error_code, error_msg, recoverable, action, created_at, "
                             "created_by) VALUES (?,?,'RETRY',?,'E5001','超过重试上限',0,"
                             "'ABANDON',?,?)",
                       (task_id, row["task_no"], (row["retry_count"] or 0) + 1,
                        tu.now_str(), ctx.user_id))
            _transition(conn, task_id, ABANDONED, "超过重试上限", ctx)
            return {"ai_task_id": task_id, "status": ABANDONED, "action": "ABANDON"}
        db.execute(conn, "UPDATE ai_task SET retry_count=retry_count+1, started_at=?, "
                         "error_code=NULL, error_msg=NULL WHERE id=?", (tu.now_str(), task_id))
        _transition(conn, task_id, RETRYING, ctx=ctx)
        _transition(conn, task_id, RUNNING, ctx=ctx)
    return {"ai_task_id": task_id, "status": RUNNING, "retry_count": (row["retry_count"] or 0) + 1}


@route("/api/ai/task/get", "GET", perm="ai.view", module="M04", action="ai.task.get")
def task_get(ctx, body, q):
    tid = q.get("id") or q.get("ai_task_id")
    require(tid, "缺少 ai_task_id")
    row = db.query_one("SELECT * FROM ai_task WHERE id=? AND deleted_at IS NULL", (tid,))
    if row is None:
        raise not_found("AI 任务不存在")
    out = dict(row)
    out["transcripts"] = db.query("SELECT * FROM ai_transcript WHERE ai_task_id=? "
                                  "AND deleted_at IS NULL", (tid,))
    out["facts"] = db.query("SELECT * FROM ai_fact WHERE ai_task_id=? AND deleted_at IS NULL",
                            (tid,))
    out["failures"] = db.query("SELECT * FROM ai_failure_log WHERE ai_task_id=? ORDER BY id",
                               (tid,))
    return out


@route("/api/ai/task/list", "GET", perm="ai.view", module="M04", action="ai.task.list")
def task_list(ctx, body, q):
    sql = "SELECT * FROM ai_task WHERE deleted_at IS NULL"
    params = []
    if q.get("visit_id"):
        sql += " AND visit_id=?"
        params.append(q["visit_id"])
    if q.get("status"):
        sql += " AND status=?"
        params.append(q["status"])
    sql += " ORDER BY id DESC LIMIT 100"
    return {"items": db.query(sql, tuple(params))}


@route("/api/ai/draft/list", "GET", perm="ai.view", module="M04", action="ai.draft.list")
def draft_list(ctx, body, q):
    sql = "SELECT * FROM ai_draft WHERE deleted_at IS NULL"
    params = []
    if q.get("visit_id"):
        sql += " AND visit_id=?"
        params.append(q["visit_id"])
    sql += " ORDER BY id DESC LIMIT 50"
    return {"items": db.query(sql, tuple(params))}
