# -*- coding: utf-8 -*-
"""M09 收费财务（F57-F65）。

表所有权：charge_item / bill / bill_item（只允许 INSERT，冲正走负数行）/ payment /
         payment_status_log / bill_status_log / refund / refund_item / reversal /
         day_settlement
状态机：03 §2.5 账单、§2.6 支付
金额：03 §3（整数分，行项目级舍入）；退款指定明细行（§3.4）；日结后只能红冲（E3006）
边界：不重算药品金额——处方取 M06 计价结果、处置取 M08 快照、检验取 M05 申请金额；
     上游表一律只读（R2），退药退款只消费 M07 已确认退药单
不做：医保结算、欠费挂账、应收账款、储值卡（01 §3）——因此无"部分支付"中间态
"""

from core import db, audit, config, timeutil as tu, ids
from core import money
from core.api import route
from core.errors import (require, require_keys, AppError, conflict, not_found,
                         BIZ_STATE_INVALID, BIZ_SETTLED_LOCKED)

MODULE = "M09"

PENDING = "PENDING"
PAID = "PAID"
PARTIAL_REFUNDED = "PARTIAL_REFUNDED"
FULLY_REFUNDED = "FULLY_REFUNDED"
BILL_VOID = "VOID"

BILL_TRANSITIONS = {
    PENDING:          [PAID, BILL_VOID],
    PAID:             [PARTIAL_REFUNDED, FULLY_REFUNDED],
    PARTIAL_REFUNDED: [FULLY_REFUNDED],
    FULLY_REFUNDED:   [],
    BILL_VOID:        [],
}

PAY_INIT = "INIT"
PAY_SUCCESS = "SUCCESS"
PAY_FAILED = "FAILED"
PAY_REFUNDED = "REFUNDED"
PAY_REVERSED = "REVERSED"

REF_DRAFT = "DRAFT"
REF_SUCCEEDED = "SUCCEEDED"
REF_FAILED = "FAILED"
REF_VOID = "VOID"


def bill_transition(conn, bill_id, to_status, reason=None, ctx=None, extra=None):
    """账单状态机唯一入口（03 §2.5，R5 禁止裸 UPDATE）。"""
    ctx = ctx or audit.get_ctx()
    row = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bill_id,))
    if row is None:
        raise not_found("账单不存在")
    frm = row["status"]
    if frm == to_status:
        return row          # 幂等：重复退款后仍是 PARTIAL_REFUNDED，不重复记日志
    if to_status not in BILL_TRANSITIONS.get(frm, []):
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "bill", bill_id,
                              "%s → %s" % (frm, to_status))
        raise conflict("账单状态不允许从 %s 转换到 %s" % (frm, to_status),
                       code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?"]
    params = [to_status, tu.now_str(), ctx.user_id]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(bill_id)
    db.execute(conn, "UPDATE bill SET %s WHERE id=?" % ",".join(sets), tuple(params))
    audit.audit_data_change(conn, "bill", bill_id, "status", frm, to_status, reason)
    db.execute(conn, """
        INSERT INTO bill_status_log (bill_id, from_status, to_status, reason, operator_id,
                                     created_at)
        VALUES (?,?,?,?,?,?)
    """, (bill_id, frm, to_status, reason, ctx.user_id, tu.now_str()))
    return row


def _pay_transition(conn, pay_id, to_status, reason=None, ctx=None):
    ctx = ctx or audit.get_ctx()
    row = db.query_one("SELECT * FROM payment WHERE id=?", (pay_id,))
    if row is None:
        raise not_found("支付记录不存在")
    frm = row["status"]
    db.execute(conn, "UPDATE payment SET status=? WHERE id=?", (to_status, pay_id))
    db.execute(conn, """
        INSERT INTO payment_status_log (payment_id, from_status, to_status, reason,
                                        operator_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (pay_id, frm, to_status, reason, ctx.user_id, tu.now_str()))
    return row


def _line_net(conn, bill_item_id):
    """明细行净额 = 金额 − 已退 − 已冲（bill_item 只允许 INSERT，故派生计算，F60 R3）。"""
    row = db.query_one("SELECT amount_fen FROM bill_item WHERE id=?", (bill_item_id,))
    if row is None:
        raise not_found("账单明细不存在")
    refunded = db.query_one("""
        SELECT COALESCE(SUM(ri.amount_fen),0) AS s FROM refund_item ri
        JOIN refund r ON r.id=ri.refund_id
        WHERE ri.bill_item_id=? AND r.status='SUCCEEDED' AND r.deleted_at IS NULL
    """, (bill_item_id,))
    reversed_ = db.query_one("""
        SELECT COALESCE(ABS(SUM(amount_fen)),0) AS s FROM bill_item
        WHERE is_reversal=1 AND reversal_id IS NOT NULL AND id<>?
          AND source_id=(SELECT source_id FROM bill_item WHERE id=?)
          AND bill_id=(SELECT bill_id FROM bill_item WHERE id=?)
    """, (bill_item_id, bill_item_id, bill_item_id))
    return int(row["amount_fen"]) - int(refunded["s"] or 0) - int(reversed_["s"] or 0)


def _settle_guard(conn, biz_date=None):
    """F58 R8 / F64 R4：当日已日结则禁止生成或修改当日账单（E3006）。"""
    d = biz_date or tu.today_str()
    row = db.query_one("SELECT id, settlement_no FROM day_settlement WHERE biz_date=? "
                       "ORDER BY id DESC LIMIT 1", (d,))
    return row


# ============================================================
# F57 收费项目
# ============================================================

@route("/api/charge_item/create", "POST", perm="charge_item.manage", module=MODULE,
       action="charge_item.create")
def charge_item_create(ctx, body, q):
    require_keys(body, ["name"])
    code = (body.get("item_code") or "").strip() or ("CH" + tu.now().strftime("%Y%m%d%H%M%S"))
    price_fen = money.to_fen(body.get("price_yuan")) if body.get("price_yuan") is not None \
        else int(body.get("price_fen") or 0)
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "charge_item.create", "charge_item", None,
                              detail={"name": body["name"]})
        cur = db.execute(conn, """
            INSERT INTO charge_item (item_code, name, alias, pinyin, category, unit,
                                     price_fen, auto_add_on_visit, remark, created_at,
                                     created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (code, body["name"].strip(), body.get("alias"), body.get("pinyin"),
              (body.get("category") or "OTHER").upper(), body.get("unit") or "次",
              price_fen, int(body.get("auto_add_on_visit") or 0), body.get("remark"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid, "item_code": code, "price_fen": price_fen}


@route("/api/charge_item/search", "GET", perm="finance.view", module=MODULE,
       action="charge_item.search")
def charge_item_search(ctx, body, q):
    kw = (q.get("keyword") or "").strip()
    sql = "SELECT * FROM charge_item WHERE deleted_at IS NULL AND is_active=1"
    params = ()
    if kw:
        sql += " AND (name LIKE ? OR alias LIKE ? OR pinyin LIKE ?)"
        like = "%" + kw + "%"
        params = (like, like, like)
    sql += " ORDER BY category, id LIMIT 200"
    return {"items": db.query(sql, params)}


# ============================================================
# F58 账单
# ============================================================

@route("/api/bill/create", "POST", perm="bill.create", module=MODULE, action="bill.create")
def bill_create(ctx, body, q):
    """F58 汇总生成账单：处方（M06）/ 处置（M08）/ 检验（M05）/ 自动收费项目（F57）。

    金额一律取上游快照，本模块不重算（R-M09-1）。
    """
    from core import bizlock
    bizlock.require_free("生成账单")        # F128 R8：基础数据导入中不收费
    from . import m06_prescription as m06
    visit_id = body.get("visit_id")
    require(visit_id, "缺少 visit_id")
    with db.tx() as conn:
        visit = db.query_one("SELECT * FROM visit WHERE id=? AND deleted_at IS NULL",
                             (visit_id,))
        if visit is None:
            raise not_found("就诊不存在")
        exist = db.query_one("SELECT id, bill_no FROM bill WHERE visit_id=? AND deleted_at "
                             "IS NULL AND status<>'VOID'", (visit_id,))
        if exist:
            raise conflict("该就诊已存在有效账单：%s（F58 R3）" % exist["bill_no"],
                           code="E3070")
        biz_date = body.get("biz_date") or tu.today_str()
        if _settle_guard(conn, biz_date):
            raise conflict("营业日 %s 已日结，不能生成新账单（F58 R8）" % biz_date,
                           code=BIZ_SETTLED_LOCKED)

        audit.audit_operation(conn, MODULE, "bill.create", "visit", visit_id)
        bill_no = ids.next_no(conn, "BILL")
        patient = db.query_one("SELECT name FROM patient WHERE id=?", (visit["patient_id"],))
        cur = db.execute(conn, """
            INSERT INTO bill (bill_no, visit_id, patient_id, patient_name_snap, status,
                              biz_date, occurred_at, created_at, created_by, updated_at,
                              updated_by)
            VALUES (?,?,?,?,'PENDING',?,?,?,?,?,?)
        """, (bill_no, visit_id, visit["patient_id"],
              (patient["name"] if patient else None), biz_date, tu.now_str(),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        bid = cur.lastrowid

        lines = []
        # ① 处方（成药 / 饮片）——金额取 F112 计价结果
        for rx in m06.prescribed_amount_for_bill(visit_id):
            stype = "HERB" if rx["prescription_type"] == "HERB" else "DRUG"
            lines.append((stype, rx["id"],
                          "%s处方 %s" % ("中药" if stype == "HERB" else "",
                                         rx["prescription_no"]),
                          int(rx["amount_fen"])))
        # ② 处置（M08，只读快照）
        for t in db.query("SELECT * FROM treatment_execution WHERE visit_id=? AND deleted_at "
                          "IS NULL AND status<>'CANCELLED' ORDER BY id", (visit_id,)):
            lines.append(("TREATMENT", t["id"], t["item_name_snap"], int(t["amount_fen"])))
        # ③ 检验（M05，只读）
        for lo in db.query("SELECT * FROM lab_order WHERE visit_id=? AND deleted_at IS NULL "
                           "AND status NOT IN ('DRAFT','CANCELLED') ORDER BY id", (visit_id,)):
            lines.append(("LAB", lo["id"], "检验申请 %s" % lo["order_no"],
                          int(lo["total_amount"] or 0)))
        # ④ 自动收费项目（如挂号费）
        if config.get_int("finance.auto_registration", 1):
            for c in db.query("SELECT * FROM charge_item WHERE deleted_at IS NULL "
                              "AND is_active=1 AND auto_add_on_visit=1 ORDER BY id"):
                lines.append(("CHARGE", c["id"], c["name"], int(c["price_fen"])))

        total = 0
        for i, (stype, sid, name, amount) in enumerate(lines, start=1):
            db.execute(conn, """
                INSERT INTO bill_item (bill_id, line_no, source_type, source_id, name_snap,
                                       unit_price_fen, quantity, amount_fen, created_at,
                                       created_by)
                VALUES (?,?,?,?,?,?,1,?,?,?)
            """, (bid, i, stype, sid, name, amount, amount, tu.now_str(), ctx.user_id))
            total += amount
        db.execute(conn, "UPDATE bill SET total_amount_fen=?, line_count=? WHERE id=?",
                   (total, len(lines), bid))
    return {"id": bid, "bill_no": bill_no, "total_amount_fen": total,
            "line_count": len(lines), "status": PENDING}


@route("/api/bill/get", "GET", perm="finance.view", module=MODULE, action="bill.get")
def bill_get(ctx, body, q):
    bid = q.get("id") or q.get("bill_id") or q.get("visit_id")
    if q.get("visit_id") and not (q.get("id") or q.get("bill_id")):
        row = db.query_one("SELECT * FROM bill WHERE visit_id=? AND deleted_at IS NULL "
                           "AND status<>'VOID' ORDER BY id DESC LIMIT 1", (q["visit_id"],))
    else:
        require(bid, "缺少 bill_id")
        row = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bid,))
    if row is None:
        raise not_found("账单不存在")
    row = dict(row)
    row["items"] = db.query("SELECT * FROM bill_item WHERE bill_id=? ORDER BY line_no",
                            (row["id"],))
    row["payments"] = db.query("SELECT * FROM payment WHERE bill_id=? ORDER BY id", (row["id"],))
    row["refunds"] = db.query("SELECT * FROM refund WHERE bill_id=? AND deleted_at IS NULL "
                              "ORDER BY id", (row["id"],))
    return row


@route("/api/bill/void", "POST", perm="bill.create", module=MODULE, action="bill.void")
def bill_void(ctx, body, q):
    bid = body.get("id") or body.get("bill_id")
    reason = (body.get("reason") or "").strip()
    require(bid, "缺少 bill_id")
    require(len(reason) >= 2, "作废必须填写原因（F58 R7）")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bid,))
        if row is None:
            raise not_found("账单不存在")
        if row["status"] != PENDING:
            raise conflict("已收费账单不可作废，请走退款或冲正（F58 R7）", code="E3071")
        if row["settlement_id"]:
            raise conflict("账单已日结，只能红字冲正（F64 R4）", code=BIZ_SETTLED_LOCKED)
        audit.audit_operation(conn, MODULE, "bill.void", "bill", bid, detail=reason)
        bill_transition(conn, bid, BILL_VOID, reason, ctx, extra={"void_reason": reason})
        audit.audit_high_risk(conn, audit.RISK_VOID, "bill", bid, "作废账单：" + reason)
    return {"id": bid, "status": BILL_VOID}


# ============================================================
# F59 收费
# ============================================================

@route("/api/bill/pay", "POST", perm="bill.pay", module=MODULE, action="bill.pay")
def bill_pay(ctx, body, q):
    """F59 收费：可多笔混合支付，但合计必须等于应收（R2，无部分支付）。"""
    bid = body.get("id") or body.get("bill_id")
    require(bid, "缺少 bill_id")
    payments = body.get("payments")
    if not payments:
        payments = [{"channel": body.get("channel") or "CASH",
                     "amount_fen": body.get("amount_fen"),
                     "received_amount_fen": body.get("received_amount_fen"),
                     "trade_no": body.get("trade_no")}]
    idem = body.get("idempotent_key")

    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM payment WHERE idempotent_key=?", (idem,))
            if done:
                b = db.query_one("SELECT * FROM bill WHERE id=?", (done["bill_id"],))
                return {"bill_id": b["id"], "status": b["status"], "duplicate": True}
        bill = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bid,))
        if bill is None:
            raise not_found("账单不存在")
        if bill["status"] != PENDING:
            raise conflict("账单状态为 %s，不能重复收费（F59 R9）" % bill["status"],
                           code="E3072")
        if bill["settlement_id"]:
            raise conflict("账单已日结，不能收费（F64 R4）", code=BIZ_SETTLED_LOCKED)

        total = 0
        norm = []
        for p in payments:
            amt = int(p.get("amount_fen") or 0)
            require(amt > 0, "支付金额必须大于 0")
            received = int(p.get("received_amount_fen") or amt)
            require(received >= amt, "实收金额不能小于本笔应收")
            norm.append({"channel": (p.get("channel") or "CASH").upper(), "amount_fen": amt,
                         "received_amount_fen": received,
                         "change_amount_fen": received - amt,
                         "trade_no": p.get("trade_no")})
            total += amt
        if total != int(bill["total_amount_fen"]):
            raise conflict("支付合计 %d 与应收 %d 不符（F59 R2：无部分支付）"
                           % (total, bill["total_amount_fen"]), code="E3073")

        audit.audit_operation(conn, MODULE, "bill.pay", "bill", bid,
                              detail={"channels": [p["channel"] for p in norm],
                                      "total": total})
        now = tu.now_str()
        for p in norm:
            cur = db.execute(conn, """
                INSERT INTO payment (bill_id, channel, amount_fen, received_amount_fen,
                                     change_amount_fen, trade_no, status, paid_at,
                                     idempotent_key, operator_id, created_at, created_by)
                VALUES (?,?,?,?,?,?,'SUCCESS',?,?,?,?,?)
            """, (bid, p["channel"], p["amount_fen"], p["received_amount_fen"],
                  p["change_amount_fen"], p["trade_no"], now, idem, ctx.user_id,
                  now, ctx.user_id))
            _pay_transition(conn, cur.lastrowid, PAY_SUCCESS, "收款成功", ctx)
        bill_transition(conn, bid, PAID, "收费完成", ctx, extra={"paid_amount_fen": total})
    return {"id": bid, "status": PAID, "paid_amount_fen": total}


# ============================================================
# F61 退款 / F63 退药退款
# ============================================================

def _create_refund(conn, ctx, bill_id, items, reason, refund_type="NORMAL",
                   drug_return_id=None, idem=None):
    """退款内核：逐行校验净额 → 写退款单 → 冲抵支付 → 推进账单状态。"""
    bill = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bill_id,))
    if bill is None:
        raise not_found("账单不存在")
    if bill["status"] not in (PAID, PARTIAL_REFUNDED):
        raise conflict("账单状态为 %s，不可退款（F61 R3）" % bill["status"],
                       code=BIZ_STATE_INVALID)
    require(items, "退款明细不能为空")

    audit.audit_operation(conn, MODULE, "refund.create", "bill", bill_id,
                          detail={"reason": reason, "type": refund_type})
    refund_no = ids.next_no(conn, "REFUND")
    cur = db.execute(conn, """
        INSERT INTO refund (refund_no, bill_id, visit_id, patient_id, drug_return_id,
                            refund_type, status, total_amount_fen, line_count, reason,
                            occurred_at, operator_id, idempotent_key, created_at, created_by,
                            updated_at, updated_by)
        VALUES (?,?,?,?,?,?,'DRAFT',0,?,?,?,?,?,?,?,?,?)
    """, (refund_no, bill_id, bill["visit_id"], bill["patient_id"], drug_return_id,
          refund_type, len(items), reason, tu.now_str(), ctx.user_id, idem,
          tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
    rid = cur.lastrowid

    total = 0
    for i, it in enumerate(items, start=1):
        bi_id = it.get("bill_item_id")
        require(bi_id, "第 %d 行缺少 bill_item_id（F61 R2）" % i)
        bi = db.query_one("SELECT * FROM bill_item WHERE id=? AND bill_id=?", (bi_id, bill_id))
        if bi is None:
            raise not_found("第 %d 行账单明细不存在" % i)
        amt = int(it.get("amount_fen") or 0)
        require(amt > 0, "第 %d 行退款金额必须大于 0" % i)
        net = _line_net(conn, bi_id)
        require(amt <= net, "第 %d 行退款 %d 超过可退净额 %d（F61 R2）" % (i, amt, net),
                code="E3074")
        db.execute(conn, """
            INSERT INTO refund_item (refund_id, line_no, bill_item_id, source_type, source_id,
                                     name_snap, quantity, amount_fen, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (rid, i, bi_id, bi["source_type"], bi["source_id"], bi["name_snap"],
              it.get("quantity") or 1, amt, tu.now_str(), ctx.user_id))
        total += amt

    db.execute(conn, "UPDATE refund SET total_amount_fen=?, line_count=?, status='SUCCEEDED', "
                     "updated_at=? WHERE id=?", (total, len(items), tu.now_str(), rid))

    # 支付冲抵：后付先退（F61 R4）
    left = total
    for p in db.query("SELECT * FROM payment WHERE bill_id=? AND status='SUCCESS' "
                      "ORDER BY id DESC", (bill_id,)):
        if left <= 0:
            break
        can = int(p["amount_fen"]) - int(p["refunded_amount_fen"] or 0)
        take = min(can, left)
        if take <= 0:
            continue
        db.execute(conn, "UPDATE payment SET refunded_amount_fen=refunded_amount_fen+? "
                         "WHERE id=?", (take, p["id"]))
        if take == can:
            _pay_transition(conn, p["id"], PAY_REFUNDED, "全额退回", ctx)
        left -= take

    db.execute(conn, "UPDATE bill SET refunded_amount_fen=refunded_amount_fen+? WHERE id=?",
               (total, bill_id))

    # 账单状态：全部退完 → FULLY_REFUNDED
    full = True
    for bi in db.query("SELECT id FROM bill_item WHERE bill_id=? AND is_reversal=0", (bill_id,)):
        if _line_net(conn, bi["id"]) > 0:
            full = False
            break
    bill_transition(conn, bill_id, FULLY_REFUNDED if full else PARTIAL_REFUNDED,
                    "退款：" + reason, ctx)
    audit.audit_high_risk(conn, audit.RISK_REFUND, "refund", rid, "退款：" + reason,
                          {"bill_id": bill_id, "amount_fen": total})
    return {"id": rid, "refund_no": refund_no, "total_amount_fen": total,
            "status": REF_SUCCEEDED, "full_refund": full}


@route("/api/refund/create", "POST", perm="refund.create", module=MODULE,
       action="refund.create")
def refund_create(ctx, body, q):
    """F61 退款：逐明细行指定金额。"""
    bill_id = body.get("bill_id")
    reason = (body.get("reason") or "").strip()
    require(bill_id, "缺少 bill_id")
    require(len(reason) >= 2, "退款必须填写原因（R-M09-10）")
    idem = body.get("idempotent_key")
    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM refund WHERE idempotent_key=? AND deleted_at "
                                "IS NULL", (idem,))
            if done:
                return dict(done, duplicate=True)
        return _create_refund(conn, ctx, bill_id, body.get("items") or [], reason,
                              "NORMAL", None, idem)


@route("/api/refund/by_drug_return", "POST", perm="refund.create", module=MODULE,
       action="refund.by_drug_return")
def refund_by_drug_return(ctx, body, q):
    """F63 退药退款：消费 M07 已确认退药单（只读），按开方时价生成的退药金额退款。"""
    rid = body.get("drug_return_id") or body.get("return_id")
    require(rid, "缺少 drug_return_id")
    with db.tx() as conn:
        rt = db.query_one("SELECT * FROM drug_return WHERE id=? AND deleted_at IS NULL",
                           (rid,))
        if rt is None:
            raise not_found("退药单不存在")
        if rt["status"] != "CONFIRMED":
            raise conflict("退药单状态为 %s，未确认不得退款（F63 R1）" % rt["status"],
                           code="E3076")
        done = db.query_one("SELECT id, refund_no FROM refund WHERE drug_return_id=? AND "
                            "deleted_at IS NULL AND status<>'VOID'", (rid,))
        if done:
            raise conflict("该退药单已退款：%s（F63 R2）" % done["refund_no"], code="E3077")

        bill = db.query_one("SELECT * FROM bill WHERE visit_id=? AND deleted_at IS NULL "
                            "AND status<>'VOID' ORDER BY id DESC LIMIT 1", (rt["visit_id"],))
        if bill is None:
            raise conflict("该退药单对应的就诊尚未生成账单（F63 R4）", code="E3078")
        bi = db.query_one("SELECT * FROM bill_item WHERE bill_id=? AND source_id=? AND "
                          "source_type IN ('DRUG','HERB')", (bill["id"], rt["prescription_id"]))
        if bi is None:
            raise conflict("账单中找不到该处方的收费明细（F63 R4）", code="E3078")

        lines = db.query("SELECT * FROM drug_return_item WHERE return_id=? ORDER BY line_no",
                         (rid,))
        amount = sum(int(x["amount_fen"]) for x in lines)
        require(amount > 0, "退药金额为 0，无需退款")
        items = [{"bill_item_id": bi["id"], "amount_fen": amount,
                  "quantity": sum(float(x["qty"]) for x in lines)}]
        reason = (body.get("reason") or ("退药退款：" + (rt["reason"] or ""))).strip()
        return _create_refund(conn, ctx, bill["id"], items, reason, "DRUG_RETURN", rid,
                              body.get("idempotent_key"))


@route("/api/refund/list", "GET", perm="finance.view", module=MODULE, action="refund.list")
def refund_list(ctx, body, q):
    sql = "SELECT * FROM refund WHERE deleted_at IS NULL"
    params = ()
    if q.get("bill_id"):
        sql += " AND bill_id=?"
        params = (q["bill_id"],)
    sql += " ORDER BY id DESC LIMIT 100"
    rows = db.query(sql, params)
    for r in rows:
        r["items"] = db.query("SELECT * FROM refund_item WHERE refund_id=? ORDER BY line_no",
                              (r["id"],))
    return {"items": rows}


# ============================================================
# F62 红字冲正
# ============================================================

@route("/api/reversal/create", "POST", perm="reversal.create", module=MODULE,
       action="reversal.create")
def reversal_create(ctx, body, q):
    """F62 冲正：新增负数明细行，不改原行（契约 §3.4 / R-M09-6）。"""
    bill_id = body.get("bill_id")
    reason = (body.get("reason") or "").strip()
    require(bill_id, "缺少 bill_id")
    require(len(reason) >= 2, "冲正必须填写原因（F62 R4）")
    idem = body.get("idempotent_key")
    items = body.get("items") or []
    require(items, "冲正明细不能为空")

    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM reversal WHERE idempotent_key=?", (idem,))
            if done:
                return dict(done, duplicate=True)
        bill = db.query_one("SELECT * FROM bill WHERE id=? AND deleted_at IS NULL", (bill_id,))
        if bill is None:
            raise not_found("账单不存在")
        if bill["status"] == BILL_VOID:
            raise conflict("已作废账单无需冲正", code=BIZ_STATE_INVALID)

        audit.audit_operation(conn, MODULE, "reversal.create", "bill", bill_id, detail=reason)
        rv_no = ids.next_no(conn, "REVERSAL")
        cur = db.execute(conn, """
            INSERT INTO reversal (reversal_no, bill_id, total_amount_fen, line_count, reason,
                                  occurred_at, operator_id, idempotent_key, remark, created_at,
                                  created_by)
            VALUES (?,?,0,?,?,?,?,?,?,?,?)
        """, (rv_no, bill_id, len(items), reason, tu.now_str(), ctx.user_id, idem,
              body.get("remark"), tu.now_str(), ctx.user_id))
        rvid = cur.lastrowid

        total = 0
        n = db.query_one("SELECT COALESCE(MAX(line_no),0) m FROM bill_item WHERE bill_id=?",
                         (bill_id,))
        line = int(n["m"])
        for it in items:
            bi_id = it.get("bill_item_id")
            require(bi_id, "冲正明细缺少 bill_item_id")
            bi = db.query_one("SELECT * FROM bill_item WHERE id=? AND bill_id=?",
                              (bi_id, bill_id))
            if bi is None:
                raise not_found("账单明细不存在：%s" % bi_id)
            amt = int(it.get("amount_fen") or 0)
            require(amt > 0, "冲正金额必须大于 0")
            net = _line_net(conn, bi_id)
            require(amt <= net, "冲正 %d 超过可冲净额 %d（F62 R5）" % (amt, net),
                    code="E3075")
            line += 1
            db.execute(conn, """
                INSERT INTO bill_item (bill_id, line_no, source_type, source_id, name_snap,
                                       unit_price_fen, quantity, amount_fen, is_reversal,
                                       reversal_id, remark, created_at, created_by)
                VALUES (?,?,?,?,?,?,1,?,1,?,?,?,?)
            """, (bill_id, line, bi["source_type"], bi["source_id"],
                  "（冲正）" + (bi["name_snap"] or ""), -amt, -amt, rvid, reason,
                  tu.now_str(), ctx.user_id))
            total += amt

        db.execute(conn, "UPDATE reversal SET total_amount_fen=? WHERE id=?", (total, rvid))
        new_total = int(bill["total_amount_fen"]) - total
        # 冲正只减应收、不碰已收（F62 口径）。已收费账单冲正是**合法业务**
        # （开错单 → 冲正 → 再走 F61 退款把多收的钱退给患者），
        # 所以这里不能因为"冲完后应收低于已收"就拦住——那是等待退款的正常中间态。
        # 真正兜住账务的是 F94 的不变量（净收不得超过原始应收，口径把冲正加回）。
        db.execute(conn, "UPDATE bill SET total_amount_fen=?, updated_at=?, updated_by=? "
                         "WHERE id=?", (new_total, tu.now_str(), ctx.user_id, bill_id))
        audit.audit_high_risk(conn, audit.RISK_REVERSAL, "reversal", rvid,
                              "红字冲正：" + reason,
                              {"bill_id": bill_id, "amount_fen": total})
        # 口径说明（重要）：红字冲正是**账务更正**，只减少应收；
        # 实际退钱走退款流程（F61）。若在此同步退款，会与负数行重复冲减同一笔金额，
        # 导致 net 被扣两次，且日结口径（03 §7：Σ收款 − Σ退款）失真。
    return {"id": rvid, "reversal_no": rv_no, "total_amount_fen": total,
            "bill_total_amount_fen": new_total}


# ============================================================
# F64 日结
# ============================================================

@route("/api/settlement/close", "POST", perm="settlement.close", module=MODULE,
       action="settlement.close")
def settlement_close(ctx, body, q):
    """F64 日结：按 payment.paid_at 归集（03 §7），快照固化，历史日结单不改（R9）。"""
    biz_date = (body.get("biz_date") or "").strip() or tu.today_str()
    idem = body.get("idempotent_key")
    with db.tx() as conn:
        if idem:
            done = db.query_one("SELECT * FROM day_settlement WHERE idempotent_key=?", (idem,))
            if done:
                return dict(done, duplicate=True)
        last = _settle_guard(conn, biz_date)
        audit.audit_operation(conn, MODULE, "settlement.close", "day_settlement", None,
                              detail={"biz_date": biz_date})
        bills = db.query("SELECT * FROM bill WHERE deleted_at IS NULL AND status<>'VOID' "
                         "AND biz_date=?", (biz_date,))
        if last:                                   # R5：增量日结
            bills = [b for b in bills if not b["settlement_id"]]
        if not bills:
            raise conflict("营业日 %s 没有可结账的账单（F64）" % biz_date, code="E3079")
        ids_ = [b["id"] for b in bills]

        ph = ",".join("?" * len(ids_))
        paid = db.query_one("""SELECT COALESCE(SUM(amount_fen),0) s, COUNT(*) c FROM payment
                               WHERE status='SUCCESS' AND bill_id IN (%s)""" % ph, tuple(ids_))
        ref = db.query_one("""SELECT COALESCE(SUM(total_amount_fen),0) s FROM refund
                              WHERE status='SUCCEEDED' AND deleted_at IS NULL
                                AND bill_id IN (%s)""" % ph, tuple(ids_))
        receivable = sum(int(b["total_amount_fen"]) for b in bills)
        pend = [b for b in bills if b["status"] == PENDING]
        channels = db.query("""SELECT channel, COUNT(*) c, COALESCE(SUM(amount_fen),0) amt
                               FROM payment WHERE status='SUCCESS' AND bill_id IN (%s)
                               GROUP BY channel""" % ph, tuple(ids_))
        paid_amt = int(paid["s"]) if paid else 0
        ref_amt = int(ref["s"]) if ref else 0
        net = paid_amt - ref_amt

        st_no = ids.next_no(conn, "DAY_SETTLEMENT", biz_date.replace("-", ""))
        cur = db.execute(conn, """
            INSERT INTO day_settlement (settlement_no, biz_date, from_settlement_id, bill_count,
                pending_count, pending_amount_fen, receivable_amount_fen, paid_amount_fen,
                refund_amount_fen, net_amount_fen, channel_json, occurred_at, operator_id,
                idempotent_key, remark, created_at, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (st_no, biz_date, last["id"] if last else None, len(bills), len(pend),
              sum(int(b["total_amount_fen"]) for b in pend), receivable, paid_amt, ref_amt,
              net, audit.dumps([dict(c) for c in channels]), tu.now_str(), ctx.user_id,
              idem, body.get("remark"), tu.now_str(), ctx.user_id))
        sid = cur.lastrowid
        for b in bills:                            # R4：日结后账单锁定
            db.execute(conn, "UPDATE bill SET settlement_id=?, settled_at=?, updated_at=?, "
                             "updated_by=? WHERE id=?",
                       (sid, tu.now_str(), tu.now_str(), ctx.user_id, b["id"]))
        audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "day_settlement", sid,
                              "日结 %s" % biz_date,
                              {"paid": paid_amt, "refund": ref_amt, "net": net})
    return {"id": sid, "settlement_no": st_no, "biz_date": biz_date, "bill_count": len(bills),
            "pending_count": len(pend), "paid_amount_fen": paid_amt,
            "refund_amount_fen": ref_amt, "net_amount_fen": net}


@route("/api/settlement/list", "GET", perm="finance.view", module=MODULE,
       action="settlement.list")
def settlement_list(ctx, body, q):
    return {"items": db.query("SELECT * FROM day_settlement ORDER BY id DESC LIMIT 100")}


# ============================================================
# F65 财务查询
# ============================================================

@route("/api/finance/bills", "GET", perm="finance.view", module=MODULE, action="finance.bills")
def finance_bills(ctx, body, q):
    """F65 交易级查询（分析类报表归 M15，避免口径分裂）。"""
    limit = min(int(q.get("limit") or 50), 200)
    sql = "SELECT * FROM bill WHERE deleted_at IS NULL"
    params = ()
    for col in ("visit_id", "patient_id", "status"):
        if q.get(col):
            sql += " AND %s=?" % col
            params += (q[col],)
    if q.get("date_from"):
        sql += " AND biz_date >= ?"
        params += (q["date_from"],)
    if q.get("date_to"):
        sql += " AND biz_date <= ?"
        params += (q["date_to"],)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (limit,)
    return {"items": db.query(sql, params)}


@route("/api/finance/payments", "GET", perm="finance.view", module=MODULE,
       action="finance.payments")
def finance_payments(ctx, body, q):
    limit = min(int(q.get("limit") or 50), 200)
    sql = "SELECT * FROM payment WHERE 1=1"
    params = ()
    if q.get("channel"):
        sql += " AND channel=?"
        params += (q["channel"].upper(),)
    if q.get("status"):
        sql += " AND status=?"
        params += (q["status"],)
    if q.get("date_from"):
        sql += " AND paid_at >= ?"
        params += (q["date_from"],)
    if q.get("date_to"):
        sql += " AND paid_at <= ?"
        params += (q["date_to"] + " 23:59:59",)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (limit,)
    return {"items": db.query(sql, params)}


# ---------- 对外服务接口 ----------

def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """M01 F04 患者合并改挂：只改 patient_id，快照字段不动。"""
    cur = db.execute(conn, "UPDATE bill SET patient_id=? WHERE patient_id=? "
                           "AND deleted_at IS NULL", (new_patient_id, old_patient_id))
    n = cur.rowcount
    cur = db.execute(conn, "UPDATE refund SET patient_id=? WHERE patient_id=? "
                           "AND deleted_at IS NULL", (new_patient_id, old_patient_id))
    return n + cur.rowcount


def import_upsert(kind, rows):
    """M14 F128 基础数据包导入（收费项目）。"""
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
                    INSERT INTO charge_item (item_code, name, alias, pinyin, category, unit,
                        price_fen, auto_add_on_visit, created_at, created_by, updated_at,
                        updated_by)
                    VALUES (?,?,?,?,?,?,?,?,?,0,?,0)
                    ON CONFLICT(item_code) DO UPDATE SET
                        name=excluded.name, category=excluded.category,
                        unit=excluded.unit, price_fen=excluded.price_fen,
                        updated_at=excluded.updated_at
                """, (code, name, pick(row, "alias", "别名"), pick(row, "pinyin", "拼音"),
                      (pick(row, "category", "类别") or "OTHER").upper(),
                      pick(row, "unit", "单位") or "次",
                      money.to_fen(price) if price else 0,
                      int(pick(row, "auto_add_on_visit", "自动并入") or 0),
                      tu.now_str(), tu.now_str()))
            ok += 1
        except Exception as exc:
            failed += 1
            errors.append({"line": i, "error": str(exc)})
    return len(rows), ok, failed, errors
