# -*- coding: utf-8 -*-
"""M07 药房与库存（F40-F51 / F114 / F115）。

表所有权：supplier / stock_in / stock_batch / stock_batch_status_log / stock / herb_stock /
         stock_transaction（只允许 INSERT）/ stock_split / drug_return / drug_return_item /
         stock_loss / stock_loss_item / stock_check / stock_check_item /
         stock_check_status_log / stock_expiry_alert / dispense / herb_dispense
状态机：03 §2.8 批次（NORMAL→DEPLETED|EXPIRED|LOCKED）、§2.12 盘点单
金额：03 §3（整数分；成本单价用毫元）；饮片单位恒为克（03 §8）
边界：处方状态回写一律走 m06_prescription.mark_dispensed / mark_returned（R2/R5）
"""

from core import db, audit, config, timeutil as tu, ids
from core import money
from core.api import route
from core.errors import (require, require_keys, AppError, conflict, not_found,
                         BIZ_STATE_INVALID, BIZ_STOCK_INSUFFICIENT, DATA_FORMAT)

MODULE = "M07"

# ---------- 状态 ----------
NORMAL = "NORMAL"
DEPLETED = "DEPLETED"
EXPIRED = "EXPIRED"
LOCKED = "LOCKED"

BATCH_TRANSITIONS = {
    NORMAL:   [DEPLETED, EXPIRED, LOCKED],
    LOCKED:   [NORMAL],              # 盘点结束解锁
    DEPLETED: [],                    # 终态（03 §2.8）
    EXPIRED:  [],                    # 终态
}

CHECK_DRAFT = "DRAFT"
CHECK_COUNTING = "COUNTING"
CHECK_ADJUSTING = "ADJUSTING"
CHECK_APPROVED = "APPROVED"
CHECK_CANCELLED = "CANCELLED"

CHECK_TRANSITIONS = {
    CHECK_DRAFT:     [CHECK_COUNTING, CHECK_CANCELLED],
    CHECK_COUNTING:  [CHECK_ADJUSTING, CHECK_CANCELLED],
    CHECK_ADJUSTING: [CHECK_APPROVED, CHECK_CANCELLED],
    CHECK_APPROVED:  [],
    CHECK_CANCELLED: [],
}

DOC_DRAFT = "DRAFT"
DOC_CONFIRMED = "CONFIRMED"
DOC_VOID = "VOID"
DOC_TRANSITIONS = {
    DOC_DRAFT:     [DOC_CONFIRMED, DOC_VOID],
    DOC_CONFIRMED: [DOC_VOID],
    DOC_VOID:      [],
}

QTY_EPS = 1e-6


def _r(v):
    """数量取整到 3 位小数，避免浮点毛刺（金额仍一律整数分）。"""
    return round(float(v or 0) + 0.0, 3)


# ---------- 批次状态机（03 §2.8）----------

def batch_transition(conn, batch_id, to_status, reason=None, ctx=None):
    """批次状态唯一入口。非法转换拒绝 E3001。"""
    row = db.query_one("SELECT * FROM stock_batch WHERE id=? AND deleted_at IS NULL",
                       (batch_id,))
    if row is None:
        raise not_found("批次不存在")
    frm = row["status"]
    if frm == to_status:
        return row
    if to_status not in BATCH_TRANSITIONS.get(frm, []):
        raise conflict("批次状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    db.execute(conn, "UPDATE stock_batch SET status=?, updated_at=?, updated_by=? WHERE id=?",
               (to_status, tu.now_str(), (ctx or audit.get_ctx()).user_id, batch_id))
    audit.audit_data_change(conn, "stock_batch", batch_id, "status", frm, to_status, reason)
    db.execute(conn, """
        INSERT INTO stock_batch_status_log (batch_id, from_status, to_status, reason,
                                            operator_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (batch_id, frm, to_status, reason, (ctx or audit.get_ctx()).user_id, tu.now_str()))
    return row


def _check_transition(conn, check_id, to_status, reason=None, ctx=None):
    row = db.query_one("SELECT * FROM stock_check WHERE id=? AND deleted_at IS NULL",
                       (check_id,))
    if row is None:
        raise not_found("盘点单不存在")
    frm = row["status"]
    if to_status not in CHECK_TRANSITIONS.get(frm, []):
        raise conflict("盘点单状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code="E3054")
    db.execute(conn, "UPDATE stock_check SET status=?, updated_at=?, updated_by=? WHERE id=?",
               (to_status, tu.now_str(), (ctx or audit.get_ctx()).user_id, check_id))
    db.execute(conn, """
        INSERT INTO stock_check_status_log (check_id, from_status, to_status, reason,
                                            operator_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (check_id, frm, to_status, reason, (ctx or audit.get_ctx()).user_id, tu.now_str()))
    return row


def _doc_transition(conn, table, doc_id, to_status, reason=None, ctx=None):
    """入库单 / 退药单 / 报损单 的通用三态状态机。"""
    row = db.query_one("SELECT * FROM %s WHERE id=? AND deleted_at IS NULL" % table, (doc_id,))
    if row is None:
        raise not_found("单据不存在")
    frm = row["status"]
    if to_status not in DOC_TRANSITIONS.get(frm, []):
        raise conflict("单据状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    db.execute(conn, "UPDATE %s SET status=?, updated_at=?, updated_by=? WHERE id=?" % table,
               (to_status, tu.now_str(), (ctx or audit.get_ctx()).user_id, doc_id))
    audit.audit_data_change(conn, table, doc_id, "status", frm, to_status, reason)
    return row


# ---------- 库存与流水（R-M07-1/R-M07-2）----------

def _stock_table(item_kind):
    return "stock" if item_kind == "DRUG" else "herb_stock"


def _qty_col(item_kind):
    return "qty" if item_kind == "DRUG" else "qty_g"


def _item_col(item_kind):
    return "drug_id" if item_kind == "DRUG" else "herb_id"


def apply_change(conn, batch_id, qty_change, trans_type, ref_type, ref_id=None,
                 ref_line_id=None, is_toxic=0, remark=None, ctx=None):
    """唯一的数量变动入口：改库存 + 写流水，必须在 db.tx() 内调用。

    返回 (balance_after, amount_fen)。
    """
    ctx = ctx or audit.get_ctx()
    b = db.query_one("SELECT * FROM stock_batch WHERE id=? AND deleted_at IS NULL", (batch_id,))
    if b is None:
        raise not_found("批次不存在")
    if b["status"] in (EXPIRED, LOCKED) and trans_type in ("OUT", "SPLIT_OUT"):
        raise conflict("批次状态为 %s，禁止出库" % b["status"], code="E3051")
    qty_change = float(qty_change)
    if abs(qty_change) < QTY_EPS:
        return 0.0, 0

    kind = b["item_kind"]
    table, qcol, icol = _stock_table(kind), _qty_col(kind), _item_col(kind)
    item_id = b[icol]
    row = db.query_one("SELECT * FROM %s WHERE %s=? AND batch_id=?" % (table, icol),
                       (item_id, batch_id))
    if row is None:
        # herb_stock 没有 unit 列（饮片单位恒为克，契约 §8），按表结构拼列
        cols = [icol, "batch_id", qcol] + (["unit"] if kind == "DRUG" else []) + \
               ["created_at", "created_by", "updated_at", "updated_by"]
        vals = [item_id, batch_id, 0.0] + ([b["unit"]] if kind == "DRUG" else []) + \
               [tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id]
        db.execute(conn, "INSERT INTO %s (%s) VALUES (%s)"
                   % (table, ",".join(cols), ",".join("?" * len(vals))), tuple(vals))
        cur = 0.0
    else:
        cur = float(row[qcol] or 0)

    after = _r(cur + qty_change)
    if after < -QTY_EPS:
        raise conflict("库存不足：%s 当前 %.3f，需要 %.3f" % (b["batch_no"], cur, -qty_change),
                       code=BIZ_STOCK_INSUFFICIENT)
    after = max(after, 0.0)

    db.execute(conn, "UPDATE %s SET %s=?, updated_at=?, updated_by=? WHERE %s=? AND batch_id=?"
               % (table, qcol, icol),
               (after, tu.now_str(), ctx.user_id, item_id, batch_id))

    remain = _r(float(b["qty_remain"]) + qty_change)
    remain = max(remain, 0.0)
    db.execute(conn, "UPDATE stock_batch SET qty_remain=?, updated_at=? WHERE id=?",
               (remain, tu.now_str(), batch_id))

    amount_fen = money.round_half_up(
        money.Decimal(str(qty_change)) * money.Decimal(b["unit_cost_milli"])
        / money.Decimal(10))
    db.execute(conn, """
        INSERT INTO stock_transaction (trans_type, item_kind, drug_id, herb_id, batch_id,
            qty_change, balance_after, unit, unit_cost_milli, amount_fen, ref_type, ref_id,
            ref_line_id, is_toxic, occurred_at, operator_id, remark, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (trans_type, kind, b["drug_id"], b["herb_id"], batch_id, qty_change, after,
          b["unit"], b["unit_cost_milli"], amount_fen, ref_type, ref_id, ref_line_id,
          is_toxic, tu.now_str(), ctx.user_id, remark, tu.now_str()))

    if remain <= QTY_EPS and b["status"] == NORMAL and trans_type in (
            "OUT", "LOSS", "SPLIT_OUT", "CHECK_ADJUST"):
        batch_transition(conn, batch_id, DEPLETED, "批次耗尽", ctx)
    return after, amount_fen


def allocate(conn, item_kind, item_id, need_qty, exclude_status=(EXPIRED, LOCKED, DEPLETED)):
    """按近效期优先（F49 R4）分配批次，返回 [(batch_row, qty)]；不足则抛 E3050。"""
    need = float(need_qty)
    if need <= QTY_EPS:
        return []
    icol = _item_col(item_kind)
    rows = db.query("""
        SELECT * FROM stock_batch
        WHERE item_kind=? AND %s=? AND deleted_at IS NULL AND qty_remain > ?
          AND status NOT IN (%s)
        ORDER BY expiry_date ASC, id ASC
    """ % (icol, ",".join("?" * len(exclude_status))),
                    (item_kind, item_id, QTY_EPS) + tuple(exclude_status))
    out, left = [], need
    for r in rows:
        if left <= QTY_EPS:
            break
        take = min(float(r["qty_remain"]), left)
        out.append((r, _r(take)))
        left = _r(left - take)
    if left > QTY_EPS:
        raise conflict("库存不足：还需 %.3f" % left, code=BIZ_STOCK_INSUFFICIENT)
    return out


def available_qty(item_kind, item_id):
    """可用量（只计 NORMAL 且未过期批次，F42 R2）。"""
    icol = _item_col(item_kind)
    row = db.query_one("""
        SELECT COALESCE(SUM(qty_remain),0) AS q FROM stock_batch
        WHERE item_kind=? AND %s=? AND deleted_at IS NULL AND status='NORMAL'
    """ % icol, (item_kind, item_id))
    return float(row["q"]) if row else 0.0


# ============================================================
# F40 入库
# ============================================================

@route("/api/stock/supplier/create", "POST", perm="stock.manage",
       module=MODULE, action="supplier.create")
def supplier_create(ctx, body, q):
    require_keys(body, ["name"])
    code = (body.get("supplier_code") or "").strip() or \
        ("S" + tu.now().strftime("%Y%m%d%H%M%S"))
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "supplier.create", "supplier", None,
                              detail={"name": body["name"]})
        cur = db.execute(conn, """
            INSERT INTO supplier (supplier_code, name, contact_name, phone, address,
                                  license_no, note, created_at, created_by, updated_at,
                                  updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (code, body["name"].strip(), body.get("contact_name"), body.get("phone"),
              body.get("address"), body.get("license_no"), body.get("note"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "supplier_code": code}


@route("/api/stock/supplier/list", "GET", perm="stock.view",
       module=MODULE, action="supplier.list")
def supplier_list(ctx, body, q):
    return {"items": db.query("SELECT * FROM supplier WHERE deleted_at IS NULL "
                              "ORDER BY id DESC LIMIT 200")}


@route("/api/stock/in/create", "POST", perm="stock.in", module=MODULE, action="stock.in.create")
def stock_in_create(ctx, body, q):
    """F40/F41 建入库单：明细行即批次（qty_remain 初始 0，确认时才生效）。"""
    items = body.get("items") or []
    require(items, "入库明细不能为空")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "stock.in.create", "stock_in", None,
                              detail={"lines": len(items)})
        in_no = ids.next_no(conn, "STOCK_IN")
        cur = db.execute(conn, """
            INSERT INTO stock_in (stock_in_no, supplier_id, invoice_no, in_type, status,
                                  line_count, occurred_at, created_at, created_by,
                                  updated_at, updated_by)
            VALUES (?,?,?,?,'DRAFT',?,?,?,?,?,?)
        """, (in_no, body.get("supplier_id"), body.get("invoice_no"),
              body.get("in_type") or "PURCHASE", len(items), tu.now_str(),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        sid = cur.lastrowid
        total = 0
        for i, it in enumerate(items, start=1):
            kind = (it.get("item_kind") or "DRUG").upper()
            require(kind in ("DRUG", "HERB"), "item_kind 取值应为 DRUG/HERB")
            item_id = it.get("drug_id") if kind == "DRUG" else it.get("herb_id")
            require(item_id, "第 %d 行缺少药品/饮片 ID" % i)
            qty = float(it.get("qty") or 0)
            if kind == "HERB" and (it.get("input_unit") or "").upper() == "KG":
                qty = qty * 1000.0            # R6：kg → g
            require(qty > 0, "第 %d 行数量必须大于 0" % i)
            expiry = (it.get("expiry_date") or "").strip()
            require(expiry, "第 %d 行效期必填（F40 R4）" % i)
            require(tu.parse(expiry) is not None, "第 %d 行效期格式应为 YYYY-MM-DD" % i)
            require(tu.parse(expiry).date() >= tu.now().date(),
                    "第 %d 行批次已过期，不能入库（F40 R4）" % i)
            unit_cost_milli = money.to_milli(it.get("cost_yuan_per_unit")) \
                if it.get("cost_yuan_per_unit") is not None else int(it.get("unit_cost_milli") or 0)
            batch_no = ids.next_no(conn, "BATCH")
            db.execute(conn, """
                INSERT INTO stock_batch (batch_no, stock_in_id, line_no, item_kind, drug_id,
                    herb_id, supplier_batch_no, produce_date, expiry_date, unit, pack_qty,
                    unit_cost_milli, unit_price_milli, qty_in, qty_remain, status,
                    created_at, created_by, updated_at, updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'NORMAL',?,?,?,?)
            """, (batch_no, sid, i, kind, it.get("drug_id"), it.get("herb_id"),
                  it.get("supplier_batch_no"), it.get("produce_date"), expiry,
                  (it.get("unit") or ("g" if kind == "HERB" else "UNIT")),
                  int(it.get("pack_qty") or 1), unit_cost_milli,
                  money.to_milli(it.get("price_yuan_per_unit"))
                  if it.get("price_yuan_per_unit") is not None
                  else int(it.get("unit_price_milli") or 0),
                  qty, tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            total += money.round_half_up(
                money.Decimal(str(qty)) * money.Decimal(unit_cost_milli) / money.Decimal(10))
        db.execute(conn, "UPDATE stock_in SET total_amount_fen=? WHERE id=?", (total, sid))
    return {"id": sid, "stock_in_no": in_no, "lines": len(items),
            "total_amount_fen": total}


@route("/api/stock/in/confirm", "POST", perm="stock.in", module=MODULE, action="stock.in.confirm")
def stock_in_confirm(ctx, body, q):
    """F40 R7：确认即生效——批次剩余量 + 库存 + 流水，同一事务。"""
    sid = body.get("id") or body.get("stock_in_id")
    require(sid, "缺少 stock_in_id")
    with db.tx() as conn:
        head = db.query_one("SELECT * FROM stock_in WHERE id=? AND deleted_at IS NULL", (sid,))
        if head is None:
            raise not_found("入库单不存在")
        rows = db.query("SELECT * FROM stock_batch WHERE stock_in_id=? AND deleted_at IS NULL "
                        "ORDER BY line_no", (sid,))
        require(rows, "入库单没有明细")
        audit.audit_operation(conn, MODULE, "stock.in.confirm", "stock_in", sid)
        for b in rows:
            # 注意：apply_change 内部已按流水增减 qty_remain，此处不要重复加一次
            apply_change(conn, b["id"], float(b["qty_in"]), "IN", "STOCK_IN", sid,
                         b["line_no"], 0, "入库 " + head["stock_in_no"], ctx)
        _doc_transition(conn, "stock_in", sid, DOC_CONFIRMED, "入库确认", ctx)
        db.execute(conn, "UPDATE stock_in SET confirmed_by=?, confirmed_at=?, occurred_at=? "
                         "WHERE id=?", (ctx.user_id, tu.now_str(), tu.now_str(), sid))
    return {"id": sid, "status": DOC_CONFIRMED, "lines": len(rows)}


@route("/api/stock/in/void", "POST", perm="stock.in", module=MODULE, action="stock.in.void")
def stock_in_void(ctx, body, q):
    sid = body.get("id") or body.get("stock_in_id")
    reason = (body.get("reason") or "").strip()
    require(sid, "缺少 stock_in_id")
    require(len(reason) >= 2, "作废必须填写原因")
    with db.tx() as conn:
        head = db.query_one("SELECT * FROM stock_in WHERE id=? AND deleted_at IS NULL", (sid,))
        if head is None:
            raise not_found("入库单不存在")
        require(head["status"] != DOC_CONFIRMED, "已确认入库单不能作废（请走报损或盘点）")
        audit.audit_operation(conn, MODULE, "stock.in.void", "stock_in", sid, detail=reason)
        _doc_transition(conn, "stock_in", sid, DOC_VOID, reason, ctx)
        db.execute(conn, "UPDATE stock_in SET void_reason=? WHERE id=?", (reason, sid))
        audit.audit_high_risk(conn, audit.RISK_VOID, "stock_in", sid, reason)
    return {"id": sid, "status": DOC_VOID}


@route("/api/stock/in/list", "GET", perm="stock.view", module=MODULE, action="stock.in.list")
def stock_in_list(ctx, body, q):
    limit = min(int(q.get("limit") or 50), 200)
    return {"items": db.query("SELECT * FROM stock_in WHERE deleted_at IS NULL "
                              "ORDER BY id DESC LIMIT ?", (limit,))}


# ============================================================
# F41/F42/F43 批次 · 库存 · 流水
# ============================================================

@route("/api/stock/batches", "GET", perm="stock.view", module=MODULE, action="stock.batches")
def batch_list(ctx, body, q):
    item_kind = (q.get("item_kind") or "DRUG").upper()
    item_id = q.get("drug_id") or q.get("herb_id")
    icol = _item_col(item_kind)
    sql = "SELECT * FROM stock_batch WHERE item_kind=? AND deleted_at IS NULL"
    params = [item_kind]
    if item_id:
        sql += " AND %s=?" % icol
        params.append(item_id)
    if q.get("only_available"):
        sql += " AND qty_remain > ? AND status='NORMAL'"
        params.append(QTY_EPS)
    sql += " ORDER BY expiry_date ASC, id ASC LIMIT 200"
    return {"items": db.query(sql, tuple(params))}


@route("/api/stock/query", "GET", perm="stock.view", module=MODULE, action="stock.query")
def stock_query(ctx, body, q):
    """F42 库存查询：成药按 drug_id，饮片按 herb_id，返回可用量与批次分布。"""
    # 传 herb_id 时 item_kind 必须跟着变 HERB，否则会把饮片 id 当药品 id 去查（查到 0 或错药）
    if q.get("item_kind"):
        item_kind = str(q["item_kind"]).upper()
    elif q.get("herb_id") and not q.get("drug_id"):
        item_kind = "HERB"
    else:
        item_kind = "DRUG"
    item_id = q.get("drug_id") if item_kind == "DRUG" else q.get("herb_id")
    require(item_id, "缺少 drug_id / herb_id")
    if item_kind not in ("DRUG", "HERB"):
        raise AppError(DATA_FORMAT, "item_kind 取值应为 DRUG / HERB")
    # 库存列成药是 qty、饮片是 qty_g，不能写死 s.qty（否则查饮片必然报 no such column）
    batches = db.query("""
        SELECT b.id, b.batch_no, b.expiry_date, b.status, b.qty_remain, b.unit_cost_milli,
               b.unit, s.%s AS book_qty
        FROM stock_batch b
        LEFT JOIN %s s ON s.batch_id=b.id
        WHERE b.item_kind=? AND b.%s=? AND b.deleted_at IS NULL
        ORDER BY b.expiry_date ASC
    """ % (_qty_col(item_kind), _stock_table(item_kind), _item_col(item_kind)),
        (item_kind, item_id))
    return {"item_kind": item_kind, "item_id": item_id,
            "available": available_qty(item_kind, item_id),
            "unit": "g" if item_kind == "HERB" else None,
            "batches": batches}


@route("/api/stock/transactions", "GET", perm="stock.view",
       module=MODULE, action="stock.transactions")
def transactions(ctx, body, q):
    """F43 库存流水查询：按批次 / 单据 / 类型 / 时间维度。"""
    limit = min(int(q.get("limit") or 100), 500)
    sql = "SELECT * FROM stock_transaction WHERE 1=1"
    params = []
    for col in ("batch_id", "item_kind", "drug_id", "herb_id", "ref_type", "ref_id",
                "ref_line_id", "trans_type"):
        if q.get(col):
            sql += " AND %s=?" % col
            params.append(q[col])
    if q.get("date_from"):
        sql += " AND occurred_at >= ?"
        params.append(q["date_from"])
    if q.get("date_to"):
        sql += " AND occurred_at <= ?"
        params.append(q["date_to"] + " 23:59:59")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return {"items": db.query(sql, tuple(params))}


# ============================================================
# F44/F45 拆零与尾差
# ============================================================

@route("/api/stock/split", "POST", perm="stock.manage", module=MODULE, action="stock.split")
def stock_split(ctx, body, q):
    """F44/F45 拆零：整包装 → 零头批次，尾差显式记录。"""
    batch_id = body.get("batch_id")
    idem = body.get("idempotent_key")
    require(batch_id, "缺少 batch_id")
    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM stock_split WHERE idempotent_key=?", (idem,))
            if done:
                return {"id": done["id"], "duplicate": True,
                        "new_batch_id": done["new_batch_id"]}
        b = db.query_one("SELECT * FROM stock_batch WHERE id=? AND deleted_at IS NULL",
                         (batch_id,))
        if b is None:
            raise not_found("批次不存在")
        require(b["item_kind"] == "DRUG", "饮片按克管理，无需拆零")
        if b["status"] != NORMAL:
            raise conflict("批次状态为 %s，不允许拆零" % b["status"], code="E3051")
        pack_qty = int(b["pack_qty"] or 0)
        require(pack_qty > 0, "包装含量未维护或非法，不能拆零（F44 R3）", code="E3056")
        if float(b["qty_remain"]) < 1 - QTY_EPS:
            raise conflict("批次数量不足，不能拆零", code=BIZ_STOCK_INSUFFICIENT)
        audit.audit_operation(conn, MODULE, "stock.split", "stock_batch", batch_id,
                              detail={"pack_qty": pack_qty})

        pack_cost = int(b["unit_cost_milli"]) * pack_qty
        pack_price = int(b["unit_price_milli"]) * pack_qty
        unit_cost = pack_cost // pack_qty
        unit_price = pack_price // pack_qty
        tail_cost = pack_cost - unit_cost * pack_qty      # R2：尾差显式
        tail_price = pack_price - unit_price * pack_qty

        new_no = ids.next_no(conn, "BATCH")
        cur = db.execute(conn, """
            INSERT INTO stock_batch (batch_no, line_no, item_kind, drug_id, supplier_batch_no,
                produce_date, expiry_date, unit, pack_qty, unit_cost_milli, unit_price_milli,
                qty_in, qty_remain, is_split, parent_batch_id, status, created_at, created_by,
                updated_at, updated_by)
            VALUES (?,1,?,?,?,?,?,?,1,?,?,?,?,1,?,'NORMAL',?,?,?,?)
        """, (new_no, b["item_kind"], b["drug_id"], b["supplier_batch_no"],
              b["produce_date"], b["expiry_date"], b["unit"], unit_cost, unit_price,
              pack_qty, 0, b["id"], tu.now_str(), ctx.user_id,
              tu.now_str(), ctx.user_id))
        new_id = cur.lastrowid

        apply_change(conn, batch_id, -1.0, "SPLIT_OUT", "SPLIT", None, None, 0,
                     "拆零出库 → " + new_no, ctx)
        apply_change(conn, new_id, float(pack_qty), "SPLIT_IN", "SPLIT", None, None, 0,
                     "拆零入库 ← " + b["batch_no"], ctx)
        cur = db.execute(conn, """
            INSERT INTO stock_split (batch_id, new_batch_id, pack_qty, pack_price_milli,
                unit_price_milli, tail_diff_milli, pack_cost_milli, unit_cost_milli,
                tail_cost_diff_milli, idempotent_key, occurred_at, operator_id, created_at,
                created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (batch_id, new_id, pack_qty, pack_price, unit_price, tail_price,
              pack_cost, unit_cost, tail_cost, idem, tu.now_str(), ctx.user_id,
              tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "new_batch_id": new_id, "batch_no": new_no,
            "pack_qty": pack_qty, "unit_price_milli": unit_price,
            "unit_cost_milli": unit_cost, "tail_diff_milli": tail_price,
            "tail_cost_diff_milli": tail_cost}


# ============================================================
# F47 报损
# ============================================================

@route("/api/stock/loss/create", "POST", perm="stock.loss", module=MODULE,
       action="stock.loss.create")
def loss_create(ctx, body, q):
    """F47 报损：创建 + 确认一步到位（明细即批次行）。"""
    items = body.get("items") or []
    reason = (body.get("reason") or "").strip()
    require(items, "报损明细不能为空")
    require(len(reason) >= 2, "报损必须填写原因")
    loss_reason = body.get("loss_reason") or "OTHER"
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "stock.loss.create", "stock_loss", None,
                              detail={"lines": len(items), "reason": reason})
        loss_no = ids.next_no(conn, "STOCK_LOSS")
        cur = db.execute(conn, """
            INSERT INTO stock_loss (loss_no, status, loss_reason, line_count, reason,
                                    occurred_at, created_at, created_by, updated_at, updated_by)
            VALUES (?,'DRAFT',?,?,?,?,?,?,?,?)
        """, (loss_no, loss_reason, len(items), reason, tu.now_str(),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        lid = cur.lastrowid
        total = 0
        for i, it in enumerate(items, start=1):
            bid = it.get("batch_id")
            qty = float(it.get("qty") or 0)
            require(bid, "第 %d 行缺少 batch_id" % i)
            require(qty > 0, "第 %d 行数量必须大于 0" % i)
            b = db.query_one("SELECT * FROM stock_batch WHERE id=? AND deleted_at IS NULL",
                             (bid,))
            if b is None:
                raise not_found("第 %d 行批次不存在" % i)
            if b["status"] == LOCKED:
                raise conflict("第 %d 行批次被盘点冻结（F47 R7）" % i, code="E3051")
            if qty > float(b["qty_remain"]) + QTY_EPS:
                raise conflict("第 %d 行报损量 %.3f 超过批次可用量 %.3f（F47 R2）"
                               % (i, qty, float(b["qty_remain"])), code="E3057")
            # R6：报损不是销售出库，允许 EXPIRED 批次出库
            _, amount = apply_change(conn, bid, -qty, "LOSS", "STOCK_LOSS", lid, i, 0,
                                     "报损：" + reason, ctx)
            db.execute(conn, """
                INSERT INTO stock_loss_item (loss_id, line_no, item_kind, drug_id, herb_id,
                    batch_id, qty, unit, unit_cost_milli, amount_fen, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (lid, i, b["item_kind"], b["drug_id"], b["herb_id"], bid, qty, b["unit"],
                  b["unit_cost_milli"], abs(amount), tu.now_str(), ctx.user_id))
            total += abs(amount)
        db.execute(conn, "UPDATE stock_loss SET total_amount_fen=?, line_count=? WHERE id=?",
                   (total, len(items), lid))
        _doc_transition(conn, "stock_loss", lid, DOC_CONFIRMED, reason, ctx)
        db.execute(conn, "UPDATE stock_loss SET confirmed_by=?, confirmed_at=? WHERE id=?",
                   (ctx.user_id, tu.now_str(), lid))
        audit.audit_high_risk(conn, audit.RISK_DELETE, "stock_loss", lid,
                              "报损：" + reason, {"amount_fen": total})
    return {"id": lid, "loss_no": loss_no, "total_amount_fen": total}


# ============================================================
# F48 盘点
# ============================================================

@route("/api/stock/check/create", "POST", perm="stock.check", module=MODULE,
       action="stock.check.create")
def check_create(ctx, body, q):
    """F48.1 建单：冻结账面量快照（R4），进入 COUNTING 时锁定相关批次。"""
    scope = body.get("scope") or "ALL"
    scope_value = body.get("scope_value")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "stock.check.create", "stock_check", None,
                              detail={"scope": scope})
        ck_no = ids.next_no(conn, "STOCK_CHECK")
        cur = db.execute(conn, """
            INSERT INTO stock_check (check_no, scope, scope_value, status, created_at,
                                     created_by, updated_at, updated_by)
            VALUES (?,?,?,'DRAFT',?,?,?,?)
        """, (ck_no, scope, scope_value, tu.now_str(), ctx.user_id,
              tu.now_str(), ctx.user_id))
        cid = cur.lastrowid
        rows = _check_scope_rows(conn, scope, scope_value)
        for i, b in enumerate(rows, start=1):
            db.execute(conn, """
                INSERT INTO stock_check_item (check_id, line_no, item_kind, drug_id, herb_id,
                    batch_id, book_qty, unit, unit_cost_milli, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (cid, i, b["item_kind"], b["drug_id"], b["herb_id"], b["id"],
                  float(b["qty_remain"]), b["unit"], b["unit_cost_milli"],
                  tu.now_str(), ctx.user_id))
        db.execute(conn, "UPDATE stock_check SET item_count=? WHERE id=?", (len(rows), cid))
        _check_transition(conn, cid, CHECK_COUNTING, "开始盘点", ctx)
        db.execute(conn, "UPDATE stock_check SET started_at=?, started_by=? WHERE id=?",
                   (tu.now_str(), ctx.user_id, cid))
        for b in rows:                              # R3/R7：冻结
            if b["status"] == NORMAL:
                batch_transition(conn, b["id"], LOCKED, "盘点冻结 %s" % ck_no, ctx)
    return {"id": cid, "check_no": ck_no, "item_count": len(rows), "status": CHECK_COUNTING}


def _check_scope_rows(conn, scope, scope_value):
    sql = "SELECT * FROM stock_batch WHERE deleted_at IS NULL AND qty_remain > ?"
    params = [QTY_EPS]
    if scope == "DRUG":
        sql += " AND item_kind='DRUG'"
    elif scope == "HERB":
        sql += " AND item_kind='HERB'"
    elif scope == "BATCH" and scope_value:
        sql += " AND id=?"
        params.append(scope_value)
    sql += " ORDER BY item_kind, drug_id, herb_id, expiry_date"
    return db.query(sql, tuple(params))


@route("/api/stock/check/input", "POST", perm="stock.check", module=MODULE,
       action="stock.check.input")
def check_input(ctx, body, q):
    """F48.2 录入实盘：差异 = 实盘 − 账面。"""
    cid = body.get("id") or body.get("check_id")
    items = body.get("items") or []
    require(cid, "缺少 check_id")
    with db.tx() as conn:
        head = db.query_one("SELECT * FROM stock_check WHERE id=? AND deleted_at IS NULL",
                            (cid,))
        if head is None:
            raise not_found("盘点单不存在")
        require(head["status"] in (CHECK_COUNTING, CHECK_ADJUSTING),
                "盘点单状态为 %s，不能录入" % head["status"], code="E3054")
        audit.audit_operation(conn, MODULE, "stock.check.input", "stock_check", cid,
                              detail={"lines": len(items)})
        diff_count = 0
        for it in items:
            bid = it.get("batch_id")
            counted = it.get("counted_qty")
            require(bid and counted is not None, "明细行缺少 batch_id / counted_qty")
            row = db.query_one("SELECT * FROM stock_check_item WHERE check_id=? AND batch_id=?",
                               (cid, bid))
            if row is None:
                raise not_found("批次 %s 不在本次盘点范围内" % bid)
            counted = float(counted)
            diff = _r(counted - float(row["book_qty"]))
            amount = money.round_half_up(
                money.Decimal(str(diff)) * money.Decimal(row["unit_cost_milli"])
                / money.Decimal(10))
            db.execute(conn, "UPDATE stock_check_item SET counted_qty=?, diff_qty=?, "
                             "diff_amount_fen=?, is_adjusted=?, reason=? WHERE id=?",
                       (counted, diff, amount, int(it.get("is_adjusted", 1)),
                        it.get("reason"), row["id"]))
            if abs(diff) > QTY_EPS:
                diff_count += 1
        db.execute(conn, "UPDATE stock_check SET diff_count=? WHERE id=?", (diff_count, cid))
        if head["status"] == CHECK_COUNTING:
            _check_transition(conn, cid, CHECK_ADJUSTING, "实盘录入完成", ctx)
    return {"id": cid, "diff_count": diff_count}


@route("/api/stock/check/approve", "POST", perm="stock.check", module=MODULE,
       action="stock.check.approve")
def check_approve(ctx, body, q):
    """F48.3/F48.4 审批：只对标记调整的行生成调整流水，随后解锁。"""
    cid = body.get("id") or body.get("check_id")
    require(cid, "缺少 check_id")
    with db.tx() as conn:
        head = db.query_one("SELECT * FROM stock_check WHERE id=? AND deleted_at IS NULL",
                            (cid,))
        if head is None:
            raise not_found("盘点单不存在")
        require(head["status"] == CHECK_ADJUSTING,
                "盘点单状态为 %s，不能审批" % head["status"], code="E3054")
        audit.audit_operation(conn, MODULE, "stock.check.approve", "stock_check", cid)
        rows = db.query("SELECT * FROM stock_check_item WHERE check_id=? AND is_adjusted=1 "
                        "AND COALESCE(diff_qty,0) <> 0", (cid,))
        total = 0
        for r in rows:
            # 解锁后才能写调整流水（LOCKED 批次禁止出库，但盘点调整是本模块的校正动作）
            b = db.query_one("SELECT * FROM stock_batch WHERE id=?", (r["batch_id"],))
            if b and b["status"] == LOCKED:
                batch_transition(conn, b["id"], NORMAL, "盘点调整前解锁", ctx)
            _, amount = apply_change(conn, r["batch_id"], float(r["diff_qty"]),
                                     "CHECK_ADJUST", "STOCK_CHECK", cid, r["line_no"], 0,
                                     "盘点调整", ctx)
            total += amount
        for r in db.query("SELECT * FROM stock_check_item WHERE check_id=?", (cid,)):
            b = db.query_one("SELECT * FROM stock_batch WHERE id=?", (r["batch_id"],))
            if b and b["status"] == LOCKED:
                batch_transition(conn, b["id"], NORMAL, "盘点结束解锁", ctx)
        db.execute(conn, "UPDATE stock_check SET total_diff_amount_fen=?, approved_at=?, "
                         "approved_by=? WHERE id=?", (total, tu.now_str(), ctx.user_id, cid))
        _check_transition(conn, cid, CHECK_APPROVED, "盘点审批", ctx)
        audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "stock_check", cid, "盘点差异调整",
                              {"rows": len(rows), "diff_amount_fen": total})
    return {"id": cid, "adjusted_rows": len(rows), "diff_amount_fen": total}


@route("/api/stock/check/cancel", "POST", perm="stock.check", module=MODULE,
       action="stock.check.cancel")
def check_cancel(ctx, body, q):
    cid = body.get("id") or body.get("check_id")
    reason = (body.get("reason") or "").strip()
    require(cid, "缺少 check_id")
    with db.tx() as conn:
        head = db.query_one("SELECT * FROM stock_check WHERE id=? AND deleted_at IS NULL",
                            (cid,))
        if head is None:
            raise not_found("盘点单不存在")
        require(head["status"] in (CHECK_DRAFT, CHECK_COUNTING, CHECK_ADJUSTING),
                "盘点单已 %s，不能取消" % head["status"], code="E3054")
        audit.audit_operation(conn, MODULE, "stock.check.cancel", "stock_check", cid,
                              detail=reason)
        for r in db.query("SELECT * FROM stock_check_item WHERE check_id=?", (cid,)):
            b = db.query_one("SELECT * FROM stock_batch WHERE id=?", (r["batch_id"],))
            if b and b["status"] == LOCKED:
                batch_transition(conn, b["id"], NORMAL, "盘点取消解锁", ctx)
        _check_transition(conn, cid, CHECK_CANCELLED, reason or "取消盘点", ctx)
    return {"id": cid, "status": CHECK_CANCELLED}


@route("/api/stock/check/get", "GET", perm="stock.view", module=MODULE, action="stock.check.get")
def check_get(ctx, body, q):
    cid = q.get("id") or q.get("check_id")
    require(cid, "缺少 check_id")
    head = db.query_one("SELECT * FROM stock_check WHERE id=? AND deleted_at IS NULL", (cid,))
    if head is None:
        raise not_found("盘点单不存在")
    items = db.query("SELECT * FROM stock_check_item WHERE check_id=? ORDER BY line_no", (cid,))
    return dict(head, items=items)


# ============================================================
# F49/F50 效期与库存预警
# ============================================================

@route("/api/stock/alert/scan", "POST", perm="stock.view", module=MODULE, action="stock.alert.scan")
def alert_scan(ctx, body, q):
    """F49/F50 扫描：到期转 EXPIRED、近效期提醒、库存下限提醒；幂等（R6）。"""
    warn_days = config.get_int("stock.expiry_warn_days", 90)
    today = tu.now().date()
    stats = {"expired": 0, "near_expiry": 0, "low_stock": 0, "resolved": 0}
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "stock.alert.scan", "stock_expiry_alert", None)
        # 到期 → EXPIRED
        for b in db.query("SELECT * FROM stock_batch WHERE deleted_at IS NULL "
                          "AND status IN ('NORMAL','LOCKED') AND expiry_date < ?",
                          (today.isoformat(),)):
            if b["status"] == NORMAL:
                batch_transition(conn, b["id"], EXPIRED, "效期到期", ctx)
            _open_alert(conn, "EXPIRED", b, None, None, ctx)
            stats["expired"] += 1
        # 近效期
        for b in db.query("SELECT * FROM stock_batch WHERE deleted_at IS NULL "
                          "AND status='NORMAL' AND qty_remain > ? AND expiry_date >= ?",
                          (QTY_EPS, today.isoformat())):
            d = tu.parse(b["expiry_date"])
            if not d:
                continue
            days = (d.date() - today).days
            if days <= warn_days:
                _open_alert(conn, "NEAR_EXPIRY", b, days, None, ctx)
                stats["near_expiry"] += 1
            else:                                   # R5：回升则关闭
                stats["resolved"] += _close_expiry_alert(conn, b["id"], ctx)
        # 库存下限（按品目汇总，F50 R3）
        for row in db.query("""
            SELECT item_kind, drug_id, herb_id, SUM(qty_remain) AS q
            FROM stock_batch WHERE deleted_at IS NULL AND status='NORMAL'
            GROUP BY item_kind, drug_id, herb_id
        """):
            thr = _safety_threshold(conn, row)
            if thr is None or thr <= 0:
                continue
            q = float(row["q"] or 0)
            atype = "OUT_OF_STOCK" if q <= QTY_EPS else ("LOW_STOCK" if q < thr else None)
            if atype:
                _open_alert(conn, atype, None, None, row, ctx, available=q, threshold=thr)
                stats[atype.lower()] = stats.get(atype.lower(), 0) + 1
            else:
                stats["resolved"] += _close_stock_alert(conn, row, ctx)
    return stats


def _open_alert(conn, alert_type, batch, days_left, item_row, ctx, available=None,
                threshold=None):
    """写入一条 OPEN 预警；已存在同对象同类型 OPEN 预警则跳过（幂等，R6）。"""
    if batch is not None:
        kind, did, hid, bid, expiry = (batch["item_kind"], batch["drug_id"],
                                       batch["herb_id"], batch["id"], batch["expiry_date"])
        available = float(batch["qty_remain"]) if available is None else available
    else:
        kind, did, hid, bid, expiry = (item_row["item_kind"], item_row["drug_id"],
                                       item_row["herb_id"], None, None)
    exist = db.query_one("SELECT id FROM stock_expiry_alert WHERE alert_type=? AND item_kind=? "
                         "AND drug_id IS ? AND herb_id IS ? AND batch_id IS ? AND status='OPEN'",
                         (alert_type, kind, did, hid, bid))
    if exist:
        return None
    cur = db.execute(conn, """
        INSERT INTO stock_expiry_alert (alert_type, item_kind, drug_id, herb_id, batch_id,
            expiry_date, days_left, available_qty, threshold_days, threshold_qty, status,
            created_at, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,'OPEN',?,?)
    """, (alert_type, kind, did, hid, bid, expiry, days_left, available,
          None, threshold, tu.now_str(), ctx.user_id))
    return cur.lastrowid


def _close_expiry_alert(conn, batch_id, ctx):
    cur = db.execute(conn, "UPDATE stock_expiry_alert SET status='RESOLVED', resolved_at=? "
                           "WHERE batch_id=? AND status='OPEN' AND alert_type IN "
                           "('NEAR_EXPIRY','EXPIRED')", (tu.now_str(), batch_id))
    return cur.rowcount


def _close_stock_alert(conn, row, ctx):
    cur = db.execute(conn, """
        UPDATE stock_expiry_alert SET status='RESOLVED', resolved_at=?
        WHERE status='OPEN' AND alert_type IN ('LOW_STOCK','OUT_OF_STOCK')
          AND item_kind=? AND drug_id IS ? AND herb_id IS ?
    """, (tu.now_str(), row["item_kind"], row["drug_id"], row["herb_id"]))
    return cur.rowcount


def _safety_threshold(conn, row):
    """F50 R1：品目阈值 > 全局默认 > 0（不预警）。"""
    try:
        if row["item_kind"] == "DRUG" and row["drug_id"]:
            r = db.query_one("SELECT safety_stock FROM drug WHERE id=?", (row["drug_id"],))
            if r and r["safety_stock"]:
                return float(r["safety_stock"])
        if row["item_kind"] == "HERB" and row["herb_id"]:
            r = db.query_one("SELECT safety_stock_g FROM herb WHERE id=?", (row["herb_id"],))
            if r and r["safety_stock_g"]:
                return float(r["safety_stock_g"])
    except Exception:
        # 资料表尚未扩展安全库存字段时降级为全局阈值（R6：不静默吞，写日志）
        from core.logging_setup import get
        get("m07").warning("读取品目安全库存失败，降级为全局阈值")
    return float(config.get_int("stock.safety_stock_default", 0))


@route("/api/stock/alerts", "GET", perm="stock.view", module=MODULE, action="stock.alerts")
def alert_list(ctx, body, q):
    status = q.get("status") or "OPEN"
    return {"items": db.query("SELECT * FROM stock_expiry_alert WHERE status=? "
                              "ORDER BY id DESC LIMIT 200", (status,))}


@route("/api/stock/alert/resolve", "POST", perm="stock.manage", module=MODULE,
       action="stock.alert.resolve")
def alert_resolve(ctx, body, q):
    aid = body.get("id")
    action = (body.get("action") or "RESOLVED").upper()
    reason = (body.get("reason") or "").strip()
    require(aid, "缺少预警 id")
    require(action in ("RESOLVED", "IGNORED"), "action 取值应为 RESOLVED/IGNORED")
    if action == "IGNORED":
        require(len(reason) >= 2, "忽略预警必须填写原因（F49 R5）")
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "stock.alert.resolve", "stock_expiry_alert", aid,
                              detail={"action": action, "reason": reason})
        cur = db.execute(conn, "UPDATE stock_expiry_alert SET status=?, reason=?, "
                               "resolved_at=? WHERE id=? AND status='OPEN'",
                         (action, reason, tu.now_str(), aid))
    return {"id": aid, "status": action, "changed": cur.rowcount}


# ============================================================
# F51 成药发药 / F115 中药发药
# ============================================================

@route("/api/pharmacy/dispense", "POST", perm="dispense.do", module=MODULE,
       action="dispense.create")
def dispense(ctx, body, q):
    """F51 成药发药：先规划（校验库存）再统一扣减，处方状态经 M06 接口回写。"""
    from . import m06_prescription as m06
    rx_id = body.get("prescription_id")
    idem = body.get("idempotent_key")
    require(rx_id, "缺少 prescription_id")
    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM dispense WHERE idempotent_key=?", (idem,))
            if done:
                return dict(done, duplicate=True)
        rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL",
                          (rx_id,))
        if rx is None:
            raise not_found("处方不存在")
        if rx["status"] != m06.CONFIRMED:
            raise conflict("处方状态为 %s，不可发药（仅 CONFIRMED 可发）" % rx["status"],
                           code=BIZ_STATE_INVALID)
        items = db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                         "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
        require(items, "处方没有明细，无法发药")

        # 阶段一：只读规划（任一行不足 → 整单拒绝，R4）
        plan = []
        for it in items:
            need = float(it["quantity"])
            allocs = allocate(conn, "DRUG", it["drug_id"], need,
                              exclude_status=(EXPIRED, LOCKED, DEPLETED))
            plan.append((it, allocs))

        audit.audit_operation(conn, MODULE, "dispense.create", "prescription", rx_id)
        cur = db.execute(conn, """
            INSERT INTO dispense (prescription_id, visit_id, patient_id, line_count,
                                  total_amount_fen, occurred_at, operator_id, idempotent_key,
                                  remark, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (rx_id, rx["visit_id"], rx["patient_id"], len(items), rx["total_amount"],
              tu.now_str(), ctx.user_id, idem, body.get("remark"),
              tu.now_str(), ctx.user_id))
        did = cur.lastrowid

        # 阶段二：实际扣减
        for it, allocs in plan:
            for b, take in allocs:
                apply_change(conn, b["id"], -take, "OUT", "DISPENSE", did, it["id"], 0,
                             "发药 " + rx["prescription_no"], ctx)
        m06.mark_dispensed(conn, rx_id, ctx)
    return {"id": did, "prescription_id": rx_id, "lines": len(items),
            "status": m06.DISPENSED}


@route("/api/pharmacy/herb_dispense", "POST", perm="dispense.do", module=MODULE,
       action="herb_dispense.create")
def herb_dispense(ctx, body, q):
    """F115 中药发药：饮片按 剂量×付数×(1+损耗‰) 出库（F114 R2）；颗粒走成药链路。"""
    from . import m06_prescription as m06
    rx_id = body.get("prescription_id")
    idem = body.get("idempotent_key")
    require(rx_id, "缺少 prescription_id")
    decoction_type = (body.get("decoction_type")
                      or ("DAIJIAN" if int(body.get("is_decoction") or 0) else "ZIJIAN")).upper()
    require(decoction_type in ("ZIJIAN", "DAIJIAN", "KELI"),
            "decoction_type 取值应为 ZIJIAN/DAIJIAN/KELI")
    loss = config.get_int("herb.loss_rate", 0)
    require(0 <= loss <= 500, "损耗率配置非法（应为 0-500 千分比）", code="E3059")

    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM herb_dispense WHERE idempotent_key=?", (idem,))
            if done:
                return dict(done, duplicate=True)
        rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL",
                          (rx_id,))
        if rx is None:
            raise not_found("处方不存在")
        if rx["status"] != m06.CONFIRMED:
            raise conflict("处方状态为 %s，不可发药" % rx["status"], code=BIZ_STATE_INVALID)
        doses = int(rx["doses"] or body.get("doses") or 0)
        require(doses > 0, "付数必须大于 0（契约 §8 必填）")
        if rx["prescription_type"] != "GRANULE":
            require(rx["decoction_method"] or body.get("decoction_method"),
                    "煎法必填（契约 §8）")
            require(rx["administration"] or body.get("administration"), "服法必填（契约 §8）")

        is_granule = (rx["prescription_type"] == "GRANULE" or decoction_type == "KELI")
        if is_granule:
            items = db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                             "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
            kind = "DRUG"
        else:
            items = db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                             "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
            kind = "HERB"
        require(items, "处方没有明细，无法发药")
        if not is_granule:
            for it in items:                        # R3：每味脚注齐备
                require(it["foot_note"], "饮片「%s」缺少脚注（契约 §8）" % it["herb_name_snap"])

        plan = []
        for it in items:
            if is_granule:
                need = float(it["quantity"])
                iid = it["drug_id"]
            else:
                need = _r(float(it["dose_g"]) * doses * (1 + loss / 1000.0))
                iid = it["herb_id"]
            allocs = allocate(conn, kind, iid, need,
                              exclude_status=(EXPIRED, LOCKED, DEPLETED))
            plan.append((it, allocs, need))

        # 代煎费与包装费取 F112 计价结果（M09/M15 口径一致，本模块不重算）
        pricing = db.query_one("SELECT * FROM prescription_pricing WHERE prescription_id=?",
                               (rx_id,)) or {}
        decoct_fee = int(pricing.get("decoction_fee_fen") or 0)
        package_fee = int(pricing.get("package_fee_fen") or 0)

        audit.audit_operation(conn, MODULE, "herb_dispense.create", "prescription", rx_id,
                              detail={"doses": doses, "decoction_type": decoction_type})
        cur = db.execute(conn, """
            INSERT INTO herb_dispense (prescription_id, visit_id, patient_id, doses,
                decoction_type, packs, herb_count, decoction_fee_fen, package_fee_fen,
                loss_rate_permille, ready_at, occurred_at, operator_id, idempotent_key,
                remark, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rx_id, rx["visit_id"], rx["patient_id"], doses, decoction_type,
              doses if decoction_type == "DAIJIAN" else None,
              len(items) if not is_granule else 0,
              decoct_fee, package_fee, loss, body.get("ready_at"), tu.now_str(),
              ctx.user_id, idem, body.get("remark"), tu.now_str(), ctx.user_id))
        hid = cur.lastrowid

        for it, allocs, _need in plan:
            toxic = int(it.get("is_toxic") or 0) if not is_granule else 0
            for b, take in allocs:
                apply_change(conn, b["id"], -take, "OUT", "HERB_DISPENSE", hid, it["id"],
                             toxic, "中药发药 " + rx["prescription_no"], ctx)
        m06.mark_dispensed(conn, rx_id, ctx)
    return {"id": hid, "prescription_id": rx_id, "doses": doses,
            "decoction_type": decoction_type, "herb_count": 0 if is_granule else len(items),
            "loss_rate_permille": loss, "status": m06.DISPENSED}


# ============================================================
# F46 退药
# ============================================================

def _issued_qty(conn, item_kind, line_id, ref_types):
    row = db.query_one("""
        SELECT COALESCE(SUM(qty_change),0) AS q FROM stock_transaction
        WHERE item_kind=? AND ref_line_id=? AND ref_type IN (%s)
    """ % ",".join("?" * len(ref_types)), (item_kind, line_id) + tuple(ref_types))
    return float(row["q"]) if row else 0.0


def _returnable_qty(conn, item_kind, line_id, out_ref, in_ref):
    issued = -_issued_qty(conn, item_kind, line_id, out_ref)
    returned = _issued_qty(conn, item_kind, line_id, in_ref)
    return _r(issued - returned)


@route("/api/pharmacy/return/create", "POST", perm="stock.return", module=MODULE,
       action="drug_return.create")
def return_create(ctx, body, q):
    """F46 退药建单 + 确认：明细必须关联来源处方明细（R1），退量 ≤ 可退量（R2）。"""
    from . import m06_prescription as m06
    rx_id = body.get("prescription_id")
    reason = (body.get("reason") or "").strip()
    items = body.get("items") or []
    require(rx_id, "缺少 prescription_id")
    require(items, "退药明细不能为空")
    require(len(reason) >= 2, "退药必须填写原因（F46 R8）")

    with db.tx() as conn:
        rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL",
                          (rx_id,))
        if rx is None:
            raise not_found("处方不存在")
        require(rx["status"] in (m06.DISPENSED, m06.PARTIAL_RETURNED),
                "处方状态为 %s，不可退药" % rx["status"], code=BIZ_STATE_INVALID)
        is_herb = rx["prescription_type"] == "HERB"

        audit.audit_operation(conn, MODULE, "drug_return.create", "prescription", rx_id,
                              detail={"reason": reason, "lines": len(items)})
        rt_no = ids.next_no(conn, "DRUG_RETURN")
        cur = db.execute(conn, """
            INSERT INTO drug_return (return_no, prescription_id, visit_id, patient_id, status,
                                     line_count, reason, occurred_at, created_at, created_by,
                                     updated_at, updated_by)
            VALUES (?,?,?,?,'DRAFT',?,?,?,?,?,?,?)
        """, (rt_no, rx_id, rx["visit_id"], rx["patient_id"], len(items), reason,
              tu.now_str(), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        rid = cur.lastrowid

        total = 0
        for i, it in enumerate(items, start=1):
            kind = "HERB" if is_herb else "DRUG"
            line_id = it.get("herb_prescription_item_id") if is_herb \
                else it.get("prescription_item_id")
            require(line_id, "第 %d 行缺少来源处方明细（R1：无源不得退药）" % i)
            qty = float(it.get("qty") or 0)
            require(qty > 0, "第 %d 行退回数量必须大于 0" % i)
            can = _returnable_qty(conn, kind, line_id, ("DISPENSE", "HERB_DISPENSE"),
                                  ("DRUG_RETURN",))
            require(qty <= can + QTY_EPS,
                    "第 %d 行退药量 %.3f 超过可退量 %.3f" % (i, qty, can), code="E3053")

            line = db.query_one("SELECT * FROM %s WHERE id=?" %
                                ("herb_prescription_item" if is_herb else "prescription_item"),
                                (line_id,))
            if line is None:
                raise not_found("第 %d 行处方明细不存在" % i)

            bid = it.get("batch_id")
            if bid:
                b = db.query_one("SELECT * FROM stock_batch WHERE id=? AND deleted_at IS NULL",
                                 (bid,))
                if b is None:
                    raise not_found("第 %d 行批次不存在" % i)
                if b["status"] == EXPIRED:
                    raise conflict("第 %d 行批次已过期，不可退入（F46 R5）" % i, code="E3051")
                if b["status"] == LOCKED:
                    raise conflict("第 %d 行批次被盘点冻结（F46 R5）" % i, code="E3051")
            else:
                # 优先回到发药扣减的批次；原批次已终态时回同品目可用批次，否则新建
                bid = _return_target_batch(conn, kind, line_id, line, rx, ctx)

            if is_herb:
                unit_price_milli = int(line["price_milli_per_g"])
                amount = money.herb_line_amount_fen(unit_price_milli, qty, 1)
            else:
                unit_price_milli = int(line["unit_price_fen"]) * 10   # 分 → 毫元
                amount = money.round_half_up(
                    money.Decimal(line["unit_price_fen"]) * money.Decimal(str(qty)))
            apply_change(conn, bid, qty, "RETURN_IN", "DRUG_RETURN", rid, line_id, 0,
                         "退药 " + rt_no, ctx)
            db.execute(conn, """
                INSERT INTO drug_return_item (return_id, line_no, item_kind, drug_id, herb_id,
                    prescription_item_id, herb_prescription_item_id, batch_id, qty, unit,
                    unit_price_milli, amount_fen, created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rid, i, kind, None if is_herb else line["drug_id"],
                  line["herb_id"] if is_herb else None,
                  None if is_herb else line_id, line_id if is_herb else None,
                  bid, qty, "g" if is_herb else line.get("unit"), unit_price_milli,
                  amount, tu.now_str(), ctx.user_id))
            total += amount

        db.execute(conn, "UPDATE drug_return SET total_amount_fen=?, line_count=? WHERE id=?",
                   (total, len(items), rid))
        _doc_transition(conn, "drug_return", rid, DOC_CONFIRMED, reason, ctx)
        db.execute(conn, "UPDATE drug_return SET confirmed_by=?, confirmed_at=? WHERE id=?",
                   (ctx.user_id, tu.now_str(), rid))

        # R9：处方状态由 M06 状态机推进
        full = all(_returnable_qty(conn, kind, it.get("herb_prescription_item_id") if is_herb
                                   else it.get("prescription_item_id"),
                                   ("DISPENSE", "HERB_DISPENSE"), ("DRUG_RETURN",)) <= QTY_EPS
                   for it in items)
        m06.mark_returned(conn, rx_id, full, ctx)
        audit.audit_high_risk(conn, audit.RISK_REFUND, "drug_return", rid,
                              "退药：" + reason,
                              {"prescription_id": rx_id, "amount_fen": total})
    return {"id": rid, "return_no": rt_no, "total_amount_fen": total,
            "status": DOC_CONFIRMED, "full_return": full}


def _return_target_batch(conn, kind, line_id, line, rx, ctx):
    """退药回库目标批次：优先发药扣减过的批次 → 同品目可用批次 → 新建批次。"""
    r = db.query_one("""SELECT batch_id FROM stock_transaction
                        WHERE item_kind=? AND ref_line_id=? AND ref_type IN
                        ('DISPENSE','HERB_DISPENSE') AND qty_change < 0
                        ORDER BY id DESC LIMIT 1""", (kind, line_id))
    if r:
        b = db.query_one("SELECT * FROM stock_batch WHERE id=? AND deleted_at IS NULL",
                         (r["batch_id"],))
        if b and b["status"] not in (EXPIRED, LOCKED):
            return b["id"]
    icol = _item_col(kind)
    item_id = (line["herb_id"] if kind == "HERB" else line["drug_id"])
    b = db.query_one("""SELECT * FROM stock_batch WHERE item_kind=? AND %s=? AND deleted_at
                        IS NULL AND status='NORMAL' ORDER BY expiry_date DESC LIMIT 1"""
                     % icol, (kind, item_id))
    if b:
        return b["id"]
    # 无可用批次：新建（继承效期与成本，保证可追溯）
    new_no = ids.next_no(conn, "BATCH")
    cur = db.execute(conn, """
        INSERT INTO stock_batch (batch_no, line_no, item_kind, drug_id, herb_id, expiry_date,
            unit, pack_qty, unit_cost_milli, qty_in, qty_remain, status, created_at,
            created_by, updated_at, updated_by)
        VALUES (?,1,?,?,?,?,?,1,0,0,0,'NORMAL',?,?,?,?)
    """, (new_no, kind, line["drug_id"] if kind == "DRUG" else None,
          line["herb_id"] if kind == "HERB" else None,
          tu.now().date().replace(year=tu.now().year + 2).isoformat(),
          "g" if kind == "HERB" else "UNIT",
          tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return cur.lastrowid


@route("/api/pharmacy/return/list", "GET", perm="stock.view", module=MODULE,
       action="drug_return.list")
def return_list(ctx, body, q):
    rx_id = q.get("prescription_id")
    sql = "SELECT * FROM drug_return WHERE deleted_at IS NULL"
    params = ()
    if rx_id:
        sql += " AND prescription_id=?"
        params = (rx_id,)
    sql += " ORDER BY id DESC LIMIT 100"
    rows = db.query(sql, params)
    for r in rows:
        r = dict(r)
        r["items"] = db.query("SELECT * FROM drug_return_item WHERE return_id=? ORDER BY line_no",
                              (r["id"],))
    return {"items": rows}


# ---------- 对外服务接口 ----------

def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """M01 F04 患者合并改挂：只改 patient_id，库存与流水不动（历史事实）。"""
    n = 0
    for t in ("dispense", "herb_dispense", "drug_return"):
        cur = db.execute(conn, "UPDATE %s SET patient_id=? WHERE patient_id=? "
                               "AND deleted_at IS NULL" % t,
                         (new_patient_id, old_patient_id))
        n += cur.rowcount
    return n


def import_upsert(kind, rows):
    """M14 F128 基础数据包导入（供应商）。M14 不得直接写 M07 表。"""
    ok, failed, errors = 0, 0, []

    def pick(row, *keys):
        for k in keys:
            if k in row and (row[k] or "").strip():
                return row[k].strip()
        return None

    for i, row in enumerate(rows, start=2):
        try:
            name = pick(row, "name", "名称", "供应商名称", "supplier_name")
            code = pick(row, "supplier_code", "编码", "供应商编码", "code")
            if not name:
                failed += 1
                errors.append({"line": i, "error": "缺少供应商名称"})
                continue
            code = code or ("S" + tu.now().strftime("%Y%m%d%H%M%S") + "%04d" % i)
            with db.tx() as conn:
                db.execute(conn, """
                    INSERT INTO supplier (supplier_code, name, contact_name, phone, address,
                                          license_no, created_at, created_by, updated_at,
                                          updated_by)
                    VALUES (?,?,?,?,?,?,?,0,?,0)
                    ON CONFLICT(supplier_code) DO UPDATE SET
                        name=excluded.name, contact_name=excluded.contact_name,
                        phone=excluded.phone, address=excluded.address,
                        license_no=excluded.license_no, updated_at=excluded.updated_at
                """, (code, name, pick(row, "contact_name", "联系人"),
                      pick(row, "phone", "电话"), pick(row, "address", "地址"),
                      pick(row, "license_no", "许可证号"), tu.now_str(), tu.now_str()))
            ok += 1
        except Exception as exc:
            failed += 1
            errors.append({"line": i, "error": str(exc)})
    return len(rows), ok, failed, errors
