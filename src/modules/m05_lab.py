# -*- coding: utf-8 -*-
"""M05 检验检查（全部外送）：F27-F31 / F117 外送送检回报 / F118 机构对账。

表所有权：lab_agency / lab_item / lab_order / lab_order_item / lab_order_status_log /
         lab_specimen / lab_result / lab_result_attachment / lab_delivery / lab_reconciliation
状态机：03 §2.9 —— DRAFT→ORDERED→SAMPLED→DELIVERED→REPORTED→REVIEWED
       ORDERED / SAMPLED → CANCELLED
业务边界：检验全部外送，结果手工回录 + 报告图片附件，**不对接仪器**
"""

from core import db, audit, config, timeutil as tu, ids, money
from core.api import route
from core.errors import require, require_keys, AppError, conflict, not_found, BIZ_STATE_INVALID

DRAFT = "DRAFT"
ORDERED = "ORDERED"
SAMPLED = "SAMPLED"
DELIVERED = "DELIVERED"
REPORTED = "REPORTED"
REVIEWED = "REVIEWED"
CANCELLED = "CANCELLED"

TRANSITIONS = {
    DRAFT:     [ORDERED, CANCELLED],
    ORDERED:   [SAMPLED, CANCELLED],
    SAMPLED:   [DELIVERED, CANCELLED],
    DELIVERED: [REPORTED, CANCELLED],
    REPORTED:  [REVIEWED],
    REVIEWED:  [],
    CANCELLED: [],
}


def transition(conn, order_id, to_status, reason=None, ctx=None, extra=None):
    row = db.query_one("SELECT * FROM lab_order WHERE id=? AND deleted_at IS NULL", (order_id,))
    if row is None:
        raise not_found("检验申请不存在")
    frm = row["status"]
    if to_status not in TRANSITIONS.get(frm, []):
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "lab_order", order_id,
                              "非法状态转换 %s→%s" % (frm, to_status))
        raise conflict("检验申请状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?", "version=version+1"]
    params = [to_status, tu.now_str(), (ctx.user_id if ctx else 0)]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(order_id)
    db.execute(conn, "UPDATE lab_order SET %s WHERE id=?" % ",".join(sets), tuple(params))
    db.execute(conn, """
        INSERT INTO lab_order_status_log (lab_order_id, from_status, to_status, reason,
                                          operator_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (order_id, frm, to_status, reason, (ctx.user_id if ctx else 0), tu.now_str()))
    audit.audit_data_change(conn, "lab_order", order_id, "status", frm, to_status, reason)
    return frm, to_status


def _load(order_id, editable_statuses=(DRAFT,)):
    row = db.query_one("SELECT * FROM lab_order WHERE id=? AND deleted_at IS NULL", (order_id,))
    if row is None:
        raise not_found("检验申请不存在")
    if editable_statuses and row["status"] not in editable_statuses:
        raise conflict("检验申请已处于 %s，不能再修改明细" % row["status"], code=BIZ_STATE_INVALID)
    return row


# ---------- 外送机构（F27 前置）----------

@route("/api/lab/agency/create", "POST", perm="lab.agency.manage", module="M05",
       action="lab.agency.create")
def agency_create(ctx, body, q):
    require_keys(body, ["agency_code", "name"])
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.agency.create", "lab_agency", None)
        require(db.query_one("SELECT id FROM lab_agency WHERE agency_code=?",
                             (body["agency_code"],)) is None, "机构编码已存在", code="E2004")
        cur = db.execute(conn, """
            INSERT INTO lab_agency (agency_code, name, contact, phone, address,
                settlement_cycle, is_active, remark, created_at, created_by, updated_at,
                updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body["agency_code"].strip(), body["name"].strip(), body.get("contact"),
              body.get("phone"), body.get("address"),
              body.get("settlement_cycle", "MONTHLY"), body.get("is_active", 1),
              body.get("remark"), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid}


@route("/api/lab/agency/list", "GET", perm="lab.view", module="M05", action="lab.agency.list")
def agency_list(ctx, body, q):
    return {"items": db.query("SELECT * FROM lab_agency WHERE deleted_at IS NULL "
                              "ORDER BY is_active DESC, id")}


# ---------- F27 外送项目目录 ----------

@route("/api/lab/item/create", "POST", perm="lab.agency.manage", module="M05",
       action="lab.item.create")
def item_create(ctx, body, q):
    require_keys(body, ["item_code", "name"])
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.item.create", "lab_item", None)
        require(db.query_one("SELECT id FROM lab_item WHERE item_code=?",
                             (body["item_code"],)) is None, "项目编码已存在", code="E2004")
        cur = db.execute(conn, """
            INSERT INTO lab_item (agency_id, item_code, name, short_name, category,
                specimen_type, container, price_fen, settle_price_fen, report_days, is_active,
                remark, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body.get("agency_id"), body["item_code"].strip(), body["name"].strip(),
              body.get("short_name"), body.get("category"), body.get("specimen_type"),
              body.get("container"),
              body.get("price_fen") or money.to_fen(body.get("price_yuan") or 0),
              body.get("settle_price_fen") or money.to_fen(body.get("settle_price_yuan") or 0),
              body.get("report_days"), body.get("is_active", 1), body.get("remark"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid}


@route("/api/lab/item/search", "GET", perm="lab.view", module="M05", action="lab.item.search")
def item_search(ctx, body, q):
    kw = (q.get("keyword") or "").strip()
    sql = "SELECT * FROM lab_item WHERE deleted_at IS NULL AND is_active=1"
    params = []
    if kw:
        like = "%%%s%%" % kw
        sql += " AND (name LIKE ? OR short_name LIKE ? OR item_code LIKE ?)"
        params += [like, like, like]
    sql += " ORDER BY category, name LIMIT 100"
    return {"items": db.query(sql, tuple(params))}


@route("/api/lab/item/list", "GET", perm="lab.view", module="M05", action="lab.item.list")
def item_list(ctx, body, q):
    """维护用清单：默认含停用项，避免"停用了就再也找不回来"。"""
    sql = "SELECT * FROM lab_item WHERE deleted_at IS NULL"
    if not (q.get("include_inactive") in ("1", "true", "yes")):
        sql += " AND is_active=1"
    sql += " ORDER BY is_active DESC, category, name LIMIT 500"
    return {"items": db.query(sql)}


@route("/api/lab/item/update", "POST", perm="lab.agency.manage", module="M05",
       action="lab.item.update")
def item_update(ctx, body, q):
    """改检验项目：收费价 / 结算价 / 报告周期 / 类别 / 标本 / 启停用。

    价格改动只影响此后新开的检验单，已开单按开单时快照计费。
    """
    iid = body.get("id")
    require(iid, "缺少 id")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM lab_item WHERE id=? AND deleted_at IS NULL", (iid,))
        if row is None:
            raise not_found("检验项目不存在")
        fields = {}
        if body.get("name"):
            fields["name"] = body["name"].strip()
        for k in ("short_name", "category", "specimen_type", "container", "remark"):
            if body.get(k) is not None:
                fields[k] = body[k]
        if body.get("price_yuan") is not None:
            fields["price_fen"] = money.to_fen(body["price_yuan"])
        if body.get("settle_price_yuan") is not None:
            fields["settle_price_fen"] = money.to_fen(body["settle_price_yuan"])
        if body.get("report_days") is not None:
            fields["report_days"] = int(body["report_days"] or 0) or None
        if body.get("is_active") is not None:
            fields["is_active"] = 1 if body["is_active"] else 0
        require(fields, "没有要修改的字段")
        sets = ", ".join("%s=?" % k for k in fields)
        db.execute(conn, "UPDATE lab_item SET %s, updated_at=?, updated_by=? WHERE id=?" % sets,
                   tuple(list(fields.values()) + [tu.now_str(), ctx.user_id, iid]))
        for k, v in fields.items():
            if row[k] != v:
                audit.audit_data_change(conn, "lab_item", iid, k, row[k], v, body.get("reason"))
        audit.audit_operation(conn, "M05", "lab.item.update", "lab_item", iid,
                              detail={"fields": list(fields.keys())})
    return {"id": iid, "changed": list(fields.keys())}


@route("/api/lab/agency/update", "POST", perm="lab.agency.manage", module="M05",
       action="lab.agency.update")
def agency_update(ctx, body, q):
    """改外送机构（名称 / 联系人 / 电话 / 启停用）。"""
    aid = body.get("id")
    require(aid, "缺少 id")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM lab_agency WHERE id=? AND deleted_at IS NULL", (aid,))
        if row is None:
            raise not_found("外送机构不存在")
        fields = {}
        if body.get("name"):
            fields["name"] = body["name"].strip()
        for k in ("contact", "phone", "address", "remark"):
            if body.get(k) is not None:
                fields[k] = body[k]
        if body.get("is_active") is not None:
            fields["is_active"] = 1 if body["is_active"] else 0
        require(fields, "没有要修改的字段")
        sets = ", ".join("%s=?" % k for k in fields)
        db.execute(conn, "UPDATE lab_agency SET %s, updated_at=?, updated_by=? WHERE id=?" % sets,
                   tuple(list(fields.values()) + [tu.now_str(), ctx.user_id, aid]))
        for k, v in fields.items():
            if row[k] != v:
                audit.audit_data_change(conn, "lab_agency", aid, k, row[k], v, body.get("reason"))
    return {"id": aid, "changed": list(fields.keys())}


# ---------- F28 检查申请 ----------

@route("/api/lab/order/create", "POST", perm="lab.order.create", module="M05",
       action="lab.order.create")
def order_create(ctx, body, q):
    """F28 开立检查申请（DRAFT）。"""
    require(body.get("visit_id"), "缺少 visit_id")
    v = db.query_one("SELECT * FROM visit WHERE id=? AND deleted_at IS NULL", (body["visit_id"],))
    if v is None:
        raise not_found("就诊不存在")
    p = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL", (v["patient_id"],))
    rec = db.query_one("SELECT id FROM record WHERE visit_id=? AND deleted_at IS NULL", (v["id"],))
    diag_text = None
    if rec:
        ds = db.query("SELECT icd10_name, tcm_name FROM diagnosis WHERE record_id=? "
                      "AND deleted_at IS NULL ORDER BY is_primary DESC, id", (rec["id"],))
        diag_text = "；".join([(d["icd10_name"] or d["tcm_name"] or "") for d in ds
                               if (d["icd10_name"] or d["tcm_name"])]) or None
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.order.create", "lab_order", None)
        order_no = ids.next_no(conn, "LAB_ORDER")
        now = tu.now_str()
        cur = db.execute(conn, """
            INSERT INTO lab_order (order_no, visit_id, patient_id, record_id, agency_id, status,
                version, patient_name_snap, gender_snap, birth_date_snap, age_text_snap,
                diag_summary, clinical_note, source, occurred_at, created_at, created_by,
                updated_at, updated_by)
            VALUES (?,?,?,?,?,'DRAFT',1,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (order_no, v["id"], v["patient_id"], (rec["id"] if rec else None),
              body.get("agency_id"), (p["name"] if p else None), (p["gender"] if p else None),
              (p["birth_date"] if p else None),
              (tu.age_text(p["birth_date"]) if p and p["birth_date"] else None),
              diag_text, body.get("clinical_note"), body.get("source", "DOCTOR"),
              now, now, ctx.user_id, now, ctx.user_id))
        oid = cur.lastrowid
        db.execute(conn, """
            INSERT INTO lab_order_status_log (lab_order_id, from_status, to_status, reason,
                                              operator_id, created_at)
            VALUES (?,NULL,'DRAFT',?,?,?)
        """, (oid, "开立检查申请", ctx.user_id, now))
    return {"id": oid, "order_no": order_no, "status": DRAFT}


@route("/api/lab/order/save/items", "POST", perm="lab.order.create", module="M05",
       action="lab.order.save.items")
def order_save_items(ctx, body, q):
    """F28 申请明细（replace 覆盖）。价格取开单时价。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    _load(oid)
    items = body.get("items") or []
    require(items, "申请明细不能为空")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.order.save.items", "lab_order", oid,
                              detail={"count": len(items)})
        db.execute(conn, "UPDATE lab_order_item SET deleted_at=?, updated_at=?, updated_by=? "
                         "WHERE lab_order_id=? AND deleted_at IS NULL",
                   (tu.now_str(), ctx.user_id, oid, oid))
        total = settle = 0
        agency_id = None
        for i, it in enumerate(items):
            li = db.query_one("SELECT * FROM lab_item WHERE id=? AND deleted_at IS NULL",
                              (it.get("lab_item_id"),))
            if li is None:
                raise not_found("检验项目不存在：%s" % it.get("lab_item_id"))
            price = it.get("price_fen") if it.get("price_fen") is not None else li["price_fen"]
            sp = it.get("settle_price_fen") if it.get("settle_price_fen") is not None \
                else li["settle_price_fen"]
            db.execute(conn, """
                INSERT INTO lab_order_item (lab_order_id, line_no, lab_item_id, item_name_snap,
                    specimen_type, price_fen, settle_price_fen, created_at, created_by,
                    updated_at, updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (oid, i + 1, li["id"], li["name"], li["specimen_type"], price or 0, sp or 0,
                  tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            total += price or 0
            settle += sp or 0
            if agency_id is None and li["agency_id"]:
                agency_id = li["agency_id"]
        db.execute(conn, "UPDATE lab_order SET total_amount=?, settle_amount=?, "
                         "agency_id=COALESCE(agency_id,?), clinical_note=COALESCE(?,clinical_note), "
                         "updated_at=?, updated_by=?, version=version+1 WHERE id=?",
                   (total, settle, agency_id, body.get("clinical_note"), tu.now_str(),
                    ctx.user_id, oid))
    return {"lab_order_id": oid, "saved": len(items), "total_amount": total,
            "settle_amount": settle}


@route("/api/lab/order/submit", "POST", perm="lab.order.create", module="M05",
       action="lab.order.submit")
def order_submit(ctx, body, q):
    """F28 提交：DRAFT→ORDERED，并自动为每个项目生成标本条码（F29）。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    row = _load(oid)
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.order.submit", "lab_order", oid)
        items = db.query("SELECT * FROM lab_order_item WHERE lab_order_id=? AND deleted_at IS NULL",
                         (oid,))
        require(items, "申请明细为空，不能提交")
        # 幂等：已有标本的不重复生成
        exist = db.query("SELECT lab_order_item_id id FROM lab_specimen WHERE lab_order_id=? "
                         "AND deleted_at IS NULL", (oid,))
        exist_ids = set(e["id"] for e in exist)
        barcodes = []
        for it in items:
            if it["id"] in exist_ids:
                continue
            bc = ids.next_no(conn, "SPECIMEN")
            db.execute(conn, """
                INSERT INTO lab_specimen (lab_order_id, lab_order_item_id, barcode, specimen_type,
                    status, created_at, created_by, updated_at, updated_by)
                VALUES (?,?,?,?,'PENDING',?,?,?,?)
            """, (oid, it["id"], bc, it["specimen_type"], tu.now_str(), ctx.user_id,
                  tu.now_str(), ctx.user_id))
            barcodes.append(bc)
        transition(conn, oid, ORDERED, body.get("reason") or "提交检查申请", ctx)
    return {"lab_order_id": oid, "status": ORDERED, "barcodes": barcodes}


# ---------- F29 标本采集与条码 ----------

@route("/api/lab/specimen/collect", "POST", perm="lab.order.create", module="M05",
       action="lab.specimen.collect")
def specimen_collect(ctx, body, q):
    """F29 采样：按条码或明细登记，全部采集完 → SAMPLED。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    row = _load(oid, editable_statuses=(ORDERED,))
    barcode = body.get("barcode")
    if barcode:
        sp = db.query_one("SELECT * FROM lab_specimen WHERE barcode=? AND deleted_at IS NULL",
                          (barcode,))
        if sp is None:
            raise not_found("条码不存在：%s" % barcode)
        with db.tx() as conn:
            audit.audit_operation(conn, "M05", "lab.specimen.collect", "lab_specimen", sp["id"],
                                  detail={"barcode": barcode})
            db.execute(conn, "UPDATE lab_specimen SET status='COLLECTED', collected_at=?, "
                             "collected_by=?, updated_at=?, updated_by=? WHERE id=?",
                       (body.get("collected_at") or tu.now_str(), ctx.user_id, tu.now_str(),
                        ctx.user_id, sp["id"]))
            n = 1
    else:
        with db.tx() as conn:
            audit.audit_operation(conn, "M05", "lab.specimen.collect", "lab_order", oid)
            cur = db.execute(conn, "UPDATE lab_specimen SET status='COLLECTED', collected_at=?, "
                                   "collected_by=?, updated_at=?, updated_by=? "
                                   "WHERE lab_order_id=? AND deleted_at IS NULL "
                                   "AND status='PENDING'",
                             (body.get("collected_at") or tu.now_str(), ctx.user_id,
                              tu.now_str(), ctx.user_id, oid))
            n = cur.rowcount
    pending = db.query_one("SELECT COUNT(*) c FROM lab_specimen WHERE lab_order_id=? "
                           "AND deleted_at IS NULL AND status='PENDING'", (oid,))
    if pending and pending["c"] == 0:
        with db.tx() as conn:
            transition(conn, oid, SAMPLED, "标本采集完成", ctx)
    return {"lab_order_id": oid, "collected": n,
            "status": db.query_one("SELECT status FROM lab_order WHERE id=?", (oid,))["status"]}


@route("/api/lab/specimen/list", "GET", perm="lab.view", module="M05",
       action="lab.specimen.list")
def specimen_list(ctx, body, q):
    oid = q.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    return {"items": db.query("SELECT * FROM lab_specimen WHERE lab_order_id=? "
                              "AND deleted_at IS NULL ORDER BY id", (oid,))}


# ---------- F117 外送送检与回报登记 ----------

@route("/api/lab/delivery/create", "POST", perm="lab.order.create", module="M05",
       action="lab.delivery.create")
def delivery_create(ctx, body, q):
    """F117 送检登记：SAMPLED → DELIVERED，按机构计算预计回报日期。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    row = _load(oid, editable_statuses=(SAMPLED,))
    agency_id = body.get("agency_id") or row["agency_id"]
    expected = None
    if agency_id:
        days = db.query_one("SELECT MAX(report_days) d FROM lab_item WHERE id IN "
                            "(SELECT lab_item_id FROM lab_order_item WHERE lab_order_id=? "
                            "AND deleted_at IS NULL)", (oid,))
        if days and days["d"]:
            import datetime
            expected = tu.fmt(tu.now() + datetime.timedelta(days=int(days["d"])))
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.delivery.create", "lab_delivery", None,
                              detail={"agency_id": agency_id})
        cur = db.execute(conn, """
            INSERT INTO lab_delivery (lab_order_id, agency_id, batch_no, delivered_at,
                delivered_by, courier, expected_report_at, status, remark, created_at,
                created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,'DELIVERED',?,?,?,?,?)
        """, (oid, agency_id, body.get("batch_no"), body.get("delivered_at") or tu.now_str(),
              ctx.user_id, body.get("courier"), expected, body.get("remark"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        did = cur.lastrowid
        transition(conn, oid, DELIVERED, "已送检", ctx)
    return {"id": did, "lab_order_id": oid, "status": DELIVERED, "expected_report_at": expected}


@route("/api/lab/delivery/return", "POST", perm="lab.order.create", module="M05",
       action="lab.delivery.return")
def delivery_return(ctx, body, q):
    """F117 回报登记：DELIVERED → REPORTED（报告已回到诊所，尚未阅片）。"""
    did = body.get("delivery_id") or body.get("id")
    require(did, "缺少 delivery_id")
    d = db.query_one("SELECT * FROM lab_delivery WHERE id=? AND deleted_at IS NULL", (did,))
    if d is None:
        raise not_found("送检记录不存在")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.delivery.return", "lab_delivery", did)
        db.execute(conn, "UPDATE lab_delivery SET returned_at=?, returned_by=?, receiver=?, "
                         "status='RETURNED', updated_at=?, updated_by=? WHERE id=?",
                   (body.get("returned_at") or tu.now_str(), ctx.user_id,
                    body.get("receiver") or body.get("receiver_name"), tu.now_str(),
                    ctx.user_id, did))
        transition(conn, d["lab_order_id"], REPORTED, "报告已回报", ctx)
    return {"id": did, "lab_order_id": d["lab_order_id"], "status": REPORTED}


# ---------- F30 结果回录 ----------

@route("/api/lab/result/save", "POST", perm="lab.result.entry", module="M05",
       action="lab.result.save")
def result_save(ctx, body, q):
    """F30 结果回录（手工）。全部明细录完 → REPORTED；医生阅片走 review。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    row = db.query_one("SELECT * FROM lab_order WHERE id=? AND deleted_at IS NULL", (oid,))
    if row is None:
        raise not_found("检验申请不存在")
    require(row["status"] in (DELIVERED, REPORTED, REVIEWED),
            "检验申请当前状态 %s 不能录入结果" % row["status"])
    items = body.get("items") or []
    require(items, "结果明细不能为空")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.result.save", "lab_order", oid,
                              detail={"count": len(items)})
        n = 0
        for it in items:
            name = it.get("item_name")
            require(name, "结果缺少项目名称")
            db.execute(conn, """
                INSERT INTO lab_result (lab_order_id, lab_order_item_id, lab_item_id, item_name,
                    result_text, unit, ref_range, abnormal_flag, result_summary, source,
                    entered_by, entered_at, reported_at, created_at, created_by, updated_at,
                    updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,'MANUAL',?,?,?,?,?,?,?)
            """, (oid, it.get("lab_order_item_id"), it.get("lab_item_id"), name,
                  it.get("result_text"), it.get("unit"), it.get("ref_range"),
                  it.get("abnormal_flag"), it.get("result_summary"), ctx.user_id,
                  tu.now_str(), it.get("reported_at"), tu.now_str(), ctx.user_id,
                  tu.now_str(), ctx.user_id))
            n += 1
        if row["status"] == DELIVERED:
            transition(conn, oid, REPORTED, "结果回录完成", ctx)
    return {"lab_order_id": oid, "saved": n}


@route("/api/lab/result/attach", "POST", perm="lab.result.entry", module="M05",
       action="lab.result.attach")
def result_attach(ctx, body, q):
    """F30 报告图片附件登记（只存路径与元信息，不存二进制）。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    require(body.get("file_path"), "缺少 file_path")
    path = body["file_path"]
    import os
    require(os.path.isfile(path), "报告文件不存在：%s" % path)
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.result.attach", "lab_result_attachment", None)
        cur = db.execute(conn, """
            INSERT INTO lab_result_attachment (lab_result_id, lab_order_id, file_path, file_name,
                file_size, mime_type, uploaded_by, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (body.get("lab_result_id"), oid, path,
              body.get("file_name") or os.path.basename(path),
              os.path.getsize(path), body.get("mime_type"), ctx.user_id, tu.now_str()))
    return {"id": cur.lastrowid}


@route("/api/lab/result/review", "POST", perm="lab.view", module="M05", action="lab.result.review")
def result_review(ctx, body, q):
    """F31 医生阅片：REPORTED → REVIEWED。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.result.review", "lab_order", oid)
        transition(conn, oid, REVIEWED, body.get("note") or "医生已阅", ctx,
                   extra={"reviewed_by": ctx.user_id, "reviewed_at": tu.now_str()})
    return {"lab_order_id": oid, "status": REVIEWED}


@route("/api/lab/order/cancel", "POST", perm="lab.order.create", module="M05",
       action="lab.order.cancel")
def order_cancel(ctx, body, q):
    """作废退费：仅 ORDERED / SAMPLED 可取消，必须填原因。"""
    oid = body.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    reason = (body.get("reason") or "").strip()
    require(reason, "作废必须填写原因")
    row = db.query_one("SELECT * FROM lab_order WHERE id=? AND deleted_at IS NULL", (oid,))
    if row is None:
        raise not_found("检验申请不存在")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.order.cancel", "lab_order", oid)
        audit.audit_high_risk(conn, audit.RISK_VOID, "lab_order", oid, reason)
        transition(conn, oid, CANCELLED, reason, ctx, extra={"cancelled_reason": reason})
    return {"lab_order_id": oid, "status": CANCELLED}


@route("/api/lab/order/get", "GET", perm="lab.view", module="M05", action="lab.order.get")
def order_get(ctx, body, q):
    oid = q.get("id") or q.get("lab_order_id")
    require(oid, "缺少 lab_order_id")
    row = db.query_one("SELECT * FROM lab_order WHERE id=? AND deleted_at IS NULL", (oid,))
    if row is None:
        raise not_found("检验申请不存在")
    out = dict(row)
    out["items"] = db.query("SELECT * FROM lab_order_item WHERE lab_order_id=? "
                            "AND deleted_at IS NULL ORDER BY line_no", (oid,))
    out["specimens"] = db.query("SELECT * FROM lab_specimen WHERE lab_order_id=? "
                                "AND deleted_at IS NULL ORDER BY id", (oid,))
    out["results"] = db.query("SELECT * FROM lab_result WHERE lab_order_id=? "
                              "AND deleted_at IS NULL ORDER BY id", (oid,))
    out["attachments"] = db.query("SELECT * FROM lab_result_attachment WHERE lab_order_id=? "
                                  "AND deleted_at IS NULL ORDER BY id", (oid,))
    out["delivery"] = db.query_one("SELECT * FROM lab_delivery WHERE lab_order_id=? "
                                   "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (oid,))
    out["status_log"] = db.query("SELECT * FROM lab_order_status_log WHERE lab_order_id=? "
                                 "ORDER BY id", (oid,))
    out["total_yuan"] = money.fen_to_yuan(row["total_amount"] or 0)
    return out


@route("/api/lab/order/list", "GET", perm="lab.view", module="M05", action="lab.order.list")
def order_list(ctx, body, q):
    vid = q.get("visit_id")
    pid = q.get("patient_id")
    if vid:
        rows = db.query("SELECT * FROM lab_order WHERE visit_id=? AND deleted_at IS NULL "
                        "ORDER BY id", (vid,))
    elif pid:
        rows = db.query("SELECT * FROM lab_order WHERE patient_id=? AND deleted_at IS NULL "
                        "ORDER BY id DESC LIMIT 100", (pid,))
    else:
        rows = db.query("SELECT * FROM lab_order WHERE deleted_at IS NULL "
                        "ORDER BY id DESC LIMIT 100")
    return {"items": rows}


# ---------- F118 机构月度对账 ----------

@route("/api/lab/reconcile", "POST", perm="lab.agency.manage", module="M05",
       action="lab.reconcile")
def reconcile(ctx, body, q):
    """F118 按机构 + 月份生成对账单（幂等：同机构同月份重算覆盖 DRAFT）。"""
    require(body.get("agency_id"), "缺少 agency_id")
    period = (body.get("period") or "").strip()
    require(len(period) == 7 and period[4] == "-", "period 格式应为 YYYY-MM")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.reconcile", "lab_reconciliation", None,
                              detail={"agency_id": body["agency_id"], "period": period})
        rows = db.query("""
            SELECT o.id, o.total_amount, o.settle_amount FROM lab_order o
            WHERE o.agency_id=? AND o.deleted_at IS NULL AND o.status<>'CANCELLED'
              AND strftime('%Y-%m', o.occurred_at)=?
        """, (body["agency_id"], period))
        cnt = len(rows)
        charged = sum(r["total_amount"] or 0 for r in rows)
        settle = sum(r["settle_amount"] or 0 for r in rows)
        old = db.query_one("SELECT id, status FROM lab_reconciliation WHERE agency_id=? "
                           "AND period=? AND deleted_at IS NULL",
                           (body["agency_id"], period))
        if old and old["status"] != "DRAFT":
            raise conflict("该机构 %s 的对账单已确认/结算，不能重算" % period, code=BIZ_STATE_INVALID)
        if old:
            db.execute(conn, "UPDATE lab_reconciliation SET order_count=?, charged_fen=?, "
                             "settle_fen=?, diff_fen=?, updated_at=?, updated_by=? WHERE id=?",
                       (cnt, charged, settle, charged - settle, tu.now_str(), ctx.user_id,
                        old["id"]))
            rid = old["id"]
        else:
            cur = db.execute(conn, """
                INSERT INTO lab_reconciliation (agency_id, period, order_count, charged_fen,
                    settle_fen, diff_fen, status, note, created_at, created_by, updated_at,
                    updated_by)
                VALUES (?,?,?,?,?,?,'DRAFT',?,?,?,?,?)
            """, (body["agency_id"], period, cnt, charged, settle, charged - settle,
                  body.get("note"), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            rid = cur.lastrowid
    return {"id": rid, "period": period, "order_count": cnt, "charged_fen": charged,
            "settle_fen": settle, "diff_fen": charged - settle,
            "diff_yuan": money.fen_to_yuan(charged - settle)}


@route("/api/lab/reconcile/settle", "POST", perm="lab.agency.manage", module="M05",
       action="lab.reconcile.settle")
def reconcile_settle(ctx, body, q):
    rid = body.get("id")
    require(rid, "缺少对账单 id")
    row = db.query_one("SELECT * FROM lab_reconciliation WHERE id=? AND deleted_at IS NULL", (rid,))
    if row is None:
        raise not_found("对账单不存在")
    require(row["status"] == "DRAFT", "只有 DRAFT 对账单可以结算")
    with db.tx() as conn:
        audit.audit_operation(conn, "M05", "lab.reconcile.settle", "lab_reconciliation", rid)
        db.execute(conn, "UPDATE lab_reconciliation SET status='SETTLED', settled_at=?, "
                         "settled_by=?, updated_at=?, updated_by=? WHERE id=?",
                   (tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id, rid))
    return {"id": rid, "status": "SETTLED"}


@route("/api/lab/reconcile/list", "GET", perm="lab.view", module="M05",
       action="lab.reconcile.list")
def reconcile_list(ctx, body, q):
    return {"items": db.query("SELECT * FROM lab_reconciliation WHERE deleted_at IS NULL "
                              "ORDER BY period DESC, id DESC LIMIT 100")}


# ---------- 对外服务接口 ----------

def result_brief(lab_result_id):
    """F16 跨模块只读：给 M03 引用检验结果用，只返回引用所需的最小字段。

    M03 不得直查 lab_* 表（04 §2 R2）。返回 None 表示结果不存在或已作废
    ——调用方据此抛 E3021。
    """
    row = db.query_one("SELECT * FROM lab_result WHERE id=?", (lab_result_id,))
    if row is None or row["deleted_at"]:
        return None
    order = db.query_one("SELECT id, order_no, status FROM lab_order WHERE id=?",
                         (row["lab_order_id"],))
    return {"id": row["id"], "lab_order_id": row["lab_order_id"],
            "order_no": (order["order_no"] if order else None),
            "order_status": (order["status"] if order else None),
            "item_name": row["item_name"], "result_text": row["result_text"],
            "unit": row["unit"], "ref_range": row["ref_range"],
            "abnormal_flag": row["abnormal_flag"],
            "result_summary": row["result_summary"],
            "reported_at": row["reported_at"], "updated_at": row["updated_at"]}


def order_brief(lab_order_id):
    """F16 跨模块只读：给 M03 引用"已开单待回报"的检查申请用。

    **不返回任何结果明细**——未回报就是没有明细，避免病历里出现假结果。
    """
    row = db.query_one("SELECT id, order_no, status, visit_id, patient_id "
                       "FROM lab_order WHERE id=? AND deleted_at IS NULL", (lab_order_id,))
    if row is None:
        return None
    return dict(row)


def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """M01 F04 患者合并改挂：只改 patient_id，快照字段不动。"""
    cur = db.execute(conn, "UPDATE lab_order SET patient_id=? WHERE patient_id=? "
                           "AND deleted_at IS NULL", (new_patient_id, old_patient_id))
    return cur.rowcount


def void_by_visit(conn, visit_id, reason, ctx=None):
    """M02 F125 就诊作废：取消尚未送检的申请（已送检的保留，需人工与机构沟通）。"""
    rows = db.query("SELECT id FROM lab_order WHERE visit_id=? AND deleted_at IS NULL "
                    "AND status IN ('DRAFT','ORDERED','SAMPLED')", (visit_id,))
    n = 0
    for r in rows:
        transition(conn, r["id"], CANCELLED, reason, ctx, extra={"cancelled_reason": reason})
        audit.audit_high_risk(conn, audit.RISK_VOID, "lab_order", r["id"],
                              "就诊作废联动取消检查申请：" + reason)
        n += 1
    return n
