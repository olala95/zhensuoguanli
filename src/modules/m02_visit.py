# -*- coding: utf-8 -*-
"""M02 就诊中心（F05 创建 / F06 队列 / F07 接诊 / F08 留观挂单 / F09 恢复 / F125 作废退号）。

表所有权：visit / visit_status_log / visit_queue / visit_suspension / visit_void
状态机：03 §2.2 —— 禁止裸 UPDATE，一律走 transition()。
"""

from core import db, audit, timeutil as tu, ids
from core.api import route
from core.errors import (require, AppError, conflict, not_found, BIZ_STATE_INVALID,
                         DATA_REQUIRED)

WAITING = "WAITING"
IN_PROGRESS = "IN_PROGRESS"
SUSPENDED = "SUSPENDED"
COMPLETED = "COMPLETED"
VOID = "VOID"

# 03 §2.2
TRANSITIONS = {
    None:         [WAITING],
    WAITING:      [IN_PROGRESS, VOID],
    IN_PROGRESS:  [SUSPENDED, COMPLETED, VOID],
    SUSPENDED:    [IN_PROGRESS, VOID],
    COMPLETED:    [],
    VOID:         [],
}


def transition(conn, visit_id, to_status, reason=None, ctx=None, extra=None):
    """状态机唯一入口。非法转换 → E3001 + 高风险审计。"""
    row = db.query_one("SELECT * FROM visit WHERE id=? AND deleted_at IS NULL", (visit_id,))
    if row is None:
        raise not_found("就诊不存在")
    frm = row["status"]
    if to_status not in TRANSITIONS.get(frm, []):
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "visit", visit_id,
                              "非法状态转换 %s→%s" % (frm, to_status))
        raise conflict("就诊状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?", "version=version+1"]
    params = [to_status, tu.now_str(), (ctx.user_id if ctx else 0)]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(visit_id)
    db.execute(conn, "UPDATE visit SET %s WHERE id=?" % ",".join(sets), tuple(params))
    audit.record_status_log(conn, "visit_status_log", "visit_id", visit_id,
                            frm, to_status, reason)
    audit.audit_data_change(conn, "visit", visit_id, "status", frm, to_status, reason)
    return frm, to_status


def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """对外服务接口：患者合并时改挂就诊（M01 通过本接口调用，不直接写 visit 表）。"""
    cur = db.execute(conn, "UPDATE visit SET patient_id=?, updated_at=? "
                           "WHERE patient_id=? AND deleted_at IS NULL",
                     (new_patient_id, tu.now_str(), old_patient_id))
    n = cur.rowcount
    if n:
        try:
            from . import m03_record
            m03_record.reassign_patient(conn, old_patient_id, new_patient_id, reason)
        except Exception:
            pass
    return n


@route("/api/visit/create", "POST", perm="visit.create", module="M02", action="visit.create")
def create(ctx, body, q):
    """F05 创建 Visit：当日流水号 V{yyyyMMdd}{3位}，自动生成候诊号。

    visit_type 决定这次就诊走中医还是西医流程（TCM/WEST），下游的病历字段、
    处方类型、药房发药队列都按它分开；NORMAL 为历史数据兼容值，按西医通用处理。
    """
    patient_id = body.get("patient_id")
    require(patient_id, "缺少 patient_id")
    visit_type = body.get("visit_type") or "TCM"
    require(visit_type in ("TCM", "WEST", "NORMAL"),
            "visit_type 取值应为 TCM（中医）/ WEST（西医）", code=DATA_REQUIRED)
    with db.tx() as conn:
        p = db.query_one("SELECT id, name FROM patient WHERE id=? AND deleted_at IS NULL",
                         (patient_id,))
        if p is None:
            raise not_found("患者不存在")
        # 同一患者当日不可重复建未完成的就诊
        dup = db.query_one("SELECT id, visit_no FROM visit WHERE patient_id=? "
                           "AND deleted_at IS NULL AND status IN ('WAITING','IN_PROGRESS','SUSPENDED')",
                           (patient_id,))
        if dup:
            raise conflict("该患者已有未完成的就诊：%s" % dup["visit_no"])
        audit.audit_operation(conn, "M02", "visit.create", "visit", None,
                              detail={"patient_id": patient_id})
        visit_no = ids.next_no(conn, "VISIT")
        today = tu.today_str()
        prior = db.query_one("SELECT COUNT(*) c FROM visit WHERE patient_id=? "
                             "AND status='COMPLETED' AND deleted_at IS NULL", (patient_id,))
        is_first = 0 if (prior and prior["c"] > 0) else 1
        doctor_id = body.get("doctor_id") or (ctx.user_id if ctx.role_code == "DOCTOR" else None)
        cur = db.execute(conn, """
            INSERT INTO visit (visit_no, patient_id, doctor_id, visit_type, status,
                               is_first_visit, registered_at, registered_by, department,
                               note, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (visit_no, patient_id, doctor_id, body.get("visit_type", "NORMAL"), WAITING,
              is_first, tu.now_str(), ctx.user_id, body.get("department"),
              body.get("note"), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        vid = cur.lastrowid
        audit.record_status_log(conn, "visit_status_log", "visit_id", vid,
                                None, WAITING, "创建就诊")
        row = db.query_one("SELECT MAX(queue_no) m FROM visit_queue WHERE queue_date=?",
                           (today,))
        queue_no = (row["m"] or 0) + 1 if row else 1
        db.execute(conn, """
            INSERT INTO visit_queue (visit_id, queue_date, queue_no, priority, status,
                                     created_at, created_by)
            VALUES (?,?,?,?,'WAITING',?,?)
        """, (vid, today, queue_no, body.get("priority", 0), tu.now_str(), ctx.user_id))
        db.execute(conn, "UPDATE patient SET last_visit_at=?, updated_at=? WHERE id=?",
                   (tu.now_str(), tu.now_str(), patient_id))
        v = db.query_one("SELECT * FROM visit WHERE id=?", (vid,))
    return dict(v)


@route("/api/visit/set_type", "POST", perm="visit.create", module="M02",
       action="visit.set_type")
def set_type(ctx, body, q):
    """切换就诊的中医/西医类型（建错类型时改，不用重建就诊）。

    已开处方、已写病历都不动；只改这次就诊走哪套流程的呈现。
    """
    vid = body.get("visit_id")
    require(vid, "缺少 visit_id")
    vt = body.get("visit_type")
    require(vt in ("TCM", "WEST"), "visit_type 取值应为 TCM（中医）/ WEST（西医）",
            code=DATA_REQUIRED)
    with db.tx() as conn:
        v = db.query_one("SELECT * FROM visit WHERE id=? AND deleted_at IS NULL", (vid,))
        if v is None:
            raise not_found("就诊不存在")
        if v["status"] in (COMPLETED, VOID):
            raise conflict("已完成或已作废的就诊不能再改诊疗类型", code=BIZ_STATE_INVALID)
        if v["visit_type"] == vt:
            return {"visit_id": vid, "visit_type": vt, "changed": False}
        audit.audit_data_change(conn, "visit", vid, "visit_type", v["visit_type"], vt,
                                body.get("reason") or "切换中医/西医类型")
        db.execute(conn, "UPDATE visit SET visit_type=?, updated_at=?, updated_by=? "
                         "WHERE id=?", (vt, tu.now_str(), ctx.user_id, vid))
    return {"visit_id": vid, "visit_type": vt, "changed": True}


@route("/api/visit/queue", "GET", perm="visit.view", module="M02", action="visit.queue")
def queue(ctx, body, q):
    """F06 候诊队列。"""
    date = q.get("date") or tu.today_str()
    status = q.get("status")
    sql = """
        SELECT v.id, v.visit_no, v.status AS visit_status, v.visit_type, v.is_first_visit,
               v.doctor_id, v.registered_at, v.started_at,
               p.id AS patient_id, p.patient_no, p.name AS patient_name, p.gender,
               p.birth_date, p.phone,
               qq.queue_no, qq.priority, qq.status AS queue_status, qq.window_no, qq.called_at,
               u.display_name AS doctor_name
        FROM visit_queue qq
        JOIN visit v ON v.id = qq.visit_id AND v.deleted_at IS NULL
        JOIN patient p ON p.id = v.patient_id
        LEFT JOIN sys_user u ON u.id = v.doctor_id
        WHERE qq.queue_date=?
    """
    params = [date]
    if status:
        sql += " AND qq.status=?"
        params.append(status)
    sql += " ORDER BY qq.priority DESC, qq.queue_no"
    return {"date": date, "items": db.query(sql, tuple(params))}


@route("/api/visit/call", "POST", perm="visit.start", module="M02", action="visit.call")
def call(ctx, body, q):
    """F06 叫号：队列状态 → CALLED（不改变 Visit 状态）。"""
    visit_id = body.get("visit_id")
    require(visit_id, "缺少 visit_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M02", "visit.call", "visit", visit_id)
        db.execute(conn, "UPDATE visit_queue SET status='CALLED', called_at=?, called_by=?, "
                         "window_no=? WHERE visit_id=?",
                   (tu.now_str(), ctx.user_id, body.get("window_no"), visit_id))
    return {"visit_id": visit_id, "queue_status": "CALLED"}


@route("/api/visit/start", "POST", perm="visit.start", module="M02", action="visit.start")
def start(ctx, body, q):
    """F07 接诊：WAITING → IN_PROGRESS。"""
    visit_id = body.get("visit_id")
    require(visit_id, "缺少 visit_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M02", "visit.start", "visit", visit_id)
        doctor_id = body.get("doctor_id") or (ctx.user_id if ctx.role_code == "DOCTOR" else None)
        frm, to = transition(conn, visit_id, IN_PROGRESS, body.get("reason"), ctx,
                             extra={"started_at": tu.now_str(), "doctor_id": doctor_id})
        db.execute(conn, "UPDATE visit_queue SET status='DONE', finished_at=? "
                         "WHERE visit_id=?", (tu.now_str(), visit_id))
        from . import m03_record
        record = m03_record.ensure_record(conn, visit_id, ctx)
        db.execute(conn, "UPDATE visit_queue SET status='DONE' WHERE visit_id=?", (visit_id,))
    return {"visit_id": visit_id, "from": frm, "to": to, "record_id": record["id"]}


@route("/api/visit/suspend", "POST", perm="visit.suspend", module="M02", action="visit.suspend")
def suspend(ctx, body, q):
    """F08 留观 / 挂单：IN_PROGRESS → SUSPENDED，必须填原因。"""
    visit_id = body.get("visit_id")
    reason = (body.get("reason") or "").strip()
    require(visit_id, "缺少 visit_id")
    require(len(reason) >= 2, "挂单/留观必须填写原因")
    require(body.get("suspend_type") in ("OBSERVATION", "HOLD"),
            "suspend_type 取值应为 OBSERVATION 或 HOLD")
    with db.tx() as conn:
        audit.audit_operation(conn, "M02", "visit.suspend", "visit", visit_id,
                              detail={"type": body["suspend_type"]})
        frm, to = transition(conn, visit_id, SUSPENDED, reason, ctx)
        db.execute(conn, """
            INSERT INTO visit_suspension (visit_id, suspend_type, reason, suspended_at,
                                          suspended_by, expected_resume_at, note, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (visit_id, body["suspend_type"], reason, tu.now_str(), ctx.user_id,
              body.get("expected_resume_at"), body.get("note"), tu.now_str()))
    return {"visit_id": visit_id, "from": frm, "to": to}


@route("/api/visit/resume", "POST", perm="visit.resume", module="M02", action="visit.resume")
def resume(ctx, body, q):
    """F09 恢复：SUSPENDED → IN_PROGRESS，补写恢复人与时间。"""
    visit_id = body.get("visit_id")
    require(visit_id, "缺少 visit_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M02", "visit.resume", "visit", visit_id)
        frm, to = transition(conn, visit_id, IN_PROGRESS, body.get("reason"), ctx)
        db.execute(conn, """
            UPDATE visit_suspension SET resumed_at=?, resumed_by=?, note=COALESCE(?,note)
            WHERE visit_id=? AND resumed_at IS NULL
        """, (tu.now_str(), ctx.user_id, body.get("note"), visit_id))
    return {"visit_id": visit_id, "from": frm, "to": to}


@route("/api/visit/complete", "POST", perm="visit.complete", module="M02", action="visit.complete")
def complete(ctx, body, q):
    """完成就诊：IN_PROGRESS → COMPLETED（终态）。"""
    visit_id = body.get("visit_id")
    require(visit_id, "缺少 visit_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M02", "visit.complete", "visit", visit_id)
        frm, to = transition(conn, visit_id, COMPLETED, body.get("reason"), ctx,
                             extra={"completed_at": tu.now_str()})
    return {"visit_id": visit_id, "from": frm, "to": to}


@route("/api/visit/void", "POST", perm="visit.void", module="M02", action="visit.void")
def void(ctx, body, q):
    """F125 就诊作废与退号：必须填原因，写 audit_high_risk，可标记需退费。"""
    visit_id = body.get("visit_id")
    reason = (body.get("reason") or "").strip()
    require(visit_id, "缺少 visit_id")
    require(len(reason) >= 2, "作废必须填写原因")
    with db.tx() as conn:
        audit.audit_operation(conn, "M02", "visit.void", "visit", visit_id,
                              detail={"reason": reason})
        frm, to = transition(conn, visit_id, VOID, reason, ctx,
                             extra={"voided_at": tu.now_str()})
        db.execute(conn, """
            INSERT INTO visit_void (visit_id, reason, voided_by, voided_at, refund_required,
                                    detail_json, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (visit_id, reason, ctx.user_id, tu.now_str(),
              body.get("refund_required", 0), audit.dumps(body.get("detail")), tu.now_str()))
        db.execute(conn, "UPDATE visit_queue SET status='CANCELLED' WHERE visit_id=?",
                   (visit_id,))
        audit.audit_high_risk(conn, audit.RISK_VOID, "visit", visit_id, reason,
                              {"refund_required": body.get("refund_required", 0)})
        # 联动：作废该就诊下未确认的处方（已确认的保留并告警，需人工退费）
        voided_rx = 0
        confirmed_rx = 0
        try:
            from modules import m06_prescription
            voided_rx = m06_prescription.void_by_visit(conn, visit_id, "就诊作废联动：" + reason,
                                                       ctx)
            row = db.query_one("SELECT COUNT(*) c FROM prescription WHERE visit_id=? "
                               "AND deleted_at IS NULL AND status IN "
                               "('CONFIRMED','DISPENSED','PARTIAL_RETURNED')", (visit_id,))
            confirmed_rx = row["c"] if row else 0
        except Exception:
            pass  # M06 尚未就绪时不阻断就诊作废，但记录日志
        # 联动：取消该就诊下未执行的处置（已完成的保留，需人工退费）
        cancelled_tr = 0
        try:
            from modules import m08_treatment
            cancelled_tr = m08_treatment.cancel_by_visit(
                conn, visit_id, "就诊作废联动：" + reason, ctx)
        except Exception:
            pass
    return {"visit_id": visit_id, "from": frm, "to": to,
            "voided_prescriptions": voided_rx, "confirmed_prescriptions": confirmed_rx,
            "cancelled_treatments": cancelled_tr}


@route("/api/visit/get", "GET", perm="visit.view", module="M02", action="visit.get")
def get(ctx, body, q):
    visit_id = q.get("id") or q.get("visit_id")
    require(visit_id, "缺少 visit_id")
    row = db.query_one("""
        SELECT v.*, p.patient_no, p.name AS patient_name, p.gender, p.birth_date, p.phone,
               u.display_name AS doctor_name, qq.queue_no, qq.status AS queue_status
        FROM visit v
        JOIN patient p ON p.id=v.patient_id
        LEFT JOIN sys_user u ON u.id=v.doctor_id
        LEFT JOIN visit_queue qq ON qq.visit_id=v.id
        WHERE v.id=? AND v.deleted_at IS NULL
    """, (visit_id,))
    if row is None:
        raise not_found("就诊不存在")
    row = dict(row)
    row["status_log"] = db.query("SELECT * FROM visit_status_log WHERE visit_id=? "
                                 "ORDER BY id", (visit_id,))
    row["suspensions"] = db.query("SELECT * FROM visit_suspension WHERE visit_id=? "
                                  "ORDER BY id", (visit_id,))
    return row


@route("/api/visit/list", "GET", perm="visit.view", module="M02", action="visit.list")
def list_visits(ctx, body, q):
    """按患者或日期列出就诊。"""
    sql = """
        SELECT v.id, v.visit_no, v.status, v.visit_type, v.is_first_visit, v.registered_at,
               v.started_at, v.completed_at, v.patient_id, p.name AS patient_name,
               p.patient_no, u.display_name AS doctor_name
        FROM visit v JOIN patient p ON p.id=v.patient_id
        LEFT JOIN sys_user u ON u.id=v.doctor_id
        WHERE v.deleted_at IS NULL
    """
    params = []
    if q.get("patient_id"):
        sql += " AND v.patient_id=?"
        params.append(q["patient_id"])
    if q.get("date"):
        sql += " AND date(v.registered_at)=?"
        params.append(q["date"])
    if q.get("status"):
        sql += " AND v.status=?"
        params.append(q["status"])
    sql += " ORDER BY v.id DESC LIMIT ?"
    params.append(min(int(q.get("limit") or 50), 200))
    return {"items": db.query(sql, tuple(params))}
