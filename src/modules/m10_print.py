# -*- coding: utf-8 -*-
"""M10 打印与硬件（F66-F75）。

表所有权：print_job / print_status_log（新增）/ print_failure / print_template /
         hardware_device / hardware_event / hardware_health
状态机：03 §2.7 —— PENDING→PRINTING→SUCCESS|FAILED；FAILED→RETRYING→PRINTING；FAILED→ABANDONED
异常码：E4xxx 硬件 / E7xxx 打印，**一律 retryable=True 且有降级路径**（03 §6）
边界：打印失败**绝不回滚**业务（R-M10-6）；硬件驱动经 core/hardware.py 注入（R8 mock）
"""

import datetime
import os
import re

from core import db, audit, config, timeutil as tu, ids
from core import hardware
from core import money
from core.api import route
from core.errors import (require, require_keys, AppError, conflict, not_found, DATA_FORMAT,
                         BIZ_STATE_INVALID, HW_PRINTER_OFFLINE, HW_AGENT_UNREACHABLE,
                         PRINT_TEMPLATE_MISSING, PRINT_RENDER_FAILED)

MODULE = "M10"

PENDING = "PENDING"
PRINTING = "PRINTING"
SUCCESS = "SUCCESS"
FAILED = "FAILED"
RETRYING = "RETRYING"
ABANDONED = "ABANDONED"

TRANSITIONS = {
    PENDING:   [PRINTING, ABANDONED],
    PRINTING:  [SUCCESS, FAILED],
    FAILED:    [RETRYING, PRINTING, ABANDONED],
    RETRYING:  [PRINTING, ABANDONED],
    SUCCESS:   [],                 # 终态（重打不换单号，只累加 reprint_count）
    ABANDONED: [PRINTING],         # 恢复后可再次执行
}

# 脚注字典（0007 迁移里同时存在两套编码，这里都兼容）
FOOTNOTE_TEXT = {
    "NORMAL": "常规", "FIRST": "先煎", "LATER": "后下", "WRAP": "包煎",
    "MELT": "烊化", "DISSOLVE": "冲服", "SEPARATE": "另煎",
    "XIANJIAN": "先煎", "HOUXIA": "后下", "BAOJIAN": "包煎", "YANGHUA": "烊化",
    "CHONGFU": "冲服", "LINGJIAN": "另煎", "ZHIJIAN": "炙煎",
}
CHANNEL_TEXT = {"CASH": "现金", "WECHAT": "微信", "ALIPAY": "支付宝",
                "CARD": "银行卡", "OTHER": "其他", "REFUND": "退款"}
GENDER_TEXT = {"M": "男", "F": "女", "U": "未知"}


def _transition(conn, job_id, to_status, reason=None, ctx=None, extra=None):
    ctx = ctx or audit.get_ctx()
    row = db.query_one("SELECT * FROM print_job WHERE id=? AND deleted_at IS NULL", (job_id,))
    if row is None:
        raise not_found("打印任务不存在")
    frm = row["status"]
    if to_status not in TRANSITIONS.get(frm, []):
        raise conflict("打印任务状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?"]
    params = [to_status, tu.now_str(), ctx.user_id]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(job_id)
    db.execute(conn, "UPDATE print_job SET %s WHERE id=?" % ",".join(sets), tuple(params))
    db.execute(conn, """
        INSERT INTO print_status_log (job_id, from_status, to_status, reason, operator_id,
                                      created_at)
        VALUES (?,?,?,?,?,?)
    """, (job_id, frm, to_status, reason, ctx.user_id, tu.now_str()))
    return row


# ---------------- 模板渲染 ----------------

def render(template_text, data):
    """极简占位符渲染：{{field}} + {{#rows}}...{{/rows}} 循环块。

    渲染在任务创建时完成并固化（R-M10-2），重打不再取数。
    """
    try:
        text = template_text or ""

        def block(m):
            name, inner = m.group(1), m.group(2)
            rows = data.get(name) or []
            out = []
            for i, r in enumerate(rows, start=1):
                row = dict(r)
                row.setdefault("seq", i)
                out.append(render(inner, row))
            return "".join(out)

        text = re.sub(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", block, text, flags=re.S)

        def field(m):
            v = data.get(m.group(1))
            return "" if v is None else str(v)

        return re.sub(r"\{\{(\w+)\}\}", field, text)
    except Exception as exc:
        raise AppError(PRINT_RENDER_FAILED, "模板渲染失败：%s" % exc, retryable=True,
                       http_status=500)


def _load_template(code=None, biz_type=None, variant=None):
    sql = "SELECT * FROM print_template WHERE is_active=1 AND deleted_at IS NULL"
    params = ()
    if code:
        sql += " AND template_code=?"
        params = (code,)
    else:
        sql += " AND biz_type=? AND COALESCE(variant,'')=COALESCE(?,'')"
        params = (biz_type, variant)
    sql += " ORDER BY is_default DESC, id LIMIT 1"
    tpl = db.query_one(sql, params)
    if tpl is None:
        raise AppError(PRINT_TEMPLATE_MISSING,
                       "找不到可用打印模板（code=%s biz=%s/%s）" % (code, biz_type, variant),
                       retryable=False, http_status=409)
    return tpl


def _clinic():
    return config.get("clinic.name") or "未命名诊所"


def _y(fen):
    return money.fen_to_yuan(int(fen or 0))


# ---------------- 数据装配（F67/F68/F69）----------------

def _prescription_data(rx_id):
    from modules import m03_record as m03
    rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
    if rx is None:
        raise not_found("处方不存在")
    if rx["status"] in ("DRAFT", "PENDING", "VOID"):
        raise conflict("处方状态为 %s，不能打印（需已确认）" % rx["status"],
                       code=BIZ_STATE_INVALID)
    is_herb = rx["prescription_type"] == "HERB"
    pricing = db.query_one("SELECT * FROM prescription_pricing WHERE prescription_id=?",
                           (rx_id,)) or {}
    doc = db.query_one("SELECT display_name FROM sys_user WHERE id=?",
                       (rx["created_by"],)) or {}
    allergy = []
    if rx["patient_id"]:
        try:
            allergy = m03_record.list_allergies(rx["patient_id"]) or []
        except Exception:
            allergy = []

    data = {
        "clinic_name": _clinic(), "prescription_no": rx["prescription_no"],
        "patient_name": rx["patient_name_snap"], "gender_text": GENDER_TEXT.get(
            rx["gender_snap"], rx["gender_snap"] or ""),
        "age_text": rx["age_text_snap"] or "", "department": rx.get("department") or "中医科",
        "doctor_name": doc.get("display_name") or "", "occurred_at": rx["occurred_at"],
        "diag_summary": rx["diag_summary"] or "", "tcm_syndrome": rx["tcm_syndrome"] or "",
        "total_yuan": _y(pricing.get("total_amount_fen", rx["total_amount"])),
        "printed_at": tu.now_str(), "print_job_no": "", "reprint_text": "",
        "allergy_text": ("过敏史：" + "、".join([a.get("allergen_name") or a.get("allergen") or ""
                                              for a in allergy if a])) if allergy else "",
    }
    if is_herb:
        items = db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                         "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
        if not items:
            raise AppError(PRINT_RENDER_FAILED, "饮片处方没有明细，无法打印", retryable=True)
        for it in items:                     # R2：脚注必出
            if not it["foot_note"]:
                raise AppError(PRINT_RENDER_FAILED,
                               "饮片「%s」缺少脚注，无法打印" % it["herb_name_snap"],
                               retryable=True)
        if not rx["decoction_method"] or not rx["administration"]:
            raise AppError(PRINT_RENDER_FAILED, "饮片处方缺少煎法或服法，无法打印",
                           retryable=True)
        data.update({
            "items": [{"name": it["herb_name_snap"], "dose_g": it["dose_g"],
                       "foot_note_text": FOOTNOTE_TEXT.get(it["foot_note"], it["foot_note"])}
                      for it in items],
            "herb_count": len(items), "doses": rx["doses"],
            "decoction_method": rx["decoction_method"], "administration": rx["administration"],
            "herb_subtotal_yuan": _y(pricing.get("herb_subtotal_fen")),
            "decoction_fee_yuan": _y(pricing.get("decoction_fee_fen")),
            "package_fee_yuan": _y(pricing.get("package_fee_fen")),
        })
    else:
        items = db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                         "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
        if not items:
            raise AppError(PRINT_RENDER_FAILED, "处方没有明细，无法打印", retryable=True)
        data["items"] = [{
            "name": it["drug_name_snap"], "spec": it["spec_snap"] or "",
            "quantity": it["quantity"], "unit": it["unit"] or "",
            "price_yuan": _y(it["unit_price_fen"]),
            "line_amount_yuan": _y(it["line_amount_fen"]),
            "dosage": it["dosage"] or "", "frequency": it["frequency"] or "",
            "route": it["route"] or "",
            "days_text": ("%s 天" % it["days"]) if it["days"] else "",
        } for it in items]
    return rx, data


def _receipt_data(bill_id):
    b = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bill_id,))
    if b is None:
        raise not_found("账单不存在")
    if b["status"] == "PENDING":
        raise conflict("账单未收费，不能打印凭条", code=BIZ_STATE_INVALID)
    items = db.query("SELECT * FROM bill_item WHERE bill_id=? ORDER BY line_no", (bill_id,))
    pays = db.query("SELECT * FROM payment WHERE bill_id=? AND status='SUCCESS' ORDER BY id",
                    (bill_id,))
    op = db.query_one("SELECT display_name FROM sys_user WHERE id=?", (b["created_by"],)) or {}
    return b, {
        "clinic_name": _clinic(), "bill_no": b["bill_no"],
        "patient_name": b["patient_name_snap"] or "", "operator_name": op.get("display_name") or "",
        "paid_at": tu.now_str(), "printed_at": tu.now_str(), "print_job_no": "",
        "reprint_text": "",
        "items": [{"name": ("（冲正）" + (it["name_snap"] or "")) if it["is_reversal"]
                   else (it["name_snap"] or ""),
                   "quantity": it["quantity"], "amount_yuan": _y(it["amount_fen"])}
                  for it in items],
        "total_yuan": _y(b["total_amount_fen"]),
        "payments": [{"channel_text": CHANNEL_TEXT.get(p["channel"], p["channel"]),
                      "amount_yuan": _y(p["amount_fen"]),
                      "change_text": ("（找零 %s 元）" % _y(p["change_amount_fen"]))
                                     if p["change_amount_fen"] else ""}
                     for p in pays],
        "refunded_yuan": _y(b["refunded_amount_fen"]),
    }


def _label_data(rx_id):
    rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
    if rx is None:
        raise not_found("处方不存在")
    if rx["status"] not in ("DISPENSED", "PARTIAL_RETURNED", "FULLY_RETURNED"):
        raise conflict("处方未发药，不能打印药袋标签", code=BIZ_STATE_INVALID)
    from modules import m03_record as m03
    allergy = []
    if rx["patient_id"]:
        try:
            allergy = m03_record.list_allergies(rx["patient_id"]) or []
        except Exception:
            allergy = []
    is_herb = rx["prescription_type"] == "HERB"
    hd = db.query_one("SELECT * FROM herb_dispense WHERE prescription_id=? "
                      "ORDER BY id DESC LIMIT 1", (rx_id,))
    data = {
        "clinic_name": _clinic(), "prescription_no": rx["prescription_no"],
        "patient_name": rx["patient_name_snap"],
        "gender_text": GENDER_TEXT.get(rx["gender_snap"], rx["gender_snap"] or ""),
        "age_text": rx["age_text_snap"] or "", "printed_at": tu.now_str(),
        "print_job_no": "", "reprint_text": "",
        "allergy_text": ("过敏史：" + "、".join([a.get("allergen_name") or a.get("allergen") or ""
                                              for a in allergy if a])) if allergy else "",
    }
    if is_herb:
        items = db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                         "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
        data["items"] = [{"name": it["herb_name_snap"],
                          "dose_text": "%sg" % it["dose_g"], "usage_text": ""}
                         for it in items]
        data["herb_text"] = "%s 付 · %s · %s%s" % (
            rx["doses"], rx["decoction_method"] or "", rx["administration"] or "",
            (" · 代煎，预计 %s 可取" % hd["ready_at"]) if hd and hd["decoction_type"] == "DAIJIAN"
            and hd["ready_at"] else "")
    else:
        items = db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                         "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
        data["items"] = []
        for it in items:
            usage = " ".join([x for x in (it["dosage"], it["frequency"], it["route"]) if x])
            if not usage:                       # R2：用法缺失 → 渲染失败
                raise AppError(PRINT_RENDER_FAILED,
                               "药品「%s」缺少用法用量，无法打印标签" % it["drug_name_snap"],
                               retryable=True)
            data["items"].append({"name": it["drug_name_snap"], "dose_text": "",
                                  "usage_text": usage})
        data["herb_text"] = ""
    return rx, data


# ---------------- F66 打印任务 ----------------

def create_job(ctx, biz_type, biz_id, data, template_code=None, variant=None,
               idem=None, biz_no=None, patient_id=None, visit_id=None, remark=None,
               device_id=None):
    """创建打印任务：取模板 → 渲染 → 固化文本 → PENDING。"""
    tpl = _load_template(template_code, biz_type, variant)
    text = render(tpl["content"], data)
    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM print_job WHERE idempotent_key=?", (idem,))
            if done:
                return dict(done, duplicate=True)
        audit.audit_operation(conn, MODULE, "print.job.create", "print_job", None,
                              detail={"biz_type": biz_type, "biz_id": biz_id,
                                      "template": tpl["template_code"]})
        job_no = ids.next_no(conn, "PRINT_JOB")
        cur = db.execute(conn, """
            INSERT INTO print_job (print_job_no, template_code, biz_type, biz_id, biz_no,
                driver_code, device_id, status, rendered_text, patient_id, visit_id,
                idempotent_key, remark, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,'PENDING',?,?,?,?,?,?,?,?,?)
        """, (job_no, tpl["template_code"], biz_type, biz_id, biz_no,
              "mock.printer", device_id, text, patient_id, visit_id, idem, remark,
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        jid = cur.lastrowid
        db.execute(conn, """
            INSERT INTO print_status_log (job_id, from_status, to_status, reason, operator_id,
                                          created_at)
            VALUES (?,NULL,'PENDING',?,?,?)
        """, (jid, "创建打印任务", ctx.user_id, tu.now_str()))
        row = db.query_one("SELECT * FROM print_job WHERE id=?", (jid,))
    return dict(row)


def execute_job(ctx, job_id, is_reprint=False):
    """送驱动执行；失败 → FAILED + print_failure（可重试）。"""
    with db.tx() as conn:
        job = db.query_one("SELECT * FROM print_job WHERE id=? AND deleted_at IS NULL",
                           (job_id,))
        if job is None:
            raise not_found("打印任务不存在")
        # 重打（F70）针对已 SUCCESS 的任务：不换单号、不走状态机，直接再次输出并留痕
        reprinting = bool(is_reprint) and job["status"] == SUCCESS
        if job["status"] == PRINTING or (job["status"] == SUCCESS and not is_reprint):
            raise conflict("任务状态为 %s，不能执行" % job["status"], code=BIZ_STATE_INVALID)
        audit.audit_operation(conn, MODULE, "print.job.execute", "print_job", job_id,
                              detail={"reprint": bool(is_reprint)})
        if not reprinting:
            _transition(conn, job_id, PRINTING, "开始打印", ctx)
        attempt = int(job["attempt_count"]) + 1
        db.execute(conn, "UPDATE print_job SET attempt_count=? WHERE id=?", (attempt, job_id))

    # 驱动调用放在事务外：硬件 I/O 不应占用写事务（05 §3）
    text = job["rendered_text"]
    try:
        from core.logging_setup import log_dir
        spool = os.path.join(log_dir() or ".", "prints")
        drv = hardware.get(job["driver_code"])
        if hasattr(drv, "spool_dir") and not getattr(drv, "spool_dir", None):
            drv.spool_dir = spool
        res, ms = hardware.call(job["driver_code"], "print_text", text,
                                {"job_no": job["print_job_no"]})
        out_path = res.get("path") if isinstance(res, dict) else None
    except AppError as exc:
        with db.tx() as conn:
            _transition(conn, job_id, FAILED, exc.message, ctx)
            db.execute(conn, """
                INSERT INTO print_failure (job_id, attempt_no, error_code, message, retryable,
                                           occurred_at, operator_id)
                VALUES (?,?,?,?,?,?,?)
            """, (job_id, attempt, exc.code, exc.message, 1 if exc.retryable else 0,
                  tu.now_str(), ctx.user_id))
            _hw_event(conn, job["driver_code"], "PRINT", "FAIL",
                      "任务 %s 第 %d 次失败" % (job["print_job_no"], attempt), exc.code,
                      0, ctx)
        raise
    with db.tx() as conn:
        if reprinting:                       # 已是终态，只留痕不改状态
            db.execute(conn, """
                INSERT INTO print_status_log (job_id, from_status, to_status, reason,
                                              operator_id, created_at)
                VALUES (?,?,?,?,?,?)
            """, (job_id, SUCCESS, SUCCESS, "重打输出成功", ctx.user_id, tu.now_str()))
            db.execute(conn, "UPDATE print_job SET printed_at=?, output_path=?, updated_at=? "
                             "WHERE id=?", (tu.now_str(), out_path, tu.now_str(), job_id))
        else:
            _transition(conn, job_id, SUCCESS, "打印成功", ctx,
                        extra={"printed_at": tu.now_str(), "output_path": out_path})
        _hw_event(conn, job["driver_code"], "PRINT", "SUCCESS",
                  "任务 %s 输出成功" % job["print_job_no"], None, ms, ctx)
    return {"id": job_id, "print_job_no": job["print_job_no"], "status": SUCCESS,
            "output_path": out_path}


def _hw_event(conn, driver_code, event_type, result, summary, error_code, ms, ctx):
    db.execute(conn, """
        INSERT INTO hardware_event (driver_code, event_type, result, summary, error_code,
                                    elapsed_ms, occurred_at, operator_id)
        VALUES (?,?,?,?,?,?,?,?)
    """, (driver_code, event_type, result, summary, error_code, ms, tu.now_str(), ctx.user_id))


# ---------------- 路由 ----------------

@route("/api/print/prescription", "POST", perm="print.create", module=MODULE,
       action="print.prescription")
def print_prescription(ctx, body, q):
    """F67 处方笺：按处方类型自动选模板（饮片方含脚注与付数）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx, data = _prescription_data(rx_id)
    variant = "HERB" if rx["prescription_type"] == "HERB" else "WEST"
    return create_job(ctx, "PRESCRIPTION", rx_id, data, body.get("template_code"), variant,
                      body.get("idempotent_key"), rx["prescription_no"],
                      rx["patient_id"], rx["visit_id"], body.get("remark"))


@route("/api/print/receipt", "POST", perm="print.create", module=MODULE,
       action="print.receipt")
def print_receipt(ctx, body, q):
    """F68 收费凭条。"""
    bill_id = body.get("bill_id")
    require(bill_id, "缺少 bill_id")
    b, data = _receipt_data(bill_id)
    return create_job(ctx, "RECEIPT", bill_id, data, body.get("template_code"), "NORMAL",
                      body.get("idempotent_key"), b["bill_no"], b["patient_id"],
                      b["visit_id"], body.get("remark"))


@route("/api/print/label", "POST", perm="print.create", module=MODULE, action="print.label")
def print_label(ctx, body, q):
    """F69 药袋标签（含中药付数与煎服法）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx, data = _label_data(rx_id)
    return create_job(ctx, "LABEL", rx_id, data, body.get("template_code"), "NORMAL",
                      body.get("idempotent_key"), rx["prescription_no"],
                      rx["patient_id"], rx["visit_id"], body.get("remark"))


@route("/api/print/job/execute", "POST", perm="print.create", module=MODULE,
       action="print.job.execute")
def job_execute(ctx, body, q):
    jid = body.get("id") or body.get("job_id")
    require(jid, "缺少 job_id")
    return execute_job(ctx, jid)


@route("/api/print/job/reprint", "POST", perm="print.create", module=MODULE,
       action="print.job.reprint")
def job_reprint(ctx, body, q):
    """F70 重打印：不换单号、不重新取数，用固化文本。"""
    jid = body.get("id") or body.get("job_id")
    reason = (body.get("reason") or "补打").strip()
    require(jid, "缺少 job_id")
    with db.tx() as conn:
        job = db.query_one("SELECT * FROM print_job WHERE id=? AND deleted_at IS NULL",
                           (jid,))
        if job is None:
            raise not_found("打印任务不存在")
        n = int(job["reprint_count"] or 0) + 1
        audit.audit_operation(conn, MODULE, "print.job.reprint", "print_job", jid,
                              detail={"reason": reason, "count": n})
        db.execute(conn, "UPDATE print_job SET reprint_count=?, reprint_reason=?, "
                         "last_reprint_at=?, status=CASE WHEN status IN ('FAILED','ABANDONED') "
                         "THEN 'PENDING' ELSE status END WHERE id=?",
                   (n, reason, tu.now_str(), jid))
        if n > config.get_int("print.max_reprint", 5):
            audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "print_job", jid,
                                  "打印任务重打次数超过阈值（%d）：%s" % (n, reason))
    r = execute_job(ctx, jid, is_reprint=True)
    r["reprint_count"] = n
    r["print_job_no"] = job["print_job_no"]     # 单号不变（契约 §2.7）
    return r


@route("/api/print/job/retry", "POST", perm="print.create", module=MODULE,
       action="print.job.retry")
def job_retry(ctx, body, q):
    """F71 重试：未超上限则 RETRYING → PRINTING；超限转 ABANDONED。"""
    jid = body.get("id") or body.get("job_id")
    require(jid, "缺少 job_id")
    with db.tx() as conn:
        job = db.query_one("SELECT * FROM print_job WHERE id=? AND deleted_at IS NULL",
                           (jid,))
        if job is None:
            raise not_found("打印任务不存在")
        if job["status"] != FAILED:
            raise conflict("仅 FAILED 任务可重试（当前 %s）" % job["status"],
                           code=BIZ_STATE_INVALID)
        mx = config.get_int("print.max_retry", 3)
        if int(job["attempt_count"] or 0) >= mx:
            audit.audit_operation(conn, MODULE, "print.job.abandon", "print_job", jid,
                                  detail="重试超限")
            _transition(conn, jid, ABANDONED, "重试超过上限 %d" % mx, ctx)
            audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "print_job", jid,
                                  "打印任务放弃：重试超过上限（凭证可能缺失）")
            return {"id": jid, "status": ABANDONED, "abandoned": True}
        audit.audit_operation(conn, MODULE, "print.job.retry", "print_job", jid)
        _transition(conn, jid, RETRYING, "自动重试", ctx)
    return execute_job(ctx, jid)


@route("/api/print/job/abandon", "POST", perm="print.create", module=MODULE,
       action="print.job.abandon")
def job_abandon(ctx, body, q):
    jid = body.get("id") or body.get("job_id")
    reason = (body.get("reason") or "").strip()
    require(jid, "缺少 job_id")
    require(len(reason) >= 2, "放弃必须填写原因")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "print.job.abandon", "print_job", jid,
                              detail=reason)
        _transition(conn, jid, ABANDONED, reason, ctx)
        audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "print_job", jid,
                              "人工放弃打印：" + reason)
    return {"id": jid, "status": ABANDONED}


@route("/api/print/job/list", "GET", perm="print.view", module=MODULE, action="print.job.list")
def job_list(ctx, body, q):
    sql = "SELECT * FROM print_job WHERE deleted_at IS NULL"
    params = ()
    if q.get("biz_type"):
        sql += " AND biz_type=?"
        params += (q["biz_type"],)
    if q.get("biz_id"):
        sql += " AND biz_id=?"
        params += (q["biz_id"],)
    if q.get("status"):
        sql += " AND status=?"
        params += (q["status"],)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (min(int(q.get("limit") or 50), 200),)
    return {"items": db.query(sql, params)}


@route("/api/print/job/get", "GET", perm="print.view", module=MODULE, action="print.job.get")
def job_get(ctx, body, q):
    jid = q.get("id") or q.get("job_id")
    require(jid, "缺少 job_id")
    row = db.query_one("SELECT * FROM print_job WHERE id=? AND deleted_at IS NULL", (jid,))
    if row is None:
        raise not_found("打印任务不存在")
    row = dict(row)
    row["logs"] = db.query("SELECT * FROM print_status_log WHERE job_id=? ORDER BY id", (jid,))
    row["failures"] = db.query("SELECT * FROM print_failure WHERE job_id=? ORDER BY id", (jid,))
    return row


@route("/api/print/templates", "GET", perm="print.view", module=MODULE,
       action="print.templates")
def templates(ctx, body, q):
    sql = "SELECT id, template_code, name, biz_type, variant, is_default, is_active " \
          "FROM print_template WHERE deleted_at IS NULL"
    params = ()
    if q.get("biz_type"):
        sql += " AND biz_type=?"
        params = (q["biz_type"],)
    sql += " ORDER BY biz_type, id"
    return {"items": db.query(sql, params)}


@route("/api/print/failures", "GET", perm="print.view", module=MODULE, action="print.failures")
def failures(ctx, body, q):
    sql = "SELECT * FROM print_failure WHERE 1=1"
    params = ()
    if q.get("job_id"):
        sql += " AND job_id=?"
        params = (q["job_id"],)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (min(int(q.get("limit") or 50), 200),)
    return {"items": db.query(sql, params)}


# ---------------- F72/F73/F74/F75 硬件 ----------------

@route("/api/hardware/devices", "GET", perm="hardware.view", module=MODULE,
       action="hardware.devices")
def devices(ctx, body, q):
    return {"items": db.query("SELECT * FROM hardware_device WHERE deleted_at IS NULL "
                              "ORDER BY id"),
            "drivers": hardware.codes()}


@route("/api/hardware/device/register", "POST", perm="hardware.manage", module=MODULE,
       action="hardware.device.register")
def device_register(ctx, body, q):
    require_keys(body, ["device_code", "device_type", "name", "driver_code"])
    require(body["device_type"] in ("PRINTER", "ID_READER", "SCANNER"),
            "device_type 取值应为 PRINTER/ID_READER/SCANNER")
    hardware.get(body["driver_code"])          # 未注册驱动直接报错
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "hardware.device.register", "hardware_device",
                              None, detail={"code": body["device_code"]})
        cur = db.execute(conn, """
            INSERT INTO hardware_device (device_code, device_type, name, vendor, model,
                                         driver_code, conn_json, status, is_default, remark,
                                         created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body["device_code"].strip(), body["device_type"], body["name"].strip(),
              body.get("vendor"), body.get("model"), body["driver_code"],
              audit.dumps(body.get("conn") or {}), body.get("status") or "ONLINE",
              int(body.get("is_default") or 0), body.get("remark"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "device_code": body["device_code"]}


@route("/api/hardware/id_card/read", "POST", perm="hardware.use", module=MODULE,
       action="hardware.id_card.read")
def id_card_read(ctx, body, q):
    """F73 身份证读取：不落库隐私，只回传；校验位与派生字段一并给出。"""
    from modules import m01_patient as m01
    code = body.get("driver_code") or "mock.id_reader"
    try:
        res, ms = hardware.call(code, "read")
    except AppError as exc:
        with db.tx() as conn:
            _hw_event(conn, code, "READ_ID", "FAIL", "读取失败", exc.code, 0, ctx)
        raise
    id_no = (res.get("id_card_no") or "").strip().upper()
    valid = m01.valid_id_card(id_no)
    with db.tx() as conn:                      # R5：只写事件摘要，不含号码
        _hw_event(conn, code, "READ_ID", "SUCCESS",
                  "读取成功，校验%s" % ("通过" if valid else "未通过"), None, ms, ctx)
    return {"name": res.get("name"), "id_card_no": id_no, "valid": valid,
            "birth_date": m01.id_card_birth_date(id_no),
            "gender": _gender_from_id(id_no)}


def _gender_from_id(id_no):
    if len(id_no) == 18:
        return "M" if int(id_no[16]) % 2 == 1 else "F"
    if len(id_no) == 15:
        return "M" if int(id_no[14]) % 2 == 1 else "F"
    return "U"


@route("/api/hardware/scan", "POST", perm="hardware.use", module=MODULE,
       action="hardware.scan")
def scan(ctx, body, q):
    """F74 扫码：只解析不猜，识别不了返回 UNKNOWN。"""
    code = body.get("driver_code") or "mock.scanner"
    payload = body.get("code")
    if payload is None:
        try:
            res, ms = hardware.call(code, "scan")
        except AppError as exc:
            with db.tx() as conn:
                _hw_event(conn, code, "SCAN", "FAIL", "扫码失败", exc.code, 0, ctx)
            raise
        payload = res.get("code")
    else:
        ms = 0
    s = (payload or "").strip()
    kind, ref = "UNKNOWN", None
    if s.startswith("BC"):
        row = db.query_one("SELECT * FROM lab_specimen WHERE barcode=?", (s,))
        kind, ref = "SPECIMEN", (dict(row) if row else None)
    elif s.startswith("BT"):
        row = db.query_one("SELECT * FROM stock_batch WHERE batch_no=?", (s,))
        kind, ref = "BATCH", (dict(row) if row else None)
    elif s.startswith("R"):
        row = db.query_one("SELECT id, prescription_no, status FROM prescription "
                           "WHERE prescription_no=? AND deleted_at IS NULL", (s,))
        kind, ref = "PRESCRIPTION", (dict(row) if row else None)
    elif s.startswith("B"):
        row = db.query_one("SELECT id, bill_no, status FROM bill WHERE bill_no=? "
                           "AND deleted_at IS NULL", (s,))
        kind, ref = "BILL", (dict(row) if row else None)
    else:
        # 条码与编码都能命中（barcode 由 0016 迁移补到 drug/herb）
        row = (db.query_one("SELECT * FROM drug WHERE deleted_at IS NULL "
                            "AND (drug_code=? OR barcode=?)", (s, s))
               or db.query_one("SELECT * FROM herb WHERE deleted_at IS NULL "
                               "AND (herb_code=? OR barcode=?)", (s, s)))
        if row:
            kind = "HERB" if "herb_code" in row.keys() else "DRUG"
            ref = dict(row)
    with db.tx() as conn:
        _hw_event(conn, code, "SCAN", "SUCCESS", "识别为 %s" % kind, None, ms, ctx)
    return {"code": s, "kind": kind, "ref": ref}


@route("/api/hardware/check_prescription", "POST", perm="hardware.use", module=MODULE,
       action="hardware.check_prescription")
def check_prescription(ctx, body, q):
    """F74 R2 发药核对：扫到的药品与处方明细不符要报警并写高风险审计。"""
    rx_id = body.get("prescription_id")
    scanned = (body.get("code") or "").strip()
    require(rx_id and scanned, "缺少 prescription_id / code")
    hit = db.query_one("""SELECT 1 FROM prescription_item i JOIN drug d ON d.id=i.drug_id
                          WHERE i.prescription_id=? AND i.deleted_at IS NULL
                            AND (d.drug_code=? OR d.barcode=?)""", (rx_id, scanned, scanned))
    hit_h = db.query_one("""SELECT 1 FROM herb_prescription_item h JOIN herb e ON e.id=h.herb_id
                            WHERE h.prescription_id=? AND h.deleted_at IS NULL
                              AND (e.herb_code=? OR e.barcode=?)""", (rx_id, scanned, scanned))
    ok = bool(hit or hit_h)
    if not ok:
        with db.tx() as conn:
            audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "prescription", rx_id,
                                  "发药核对不一致：扫到 %s" % scanned,
                                  {"scanned": scanned})
    return {"prescription_id": rx_id, "scanned": scanned, "match": ok, "mismatch": not ok}


def probe_and_record(ctx=None):
    """F75 / F98：探测全部驱动 → 落 hardware_health → 同步 device.status。

    抽出为服务函数，供路由与 M13 看门狗共用（看门狗不得直写本模块表）。
    """
    ctx = ctx or audit.get_ctx()
    results = hardware.status_all()
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "hardware.health", "hardware_health", None)
        for r in results:
            db.execute(conn, """
                INSERT INTO hardware_health (driver_code, result, online, elapsed_ms, detail,
                                             checked_at)
                VALUES (?,?,?,?,?,?)
            """, (r["driver"], "OK" if r["online"] else "FAIL",
                  1 if r["online"] else 0, r.get("elapsed_ms", 0), r.get("detail", ""),
                  tu.now_str()))
            dev = db.query_one("SELECT id FROM hardware_device WHERE driver_code=? "
                               "AND deleted_at IS NULL", (r["driver"],))
            if dev:
                db.execute(conn, "UPDATE hardware_device SET status=?, updated_at=? WHERE id=?",
                           ("ONLINE" if r["online"] else "OFFLINE", tu.now_str(), dev["id"]))
    return {"items": results, "checked_at": tu.now_str()}


def record_event(driver_code, event_type, result, summary=None, error_code=None,
                 elapsed_ms=0, ctx=None):
    """写一条硬件事件（hardware_event 属 M10，M13 看门狗经本接口留痕）。"""
    ctx = ctx or audit.get_ctx()
    with db.tx() as conn:
        _hw_event(conn, driver_code, event_type, result, summary, error_code, elapsed_ms, ctx)
    return {"driver_code": driver_code, "event_type": event_type, "result": result}


@route("/api/hardware/health", "GET", perm="hardware.view", module=MODULE,
       action="hardware.health")
def health(ctx, body, q):
    """F75 硬件监控：探测全部驱动并落心跳。"""
    return probe_and_record(ctx)


# ---------------- F98 看门狗 / F96 崩溃收敛（M13 经本接口调用，不得直改本模块表）----------

def recover_stale_printing(ctx=None):
    """把卡在 PRINTING 的任务收敛为 FAILED（可重打）。

    幂等：状态已非 PRINTING 的不重复处理。打印无资金/库存影响，允许重打（F98 R5）。
    表所有权：print_job / print_failure 属 M10。
    返回 {"scanned":n, "recovered":n, "items":[...]}
    """
    timeout = max(config.get_int("print.job_timeout_sec", 120), 1)
    cutoff = tu.fmt(tu.now() - datetime.timedelta(seconds=timeout))
    rows = db.query("""
        SELECT id, print_job_no, status, attempt_count
        FROM print_job
        WHERE status='PRINTING' AND deleted_at IS NULL
          AND COALESCE(printed_at, created_at) IS NOT NULL
          AND COALESCE(printed_at, created_at) < ?
    """, (cutoff,))
    out = {"scanned": len(rows), "recovered": 0, "items": []}
    ctx = ctx or audit.get_ctx()
    for r in rows:
        with db.tx() as conn:
            _transition(conn, r["id"], FAILED, "看门狗：打印超时收敛", ctx)
            db.execute(conn, """
                INSERT INTO print_failure (job_id, attempt_no, error_code, message, retryable,
                                           occurred_at, operator_id)
                VALUES (?,?,?,?,1,?,?)
            """, (r["id"], (r["attempt_count"] or 0) + 1, HW_AGENT_UNREACHABLE,
                  "打印任务卡在 PRINTING 超过 %ds，看门狗收敛为失败（可重打）" % timeout,
                  tu.now_str(), ctx.user_id))
            out["recovered"] += 1
            out["items"].append({"print_job_no": r["print_job_no"], "action": "FAILED"})
    return out


def set_device_status(device_id, status, ctx=None):
    """看门狗降级/恢复设备状态（F98 R4）。状态推进走本模块，M13 不得直改。"""
    if status not in ("ONLINE", "OFFLINE", "DEGRADED"):
        raise AppError(DATA_FORMAT, "非法的设备状态：%s" % status)
    ctx = ctx or audit.get_ctx()
    with db.tx() as conn:
        db.execute(conn, "UPDATE hardware_device SET status=?, updated_at=?, updated_by=? "
                         "WHERE id=? AND deleted_at IS NULL",
                   (status, tu.now_str(), ctx.user_id, device_id))
    return {"device_id": device_id, "status": status}


@route("/api/hardware/events", "GET", perm="hardware.view", module=MODULE,
       action="hardware.events")
def events(ctx, body, q):
    sql = "SELECT * FROM hardware_event WHERE 1=1"
    params = ()
    if q.get("event_type"):
        sql += " AND event_type=?"
        params = (q["event_type"],)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (min(int(q.get("limit") or 100), 500),)
    return {"items": db.query(sql, params)}
