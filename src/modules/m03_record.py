# -*- coding: utf-8 -*-
"""M03 临床病历（F10-F19 / F108 中医四诊 / F109 辨证论治 / F126 模板与常用语）。

表所有权：record / record_status_log / record_* / vital_sign / physical_exam /
         diagnosis / medical_order / tcm_four_diagnosis / tcm_syndrome /
         record_template / record_signature
边界：药品只能走处方（F34/F111）——medical_order 拒绝 order_type='DRUG'。
状态机：03 §2.3 —— DRAFT → CONFIRMED → ARCHIVED → SEALED；CONFIRMED 后不得直接编辑。
"""

from core import db, audit, timeutil as tu
from core.api import route
from core.errors import (require, AppError, conflict, not_found, BIZ_ALREADY_CONFIRMED,
                         DATA_REQUIRED)

DRAFT = "DRAFT"
CONFIRMED = "CONFIRMED"
ARCHIVED = "ARCHIVED"
SEALED = "SEALED"
UNSEALED = "UNSEALED"

TRANSITIONS = {
    DRAFT:     [CONFIRMED],
    CONFIRMED: [ARCHIVED],
    ARCHIVED:  [SEALED],
    SEALED:    [UNSEALED],
    UNSEALED:  [ARCHIVED],          # 解封→更正→**重新归档**（开新归档号，M11 S3 闭环）
}

EDITABLE = (DRAFT,)


def transition(conn, record_id, to_status, reason=None, ctx=None, extra=None):
    row = db.query_one("SELECT * FROM record WHERE id=? AND deleted_at IS NULL", (record_id,))
    if row is None:
        raise not_found("病历不存在")
    frm = row["status"]
    if to_status not in TRANSITIONS.get(frm, []):
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "record", record_id,
                              "非法状态转换 %s→%s" % (frm, to_status))
        raise conflict("病历状态不允许从 %s 转换到 %s" % (frm, to_status), code="E3001")
    sets = ["status=?", "updated_at=?", "updated_by=?", "version=version+1"]
    params = [to_status, tu.now_str(), (ctx.user_id if ctx else 0)]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(record_id)
    db.execute(conn, "UPDATE record SET %s WHERE id=?" % ",".join(sets), tuple(params))
    audit.record_status_log(conn, "record_status_log", "record_id", record_id,
                            frm, to_status, reason)
    audit.audit_data_change(conn, "record", record_id, "status", frm, to_status, reason)
    return frm, to_status


def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """对外服务接口：患者合并时改挂病历（M01 通过本接口调用）。"""
    cur = db.execute(conn, "UPDATE record SET patient_id=? "
                           "WHERE patient_id=? AND deleted_at IS NULL",
                     (new_patient_id, old_patient_id))
    return cur.rowcount


# F77 允许修改留痕的字段白名单（M11 通过 apply_amendment 调用，路径 = 别名.列名）
AMENDABLE = {
    "chief_complaint":  ("record_chief_complaint", ["content", "duration"]),
    "present_illness":  ("record_present_illness",
                         ["content", "onset_time", "onset_type", "aggravate", "relieve",
                          "treatment_hx", "general"]),
    "tcm_four":         ("tcm_four_diagnosis",
                         ["inspection", "auscultation", "interrogation", "palpation",
                          "tongue_body", "tongue_coating", "pulse_desc", "constitution",
                          "note"]),
}


def build_snapshot(record_id):
    """对外服务接口（M11 F76 消费）：病历完整内容快照，**自包含 dict**。

    M11 只能经本接口取内容，不得直接查 M03 的表（R-M11-10）。
    """
    rec = db.query_one("SELECT id, visit_id, patient_id, record_type, status, version, source, "
                       "confirmed_by, confirmed_at, note "
                       "FROM record WHERE id=? AND deleted_at IS NULL", (record_id,))
    if rec is None:
        raise not_found("病历不存在")

    def rows(sql):
        return db.query(sql, (record_id,))

    return {
        "record": dict(rec),
        "chief_complaint": db.query_one(
            "SELECT content, duration FROM record_chief_complaint "
            "WHERE record_id=? AND deleted_at IS NULL", (record_id,)),
        "present_illness": db.query_one(
            "SELECT content, onset_time, onset_type, aggravate, relieve, treatment_hx, general "
            "FROM record_present_illness WHERE record_id=? AND deleted_at IS NULL", (record_id,)),
        "histories": rows("SELECT hist_type, disease, diagnosed_at, treatment, outcome, content, "
                          "is_abnormal FROM record_history WHERE record_id=? AND deleted_at IS NULL "
                          "ORDER BY id"),
        "allergies": rows("SELECT allergen, allergen_type, manifestation, severity "
                          "FROM record_allergy WHERE record_id=? AND deleted_at IS NULL ORDER BY id"),
        "vitals": rows("SELECT temperature_c, pulse, respiration, systolic, diastolic, spo2, "
                       "height_cm, weight_kg, bmi, pain_score, measured_at FROM vital_sign "
                       "WHERE record_id=? AND deleted_at IS NULL ORDER BY id"),
        "exams": rows("SELECT section, content, is_abnormal, seq_no FROM physical_exam "
                      "WHERE record_id=? AND deleted_at IS NULL ORDER BY seq_no, id"),
        "exam_refs": rows("SELECT ref_type, lab_order_id, lab_result_id, summary, occurred_at "
                          "FROM record_exam_ref WHERE record_id=? AND deleted_at IS NULL"),
        "diagnoses": rows("SELECT diag_type, icd10_code, icd10_name, tcm_code, tcm_name, "
                          "is_primary, seq_no, confidence FROM diagnosis "
                          "WHERE record_id=? AND deleted_at IS NULL ORDER BY seq_no, id"),
        "orders": rows("SELECT order_type, content, dosage, frequency, days, seq_no, status "
                       "FROM medical_order WHERE record_id=? AND deleted_at IS NULL "
                       "ORDER BY seq_no, id"),
        "tcm_four": db.query_one(
            "SELECT inspection, auscultation, interrogation, palpation, tongue_body, "
            "tongue_coating, pulse_desc, constitution, note FROM tcm_four_diagnosis "
            "WHERE record_id=? AND deleted_at IS NULL", (record_id,)),
        "syndromes": rows("SELECT syndrome_name, syndrome_code, syndrome_category, basis, "
                          "treatment_principle, treatment_method, formula_ref, seq_no "
                          "FROM tcm_syndrome WHERE record_id=? AND deleted_at IS NULL "
                          "ORDER BY seq_no, id"),
        "signatures": rows("SELECT signer_id, signer_name, role_code, signed_at, sign_method, "
                           "version_no FROM record_signature WHERE record_id=? ORDER BY id"),
    }


def apply_amendment(conn, record_id, changes, ctx):
    """对外服务接口（M11 F77 消费）：已确认病历按 field_path 写内容。

    这是 **CONFIRMED 之后唯一允许写 M03 内容的入口**；调用方必须先在
    record_amendment 留下修改痕迹，且整个流程在同一事务内（R-M11-1）。
    返回 [{field_path, before_value, after_value}]。
    """
    rec = db.query_one("SELECT * FROM record WHERE id=? AND deleted_at IS NULL", (record_id,))
    if rec is None:
        raise not_found("病历不存在")
    if rec["status"] not in (CONFIRMED, UNSEALED):
        raise conflict("只有 CONFIRMED（或解封后 UNSEALED）病历可通过修改留痕写入内容"
                       "（当前 %s）" % rec["status"], code=BIZ_STATE_INVALID)
    out = []
    for ch in changes or []:
        path = (ch.get("field_path") or "").strip()
        alias, _, col = path.partition(".")
        if alias not in AMENDABLE:
            raise AppError(DATA_REQUIRED, "不支持修改留痕的字段：%s" % path)
        table, cols = AMENDABLE[alias]
        if col not in cols:
            raise AppError(DATA_REQUIRED, "字段 %s 不在可修改范围内" % path)
        row = db.query_one("SELECT * FROM %s WHERE record_id=? AND deleted_at IS NULL" % table,
                           (record_id,))
        before = row[col] if row else None
        after = ch.get("after_value")
        now = tu.now_str()
        if row is None:
            cur = db.execute(conn, """
                INSERT INTO %s (record_id, %s, source, created_at, created_by,
                                updated_at, updated_by)
                VALUES (?,?,'DOCTOR',?,?,?,?)
            """ % (table, col), (record_id, after, now, ctx.user_id, now, ctx.user_id))
            rid_row = cur.lastrowid
        else:
            rid_row = row["id"]
            db.execute(conn, "UPDATE %s SET %s=?, updated_at=?, updated_by=? WHERE id=?"
                       % (table, col), (after, now, ctx.user_id, row["id"]))
        audit.audit_data_change(conn, table, rid_row, col, before, after,
                                ch.get("reason") or "病历修改留痕")
        out.append({"field_path": path, "before_value": before, "after_value": after})
    db.execute(conn, "UPDATE record SET updated_at=?, updated_by=? WHERE id=?",
               (tu.now_str(), ctx.user_id, record_id))
    return out


def list_allergies(patient_id):
    """对外服务接口（F35 消费）：取患者过敏史。

    M06 只能经本接口读取，不得直接查 record_allergy（跨模块禁写、禁裸读）。
    **接口抛异常时，调用方必须按"过敏史未知"降级，绝不可视为"无过敏"（F35 R3）。**
    """
    require(patient_id, "缺少 patient_id")
    return db.query("SELECT * FROM record_allergy WHERE patient_id=? AND deleted_at IS NULL "
                    "ORDER BY id", (patient_id,))


def ensure_record(conn, visit_id, ctx):
    """接诊时确保存在一份 DRAFT 病历（幂等）。"""
    row = db.query_one("SELECT * FROM record WHERE visit_id=? AND deleted_at IS NULL",
                       (visit_id,))
    if row:
        return dict(row)
    v = db.query_one("SELECT * FROM visit WHERE id=?", (visit_id,))
    if v is None:
        raise not_found("就诊不存在")
    cur = db.execute(conn, """
        INSERT INTO record (visit_id, patient_id, record_type, status, source,
                            created_at, created_by, updated_at, updated_by)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (visit_id, v["patient_id"], "TCM" if v["visit_type"] == "TCM" else "WEST",
          DRAFT, "DOCTOR", tu.now_str(), ctx.user_id if ctx else 0,
          tu.now_str(), ctx.user_id if ctx else 0))
    rid = cur.lastrowid
    audit.record_status_log(conn, "record_status_log", "record_id", rid, None, DRAFT,
                            "接诊自动创建")
    return dict(db.query_one("SELECT * FROM record WHERE id=?", (rid,)))


def _load_editable(record_id):
    """CONFIRMED 之后不得直接编辑（修改走 M11 F77 建版本留痕）。"""
    row = db.query_one("SELECT * FROM record WHERE id=? AND deleted_at IS NULL", (record_id,))
    if row is None:
        raise not_found("病历不存在")
    if row["status"] not in EDITABLE:
        raise conflict("病历已确认（%s），不能直接编辑；修改需走病历修改留痕流程" % row["status"],
                       code=BIZ_ALREADY_CONFIRMED)
    return row


def _upsert_single(table, fields):
    """生成"有则更新、无则插入"的保存函数（用于 record 的一对一子表）。"""

    def save(ctx, body, q):
        rid = body.get("record_id")
        require(rid, "缺少 record_id")
        rec = _load_editable(rid)
        data = {f: body.get(f) for f in fields}
        data["source"] = body.get("source", "DOCTOR")
        with db.tx() as conn:
            audit.audit_operation(conn, "M03", "record.save.%s" % table, "record", rid,
                                  detail={"fields": list(data.keys())})
            row = db.query_one("SELECT * FROM %s WHERE record_id=?" % table, (rid,))
            if row is None:
                cols = ["record_id"] + list(data.keys()) + \
                       ["created_at", "created_by", "updated_at", "updated_by"]
                vals = [rid] + list(data.values()) + \
                       [tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id]
                db.execute(conn, "INSERT INTO %s (%s) VALUES (%s)" %
                           (table, ",".join(cols), ",".join("?" * len(vals))), tuple(vals))
                action = "created"
            else:
                for f, v in data.items():
                    if row[f] != v:
                        audit.audit_data_change(conn, table, row["id"], f, row[f], v,
                                                body.get("reason"))
                sets = ["%s=?" % f for f in data.keys()] + ["updated_at=?", "updated_by=?"]
                vals = list(data.values()) + [tu.now_str(), ctx.user_id]
                if row["deleted_at"]:
                    # 软删过的行：恢复使用（一对一表用 record_id 唯一约束，不能重复插入）
                    sets.append("deleted_at=?")
                    vals.append(None)
                    action = "restored"
                else:
                    action = "updated"
                vals.append(row["id"])
                db.execute(conn, "UPDATE %s SET %s WHERE id=?" % (table, ",".join(sets)),
                           tuple(vals))
            db.execute(conn, "UPDATE record SET updated_at=?, updated_by=? WHERE id=?",
                       (tu.now_str(), ctx.user_id, rid))
        return {"record_id": rid, "action": action}

    return save


# F10 主诉 / F11 现病史 / F108 中医四诊
save_chief = route("/api/record/save/chief", "POST", perm="record.edit",
                   module="M03", action="record.chief_complaint")(
    _upsert_single("record_chief_complaint", ["content", "duration"]))

save_present = route("/api/record/save/present", "POST", perm="record.edit",
                     module="M03", action="record.present_illness")(
    _upsert_single("record_present_illness",
                   ["content", "onset_time", "onset_type", "aggravate", "relieve",
                    "treatment_hx", "general"]))

save_tcm_four = route("/api/record/save/tcm_four", "POST", perm="record.edit",
                      module="M03", action="record.tcm_four_diagnosis")(
    _upsert_single("tcm_four_diagnosis",
                   ["inspection", "auscultation", "interrogation", "palpation",
                    "tongue_body", "tongue_coating", "pulse_desc", "constitution", "note"]))


@route("/api/record/create", "POST", perm="record.edit", module="M03", action="record.create")
def create(ctx, body, q):
    require(body.get("visit_id"), "缺少 visit_id")
    # 幂等接口：病历已存在时直接返回。审计由 API 层统一写，
    # 这里不再重复写一条（重复写会在前端重复进入病历页时把审计表刷爆）。
    with db.tx() as conn:
        rec = ensure_record(conn, body["visit_id"], ctx)
    return rec


@route("/api/record/get", "GET", perm="record.view", module="M03", action="record.get")
def get(ctx, body, q):
    visit_id = q.get("visit_id")
    rid = q.get("id") or q.get("record_id")
    if not rid and not visit_id:
        raise AppError("E2001", "缺少 record_id 或 visit_id")
    if not rid:
        row = db.query_one("SELECT id FROM record WHERE visit_id=? AND deleted_at IS NULL",
                           (visit_id,))
        if row is None:
            return {"exists": False}
        rid = row["id"]
    rec = db.query_one("SELECT * FROM record WHERE id=? AND deleted_at IS NULL", (rid,))
    if rec is None:
        raise not_found("病历不存在")
    out = dict(rec)
    # 契约：无论用 id 还是 visit_id 查，**只要查到就必须带 exists=True**。
    # 只在"该就诊还没有病历"时返回 {"exists": false}。
    # 不补这个字段的话，调用方写 `if (!r.exists)` 会永远成立 → 无限递归建病历。
    out["exists"] = True
    out["chief_complaint"] = db.query_one(
        "SELECT * FROM record_chief_complaint WHERE record_id=? AND deleted_at IS NULL", (rid,))
    out["present_illness"] = db.query_one(
        "SELECT * FROM record_present_illness WHERE record_id=? AND deleted_at IS NULL", (rid,))
    out["histories"] = db.query(
        "SELECT * FROM record_history WHERE record_id=? AND deleted_at IS NULL ORDER BY id", (rid,))
    out["allergies"] = db.query(
        "SELECT * FROM record_allergy WHERE (record_id=? OR patient_id=?) AND deleted_at IS NULL "
        "ORDER BY id", (rid, rec["patient_id"]))
    out["vitals"] = db.query(
        "SELECT * FROM vital_sign WHERE record_id=? AND deleted_at IS NULL ORDER BY id", (rid,))
    out["exams"] = db.query(
        "SELECT * FROM physical_exam WHERE record_id=? AND deleted_at IS NULL ORDER BY seq_no, id",
        (rid,))
    out["exam_refs"] = _exam_ref_view(rid)      # F16：含待回报/引用失效/源数据已更新提示
    out["diagnoses"] = db.query(
        "SELECT * FROM diagnosis WHERE record_id=? AND deleted_at IS NULL ORDER BY seq_no, id",
        (rid,))
    out["orders"] = db.query(
        "SELECT * FROM medical_order WHERE record_id=? AND deleted_at IS NULL ORDER BY seq_no, id",
        (rid,))
    out["tcm_four"] = db.query_one(
        "SELECT * FROM tcm_four_diagnosis WHERE record_id=? AND deleted_at IS NULL", (rid,))
    out["syndromes"] = db.query(
        "SELECT * FROM tcm_syndrome WHERE record_id=? AND deleted_at IS NULL ORDER BY seq_no, id",
        (rid,))
    out["signature"] = db.query_one(
        "SELECT * FROM record_signature WHERE record_id=? ORDER BY id DESC LIMIT 1", (rid,))
    out["status_log"] = db.query("SELECT * FROM record_status_log WHERE record_id=? ORDER BY id",
                                 (rid,))
    age = tu.calc_age(rec.get("birth_date"))
    out["age_text"] = None
    return out


@route("/api/record/save/history", "POST", perm="record.edit", module="M03",
       action="record.history")
def save_history(ctx, body, q):
    """F12 既往史 / 个人史 / 家族史（多条）。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    items = body.get("items") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.history", "record", rid)
        if body.get("replace"):
            db.execute(conn, "UPDATE record_history SET deleted_at=?, updated_by=? "
                             "WHERE record_id=? AND deleted_at IS NULL",
                       (tu.now_str(), ctx.user_id, rid))
        n = 0
        for it in items:
            if not (it.get("disease") or it.get("content")):
                continue
            if it.get("id"):
                db.execute(conn, """
                    UPDATE record_history SET hist_type=?, disease=?, diagnosed_at=?,
                           treatment=?, outcome=?, content=?, is_abnormal=?, source=?,
                           updated_at=?, updated_by=?
                    WHERE id=? AND record_id=?
                """, (it.get("hist_type", "PAST"), it.get("disease"), it.get("diagnosed_at"),
                      it.get("treatment"), it.get("outcome"), it.get("content"),
                      it.get("is_abnormal", 0), it.get("source", "DOCTOR"),
                      tu.now_str(), ctx.user_id, it["id"], rid))
            else:
                db.execute(conn, """
                    INSERT INTO record_history (record_id, hist_type, disease, diagnosed_at,
                        treatment, outcome, content, is_abnormal, source, created_at, created_by,
                        updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (rid, it.get("hist_type", "PAST"), it.get("disease"), it.get("diagnosed_at"),
                      it.get("treatment"), it.get("outcome"), it.get("content"),
                      it.get("is_abnormal", 0), it.get("source", "DOCTOR"),
                      tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
    return {"record_id": rid, "saved": n}


@route("/api/record/save/allergy", "POST", perm="record.edit", module="M03",
       action="record.allergy")
def save_allergy(ctx, body, q):
    """F13 过敏史：写入患者级别，全就诊可见。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    rec = _load_editable(rid)
    items = body.get("items") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.allergy", "record", rid)
        n = 0
        for it in items:
            if not it.get("allergen"):
                continue
            if it.get("id"):
                db.execute(conn, """
                    UPDATE record_allergy SET allergen=?, allergen_type=?, manifestation=?,
                           severity=?, updated_at=?, updated_by=?
                    WHERE id=? AND deleted_at IS NULL
                """, (it["allergen"], it.get("allergen_type"), it.get("manifestation"),
                      it.get("severity"), tu.now_str(), ctx.user_id, it["id"]))
            else:
                db.execute(conn, """
                    INSERT INTO record_allergy (record_id, patient_id, allergen, allergen_type,
                        manifestation, severity, source, created_at, created_by,
                        updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (rid, rec["patient_id"], it["allergen"], it.get("allergen_type", "DRUG"),
                      it.get("manifestation"), it.get("severity"), it.get("source", "DOCTOR"),
                      tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
    return {"record_id": rid, "saved": n}


@route("/api/record/save/vital", "POST", perm="record.edit", module="M03", action="record.vital")
def save_vital(ctx, body, q):
    """F14 生命体征（一次测量一条）。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.vital", "record", rid)
        h, w = body.get("height_cm"), body.get("weight_kg")
        bmi = None
        if h and w and h > 0:
            bmi = round(float(w) / ((float(h) / 100) ** 2), 1)
        if body.get("id"):
            db.execute(conn, """
                UPDATE vital_sign SET temperature_c=?, pulse=?, respiration=?, systolic=?,
                       diastolic=?, spo2=?, height_cm=?, weight_kg=?, bmi=?, pain_score=?,
                       measured_at=?, measured_by=?, note=?
                WHERE id=? AND record_id=?
            """, (body.get("temperature_c"), body.get("pulse"), body.get("respiration"),
                  body.get("systolic"), body.get("diastolic"), body.get("spo2"),
                  h, w, bmi, body.get("pain_score"), body.get("measured_at") or tu.now_str(),
                  ctx.user_id, body.get("note"), body["id"], rid))
            vid = body["id"]
        else:
            cur = db.execute(conn, """
                INSERT INTO vital_sign (record_id, temperature_c, pulse, respiration, systolic,
                    diastolic, spo2, height_cm, weight_kg, bmi, pain_score, measured_at,
                    measured_by, note, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rid, body.get("temperature_c"), body.get("pulse"), body.get("respiration"),
                  body.get("systolic"), body.get("diastolic"), body.get("spo2"),
                  h, w, bmi, body.get("pain_score"), body.get("measured_at") or tu.now_str(),
                  ctx.user_id, body.get("note"), tu.now_str(), ctx.user_id))
            vid = cur.lastrowid
    return {"record_id": rid, "vital_id": vid, "bmi": bmi}


@route("/api/record/save/exam", "POST", perm="record.edit", module="M03", action="record.exam")
def save_exam(ctx, body, q):
    """F15 查体（分节多条）。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    items = body.get("items") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.exam", "record", rid)
        if body.get("replace"):
            db.execute(conn, "UPDATE physical_exam SET deleted_at=? "
                             "WHERE record_id=? AND deleted_at IS NULL", (tu.now_str(), rid))
        n = 0
        for i, it in enumerate(items):
            if not it.get("content"):
                continue
            if it.get("id") and not body.get("replace"):
                db.execute(conn, """
                    UPDATE physical_exam SET section=?, content=?, is_abnormal=?, seq_no=?,
                           updated_at=?, updated_by=? WHERE id=? AND record_id=?
                """, (it.get("section"), it["content"], it.get("is_abnormal", 0),
                      it.get("seq_no", i), tu.now_str(), ctx.user_id, it["id"], rid))
            else:
                db.execute(conn, """
                    INSERT INTO physical_exam (record_id, section, content, is_abnormal,
                        seq_no, created_at, created_by, updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (rid, it.get("section"), it["content"], it.get("is_abnormal", 0),
                      it.get("seq_no", i), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
    return {"record_id": rid, "saved": n}


# ============================================================
# F16 辅助检查记录
# ============================================================

EXAM_REF_TYPES = ("LAB", "IMAGING", "OTHER", "POINT")
EXAM_POINT = "POINT"                 # 院内即时检查（不走 M05）
EXAM_REF_INVALID = "E3021"           # 引用的结果不存在或已作废


def _exam_ref_view(record_id):
    """F16 R3/R5：派生展示视图（不落库）。即时检查、已回报、待回报、引用失效四类。

    **不看 M05 的结果文本**——引用只留摘要，展示时用派生状态，
    源数据更正后只提示"已更新"，已确认病历的内容绝不自动改（R3）。
    """
    from modules import m05_lab as m05
    out = []
    for r in db.query("SELECT * FROM record_exam_ref WHERE record_id=? AND deleted_at IS NULL "
                      "ORDER BY id", (record_id,)):
        item = dict(r)
        if r["ref_type"] == EXAM_POINT:
            item["display_status"] = "即时检查"
            item["display_text"] = "%s %s%s" % (
                r["exam_name"] or "", r["exam_value"] or "",
                (" " + r["unit"]) if r["unit"] else "")
        elif r["lab_result_id"]:
            cur = m05.result_brief(r["lab_result_id"])
            if cur is None:                       # 源结果被删除/作废：提示失效，不自动改
                item["display_status"] = "引用失效"
                item["expired"] = True
            else:
                item["display_status"] = "已回报"
                item["source_now_updated_at"] = cur["updated_at"]
                if r["source_updated_at"] and cur["updated_at"] \
                        and cur["updated_at"] != r["source_updated_at"]:
                    item["source_changed"] = True
                    item["source_changed_at"] = cur["updated_at"]
                    item["display_status"] = "已回报（源数据已于 %s 更新）" % cur["updated_at"]
                item["display_text"] = r["summary"] or cur["result_summary"] or ""
        else:
            item["display_status"] = "已开单待回报"   # R5：不显示为空白
            item["display_text"] = r["summary"] or "已开单，等待机构回报"
        out.append(item)
    return out


@route("/api/record/save/exam_ref", "POST", perm="record.edit", module="M03",
       action="record.exam_ref")
def save_exam_ref(ctx, body, q):
    """F16 辅助检查记录。

    引用（R1/R2）：{ref_type: LAB|IMAGING|OTHER, lab_order_id, lab_result_id?, summary?}
        · 给 lab_result_id → 已回报，**只存引用 + 一句摘要**，绝不复制结果全文
        · 只给 lab_order_id → 已开单待回报（R5）
    即时（R4）：{ref_type: POINT, exam_name, exam_value, unit?, attachment_path?} 不走 M05
    """
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    items = body.get("items") or []
    from modules import m05_lab as m05      # M03 不得直查 lab_* 表（04 §2 R2）
    now = tu.now_str()
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.exam_ref", "record", rid)
        if body.get("replace"):
            db.execute(conn, "UPDATE record_exam_ref SET deleted_at=?, updated_at=?, "
                             "updated_by=? WHERE record_id=? AND deleted_at IS NULL",
                       (now, now, ctx.user_id, rid))
        n = 0
        for it in items:
            # 移除引用：只摘引用，绝不动 M05 里的原始结果（R7）
            if it.get("id") and it.get("deleted"):
                old = db.query_one("SELECT * FROM record_exam_ref WHERE id=? AND record_id=?",
                                   (it["id"], rid))
                if old is None:
                    continue
                audit.audit_data_change(conn, "record_exam_ref", it["id"], "deleted_at",
                                        old["deleted_at"], now,
                                        body.get("reason") or "移除辅助检查引用")
                db.execute(conn, "UPDATE record_exam_ref SET deleted_at=?, updated_at=?, "
                                 "updated_by=? WHERE id=? AND record_id=?",
                           (now, now, ctx.user_id, it["id"], rid))
                n += 1
                continue

            # 更新已有条目时，未显式给的字段沿用原值（只传要改的字段是正常用法）
            old_row = None
            if it.get("id") and not body.get("replace"):
                old_row = db.query_one("SELECT * FROM record_exam_ref WHERE id=? AND record_id=?",
                                       (it["id"], rid))
                if old_row is None:
                    continue
            def _f(key, default=None):
                """更新时未显式给的字段沿用原值，避免"只改一个值却把别的清空"。"""
                if key in it:
                    return it[key]
                return old_row[key] if old_row is not None else default

            ref_type = (it.get("ref_type") or (old_row["ref_type"] if old_row else "LAB")).upper()
            require(ref_type in EXAM_REF_TYPES,
                    "ref_type 取值应为 LAB/IMAGING/OTHER/POINT")
            order_id = _f("lab_order_id")
            result_id = _f("lab_result_id")
            source_updated_at = None
            if ref_type == EXAM_POINT:
                # R4 院内即时检查：直接记录，不经过 M05
                require(_f("exam_name") and str(_f("exam_value") or "").strip(),
                        "即时检查必须填写项目名称与结果值（F16 R4）")
                require(not order_id and not result_id,
                        "即时检查不走 M05，不能带 lab_order_id / lab_result_id（F16 R4）")
                order_id = result_id = None
            else:
                require(order_id or result_id,
                        "引用检查必须给 lab_order_id 或 lab_result_id")
                if result_id:
                    brief = m05.result_brief(result_id)
                    if brief is None:
                        raise conflict("引用的检验结果不存在或已作废：%s" % result_id,
                                       code=EXAM_REF_INVALID)
                    order_id = order_id or brief["lab_order_id"]
                    source_updated_at = brief["updated_at"]     # R3 溯源快照
                else:
                    # R5 只给申请单：允许登记，展示为"已开单待回报"
                    if m05.order_brief(order_id) is None:
                        raise conflict("引用的检查申请不存在：%s" % order_id,
                                       code=EXAM_REF_INVALID)
                    result_id = None

            fields = {
                "ref_type": ref_type,
                "lab_order_id": order_id,
                "lab_result_id": result_id,
                "summary": _f("summary"),
                "occurred_at": _f("occurred_at") or now,
                "exam_name": _f("exam_name"),
                "exam_value": _f("exam_value"),
                "unit": _f("unit"),
                "abnormal_flag": _f("abnormal_flag"),
                "attachment_path": _f("attachment_path"),
                "source": "POINT" if ref_type == EXAM_POINT else "M05",
                "source_updated_at": source_updated_at,
            }
            if old_row is not None:
                for k, v in fields.items():                     # R8 逐字段留痕
                    if old_row.get(k) != v:
                        audit.audit_data_change(conn, "record_exam_ref", it["id"], k,
                                                old_row.get(k), v, body.get("reason"))
                sets = ["%s=?" % k for k in fields] + ["updated_at=?", "updated_by=?"]
                db.execute(conn, "UPDATE record_exam_ref SET %s WHERE id=? AND record_id=?"
                           % ",".join(sets),
                           tuple(list(fields.values()) + [now, ctx.user_id, it["id"], rid]))
            else:
                cols = ["record_id"] + list(fields.keys()) + \
                       ["created_at", "created_by", "updated_at", "updated_by"]
                vals = [rid] + list(fields.values()) + [now, ctx.user_id, now, ctx.user_id]
                db.execute(conn, "INSERT INTO record_exam_ref (%s) VALUES (%s)"
                           % (",".join(cols), ",".join("?" * len(vals))), tuple(vals))
            n += 1
        db.execute(conn, "UPDATE record SET updated_at=?, updated_by=? WHERE id=?",
                   (now, ctx.user_id, rid))
    return {"record_id": rid, "saved": n}


@route("/api/record/exam_ref/list", "GET", perm="record.view", module="M03",
       action="record.exam_ref.list")
def exam_ref_list(ctx, body, q):
    """F16：辅助检查展示视图（含待回报 / 引用失效 / 源数据已更新 提示）。"""
    rid = q.get("record_id") or q.get("id")
    require(rid, "缺少 record_id")
    rec = db.query_one("SELECT id FROM record WHERE id=? AND deleted_at IS NULL", (rid,))
    if rec is None:
        raise not_found("病历不存在")
    return {"record_id": rid, "items": _exam_ref_view(rid)}


@route("/api/record/save/diagnosis", "POST", perm="record.edit", module="M03",
       action="record.diagnosis")
def save_diagnosis(ctx, body, q):
    """F17 诊断：西医 ICD-10 与中医证型。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    items = body.get("items") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.diagnosis", "record", rid)
        if body.get("replace"):
            db.execute(conn, "UPDATE diagnosis SET deleted_at=? "
                             "WHERE record_id=? AND deleted_at IS NULL", (tu.now_str(), rid))
        n = 0
        for i, it in enumerate(items):
            if not (it.get("icd10_name") or it.get("tcm_name") or it.get("icd10_code")
                    or it.get("tcm_code")):
                continue
            diag_type = it.get("diag_type") or ("TCM" if it.get("tcm_name") else "WEST")
            if it.get("id") and not body.get("replace"):
                db.execute(conn, """
                    UPDATE diagnosis SET diag_type=?, icd10_code=?, icd10_name=?, tcm_code=?,
                           tcm_name=?, is_primary=?, seq_no=?, confidence=?, source=?, note=?,
                           updated_at=?, updated_by=? WHERE id=? AND record_id=?
                """, (diag_type, it.get("icd10_code"), it.get("icd10_name"), it.get("tcm_code"),
                      it.get("tcm_name"), it.get("is_primary", 0), it.get("seq_no", i),
                      it.get("confidence", "CONFIRMED"), it.get("source", "DOCTOR"),
                      it.get("note"), tu.now_str(), ctx.user_id, it["id"], rid))
            else:
                db.execute(conn, """
                    INSERT INTO diagnosis (record_id, diag_type, icd10_code, icd10_name,
                        tcm_code, tcm_name, is_primary, seq_no, confidence, source, note,
                        created_at, created_by, updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (rid, diag_type, it.get("icd10_code"), it.get("icd10_name"),
                      it.get("tcm_code"), it.get("tcm_name"), it.get("is_primary", 0),
                      it.get("seq_no", i), it.get("confidence", "CONFIRMED"),
                      it.get("source", "DOCTOR"), it.get("note"),
                      tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
    return {"record_id": rid, "saved": n}


@route("/api/record/save/order", "POST", perm="record.edit", module="M03", action="record.order")
def save_order(ctx, body, q):
    """F18 医嘱（非药品）。药品只能走处方 —— 此处拒绝 DRUG 类型。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    items = body.get("items") or []
    for it in items:
        if it.get("order_type") == "DRUG":
            raise conflict("药品不得写在医嘱中，只能开处方（F34/F111）", code="E3001")
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.order", "record", rid)
        n = 0
        for i, it in enumerate(items):
            if not it.get("content"):
                continue
            require(it.get("order_type") in ("TREATMENT", "LAB", "ADVICE", "REFERRAL",
                                             "FOLLOWUP"),
                    "order_type 取值非法")
            if it.get("id"):
                db.execute(conn, """
                    UPDATE medical_order SET order_type=?, content=?, item_ref_type=?,
                           item_ref_id=?, dosage=?, frequency=?, days=?, seq_no=?, note=?,
                           updated_at=?, updated_by=? WHERE id=? AND record_id=?
                """, (it["order_type"], it["content"], it.get("item_ref_type"),
                      it.get("item_ref_id"), it.get("dosage"), it.get("frequency"),
                      it.get("days"), it.get("seq_no", i), it.get("note"),
                      tu.now_str(), ctx.user_id, it["id"], rid))
            else:
                db.execute(conn, """
                    INSERT INTO medical_order (record_id, order_type, content, item_ref_type,
                        item_ref_id, dosage, frequency, days, seq_no, status, source, note,
                        created_at, created_by, updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,?,?,?)
                """, (rid, it["order_type"], it["content"], it.get("item_ref_type"),
                      it.get("item_ref_id"), it.get("dosage"), it.get("frequency"),
                      it.get("days"), it.get("seq_no", i), it.get("source", "DOCTOR"),
                      it.get("note"), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
    return {"record_id": rid, "saved": n}


@route("/api/record/save/syndrome", "POST", perm="record.edit", module="M03",
       action="record.syndrome")
def save_syndrome(ctx, body, q):
    """F109 辨证论治（可多条证型）。"""
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    _load_editable(rid)
    items = body.get("items") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.save.syndrome", "record", rid)
        if body.get("replace"):
            db.execute(conn, "UPDATE tcm_syndrome SET deleted_at=? "
                             "WHERE record_id=? AND deleted_at IS NULL", (tu.now_str(), rid))
        n = 0
        for i, it in enumerate(items):
            if not it.get("syndrome_name"):
                continue
            if it.get("id") and not body.get("replace"):
                db.execute(conn, """
                    UPDATE tcm_syndrome SET syndrome_name=?, syndrome_code=?,
                           syndrome_category=?, basis=?, treatment_principle=?,
                           treatment_method=?, formula_ref=?, seq_no=?, source=?,
                           updated_at=?, updated_by=? WHERE id=? AND record_id=?
                """, (it["syndrome_name"], it.get("syndrome_code"), it.get("syndrome_category"),
                      it.get("basis"), it.get("treatment_principle"), it.get("treatment_method"),
                      it.get("formula_ref"), it.get("seq_no", i), it.get("source", "DOCTOR"),
                      tu.now_str(), ctx.user_id, it["id"], rid))
            else:
                db.execute(conn, """
                    INSERT INTO tcm_syndrome (record_id, syndrome_name, syndrome_code,
                        syndrome_category, basis, treatment_principle, treatment_method,
                        formula_ref, seq_no, source, created_at, created_by,
                        updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (rid, it["syndrome_name"], it.get("syndrome_code"),
                      it.get("syndrome_category"), it.get("basis"),
                      it.get("treatment_principle"), it.get("treatment_method"),
                      it.get("formula_ref"), it.get("seq_no", i), it.get("source", "DOCTOR"),
                      tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
    return {"record_id": rid, "saved": n}


@route("/api/record/confirm", "POST", perm="record.confirm", module="M03", action="record.confirm")
def confirm(ctx, body, q):
    """F19 病历确认签名：DRAFT → CONFIRMED，写 record_signature。

    校验：主诉必填、至少一条诊断。确认后内容锁定（修改走 M11 F77）。
    """
    rid = body.get("record_id")
    require(rid, "缺少 record_id")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM record WHERE id=? AND deleted_at IS NULL", (rid,))
        if row is None:
            raise not_found("病历不存在")
        if row["status"] != DRAFT:
            raise conflict("只有草稿状态的病历可以确认", code=BIZ_ALREADY_CONFIRMED)
        cc = db.query_one("SELECT content FROM record_chief_complaint "
                          "WHERE record_id=? AND deleted_at IS NULL", (rid,))
        require(cc and (cc["content"] or "").strip(), "主诉为空，不能确认病历")
        dx = db.query_one("SELECT COUNT(*) c FROM diagnosis "
                          "WHERE record_id=? AND deleted_at IS NULL", (rid,))
        require(dx and dx["c"] > 0, "至少填写一条诊断才能确认病历")
        require((body.get("password") or "").strip() or body.get("sign_method") == "CARD",
                "确认需要口令或刷卡签名")
        if body.get("password"):
            from core import security
            u = db.query_one("SELECT password_hash, password_salt, display_name "
                             "FROM sys_user WHERE id=?", (ctx.user_id,))
            if not security.verify_password(body["password"], u["password_salt"],
                                            u["password_hash"]):
                raise conflict("签名口令校验失败", code="E6003")
        audit.audit_operation(conn, "M03", "record.confirm", "record", rid,
                              detail={"sign_method": body.get("sign_method", "PASSWORD")})
        frm, to = transition(conn, rid, CONFIRMED, body.get("reason"), ctx,
                             extra={"confirmed_by": ctx.user_id,
                                    "confirmed_at": tu.now_str()})
        u = db.query_one("SELECT display_name FROM sys_user WHERE id=?", (ctx.user_id,))
        db.execute(conn, """
            INSERT INTO record_signature (record_id, signer_id, signer_name, role_code,
                signed_at, sign_method, sign_hash, version_no)
            VALUES (?,?,?,?,?,?,?,?)
        """, (rid, ctx.user_id, (u["display_name"] if u else None), ctx.role_code,
              tu.now_str(), body.get("sign_method", "PASSWORD"),
              audit.dumps({"record_id": rid, "version": row["version"] + 1,
                           "signed_at": tu.now_str()}), row["version"] + 1))
        # F76：确认即创建 v1 版本快照 + Hash 链首条（同一事务，避免"已确认无版本"）
        # 延迟导入：M11 依赖 M03，此处不能产生模块级循环导入
        from modules import m11_archive
        ver = m11_archive.create_version(conn, rid, source="CONFIRM",
                                         change_note="病历确认签名", ctx=ctx)
    return {"record_id": rid, "from": frm, "to": to, "version_no": ver["version_no"],
            "hash": ver["hash"]}


# ---------- F126 病历模板与常用语 ----------

@route("/api/record/templates", "GET", perm="record.view", module="M03", action="record.templates")
def templates(ctx, body, q):
    tpl_type = q.get("template_type")
    sql = """SELECT * FROM record_template WHERE deleted_at IS NULL AND is_active=1
             AND (scope='SYSTEM' OR owner_id=?)"""
    params = [ctx.user_id]
    if tpl_type:
        sql += " AND template_type=?"
        params.append(tpl_type)
    sql += " ORDER BY template_type, usage_count DESC, id DESC"
    return {"items": db.query(sql, tuple(params))}


@route("/api/record/template/create", "POST", perm="record.template.manage",
       module="M03", action="record.template.create")
def template_create(ctx, body, q):
    require(body.get("name"), "缺少模板名称")
    require(body.get("template_type") in ("RECORD", "PHRASE", "TCM_RECORD"),
            "template_type 取值非法")
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.template.create", "record_template", None)
        cur = db.execute(conn, """
            INSERT INTO record_template (template_type, scope, owner_id, category, name,
                content_json, created_at, created_by, updated_at, updated_by)
            VALUES (?,'USER',?,?,?,?,?,?,?,?)
        """, (body["template_type"], ctx.user_id, body.get("category"), body["name"].strip(),
              audit.dumps(body.get("content", {})), tu.now_str(), ctx.user_id,
              tu.now_str(), ctx.user_id))
        tid = cur.lastrowid
    return {"id": tid}


@route("/api/record/template/use", "POST", perm="record.edit", module="M03",
       action="record.template.use")
def template_use(ctx, body, q):
    """应用模板到病历：仅写入 DRAFT 病历的对应字段，并累加使用次数。"""
    rid = body.get("record_id")
    tid = body.get("template_id")
    require(rid and tid, "缺少 record_id / template_id")
    _load_editable(rid)
    tpl = db.query_one("SELECT * FROM record_template WHERE id=? AND deleted_at IS NULL", (tid,))
    if tpl is None:
        raise not_found("模板不存在")
    import json
    try:
        content = json.loads(tpl["content_json"] or "{}")
    except ValueError:
        content = {}
    with db.tx() as conn:
        audit.audit_operation(conn, "M03", "record.template.use", "record", rid,
                              detail={"template_id": tid})
        db.execute(conn, "UPDATE record_template SET usage_count=usage_count+1 WHERE id=?",
                   (tid,))
    return {"record_id": rid, "template_id": tid, "content": content}
