# -*- coding: utf-8 -*-
"""M08 处置执行（F52-F56 / F116）。

表所有权：treatment_item / treatment_package / treatment_package_item /
         treatment_execution / treatment_status_log / treatment_process（只追加）/
         tcm_treatment_record
状态机：03 §2.11 —— SCHEDULED→IN_PROGRESS→COMPLETED；SCHEDULED→CANCELLED；
       IN_PROGRESS→INTERRUPTED（必填原因）；INTERRUPTED→IN_PROGRESS|CANCELLED
金额：03 §3.1（行小计 = round(单价 × 数量)），单价取开单时快照（调价不追溯）
边界：M09 只读本模块数据；Visit 作废经 cancel_by_visit 接口取消未执行处置
"""

from core import db, audit, timeutil as tu, ids
from core import money
from core.api import route
from core.errors import (require, require_keys, AppError, conflict, not_found,
                         BIZ_STATE_INVALID, DATA_REQUIRED)

MODULE = "M08"

SCHEDULED = "SCHEDULED"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
CANCELLED = "CANCELLED"
INTERRUPTED = "INTERRUPTED"

TRANSITIONS = {
    SCHEDULED:   [IN_PROGRESS, CANCELLED],
    IN_PROGRESS: [COMPLETED, INTERRUPTED],
    INTERRUPTED: [IN_PROGRESS, CANCELLED],
    COMPLETED:   [],
    CANCELLED:   [],
}

CATEGORIES = ("INJECTION", "DRESSING", "NEBULIZE", "PHYSIO", "TCM", "EXAM", "OTHER")
THERAPIES = ("ACUP", "TUINA", "CUPPING", "MOXA", "GUASHA", "PATCH", "OTHER")
PHASES = ("PRE", "STEP", "OBSERVE", "REACTION", "AFTER")


def transition(conn, exec_id, to_status, reason=None, ctx=None, extra=None):
    """状态机唯一入口（03 §2.1/R5）。非法转换 → E3001 + 高风险审计。"""
    ctx = ctx or audit.get_ctx()
    row = db.query_one("SELECT * FROM treatment_execution WHERE id=? AND deleted_at IS NULL",
                       (exec_id,))
    if row is None:
        raise not_found("处置执行记录不存在")
    frm = row["status"]
    if to_status not in TRANSITIONS.get(frm, []):
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "treatment_execution", exec_id,
                              "%s → %s" % (frm, to_status))
        raise conflict("处置状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?"]
    params = [to_status, tu.now_str(), ctx.user_id]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(exec_id)
    db.execute(conn, "UPDATE treatment_execution SET %s WHERE id=?" % ",".join(sets),
               tuple(params))
    audit.audit_data_change(conn, "treatment_execution", exec_id, "status", frm, to_status,
                            reason)
    db.execute(conn, """
        INSERT INTO treatment_status_log (exec_id, from_status, to_status, reason,
                                          operator_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (exec_id, frm, to_status, reason, ctx.user_id, tu.now_str()))
    return row


# ============================================================
# F52 处置项目
# ============================================================

@route("/api/treatment/item/create", "POST", perm="treatment.manage",
       module=MODULE, action="treatment.item.create")
def item_create(ctx, body, q):
    require_keys(body, ["name"])
    code = (body.get("item_code") or "").strip() or \
        ("TZ" + tu.now().strftime("%Y%m%d%H%M%S"))
    category = (body.get("category") or "OTHER").upper()
    require(category in CATEGORIES, "category 取值非法：%s" % category)
    price_fen = money.to_fen(body.get("price_yuan")) if body.get("price_yuan") is not None \
        else int(body.get("price_fen") or 0)
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "treatment.item.create", "treatment_item", None,
                              detail={"name": body["name"]})
        cur = db.execute(conn, """
            INSERT INTO treatment_item (item_code, name, alias, pinyin, category, unit,
                                        price_fen, need_record, is_tcm, duration_min,
                                        remark, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (code, body["name"].strip(), body.get("alias"), body.get("pinyin"),
              category, body.get("unit") or "次", price_fen,
              int(body.get("need_record") or 0), int(body.get("is_tcm") or 0),
              body.get("duration_min"), body.get("remark"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "item_code": code, "price_fen": price_fen}


@route("/api/treatment/item/search", "GET", perm="treatment.view",
       module=MODULE, action="treatment.item.search")
def item_search(ctx, body, q):
    kw = (q.get("keyword") or "").strip()
    sql = "SELECT * FROM treatment_item WHERE deleted_at IS NULL AND is_active=1"
    params = ()
    if kw:
        sql += " AND (name LIKE ? OR alias LIKE ? OR pinyin LIKE ? OR item_code LIKE ?)"
        like = "%" + kw + "%"
        params = (like, like, like, like)
    if q.get("category"):
        sql += " AND category=?"
        params += (q["category"].upper(),)
    sql += " ORDER BY category, id LIMIT 200"
    return {"items": db.query(sql, params)}


@route("/api/treatment/item/update", "POST", perm="treatment.manage",
       module=MODULE, action="treatment.item.update")
def item_update(ctx, body, q):
    """调价不追溯已开立的执行记录（R-M08-4）。"""
    tid = body.get("id")
    require(tid, "缺少 id")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM treatment_item WHERE id=? AND deleted_at IS NULL",
                           (tid,))
        if row is None:
            raise not_found("处置项目不存在")
        audit.audit_operation(conn, MODULE, "treatment.item.update", "treatment_item", tid)
        sets, params, changed = [], [], []
        for f in ("name", "alias", "pinyin", "category", "unit", "remark"):
            if f in body and body[f] is not None and row[f] != body[f]:
                sets.append("%s=?" % f)
                params.append(body[f])
                changed.append((f, row[f], body[f]))
        if body.get("price_fen") is not None and int(body["price_fen"]) != row["price_fen"]:
            sets.append("price_fen=?")
            params.append(int(body["price_fen"]))
            changed.append(("price_fen", row["price_fen"], int(body["price_fen"])))
        for f in ("need_record", "is_tcm", "duration_min", "is_active"):
            if f in body and body[f] is not None:
                sets.append("%s=?" % f)
                params.append(int(body[f]) if f != "duration_min" else body[f])
        if not sets:
            return {"id": tid, "changed": 0}
        sets += ["updated_at=?", "updated_by=?"]
        params += [tu.now_str(), ctx.user_id, tid]
        db.execute(conn, "UPDATE treatment_item SET %s WHERE id=?" % ",".join(sets),
                   tuple(params))
        for f, b, a in changed:
            audit.audit_data_change(conn, "treatment_item", tid, f, b, a, body.get("reason"))
    return {"id": tid, "changed": len(changed)}


# ============================================================
# F53 处置组套
# ============================================================

@route("/api/treatment/package/create", "POST", perm="treatment.manage",
       module=MODULE, action="treatment.package.create")
def package_create(ctx, body, q):
    require_keys(body, ["name"])
    items = body.get("items") or []
    require(items, "组套明细不能为空")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "treatment.package.create", "treatment_package",
                              None, detail={"name": body["name"]})
        code = (body.get("package_code") or "").strip() or \
            ("TP" + tu.now().strftime("%Y%m%d%H%M%S"))
        cur = db.execute(conn, """
            INSERT INTO treatment_package (package_code, name, category, scope, owner_id,
                                           remark, created_at, created_by, updated_at,
                                           updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (code, body["name"].strip(), body.get("category"),
              body.get("scope") or "CLINIC", body.get("owner_id") or ctx.user_id,
              body.get("remark"), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        pid = cur.lastrowid
        for i, it in enumerate(items, start=1):
            item = db.query_one("SELECT * FROM treatment_item WHERE id=? AND deleted_at IS NULL "
                                "AND is_active=1", (it.get("item_id"),))
            if item is None:
                raise conflict("第 %d 行项目不存在或已停用（F53 R1）" % i, code="E3060")
            db.execute(conn, """
                INSERT INTO treatment_package_item (package_id, item_id, item_name_snap,
                    quantity, unit_snap, price_fen_snap, seq_no, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (pid, item["id"], item["name"], float(it.get("quantity") or 1),
                  item["unit"], item["price_fen"], i, tu.now_str(), ctx.user_id))
    return {"id": pid, "package_code": code, "lines": len(items)}


@route("/api/treatment/package/list", "GET", perm="treatment.view",
       module=MODULE, action="treatment.package.list")
def package_list(ctx, body, q):
    rows = db.query("SELECT * FROM treatment_package WHERE deleted_at IS NULL AND is_active=1 "
                    "ORDER BY id DESC LIMIT 200")
    for r in rows:
        r["items"] = db.query("SELECT * FROM treatment_package_item WHERE package_id=? "
                              "ORDER BY seq_no", (r["id"],))
    return {"items": rows}


# ============================================================
# F54 开立 / F55 执行
# ============================================================

def _new_execution(conn, ctx, visit, item, quantity, package_id=None):
    exec_no = ids.next_no(conn, "TREATMENT_EXEC")
    amount = money.line_amount_fen(int(item["price_fen"]), float(quantity))
    cur = db.execute(conn, """
        INSERT INTO treatment_execution (exec_no, visit_id, patient_id, item_id,
            item_name_snap, category_snap, unit_snap, price_fen, quantity, amount_fen,
            package_id, status, patient_name_snap, gender_snap, age_text_snap, ordered_by,
            ordered_at, occurred_at, created_at, created_by, updated_at, updated_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'SCHEDULED',?,?,?,?,?,?,?,?,?,?)
    """, (exec_no, visit["id"], visit["patient_id"], item["id"], item["name"],
          item["category"], item["unit"], int(item["price_fen"]), float(quantity), amount,
          package_id, visit.get("patient_name_snap"), visit.get("gender_snap"),
          visit.get("age_text_snap"), ctx.user_id, tu.now_str(), tu.now_str(),
          tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    eid = cur.lastrowid
    db.execute(conn, """
        INSERT INTO treatment_status_log (exec_id, from_status, to_status, reason,
                                          operator_id, created_at)
        VALUES (?,NULL,'SCHEDULED',?,?,?)
    """, (eid, "开立处置", ctx.user_id, tu.now_str()))
    return {"id": eid, "exec_no": exec_no, "amount_fen": amount}


@route("/api/treatment/order", "POST", perm="treatment.order", module=MODULE,
       action="treatment.order")
def order(ctx, body, q):
    """F54 开立处置：支持直接项目列表或应用组套（组套展开为独立记录，R-M08-8）。"""
    visit_id = body.get("visit_id")
    require(visit_id, "缺少 visit_id")
    items = body.get("items") or []
    package_id = body.get("package_id")
    if package_id and not items:
        items = [{"item_id": r["item_id"], "quantity": r["quantity"]} for r in
                 db.query("SELECT * FROM treatment_package_item WHERE package_id=? "
                          "ORDER BY seq_no", (package_id,))]
    require(items, "缺少处置明细")

    with db.tx() as conn:
        visit = db.query_one("SELECT * FROM visit WHERE id=? AND deleted_at IS NULL",
                             (visit_id,))
        if visit is None:
            raise not_found("就诊不存在")
        if visit["status"] not in ("WAITING", "IN_PROGRESS", "SUSPENDED"):
            raise conflict("就诊状态为 %s，不能开立处置（F54 R1）" % visit["status"],
                           code="E3061")
        audit.audit_operation(conn, MODULE, "treatment.order", "visit", visit_id,
                              detail={"lines": len(items), "package_id": package_id})
        out = []
        for i, it in enumerate(items, start=1):
            # 注意单参数元组必须带逗号，否则 sqlite3 会把 int 当成"参数序列"而报错
            item = db.query_one("SELECT * FROM treatment_item WHERE id=? AND deleted_at IS NULL "
                                "AND is_active=1", (it.get("item_id") or it.get("id"),))
            if item is None:
                raise conflict("第 %d 行项目不存在或已停用（F54 R1）" % i, code="E3060")
            qty = float(it.get("quantity") or 1)
            require(qty > 0, "第 %d 行数量必须大于 0" % i)
            out.append(_new_execution(conn, ctx, visit, item, qty, package_id))
        if package_id:
            db.execute(conn, "UPDATE treatment_package SET usage_count=usage_count+1 "
                             "WHERE id=?", (package_id,))
    return {"visit_id": visit_id, "count": len(out), "items": out,
            "total_amount_fen": sum(x["amount_fen"] for x in out)}


@route("/api/treatment/start", "POST", perm="treatment.execute", module=MODULE,
       action="treatment.start")
def start(ctx, body, q):
    eid = body.get("id") or body.get("exec_id")
    require(eid, "缺少 exec_id")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "treatment.start", "treatment_execution", eid)
        transition(conn, eid, IN_PROGRESS, "开始执行", ctx,
                   extra={"executor_id": ctx.user_id, "executed_at": tu.now_str()})
    return {"id": eid, "to": IN_PROGRESS}


@route("/api/treatment/complete", "POST", perm="treatment.execute", module=MODULE,
       action="treatment.complete")
def complete(ctx, body, q):
    """F55 完成：need_record / 中医项目必须已填写记录（R3/R5）。"""
    eid = body.get("id") or body.get("exec_id")
    require(eid, "缺少 exec_id")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM treatment_execution WHERE id=? AND deleted_at IS NULL",
                           (eid,))
        if row is None:
            raise not_found("处置执行记录不存在")
        item = db.query_one("SELECT * FROM treatment_item WHERE id=?", (row["item_id"],)) or {}
        if int(item.get("need_record") or 0):
            n = db.query_one("SELECT COUNT(*) c FROM treatment_process WHERE exec_id=?", (eid,))
            if not n or n["c"] <= 0:
                raise AppError(DATA_REQUIRED, "该处置要求填写执行记录（F56）后才能完成",
                               retryable=False, http_status=400)
        if int(item.get("is_tcm") or 0):
            n = db.query_one("SELECT COUNT(*) c FROM tcm_treatment_record WHERE exec_id=?",
                             (eid,))
            if not n or n["c"] <= 0:
                raise AppError(DATA_REQUIRED,
                               "中医治疗必须填写治疗记录（F116：穴位/手法/留针时长）",
                               retryable=False, http_status=400)
        audit.audit_operation(conn, MODULE, "treatment.complete", "treatment_execution", eid)
        transition(conn, eid, COMPLETED, "执行完成", ctx,
                   extra={"executor_id": ctx.user_id, "executed_at": tu.now_str(),
                          "occurred_at": tu.now_str()})
    return {"id": eid, "to": COMPLETED}


@route("/api/treatment/interrupt", "POST", perm="treatment.execute", module=MODULE,
       action="treatment.interrupt")
def interrupt(ctx, body, q):
    eid = body.get("id") or body.get("exec_id")
    reason = (body.get("reason") or "").strip()
    require(eid, "缺少 exec_id")
    require(len(reason) >= 2, "中断必须填写原因（F55 R2）")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "treatment.interrupt", "treatment_execution", eid,
                              detail=reason)
        transition(conn, eid, INTERRUPTED, reason, ctx, extra={"interrupt_reason": reason})
        audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "treatment_execution", eid,
                              "处置中断：" + reason)
    return {"id": eid, "to": INTERRUPTED}


@route("/api/treatment/resume", "POST", perm="treatment.execute", module=MODULE,
       action="treatment.resume")
def resume(ctx, body, q):
    eid = body.get("id") or body.get("exec_id")
    require(eid, "缺少 exec_id")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "treatment.resume", "treatment_execution", eid)
        transition(conn, eid, IN_PROGRESS, "中断后继续执行", ctx)
    return {"id": eid, "to": IN_PROGRESS}


@route("/api/treatment/cancel", "POST", perm="treatment.order", module=MODULE,
       action="treatment.cancel")
def cancel(ctx, body, q):
    eid = body.get("id") or body.get("exec_id")
    reason = (body.get("reason") or "").strip()
    require(eid, "缺少 exec_id")
    require(len(reason) >= 2, "取消必须填写原因（R-M08-3）")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM treatment_execution WHERE id=? AND deleted_at IS NULL",
                           (eid,))
        if row is None:
            raise not_found("处置执行记录不存在")
        if row["status"] == COMPLETED:
            raise conflict("已完成的处置不可直接取消，请先走退款流程（F55 R6）", code="E3062")
        audit.audit_operation(conn, MODULE, "treatment.cancel", "treatment_execution", eid,
                              detail=reason)
        transition(conn, eid, CANCELLED, reason, ctx, extra={"cancel_reason": reason})
        audit.audit_high_risk(conn, audit.RISK_VOID, "treatment_execution", eid,
                              "取消处置：" + reason)
    return {"id": eid, "to": CANCELLED}


@route("/api/treatment/list", "GET", perm="treatment.view", module=MODULE,
       action="treatment.list")
def list_by_visit(ctx, body, q):
    vid = q.get("visit_id")
    pid = q.get("patient_id")
    sql = "SELECT * FROM treatment_execution WHERE deleted_at IS NULL"
    params = ()
    if vid:
        sql += " AND visit_id=?"
        params = (vid,)
    elif pid:
        sql += " AND patient_id=?"
        params = (pid,)
    if q.get("status"):
        sql += " AND status=?"
        params += (q["status"],)
    sql += " ORDER BY id DESC LIMIT 200"
    return {"items": db.query(sql, params)}


# ============================================================
# F56 治疗过程
# ============================================================

@route("/api/treatment/process/add", "POST", perm="treatment.execute", module=MODULE,
       action="treatment.process.add")
def process_add(ctx, body, q):
    """F56 过程记录：只追加（R3），异常反应写高风险审计（R4）。"""
    eid = body.get("exec_id") or body.get("id")
    content = (body.get("content") or "").strip()
    require(eid, "缺少 exec_id")
    require(content, "过程内容不能为空")
    phase = (body.get("phase") or "STEP").upper()
    require(phase in PHASES, "phase 取值非法：%s" % phase)
    abnormal = int(body.get("is_abnormal") or 0)
    if abnormal:
        require(len((body.get("content") or "")) >= 4, "异常反应需写明具体情况")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM treatment_execution WHERE id=? AND deleted_at IS NULL",
                           (eid,))
        if row is None:
            raise not_found("处置执行记录不存在")
        audit.audit_operation(conn, MODULE, "treatment.process.add", "treatment_execution", eid,
                              detail={"phase": phase})
        n = db.query_one("SELECT COALESCE(MAX(seq_no),0) m FROM treatment_process "
                         "WHERE exec_id=?", (eid,))
        cur = db.execute(conn, """
            INSERT INTO treatment_process (exec_id, seq_no, phase, content, is_abnormal,
                                           attachment, checksum, occurred_at, operator_id,
                                           created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (eid, int(n["m"]) + 1, phase, content, abnormal, body.get("attachment"),
              body.get("checksum"), tu.now_str(), ctx.user_id, tu.now_str()))
        if abnormal:
            audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "treatment_process",
                                  cur.lastrowid, "治疗异常反应：" + content)
    return {"id": cur.lastrowid, "exec_id": eid, "seq_no": int(n["m"]) + 1}


@route("/api/treatment/process/list", "GET", perm="treatment.view", module=MODULE,
       action="treatment.process.list")
def process_list(ctx, body, q):
    eid = q.get("exec_id")
    require(eid, "缺少 exec_id")
    return {"items": db.query("SELECT * FROM treatment_process WHERE exec_id=? ORDER BY seq_no",
                              (eid,))}


# ============================================================
# F116 中医治疗执行记录
# ============================================================

@route("/api/treatment/tcm/save", "POST", perm="treatment.execute", module=MODULE,
       action="treatment.tcm.save")
def tcm_save(ctx, body, q):
    """F116 中医治疗记录：针灸必填穴位/手法/留针时长（R2）；只追加不改（R7）。"""
    eid = body.get("exec_id") or body.get("id")
    require(eid, "缺少 exec_id")
    therapy = (body.get("therapy_type") or "ACUP").upper()
    require(therapy in THERAPIES, "therapy_type 取值非法：%s" % therapy)
    if therapy == "ACUP":
        require((body.get("points") or "").strip(), "针灸必须记录穴位（F116 R2）")
        require((body.get("manipulation") or "").strip(), "针灸必须记录手法（F116 R2）")
        require(body.get("retain_minutes") is not None, "针灸必须记录留针时长（F116 R2）")
    abnormal = int(body.get("is_abnormal") or 0)
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM treatment_execution WHERE id=? AND deleted_at IS NULL",
                           (eid,))
        if row is None:
            raise not_found("处置执行记录不存在")
        item = db.query_one("SELECT * FROM treatment_item WHERE id=?", (row["item_id"],)) or {}
        if not int(item.get("is_tcm") or 0):
            raise conflict("非中医项目不得记录中医治疗（F116 R6）", code="E3063")
        audit.audit_operation(conn, MODULE, "treatment.tcm.save", "treatment_execution", eid,
                              detail={"therapy_type": therapy})
        cur = db.execute(conn, """
            INSERT INTO tcm_treatment_record (exec_id, visit_id, patient_id, therapy_type,
                points, manipulation, retain_minutes, body_part, posture, syndrome_snap,
                reaction, is_abnormal, performed_at, operator_id, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (eid, row["visit_id"], row["patient_id"], therapy,
              (body.get("points") or "").strip() or None, body.get("manipulation"),
              body.get("retain_minutes"), body.get("body_part"), body.get("posture"),
              body.get("syndrome_snap"), body.get("reaction"), abnormal,
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        if abnormal:
            audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "tcm_treatment_record",
                                  cur.lastrowid,
                                  "中医治疗异常反应：" + (body.get("reaction") or ""))
    return {"id": cur.lastrowid, "exec_id": eid, "therapy_type": therapy}


@route("/api/treatment/tcm/list", "GET", perm="treatment.view", module=MODULE,
       action="treatment.tcm.list")
def tcm_list(ctx, body, q):
    eid = q.get("exec_id")
    sql = "SELECT * FROM tcm_treatment_record WHERE 1=1"
    params = ()
    if eid:
        sql += " AND exec_id=?"
        params = (eid,)
    elif q.get("patient_id"):
        sql += " AND patient_id=?"
        params = (q["patient_id"],)
    sql += " ORDER BY id DESC LIMIT 200"
    return {"items": db.query(sql, params)}


# ---------- 对外服务接口 ----------

def cancel_by_visit(conn, visit_id, reason, ctx=None):
    """M02 F125 就诊作废：取消该就诊下未执行的处置；已完成的保留（需人工退费）。"""
    rows = db.query("SELECT id, status FROM treatment_execution WHERE visit_id=? "
                    "AND deleted_at IS NULL AND status IN ('SCHEDULED','IN_PROGRESS',"
                    "'INTERRUPTED')", (visit_id,))
    n = 0
    for r in rows:
        transition(conn, r["id"], CANCELLED, reason, ctx, extra={"cancel_reason": reason})
        audit.audit_high_risk(conn, audit.RISK_VOID, "treatment_execution", r["id"],
                              "就诊作废联动取消处置：" + reason)
        n += 1
    return n


def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """M01 F04 患者合并改挂：只改 patient_id，快照字段不动。"""
    cur = db.execute(conn, "UPDATE treatment_execution SET patient_id=? WHERE patient_id=? "
                           "AND deleted_at IS NULL", (new_patient_id, old_patient_id))
    n = cur.rowcount
    cur = db.execute(conn, "UPDATE tcm_treatment_record SET patient_id=? WHERE patient_id=?",
                     (new_patient_id, old_patient_id))
    return n + cur.rowcount


def import_upsert(kind, rows):
    """M14 F128 基础数据包导入（处置项目）。"""
    ok, failed, errors = 0, 0, []

    def pick(row, *keys):
        for k in keys:
            if k in row and (row[k] or "").strip():
                return row[k].strip()
        return None

    for i, row in enumerate(rows, start=2):
        try:
            code = pick(row, "item_code", "编码", "项目编码", "code")
            name = pick(row, "name", "名称", "项目名称")
            if not (code and name):
                failed += 1
                errors.append({"line": i, "error": "缺少编码或名称"})
                continue
            price = pick(row, "price_yuan", "单价", "价格", "price_fen")
            with db.tx() as conn:
                db.execute(conn, """
                    INSERT INTO treatment_item (item_code, name, alias, pinyin, category,
                        unit, price_fen, need_record, is_tcm, created_at, created_by,
                        updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,0,?,0)
                    ON CONFLICT(item_code) DO UPDATE SET
                        name=excluded.name, category=excluded.category,
                        unit=excluded.unit, price_fen=excluded.price_fen,
                        updated_at=excluded.updated_at
                """, (code, name, pick(row, "alias", "别名"), pick(row, "pinyin", "拼音"),
                      (pick(row, "category", "类别") or "OTHER").upper(),
                      pick(row, "unit", "单位") or "次",
                      money.to_fen(price) if price else 0,
                      int(pick(row, "need_record", "需要执行记录") or 0),
                      int(pick(row, "is_tcm", "中医") or 0),
                      tu.now_str(), tu.now_str()))
            ok += 1
        except Exception as exc:
            failed += 1
            errors.append({"line": i, "error": str(exc)})
    return len(rows), ok, failed, errors
