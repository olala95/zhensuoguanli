# -*- coding: utf-8 -*-
"""M15 经营统计（F119-F124）· 只读聚合层。

表所有权：report_definition / report_snapshot（可选缓存）
其余数据**只读，且只能通过 v_* 视图取数**（04 §4 / R-M15-1）：
    v_revenue_daily / v_visit_stats / v_stock_ledger /
    v_prescription_stats / v_patient_stats / v_diagnosis_stats
口径：03 §7（营收=收款−退款，按 paid_at 归集；门诊量仅 COMPLETED；进销存六段式…）
边界：报表查询不写审计（审计只记写操作）；导出属数据外流，写 audit_high_risk
"""

import csv
import datetime
import os

from core import db, audit, config, timeutil as tu
from core import money
from core.api import route
from core.errors import require, AppError, not_found, DATA_FORMAT

MODULE = "M15"
MAX_RANGE_DAYS = 366
MAX_EXPORT_ROWS = 50000

# 口径说明（随结果返回，便于与手工核对对齐，R-M15-9）
CRITERIA = {
    "revenue": "营收 = Σ 成功收款 − Σ 成功退款；归集时间 = payment.paid_at",
    "visit": "门诊量 = COUNT(visit) WHERE status='COMPLETED'（不含 VOID/SUSPENDED）",
    "first_visit": "初诊 = 该患者此次就诊之前无 COMPLETED 就诊",
    "stock": "期末 = 期初 + 入库 − 出库(发药) + 退药 − 报损 ± 盘点调整（拆零成对抵消）",
    "prescription": "有效处方 = CONFIRMED/DISPENSED/PARTIAL_RETURNED/FULLY_RETURNED；作废单列",
    "diagnosis": "疾病谱按 ICD-10 / 中医证型分组；同一就诊同一编码只计一次；排除 EXCLUDED",
}

REPORT_KINDS = ("revenue", "visit", "doctor", "stock", "prescription", "patient")


def _view_ready(name):
    row = db.query_one("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,))
    return row is not None


def _require_view(name):
    if not _view_ready(name):
        raise AppError("E1007", "视图 %s 未创建，请先执行迁移（M15 只查视图，不降级查原表）"
                       % name, retryable=False, http_status=409)


def _range(q):
    """日期区间（含首尾，字符串比较天然支持 ISO 日期）；默认最近 30 天。"""
    today = tu.now().date()
    d_from = (q.get("from") or q.get("date_from") or "").strip()
    d_to = (q.get("to") or q.get("date_to") or "").strip()
    if not d_from:
        d_from = (today - datetime.timedelta(days=29)).isoformat()
    if not d_to:
        d_to = today.isoformat()
    for d in (d_from, d_to):
        if tu.parse(d) is None:
            raise AppError(DATA_FORMAT, "日期格式应为 YYYY-MM-DD：%s" % d,
                           retryable=False, http_status=400)
    if d_from > d_to:
        raise AppError(DATA_FORMAT, "起始日期不能晚于结束日期", retryable=False,
                       http_status=400)
    if (tu.parse(d_to).date() - tu.parse(d_from).date()).days > MAX_RANGE_DAYS:
        raise AppError("E3080", "查询区间不能超过 %d 天，请缩小范围" % MAX_RANGE_DAYS,
                       retryable=False, http_status=400)
    return d_from, d_to


def _criteria(kind, d_from, d_to, extra=None):
    c = {"report": kind, "from": d_from, "to": d_to,
         "generated_at": tu.now_str(), "basis": CRITERIA.get(kind, "")}
    if extra:
        c.update(extra)
    return c


def _fen(v):
    return int(v or 0)


def _yuan(v):
    return money.fen_to_yuan(_fen(v))


def _pct(num, den):
    if not den:
        return None
    return round(float(num) * 100.0 / float(den), 2)


# ============================================================
# F119 日营收与班结
# ============================================================

@route("/api/report/revenue_daily", "GET", perm="report.view", module=MODULE,
       action="report.revenue_daily")
def revenue_daily(ctx, body, q):
    """F119 日营收：收款为正、退款为负（视图已定符号），营收 = SUM(amount_fen)。"""
    _require_view("v_revenue_daily")
    d_from, d_to = _range(q)
    rows = db.query("""
        SELECT biz_date, channel, kind, SUM(amount_fen) AS amt, COUNT(*) AS cnt
        FROM v_revenue_daily WHERE biz_date >= ? AND biz_date <= ?
        GROUP BY biz_date, channel, kind ORDER BY biz_date, channel
    """, (d_from, d_to))

    paid = refund = 0
    by_day, by_channel = {}, {}
    for r in rows:
        amt, cnt = _fen(r["amt"]), int(r["cnt"] or 0)
        day = by_day.setdefault(r["biz_date"],
                                {"paid_amount_fen": 0, "refund_amount_fen": 0,
                                 "net_amount_fen": 0, "pay_count": 0})
        if r["kind"] == "PAY":
            paid += amt
            day["paid_amount_fen"] += amt
            day["pay_count"] += cnt
            ch = by_channel.setdefault(r["channel"], {"amount_fen": 0, "count": 0})
            ch["amount_fen"] += amt
            ch["count"] += cnt
        else:
            refund += -amt
            day["refund_amount_fen"] += -amt
        day["net_amount_fen"] = day["paid_amount_fen"] - day["refund_amount_fen"]

    net = paid - refund
    # 门诊量（客单价分母，口径同 F120）
    _require_view("v_visit_stats")
    vrow = db.query_one("""
        SELECT COUNT(*) AS c FROM v_visit_stats
        WHERE status='COMPLETED' AND biz_date >= ? AND biz_date <= ?
    """, (d_from, d_to))
    visits = int(vrow["c"]) if vrow else 0
    avg = int(money.round_half_up(money.Decimal(net) / money.Decimal(visits))) if visits else None

    pending = db.query_one("""
        SELECT COUNT(*) AS c, COALESCE(SUM(total_amount_fen),0) AS amt FROM bill
        WHERE deleted_at IS NULL AND status='PENDING' AND biz_date >= ? AND biz_date <= ?
    """, (d_from, d_to)) or {"c": 0, "amt": 0}

    return {
        "criteria": _criteria("revenue", d_from, d_to),
        "summary": {
            "paid_amount_fen": paid, "paid_amount_yuan": _yuan(paid),
            "refund_amount_fen": refund, "refund_amount_yuan": _yuan(refund),
            "net_amount_fen": net, "net_amount_yuan": _yuan(net),
            "visit_count": visits,
            "avg_ticket_fen": avg,
            "avg_ticket_yuan": money.fen_to_yuan(avg) if avg is not None else None,
            "pending_count": int(pending["c"] or 0),
            "pending_amount_fen": _fen(pending["amt"]),
        },
        "by_channel": [dict(channel=k, **v, amount_yuan=_yuan(v["amount_fen"]))
                       for k, v in sorted(by_channel.items())],
        "by_day": [dict(biz_date=k, **v) for k, v in sorted(by_day.items())],
    }


# ============================================================
# F120 门诊量与医生工作量
# ============================================================

@route("/api/report/visit_stats", "GET", perm="report.view", module=MODULE,
       action="report.visit_stats")
def visit_stats(ctx, body, q):
    _require_view("v_visit_stats")
    d_from, d_to = _range(q)
    gran = (q.get("granularity") or "day").lower()
    if gran not in ("day", "month"):
        raise AppError(DATA_FORMAT, "granularity 取值应为 day/month", retryable=False,
                       http_status=400)
    expr = "biz_date" if gran == "day" else "substr(biz_date,1,7)"
    # 诊疗类型筛选：TCM 只看中医，WEST 看西医（含历史 NORMAL 数据），空=全部
    vtype = (q.get("visit_type") or "").strip().upper()
    vfilter, vparams = "", []
    if vtype == "TCM":
        vfilter = " AND visit_type='TCM'"
    elif vtype == "WEST":
        vfilter = " AND visit_type IN ('WEST','NORMAL')"
    rows = db.query("""
        SELECT {g} AS period, COUNT(*) AS total,
               SUM(CASE WHEN prior_completed = 0 THEN 1 ELSE 0 END) AS first_visit,
               SUM(CASE WHEN prior_completed > 0 THEN 1 ELSE 0 END) AS revisit
        FROM v_visit_stats
        WHERE status='COMPLETED' AND biz_date >= ? AND biz_date <= ?{f}
        GROUP BY {g} ORDER BY {g}
    """.format(g=expr, f=vfilter), tuple([d_from, d_to] + vparams))
    by_type = db.query("""
        SELECT visit_type, COUNT(*) AS c FROM v_visit_stats
        WHERE status='COMPLETED' AND biz_date >= ? AND biz_date <= ?
        GROUP BY visit_type ORDER BY c DESC
    """, (d_from, d_to))
    total = sum(int(r["total"] or 0) for r in rows)
    first = sum(int(r["first_visit"] or 0) for r in rows)
    return {
        "criteria": _criteria("visit", d_from, d_to,
                              {"granularity": gran, "visit_type": vtype or "ALL"}),
        "summary": {"visit_count": total, "first_visit": first,
                    "revisit": total - first,
                    "revisit_rate": _pct(total - first, total)},
        "by_type": [{"visit_type": r["visit_type"], "count": int(r["c"] or 0)}
                    for r in by_type],
        "trend": [{"period": r["period"], "total": int(r["total"] or 0),
                   "first_visit": int(r["first_visit"] or 0),
                   "revisit": int(r["revisit"] or 0)} for r in rows],
    }


@route("/api/report/doctor_workload", "GET", perm="report.view", module=MODULE,
       action="report.doctor_workload")
def doctor_workload(ctx, body, q):
    """F120 医生工作量：接诊人次 / 开方数 / 处置执行数 / 产生营收（按 paid_at 归集）。"""
    _require_view("v_visit_stats")
    _require_view("v_revenue_daily")
    d_from, d_to = _range(q)

    visits = db.query("""
        SELECT doctor_id, doctor_name, COUNT(*) AS visit_count,
               SUM(rx_count) AS rx_count, SUM(treatment_count) AS treatment_count
        FROM v_visit_stats
        WHERE status='COMPLETED' AND biz_date >= ? AND biz_date <= ?
        GROUP BY doctor_id, doctor_name
    """, (d_from, d_to))
    rev = db.query("""
        SELECT v.doctor_id AS doctor_id, SUM(r.amount_fen) AS amt
        FROM v_revenue_daily r
        JOIN v_visit_stats v ON v.visit_id = r.visit_id
        WHERE r.biz_date >= ? AND r.biz_date <= ?
        GROUP BY v.doctor_id
    """, (d_from, d_to))
    rev_map = {r["doctor_id"]: _fen(r["amt"]) for r in rev}

    items = []
    for r in visits:
        amt = rev_map.get(r["doctor_id"], 0)
        items.append({
            "doctor_id": r["doctor_id"],
            "doctor_name": r["doctor_name"] or "未归属",
            "visit_count": int(r["visit_count"] or 0),
            "rx_count": int(r["rx_count"] or 0),
            "treatment_count": int(r["treatment_count"] or 0),
            "revenue_fen": amt, "revenue_yuan": _yuan(amt),
        })
    items.sort(key=lambda x: -x["revenue_fen"])
    return {"criteria": _criteria("visit", d_from, d_to,
                                  {"note": "营收归集到接诊医生，按 payment.paid_at"}),
            "items": items,
            "total_revenue_fen": sum(i["revenue_fen"] for i in items)}


# ============================================================
# F121 药品进销存
# ============================================================

@route("/api/report/stock_ledger", "GET", perm="report.view", module=MODULE,
       action="report.stock_ledger")
def stock_ledger(ctx, body, q):
    """F121 进销存：期初(流水推算) → 入库 → 出库 → 退药 → 报损 → 盘点 → 期末。"""
    _require_view("v_stock_ledger")
    d_from, d_to = _range(q)
    kind = (q.get("item_kind") or "").upper()
    params, kf = [d_from, d_to], ""
    if kind in ("DRUG", "HERB"):
        kf = " AND item_kind = ?"
        params.append(kind)

    opening = db.query("""
        SELECT item_kind, drug_id, herb_id, batch_id,
               COALESCE(SUM(qty_change),0) AS qty, COALESCE(SUM(amount_fen),0) AS amt
        FROM v_stock_ledger WHERE occurred_at < ? {k}
        GROUP BY item_kind, drug_id, herb_id, batch_id
    """.format(k=kf), tuple([d_from] + ([kind] if kf else [])))
    period = db.query("""
        SELECT item_kind, drug_id, herb_id, batch_id, item_name, spec, unit,
               batch_no, expiry_date,
               SUM(CASE WHEN trans_type='IN'           THEN qty_change ELSE 0 END) AS qty_in,
               SUM(CASE WHEN trans_type='OUT'          THEN -qty_change ELSE 0 END) AS qty_out,
               SUM(CASE WHEN trans_type='RETURN_IN'    THEN qty_change ELSE 0 END) AS qty_return,
               SUM(CASE WHEN trans_type='LOSS'         THEN -qty_change ELSE 0 END) AS qty_loss,
               COALESCE(SUM(CASE WHEN trans_type='CHECK_ADJUST' THEN qty_change ELSE 0 END),0) AS qty_check,
               COALESCE(SUM(amount_fen),0) AS amount_fen
        FROM v_stock_ledger WHERE biz_date >= ? AND biz_date <= ? {k}
        GROUP BY item_kind, drug_id, herb_id, batch_id
    """.format(k=kf), tuple(params))

    key = lambda r: (r["item_kind"], r["drug_id"], r["herb_id"], r["batch_id"])
    op_map = {key(r): (float(r["qty"] or 0), _fen(r["amt"])) for r in opening}

    items = []
    for r in period:
        o_qty, o_amt = op_map.get(key(r), (0.0, 0))
        end = round(o_qty + float(r["qty_in"] or 0) - float(r["qty_out"] or 0)
                    + float(r["qty_return"] or 0) - float(r["qty_loss"] or 0)
                    + float(r["qty_check"] or 0), 3)
        items.append({
            "item_kind": r["item_kind"], "item_name": r["item_name"],
            "spec": r["spec"], "unit": r["unit"],
            "drug_id": r["drug_id"], "herb_id": r["herb_id"],
            "batch_id": r["batch_id"], "batch_no": r["batch_no"],
            "expiry_date": r["expiry_date"],
            "opening_qty": round(o_qty, 3),
            "in_qty": round(float(r["qty_in"] or 0), 3),
            "out_qty": round(float(r["qty_out"] or 0), 3),
            "return_qty": round(float(r["qty_return"] or 0), 3),
            "loss_qty": round(float(r["qty_loss"] or 0), 3),
            "check_qty": round(float(r["qty_check"] or 0), 3),
            "ending_qty": end,
            "amount_fen": _fen(r["amount_fen"]), "amount_yuan": _yuan(r["amount_fen"]),
            "negative_alert": end < -0.001,      # R7：负库存告警
        })
    items.sort(key=lambda x: (x["item_kind"], -(x["amount_fen"])))

    # 品目级汇总（同品目多批次合并；单位相同才可加）
    summary = {}
    for it in items:
        k = (it["item_kind"], it["drug_id"], it["herb_id"], it["unit"])
        s = summary.setdefault(k, {"item_kind": it["item_kind"], "item_name": it["item_name"],
                                   "unit": it["unit"], "opening_qty": 0.0, "in_qty": 0.0,
                                   "out_qty": 0.0, "return_qty": 0.0, "loss_qty": 0.0,
                                   "check_qty": 0.0, "ending_qty": 0.0, "amount_fen": 0})
        for f in ("opening_qty", "in_qty", "out_qty", "return_qty", "loss_qty",
                  "check_qty", "ending_qty"):
            s[f] = round(s[f] + it[f], 3)
        s["amount_fen"] += it["amount_fen"]
    return {
        "criteria": _criteria("stock", d_from, d_to,
                              {"note": "拆零成对抵消不计入六段式；盘点差异单独列示"}),
        "summary": list(summary.values()),
        "batches": items,
    }


# ============================================================
# F122 处方统计
# ============================================================

@route("/api/report/prescription_stats", "GET", perm="report.view", module=MODULE,
       action="report.prescription_stats")
def prescription_stats(ctx, body, q):
    _require_view("v_prescription_stats")
    d_from, d_to = _range(q)
    valid = ("CONFIRMED", "DISPENSED", "PARTIAL_RETURNED", "FULLY_RETURNED")
    ph = ",".join("?" * len(valid))
    by_type = db.query("""
        SELECT prescription_type, COUNT(*) AS c, COALESCE(SUM(amount_fen),0) AS amt
        FROM v_prescription_stats
        WHERE status IN (%s) AND biz_date >= ? AND biz_date <= ?
        GROUP BY prescription_type
    """ % ph, tuple(valid) + (d_from, d_to))
    total = db.query_one("""
        SELECT COUNT(*) AS c, COALESCE(SUM(amount_fen),0) AS amt,
               SUM(has_antibiotic) AS ab, SUM(has_injection) AS inj,
               COALESCE(SUM(herb_line_count),0) AS herb_lines,
               COALESCE(SUM(doses),0) AS doses
        FROM v_prescription_stats
        WHERE status IN (%s) AND biz_date >= ? AND biz_date <= ?
    """ % ph, tuple(valid) + (d_from, d_to))
    voided = db.query_one("""
        SELECT COUNT(*) AS c FROM v_prescription_stats
        WHERE status='VOID' AND biz_date >= ? AND biz_date <= ?
    """, (d_from, d_to))

    n = int(total["c"] or 0)
    amt = _fen(total["amt"])
    herb_rx = [r for r in by_type if r["prescription_type"] == "HERB"]
    herb_count = sum(int(r["c"] or 0) for r in herb_rx)
    avg_herb = (round(float(total["herb_lines"] or 0) / herb_count, 2) if herb_count else None)
    avg_doses = (round(float(total["doses"] or 0) / herb_count, 2) if herb_count else None)

    return {
        "criteria": _criteria("prescription", d_from, d_to),
        "summary": {
            "valid_count": n, "void_count": int(voided["c"] or 0),
            "amount_fen": amt, "amount_yuan": _yuan(amt),
            "avg_amount_fen": int(money.round_half_up(money.Decimal(amt) / money.Decimal(n)))
                              if n else None,
            "antibiotic_rate": _pct(int(total["ab"] or 0), n),
            "injection_rate": _pct(int(total["inj"] or 0), n),
            "avg_herb_lines": avg_herb, "avg_doses": avg_doses,
        },
        "by_type": [{"prescription_type": r["prescription_type"],
                     "count": int(r["c"] or 0),
                     "count_pct": _pct(int(r["c"] or 0), n),
                     "amount_fen": _fen(r["amt"]), "amount_yuan": _yuan(r["amt"])}
                    for r in by_type],
    }


# ============================================================
# F123 患者与疾病谱
# ============================================================

@route("/api/report/patient_stats", "GET", perm="report.view", module=MODULE,
       action="report.patient_stats")
def patient_stats(ctx, body, q):
    _require_view("v_patient_stats")
    _require_view("v_diagnosis_stats")
    d_from, d_to = _range(q)
    top_n = min(int(q.get("top") or 20), 100)

    new_patients = db.query_one("""
        SELECT COUNT(*) AS c FROM v_patient_stats WHERE biz_date >= ? AND biz_date <= ?
    """, (d_from, d_to))

    v = db.query_one("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN prior_completed = 0 THEN 1 ELSE 0 END) AS first_visit
        FROM v_visit_stats WHERE status='COMPLETED' AND biz_date >= ? AND biz_date <= ?
    """, (d_from, d_to))
    total_v = int(v["total"] or 0)
    first_v = int(v["first_visit"] or 0)

    # 有就诊的患者 → 年龄分档与频次
    pats = db.query("""
        SELECT p.patient_id, p.gender, p.birth_date, p.visit_count
        FROM v_patient_stats p
        WHERE EXISTS (SELECT 1 FROM v_visit_stats v
                      WHERE v.patient_id = p.patient_id AND v.status='COMPLETED'
                        AND v.biz_date >= ? AND v.biz_date <= ?)
    """, (d_from, d_to))
    age_buckets = {"0-14": 0, "15-44": 0, "45-64": 0, "65+": 0, "未知": 0}
    freq = {"1 次": 0, "2-3 次": 0, "4 次以上": 0}
    for p in pats:
        age = tu.calc_age(p["birth_date"])
        if age is None:
            age_buckets["未知"] += 1
        else:
            y = age[0]
            k = "0-14" if y <= 14 else ("15-44" if y <= 44 else ("65+" if y >= 65 else "45-64"))
            age_buckets[k] += 1
        c = int(p["visit_count"] or 0)
        freq["1 次" if c <= 1 else ("2-3 次" if c <= 3 else "4 次以上")] += 1

    dx_west = db.query("""
        SELECT COALESCE(icd10_code,'') AS code, COALESCE(icd10_name,'未命名') AS name,
               COUNT(DISTINCT visit_id) AS c
        FROM v_diagnosis_stats
        WHERE diag_type='WEST' AND COALESCE(confidence,'') <> 'EXCLUDED'
          AND biz_date >= ? AND biz_date <= ?
        GROUP BY code, name ORDER BY c DESC, name LIMIT ?
    """, (d_from, d_to, top_n))
    dx_tcm = db.query("""
        SELECT COALESCE(tcm_code,'') AS code, COALESCE(tcm_name,'未命名') AS name,
               COUNT(DISTINCT visit_id) AS c
        FROM v_diagnosis_stats
        WHERE diag_type='TCM' AND COALESCE(confidence,'') <> 'EXCLUDED'
          AND biz_date >= ? AND biz_date <= ?
        GROUP BY code, name ORDER BY c DESC, name LIMIT ?
    """, (d_from, d_to, top_n))

    return {
        "criteria": _criteria("diagnosis", d_from, d_to, {"top": top_n}),
        "summary": {
            "new_patient_count": int(new_patients["c"] or 0),
            "visit_count": total_v, "first_visit": first_v,
            "revisit_rate": _pct(total_v - first_v, total_v),
            "active_patient_count": len(pats),
        },
        "age_buckets": [{"bucket": k, "count": v2} for k, v2 in age_buckets.items()],
        "visit_freq": [{"bucket": k, "count": v2} for k, v2 in freq.items()],
        "disease_top_west": [{"code": r["code"], "name": r["name"],
                              "patient_visits": int(r["c"] or 0)} for r in dx_west],
        "syndrome_top_tcm": [{"code": r["code"], "name": r["name"],
                              "patient_visits": int(r["c"] or 0)} for r in dx_tcm],
    }


# ============================================================
# F124 导出与打印数据
# ============================================================

def _rows_for(kind, q):
    """把各报表转成 (表头, 行列表)，供导出与打印共用。"""
    if kind == "revenue":
        d = revenue_daily(ctx_dummy(), {}, q)
        head = ["营业日", "渠道", "类型", "金额(分)", "金额(元)", "笔数"]
        rows = []
        for r in d["by_day"]:
            rows.append([r["biz_date"], "合计", "收款", r["paid_amount_fen"],
                         money.fen_to_yuan(r["paid_amount_fen"]), r["pay_count"]])
            rows.append([r["biz_date"], "合计", "退款", -r["refund_amount_fen"],
                         money.fen_to_yuan(-r["refund_amount_fen"]), ""])
            rows.append([r["biz_date"], "合计", "净营收", r["net_amount_fen"],
                         money.fen_to_yuan(r["net_amount_fen"]), ""])
        for r in d["by_channel"]:
            rows.append([d["criteria"]["from"] + "~" + d["criteria"]["to"], r["channel"],
                         "收款", r["amount_fen"], r["amount_yuan"], r["count"]])
        return head, rows, d["criteria"]
    if kind == "visit":
        d = visit_stats(ctx_dummy(), {}, q)
        head = ["期间", "就诊人次", "初诊", "复诊"]
        rows = [[r["period"], r["total"], r["first_visit"], r["revisit"]] for r in d["trend"]]
        return head, rows, d["criteria"]
    if kind == "doctor":
        d = doctor_workload(ctx_dummy(), {}, q)
        head = ["医生", "接诊人次", "开方数", "处置执行数", "营收(分)", "营收(元)"]
        rows = [[r["doctor_name"], r["visit_count"], r["rx_count"], r["treatment_count"],
                 r["revenue_fen"], r["revenue_yuan"]] for r in d["items"]]
        return head, rows, d["criteria"]
    if kind == "stock":
        d = stock_ledger(ctx_dummy(), {}, q)
        head = ["类别", "品目", "规格", "单位", "批次", "效期", "期初", "入库", "出库",
                "退药", "报损", "盘点", "期末", "金额(分)", "金额(元)"]
        rows = [[r["item_kind"], r["item_name"], r["spec"] or "", r["unit"] or "",
                 r["batch_no"] or "", r["expiry_date"] or "", r["opening_qty"], r["in_qty"],
                 r["out_qty"], r["return_qty"], r["loss_qty"], r["check_qty"],
                 r["ending_qty"], r["amount_fen"], r["amount_yuan"]] for r in d["batches"]]
        return head, rows, d["criteria"]
    if kind == "prescription":
        d = prescription_stats(ctx_dummy(), {}, q)
        head = ["处方类型", "数量", "占比%", "金额(分)", "金额(元)"]
        rows = [[r["prescription_type"], r["count"], r["count_pct"], r["amount_fen"],
                 r["amount_yuan"]] for r in d["by_type"]]
        rows.append(["作废", d["summary"]["void_count"], "", "", ""])
        return head, rows, d["criteria"]
    if kind == "patient":
        d = patient_stats(ctx_dummy(), {}, q)
        head = ["榜种", "编码", "名称", "就诊人次"]
        rows = []
        for r in d["disease_top_west"]:
            rows.append(["西医疾病谱", r["code"], r["name"], r["patient_visits"]])
        for r in d["syndrome_top_tcm"]:
            rows.append(["中医证型", r["code"], r["name"], r["patient_visits"]])
        return head, rows, d["criteria"]
    raise AppError(DATA_FORMAT, "不支持的报表类型：%s" % kind, retryable=False, http_status=400)


def ctx_dummy():
    """导出/打印内部复用查询函数时的只读上下文（查询本身不写审计）。"""
    from core.context import get_ctx
    return get_ctx()


@route("/api/report/export", "POST", perm="report.export", module=MODULE,
       action="report.export")
def export(ctx, body, q):
    """F124 导出 CSV（UTF-8 带 BOM，Excel 可直接打开），写高风险审计。"""
    kind = (body.get("kind") or body.get("report") or "").lower()
    require(kind in REPORT_KINDS, "报表类型应为 %s" % "/".join(REPORT_KINDS))
    head, rows, crit = _rows_for(kind, body)
    if len(rows) > MAX_EXPORT_ROWS:
        raise AppError("E3081", "导出行数 %d 超过上限 %d，请缩小查询区间"
                       % (len(rows), MAX_EXPORT_ROWS), retryable=False, http_status=400)

    from core.logging_setup import log_dir
    base = log_dir() or "."
    out_dir = os.path.join(base, "reports")
    os.makedirs(out_dir, exist_ok=True)
    stamp = tu.now().strftime("%Y%m%d_%H%M%S")
    fname = "%s_%s_%s_%s.csv" % (kind, crit["from"], crit["to"], stamp)
    path = os.path.join(out_dir, fname)
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:   # R1：带 BOM
            w = csv.writer(f)
            w.writerow(head)
            w.writerows(rows)
            w.writerow([])
            w.writerow(["口径说明", crit["basis"]])
            w.writerow(["查询区间", "%s ~ %s" % (crit["from"], crit["to"])])
            w.writerow(["生成时间", crit["generated_at"]])
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise AppError("E1002", "导出文件写入失败：%s" % exc, retryable=True, http_status=507)

    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "report.export", "report_snapshot", None,
                              detail={"kind": kind, "rows": len(rows), "file": fname})
        audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "report", kind,
                              "经营数据导出：" + fname,
                              {"rows": len(rows), "from": crit["from"], "to": crit["to"],
                               "file": path})
    return {"kind": kind, "file": path, "file_name": fname, "rows": len(rows),
            "criteria": crit}


@route("/api/report/print_data", "GET", perm="report.view", module=MODULE,
       action="report.print_data")
def print_data(ctx, body, q):
    """F124 打印数据：结构化表头/行/口径/页脚，打印硬件交互归 M10。"""
    kind = (q.get("kind") or "").lower()
    require(kind in REPORT_KINDS, "报表类型应为 %s" % "/".join(REPORT_KINDS))
    head, rows, crit = _rows_for(kind, q)
    return {"kind": kind, "title": REPORT_TITLES.get(kind, kind), "header": head,
            "rows": rows, "criteria": crit,
            "footer": "口径：%s｜区间：%s ~ %s｜生成时间：%s"
                      % (crit["basis"], crit["from"], crit["to"], crit["generated_at"])}


REPORT_TITLES = {
    "revenue": "日营收与班结报表",
    "visit": "门诊量统计",
    "doctor": "医生工作量统计",
    "stock": "药品进销存月报",
    "prescription": "处方统计",
    "patient": "患者与疾病谱统计",
}


@route("/api/report/definitions", "GET", perm="report.view", module=MODULE,
       action="report.definitions")
def definitions(ctx, body, q):
    return {"items": db.query("SELECT * FROM report_definition WHERE deleted_at IS NULL "
                              "ORDER BY id")}
