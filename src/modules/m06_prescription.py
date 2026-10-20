# -*- coding: utf-8 -*-
"""M06 处方与用药（F32-F39 / F110-F113 / F127）。

表所有权：drug / herb / herb_compatibility / prescription / prescription_status_log /
         prescription_item / herb_prescription_item / prescription_pricing /
         prescription_rule_hit / skin_test / prescription_template /
         prescription_template_item / medication_guide
状态机：03 §2.4 —— DRAFT→PENDING→CONFIRMED→DISPENSED；已发药不可作废（E3008）
计价：03 §3.1 成药 / §3.2 饮片；中药特殊规则见 03 §8
边界：过敏数据来自 M03（只读，经 m03_record.list_allergies 服务接口），接口失败按"未知"降级
"""

from decimal import Decimal

from core import db, audit, config, timeutil as tu, ids
from core import money
from core.api import route
from core.errors import (require, require_keys, AppError, conflict, not_found,
                         BIZ_STATE_INVALID, BIZ_SKIN_TEST_REQUIRED, BIZ_DISPENSED_NOT_VOIDABLE,
                         BIZ_ALREADY_CONFIRMED, BIZ_OVER_DOSE)

# ---------- 状态 ----------
DRAFT = "DRAFT"
PENDING = "PENDING"
CONFIRMED = "CONFIRMED"
DISPENSED = "DISPENSED"
PARTIAL_RETURNED = "PARTIAL_RETURNED"
FULLY_RETURNED = "FULLY_RETURNED"
VOID = "VOID"

TRANSITIONS = {
    DRAFT:            [PENDING, VOID],
    PENDING:          [CONFIRMED, VOID],
    CONFIRMED:        [DISPENSED, VOID],
    DISPENSED:        [PARTIAL_RETURNED, FULLY_RETURNED],
    PARTIAL_RETURNED: [FULLY_RETURNED],
    FULLY_RETURNED:   [],
    VOID:             [],
}

EDITABLE = (DRAFT,)

# 过敏类别 → 中文关键词（F35 R2：同类匹配，宁可多报不可漏报）
ALLERGY_CLASS_LABEL = {
    "PENICILLIN":    "青霉素",
    "CEPHALOSPORIN": "头孢",
    "SULFA":         "磺胺",
    "NSAID":         "非甾体",
    "MACROLIDE":     "大环内酯",
    "QUINOLONE":     "喹诺酮",
}

# 规则类型 → 严重度
SEV_BLOCK = "BLOCK"
SEV_FORCE = "FORCE_CONFIRM"
SEV_WARN = "WARN"


def transition(conn, rx_id, to_status, reason=None, ctx=None, extra=None):
    """状态机唯一入口（03 §2.1）。非法转换 → 拒绝 + 高风险审计。"""
    row = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
    if row is None:
        raise not_found("处方不存在")
    frm = row["status"]
    # 先判"已发药不可作废"（E3008），语义优先于通用的非法状态转换提示
    if to_status == VOID and row["dispensed_at"]:
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "prescription", rx_id,
                              "已发药处方尝试作废，已拒绝：%s" % row["prescription_no"])
        raise conflict("已发药的处方不能作废，请走退药流程（M07 F46）",
                       code=BIZ_DISPENSED_NOT_VOIDABLE)
    if to_status not in TRANSITIONS.get(frm, []):
        audit.audit_high_risk(conn, "ILLEGAL_TRANSITION", "prescription", rx_id,
                              "非法状态转换 %s→%s" % (frm, to_status))
        raise conflict("处方状态不允许从 %s 转换到 %s" % (frm, to_status), code=BIZ_STATE_INVALID)
    sets = ["status=?", "updated_at=?", "updated_by=?", "version=version+1"]
    params = [to_status, tu.now_str(), (ctx.user_id if ctx else 0)]
    for k, v in (extra or {}).items():
        sets.append("%s=?" % k)
        params.append(v)
    params.append(rx_id)
    db.execute(conn, "UPDATE prescription SET %s WHERE id=?" % ",".join(sets), tuple(params))
    db.execute(conn, """
        INSERT INTO prescription_status_log (prescription_id, from_status, to_status, reason,
                                             operator_id, created_at)
        VALUES (?,?,?,?,?,?)
    """, (rx_id, frm, to_status, reason, (ctx.user_id if ctx else 0), tu.now_str()))
    audit.audit_data_change(conn, "prescription", rx_id, "status", frm, to_status, reason)
    return frm, to_status


def _load(rx_id, editable=True):
    row = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
    if row is None:
        raise not_found("处方不存在")
    if editable and row["status"] not in EDITABLE:
        raise conflict("处方已提交（%s），不能修改明细；需变更请作废后重开" % row["status"],
                       code=BIZ_ALREADY_CONFIRMED)
    return row


def _hit(conn, rx_id, rule_type, severity, entity_type=None, entity_id=None, entity_name=None,
         limit_value=None, actual_value=None, detail=None, action=None, reason=None, ctx=None):
    db.execute(conn, """
        INSERT INTO prescription_rule_hit (prescription_id, rule_type, severity, entity_type,
            entity_id, entity_name, limit_value, actual_value, detail_json, doctor_action,
            reason, created_at, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (rx_id, rule_type, severity, entity_type, entity_id, entity_name,
          (None if limit_value is None else str(limit_value)),
          (None if actual_value is None else str(actual_value)),
          audit.dumps(detail or {}), action, reason, tu.now_str(),
          (ctx.user_id if ctx else 0)))


# ---------- 过敏（F35.1）：数据来自 M03 ----------

def _allergies(patient_id):
    """经 M03 服务接口取过敏史。接口失败 → 抛异常，由调用方按"未知"降级（F35 R3）。"""
    from modules import m03_record
    return m03_record.list_allergies(patient_id)


def _match_allergy(allergen, drug):
    """过敏原文本 vs 药品：同名 / 同类（F35 R2，宁可多报）。"""
    if not allergen:
        return False
    a = allergen.strip()
    if not a:
        return False
    names = [drug.get("generic_name") or "", drug.get("trade_name") or "",
             drug.get("alias") or ""]
    for n in names:
        if n and (a in n or n in a):
            return True
    cls = drug.get("allergy_class")
    if cls:
        label = ALLERGY_CLASS_LABEL.get(cls, cls)
        if label and (a in label or label in a):
            return True
        # 过敏原写的是"青霉素类"，药品类别也是青霉素 → 命中
        if a.rstrip("类") in label or label.rstrip("类") in a:
            return True
    return False


# ---------- 规则校验（F35 / F113）----------

def _validate(conn, rx, ctx, body=None):
    """返回 (block_hits, warn_hits, need_confirm)。

    - block_hits：强制拦截项，非空即拒绝
    - warn_hits：提醒项，写入命中记录但不阻断
    - need_confirm：需二次确认（毒性饮片超量）
    """
    body = body or {}
    rx_id = rx["id"]
    blocks, warns = [], []
    need_confirm = False

    # ---- 过敏（F35.1）----
    try:
        allergies = _allergies(rx["patient_id"])
        allergy_unknown = False
    except Exception as exc:  # 接口失败 → 向"更谨慎"降级（F35 R3 / AC4）
        allergies = []
        allergy_unknown = True
        _hit(conn, rx_id, "ALLERGY", SEV_BLOCK, detail={"error": str(exc)},
             reason="过敏史接口不可用", ctx=ctx)

    items = db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                     "AND deleted_at IS NULL", (rx_id,))
    for it in items:
        drug = db.query_one("SELECT * FROM drug WHERE id=?", (it["drug_id"],))
        if drug is None:
            continue
        if allergy_unknown:
            # 未知 = 要求人工确认，绝不视为"无过敏"
            if not body.get("allergy_ack"):
                blocks.append({"rule": "ALLERGY", "code": "E3021",
                               "message": "过敏史未知（接口不可用），需人工确认后方可提交"})
            continue
        for al in allergies:
            if (al.get("allergen_type") or "DRUG") != "DRUG":
                continue
            if _match_allergy(al.get("allergen"), drug):
                hit = {"rule": "ALLERGY", "code": "E3020", "entity": it["drug_name_snap"],
                       "message": "患者过敏史命中：%s（过敏原 %s）"
                                  % (it["drug_name_snap"], al.get("allergen"))}
                if body.get("allergy_override") and (body.get("override_reason") or "").strip():
                    _hit(conn, rx_id, "ALLERGY", SEV_BLOCK, "DRUG", it["drug_id"],
                         it["drug_name_snap"], al.get("allergen"), None,
                         {"allergen": al.get("allergen")}, "FORCED",
                         body.get("override_reason"), ctx)
                    audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "prescription", rx_id,
                                          "过敏拦截被强制放行：%s / 理由：%s"
                                          % (it["drug_name_snap"], body.get("override_reason")))
                else:
                    _hit(conn, rx_id, "ALLERGY", SEV_BLOCK, "DRUG", it["drug_id"],
                         it["drug_name_snap"], al.get("allergen"), None,
                         {"allergen": al.get("allergen")}, None, None, ctx)
                    blocks.append(hit)
                break

    # ---- 皮试前置（F35.2）：确认时才强制，提交时提醒 ----
    need_test = [it for it in items if it["need_skin_test"]]
    if need_test:
        for it in need_test:
            st = db.query_one("SELECT * FROM skin_test WHERE patient_id=? AND drug_id=? "
                              "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
                              (rx["patient_id"], it["drug_id"]))
            if st is None or st["result"] != "NEGATIVE":
                msg = "药品 %s 需皮试：%s" % (it["drug_name_snap"],
                                            "未做皮试" if st is None
                                            else "皮试结果为阳性" if st["result"] == "POSITIVE"
                                            else "皮试结果待判读")
                _hit(conn, rx_id, "SKIN_TEST", SEV_BLOCK, "DRUG", it["drug_id"],
                     it["drug_name_snap"], None, st["result"] if st else None,
                     {}, None, None, ctx)
                blocks.append({"rule": "SKIN_TEST", "code": "E3035", "message": msg})

    # ---- 成药剂量与重复（F35.3 / F35.4）----
    seen = {}
    for it in items:
        key = it["drug_id"]
        if key in seen:
            _hit(conn, rx_id, "DUPLICATE", SEV_WARN, "DRUG", key, it["drug_name_snap"],
                 detail={"line_no": it["line_no"]}, ctx=ctx)
            warns.append({"rule": "DUPLICATE", "message": "重复用药：%s 出现多次" % it["drug_name_snap"]})
        seen[key] = it
    for it in items:
        drug = db.query_one("SELECT * FROM drug WHERE id=?", (it["drug_id"],))
        if not drug:
            continue
        if drug["max_single_dose"] and it.get("dosage"):
            try:
                dv = float(str(it["dosage"]).strip().split()[0])
            except (ValueError, IndexError):
                dv = None
            if dv is not None and dv > float(drug["max_single_dose"]):
                _hit(conn, rx_id, "DOSE", SEV_WARN, "DRUG", it["drug_id"], it["drug_name_snap"],
                     drug["max_single_dose"], dv, {"unit": drug["dose_unit"]},
                     body.get("dose_ack") and "ACCEPTED" or None, ctx=ctx)
                warns.append({"rule": "DOSE",
                              "message": "%s 单次剂量 %s 超过上限 %s（提醒，不阻断）"
                                         % (it["drug_name_snap"], dv, drug["max_single_dose"])})

    # ---- 饮片规则（F113）----
    herbs = db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                     "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
    if rx["prescription_type"] in ("HERB", "GRANULE") or herbs:
        # 必填项（F113 R2）
        missing = []
        if not rx.get("doses"):
            missing.append("付数")
        if not (rx.get("decoction_method") or "").strip():
            missing.append("煎法")
        if not (rx.get("administration") or "").strip():
            missing.append("服法")
        if missing:
            _hit(conn, rx_id, "REQUIRED", SEV_BLOCK, detail={"missing": missing}, ctx=ctx)
            blocks.append({"rule": "REQUIRED", "code": "E3042",
                           "message": "饮片处方必填项缺失：%s" % "、".join(missing)})

        # 重复药味（强制拦截，属数据错误）
        hseen = {}
        for h in herbs:
            if h["herb_id"] in hseen:
                _hit(conn, rx_id, "DUPLICATE", SEV_BLOCK, "HERB", h["herb_id"],
                     h["herb_name_snap"], detail={"line_no": h["line_no"]}, ctx=ctx)
                blocks.append({"rule": "DUPLICATE", "code": "E3045",
                               "message": "重复药味：%s 出现多次" % h["herb_name_snap"]})
            hseen[h["herb_id"]] = h

        # 毒性饮片超量（强制二次确认）与剂量上限（仅提醒）
        for h in herbs:
            herb = db.query_one("SELECT * FROM herb WHERE id=?", (h["herb_id"],))
            if not herb:
                continue
            dose_g = float(h["dose_g"] or 0)
            limit = herb.get("toxic_limit_g") if herb.get("is_toxic") else herb.get("max_dose_g")
            if herb.get("is_toxic"):
                tl = float(herb.get("toxic_limit_g") or herb.get("max_dose_g") or 0)
                if tl and dose_g > tl:
                    confirmed = body.get("toxic_confirm")
                    reason = (body.get("toxic_reason") or "").strip()
                    _hit(conn, rx_id, "TOXIC", SEV_FORCE, "HERB", h["herb_id"],
                         h["herb_name_snap"], tl, dose_g,
                         {"toxic_confirm": confirmed, "reason": reason},
                         "FORCED" if confirmed else None, reason or None, ctx)
                    if confirmed and reason:
                        audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "prescription", rx_id,
                                              "毒性饮片超量放行：%s %sg > 限量 %sg；理由：%s"
                                              % (h["herb_name_snap"], dose_g, tl, reason))
                    else:
                        need_confirm = True
                        blocks.append({
                            "rule": "TOXIC", "code": "E3040",
                            "message": "毒性饮片 %s 剂量 %sg 超过限量 %sg，需二次确认并填写理由"
                                       % (h["herb_name_snap"], dose_g, tl),
                            "limit": tl, "actual": dose_g})
            elif herb.get("max_dose_g"):
                md = float(herb["max_dose_g"])
                if dose_g > md:
                    _hit(conn, rx_id, "DOSE", SEV_WARN, "HERB", h["herb_id"],
                         h["herb_name_snap"], md, dose_g, {},
                         "ACCEPTED" if body.get("dose_ack") else None, ctx=ctx)
                    warns.append({"rule": "DOSE",
                                  "message": "%s %sg 超过常用上限 %sg（提醒，不阻断）"
                                             % (h["herb_name_snap"], dose_g, md)})

        # 配伍禁忌（只提醒，F113 R4）
        names = [h["herb_name_snap"] for h in herbs]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                row = db.query_one("SELECT * FROM herb_compatibility WHERE is_active=1 "
                                   "AND deleted_at IS NULL AND ((herb_a=? AND herb_b=?) "
                                   "OR (herb_a=? AND herb_b=?))", (a, b, b, a))
                if row:
                    _hit(conn, rx_id, "COMPATIBILITY", SEV_WARN, "HERB", None,
                         "%s + %s" % (a, b), None, None,
                         {"taboo_type": row["taboo_type"]},
                         "ACCEPTED" if body.get("compat_ack") else None, ctx=ctx)
                    warns.append({"rule": "COMPATIBILITY", "code": "E3041",
                                  "message": "配伍禁忌（%s）：%s 与 %s 同方，仅提醒不阻断"
                                             % (row["taboo_type"], a, b)})

        # 妊娠禁忌（提醒）
        for h in herbs:
            herb = db.query_one("SELECT pregnancy_flag FROM herb WHERE id=?", (h["herb_id"],))
            if herb and herb.get("pregnancy_flag"):
                _hit(conn, rx_id, "PREGNANCY", SEV_WARN, "HERB", h["herb_id"],
                     h["herb_name_snap"], None, herb["pregnancy_flag"], ctx=ctx)
                warns.append({"rule": "PREGNANCY", "code": "E3046",
                              "message": "%s 妊娠%s，请确认患者情况"
                                         % (h["herb_name_snap"],
                                            "禁用" if herb["pregnancy_flag"] == "CONTRAINDICATED"
                                            else "慎用")})

        # 味数异常（提醒）
        max_cnt = config.get_int("herb.max_herb_count", 20)
        if max_cnt and len(herbs) > max_cnt:
            _hit(conn, rx_id, "HERB_COUNT", SEV_WARN, detail={"count": len(herbs),
                                                              "threshold": max_cnt}, ctx=ctx)
            warns.append({"rule": "HERB_COUNT",
                          "message": "本方共 %d 味，超过提醒阈值 %d" % (len(herbs), max_cnt)})

    return blocks, warns, need_confirm


# ---------- 计价（F112）----------

def _price(conn, rx, ctx):
    """全量重算（F112 R3）。返回计价明细 dict。"""
    rx_id = rx["id"]
    # 付数只对中药（饮片/颗粒）有意义；成药处方保持 NULL，
    # 否则会被 F122「平均付数」统计带偏（这是曾经踩到的坑）。
    is_herb_rx = rx["prescription_type"] in ("HERB", "GRANULE")
    doses = int(rx.get("doses") or config.get_int("herb.default_doses", 7)) \
        if (is_herb_rx or rx.get("doses")) else None

    drug_sub = 0
    exact_drug = Decimal(0)
    for it in db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                       "AND deleted_at IS NULL", (rx_id,)):
        amt = money.line_amount_fen(it["unit_price_fen"] or 0, it["quantity"] or 0)
        exact_drug += Decimal(it["unit_price_fen"] or 0) * Decimal(str(it["quantity"] or 0))
        if amt != it["line_amount_fen"]:
            db.execute(conn, "UPDATE prescription_item SET line_amount_fen=? WHERE id=?",
                       (amt, it["id"]))
        drug_sub += amt

    herb_sub = 0
    exact_herb = Decimal(0)
    herbs = db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                     "AND deleted_at IS NULL", (rx_id,))
    for h in herbs:
        price = h["price_milli_per_g"] or 0
        if price <= 0:
            raise conflict("饮片 %s 未定价，不能计价（不允许按 0 计价）" % h["herb_name_snap"],
                           code="E3044")
        amt = money.herb_line_amount_fen(price, h["dose_g"] or 0, doses)
        exact_herb += Decimal(price) * Decimal(str(h["dose_g"] or 0)) * Decimal(doses) / Decimal(10)
        if amt != h["line_amount_fen"]:
            db.execute(conn, "UPDATE herb_prescription_item SET line_amount_fen=? WHERE id=?",
                       (amt, h["id"]))
        herb_sub += amt

    decoction_fee = 0
    mode = "NONE"
    if rx.get("is_decoction"):
        per_dose = config.get_int("herb.decoct_fee_per_dose", 0)
        per_rx = config.get_int("herb.decoct_fee_per_rx", 0)
        if per_dose > 0:
            decoction_fee = per_dose * doses
            mode = "PER_DOSE"
        else:
            decoction_fee = per_rx
            mode = "PER_RX"

    package_fee = config.get_int("herb.package_fee", 0) if rx["prescription_type"] == "HERB" else 0
    total = drug_sub + herb_sub + decoction_fee + package_fee
    # 尾差 = 已舍入之和 − 精确总额舍入值（显式记录，不隐式吸收，F112 R6）
    exact_total = money.round_half_up(exact_drug + exact_herb) + decoction_fee + package_fee
    tail = total - exact_total

    db.execute(conn, "DELETE FROM prescription_pricing WHERE prescription_id=?", (rx_id,))
    db.execute(conn, """
        INSERT INTO prescription_pricing (prescription_id, drug_subtotal_fen, herb_subtotal_fen,
            decoction_fee_fen, package_fee_fen, total_amount_fen, tail_diff_fen, doses_snapshot,
            decoction_mode, priced_at, priced_by, created_at, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (rx_id, drug_sub, herb_sub, decoction_fee, package_fee, total, tail, doses, mode,
          tu.now_str(), (ctx.user_id if ctx else 0), tu.now_str(), (ctx.user_id if ctx else 0)))
    # 成药处方不写 doses（保持 NULL），避免污染中药口径统计
    if doses is None:
        db.execute(conn, "UPDATE prescription SET total_amount=?, updated_at=?, updated_by=? "
                         "WHERE id=?", (total, tu.now_str(),
                                        (ctx.user_id if ctx else 0), rx_id))
    else:
        db.execute(conn, "UPDATE prescription SET total_amount=?, doses=?, updated_at=?, "
                         "updated_by=? WHERE id=?",
                   (total, doses, tu.now_str(), (ctx.user_id if ctx else 0), rx_id))
    return {
        "drug_subtotal_fen": drug_sub,
        "herb_subtotal_fen": herb_sub,
        "decoction_fee_fen": decoction_fee,
        "package_fee_fen": package_fee,
        "total_amount_fen": total,
        "tail_diff_fen": tail,
        "doses": doses,
        "decoction_mode": mode,
        "total_yuan": money.fen_to_yuan(total),
    }


# ---------- F32/F33 药品资料与搜索 ----------

@route("/api/drug/search", "GET", perm="drug.view", module="M06", action="drug.search")
def drug_search(ctx, body, q):
    """F33 药品搜索：通用名 / 商品名 / 别名 / 拼音。"""
    kw = (q.get("keyword") or "").strip()
    if not kw:
        return {"items": []}
    like = "%%%s%%" % kw
    return {"items": db.query(
        "SELECT * FROM drug WHERE deleted_at IS NULL AND is_active=1 AND "
        "(generic_name LIKE ? OR trade_name LIKE ? OR alias LIKE ? OR pinyin LIKE ? "
        "OR drug_code LIKE ?) ORDER BY generic_name LIMIT 50",
        (like, like, like, like, like))}


@route("/api/drug/create", "POST", perm="drug.manage", module="M06", action="drug.create")
def drug_create(ctx, body, q):
    """F32 药品资料建档。"""
    require_keys(body, ["drug_code", "generic_name"])
    if body.get("price_fen") is None and body.get("price_yuan") is not None:
        body["price_fen"] = money.to_fen(body["price_yuan"])
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "drug.create", "drug", None)
        exists = db.query_one("SELECT id FROM drug WHERE drug_code=?", (body["drug_code"],))
        require(exists is None, "药品编码已存在：%s" % body["drug_code"], code="E2004")
        cur = db.execute(conn, """
            INSERT INTO drug (drug_code, generic_name, trade_name, alias, pinyin, spec,
                dosage_form, unit, pack_qty, manufacturer, approval_no, price_fen, drug_class,
                allergy_class, is_antibiotic, is_injection, need_skin_test, skin_test_type,
                max_single_dose, max_daily_dose, dose_unit, default_usage, default_freq,
                default_route, is_active, remark, barcode, created_at, created_by, updated_at,
                updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body["drug_code"].strip(), body["generic_name"].strip(), body.get("trade_name"),
              body.get("alias"), body.get("pinyin"), body.get("spec"), body.get("dosage_form"),
              body.get("unit", "盒"), body.get("pack_qty", 1), body.get("manufacturer"),
              body.get("approval_no"), body.get("price_fen", 0), body.get("drug_class"),
              body.get("allergy_class"), body.get("is_antibiotic", 0), body.get("is_injection", 0),
              body.get("need_skin_test", 0), body.get("skin_test_type"),
              body.get("max_single_dose"), body.get("max_daily_dose"), body.get("dose_unit"),
              body.get("default_usage"), body.get("default_freq"), body.get("default_route"),
              body.get("is_active", 1), body.get("remark"), body.get("barcode"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        did = cur.lastrowid
    return {"id": did, "drug_code": body["drug_code"]}


@route("/api/drug/update", "POST", perm="drug.manage", module="M06", action="drug.update")
def drug_update(ctx, body, q):
    did = body.get("id")
    require(did, "缺少药品 id")
    row = db.query_one("SELECT * FROM drug WHERE id=? AND deleted_at IS NULL", (did,))
    if row is None:
        raise not_found("药品不存在")
    fields = ["generic_name", "trade_name", "alias", "pinyin", "spec", "dosage_form", "unit",
              "manufacturer", "price_fen", "drug_class", "allergy_class", "is_antibiotic",
              "is_injection", "need_skin_test", "max_single_dose", "max_daily_dose",
              "default_usage", "default_freq", "default_route", "is_active", "remark",
              "barcode"]
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "drug.update", "drug", did)
        for f in fields:
            if f in body and body[f] != row[f]:
                audit.audit_data_change(conn, "drug", did, f, row[f], body[f], body.get("reason"))
                db.execute(conn, "UPDATE drug SET %s=?, updated_at=?, updated_by=? WHERE id=?"
                           % f, (body[f], tu.now_str(), ctx.user_id, did))
    return {"id": did}


# ---------- F110 饮片资料 ----------

@route("/api/herb/search", "GET", perm="herb.view", module="M06", action="herb.search")
def herb_search(ctx, body, q):
    kw = (q.get("keyword") or "").strip()
    if not kw:
        return {"items": []}
    like = "%%%s%%" % kw
    return {"items": db.query(
        "SELECT * FROM herb WHERE deleted_at IS NULL AND is_active=1 AND "
        "(name LIKE ? OR alias LIKE ? OR pinyin LIKE ? OR herb_code LIKE ?) "
        "ORDER BY name LIMIT 50", (like, like, like, like))}


@route("/api/herb/create", "POST", perm="herb.manage", module="M06", action="herb.create")
def herb_create(ctx, body, q):
    """F110 饮片资料建档。单价可用 price_milli_per_g 或 price_yuan_per_g。"""
    require_keys(body, ["herb_code", "name"])
    price_milli = body.get("price_milli_per_g")
    if price_milli is None and body.get("price_yuan_per_g") is not None:
        price_milli = money.to_milli(body["price_yuan_per_g"])
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "herb.create", "herb", None)
        exists = db.query_one("SELECT id FROM herb WHERE herb_code=?", (body["herb_code"],))
        require(exists is None, "饮片编码已存在：%s" % body["herb_code"], code="E2004")
        cur = db.execute(conn, """
            INSERT INTO herb (herb_code, name, alias, pinyin, nature, flavor, meridian,
                efficacy, indication, price_milli_per_g, usual_dose_g, max_dose_g, is_toxic,
                toxic_limit_g, pregnancy_flag, stock_unit, is_active, remark, barcode,
                created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body["herb_code"].strip(), body["name"].strip(), body.get("alias"),
              body.get("pinyin"), body.get("nature"), body.get("flavor"), body.get("meridian"),
              body.get("efficacy"), body.get("indication"), price_milli or 0,
              body.get("usual_dose_g"), body.get("max_dose_g"), body.get("is_toxic", 0),
              body.get("toxic_limit_g"), body.get("pregnancy_flag"), body.get("stock_unit", "g"),
              body.get("is_active", 1), body.get("remark"), body.get("barcode"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        hid = cur.lastrowid
    return {"id": hid, "herb_code": body["herb_code"]}


@route("/api/herb/update", "POST", perm="herb.manage", module="M06", action="herb.update")
def herb_update(ctx, body, q):
    """调价不追溯已开处方（F112 R7：处方明细存的是开方时价）。"""
    hid = body.get("id")
    require(hid, "缺少饮片 id")
    row = db.query_one("SELECT * FROM herb WHERE id=? AND deleted_at IS NULL", (hid,))
    if row is None:
        raise not_found("饮片不存在")
    fields = ["name", "alias", "pinyin", "nature", "flavor", "meridian", "efficacy",
              "indication", "price_milli_per_g", "usual_dose_g", "max_dose_g", "is_toxic",
              "toxic_limit_g", "pregnancy_flag", "is_active", "remark", "barcode"]
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "herb.update", "herb", hid)
        for f in fields:
            if f in body and body[f] != row[f]:
                audit.audit_data_change(conn, "herb", hid, f, row[f], body[f], body.get("reason"))
                db.execute(conn, "UPDATE herb SET %s=?, updated_at=?, updated_by=? WHERE id=?"
                           % f, (body[f], tu.now_str(), ctx.user_id, hid))
    return {"id": hid}


# ---------- F34 处方开立 ----------

@route("/api/prescription/create", "POST", perm="prescription.create", module="M06",
       action="prescription.create")
def create(ctx, body, q):
    """F34.1 开立处方（DRAFT）。取患者快照（R-M06-9）。"""
    from core import bizlock
    bizlock.require_free("开立处方")        # F128 R8：基础数据导入中不开方
    require(body.get("visit_id"), "缺少 visit_id")
    rx_type = body.get("prescription_type") or "WEST"
    require(rx_type in ("WEST", "HERB", "GRANULE"), "prescription_type 取值非法")
    v = db.query_one("SELECT * FROM visit WHERE id=? AND deleted_at IS NULL",
                     (body["visit_id"],))
    if v is None:
        raise not_found("就诊不存在")
    p = db.query_one("SELECT * FROM patient WHERE id=? AND deleted_at IS NULL",
                     (v["patient_id"],))
    rec = db.query_one("SELECT id FROM record WHERE visit_id=? AND deleted_at IS NULL",
                       (v["id"],))
    diags = []
    if rec:
        diags = db.query("SELECT icd10_name, tcm_name FROM diagnosis WHERE record_id=? "
                         "AND deleted_at IS NULL ORDER BY is_primary DESC, id", (rec["id"],))
    diag_text = "；".join([(d["icd10_name"] or d["tcm_name"] or "") for d in diags if
                          (d["icd10_name"] or d["tcm_name"])])
    syn = None
    if rec:
        s = db.query_one("SELECT syndrome_name FROM tcm_syndrome WHERE record_id=? "
                         "AND deleted_at IS NULL ORDER BY id LIMIT 1", (rec["id"],))
        syn = s["syndrome_name"] if s else None

    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "prescription.create", "prescription", None)
        rx_no = ids.next_no(conn, "PRESCRIPTION")
        now = tu.now_str()
        cur = db.execute(conn, """
            INSERT INTO prescription (prescription_no, visit_id, patient_id, record_id,
                prescription_type, status, version, patient_name_snap, gender_snap,
                birth_date_snap, age_text_snap, diag_summary, tcm_syndrome, doses, is_decoction,
                decoction_method, administration, source, ai_task_id, occurred_at,
                created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,'DRAFT',1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rx_no, v["id"], v["patient_id"], (rec["id"] if rec else None), rx_type,
              (p["name"] if p else None), (p["gender"] if p else None),
              (p["birth_date"] if p else None),
              (tu.age_text(p["birth_date"]) if p and p["birth_date"] else None),
              diag_text or None, syn,
              body.get("doses") or (config.get_int("herb.default_doses", 7)
                                    if rx_type == "HERB" else None),
              body.get("is_decoction", 0), body.get("decoction_method"),
              body.get("administration"), body.get("source", "DOCTOR"), body.get("ai_task_id"),
              now, now, ctx.user_id, now, ctx.user_id))
        rx_id = cur.lastrowid
        db.execute(conn, """
            INSERT INTO prescription_status_log (prescription_id, from_status, to_status, reason,
                                                 operator_id, created_at)
            VALUES (?,NULL,'DRAFT',?,?,?)
        """, (rx_id, "开立处方", ctx.user_id, now))
    return {"id": rx_id, "prescription_no": rx_no, "status": DRAFT}


@route("/api/prescription/save/items", "POST", perm="prescription.edit", module="M06",
       action="prescription.save.items")
def save_items(ctx, body, q):
    """F34.2 成药明细（replace 全量覆盖）。单价取开方时价（F112 R7）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx = _load(rx_id)
    items = body.get("items") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "prescription.save.items", "prescription", rx_id,
                              detail={"count": len(items)})
        db.execute(conn, "UPDATE prescription_item SET deleted_at=?, updated_at=?, updated_by=? "
                         "WHERE prescription_id=? AND deleted_at IS NULL",
                   (tu.now_str(), ctx.user_id, rx_id, rx_id))
        n = 0
        for i, it in enumerate(items):
            did = it.get("drug_id")
            require(did, "明细缺少 drug_id")
            drug = db.query_one("SELECT * FROM drug WHERE id=? AND deleted_at IS NULL", (did,))
            if drug is None:
                raise not_found("药品不存在：%s" % did)
            require((drug["price_fen"] or 0) > 0 or it.get("unit_price_fen") is not None,
                    "药品 %s 未定价，不允许按 0 计价" % drug["generic_name"], code="E3044")
            qty = it.get("quantity") or 1
            price = it.get("unit_price_fen") if it.get("unit_price_fen") is not None \
                else drug["price_fen"]
            amt = money.line_amount_fen(price, qty)
            db.execute(conn, """
                INSERT INTO prescription_item (prescription_id, line_no, drug_id, drug_name_snap,
                    spec_snap, dosage_form_snap, unit, unit_price_fen, quantity, line_amount_fen,
                    dosage, frequency, route, days, is_antibiotic, need_skin_test, remark,
                    created_at, created_by, updated_at, updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rx_id, i + 1, did, drug["generic_name"], drug["spec"], drug["dosage_form"],
                  it.get("unit") or drug["unit"], price, qty, amt, it.get("dosage"),
                  it.get("frequency") or drug["default_freq"],
                  it.get("route") or drug["default_route"], it.get("days"),
                  drug["is_antibiotic"], drug["need_skin_test"], it.get("remark"),
                  tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
        db.execute(conn, "UPDATE prescription SET updated_at=?, updated_by=?, version=version+1 "
                         "WHERE id=?", (tu.now_str(), ctx.user_id, rx_id))
    return {"prescription_id": rx_id, "saved": n}


@route("/api/prescription/save/herbs", "POST", perm="prescription.edit", module="M06",
       action="prescription.save.herbs")
def save_herbs(ctx, body, q):
    """F111 饮片明细 + 付数/煎法/服法（契约 §8 必填）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx = _load(rx_id)
    items = body.get("items") or []
    doses = body.get("doses")
    decoction = body.get("decoction_method")
    admin = body.get("administration")
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "prescription.save.herbs", "prescription", rx_id,
                              detail={"count": len(items), "doses": doses})
        if doses is not None and doses != rx.get("doses"):
            audit.audit_data_change(conn, "prescription", rx_id, "doses", rx.get("doses"), doses,
                                    body.get("reason"))
            db.execute(conn, "UPDATE prescription SET doses=? WHERE id=?", (doses, rx_id))
        if decoction is not None and decoction != rx.get("decoction_method"):
            db.execute(conn, "UPDATE prescription SET decoction_method=? WHERE id=?",
                       (decoction, rx_id))
        if admin is not None and admin != rx.get("administration"):
            db.execute(conn, "UPDATE prescription SET administration=? WHERE id=?", (admin, rx_id))
        if body.get("is_decoction") is not None:
            db.execute(conn, "UPDATE prescription SET is_decoction=? WHERE id=?",
                       (1 if body["is_decoction"] else 0, rx_id))

        db.execute(conn, "UPDATE herb_prescription_item SET deleted_at=?, updated_at=?, "
                         "updated_by=? WHERE prescription_id=? AND deleted_at IS NULL",
                   (tu.now_str(), ctx.user_id, rx_id, rx_id))
        n = 0
        for i, it in enumerate(items):
            hid = it.get("herb_id")
            require(hid, "明细缺少 herb_id")
            herb = db.query_one("SELECT * FROM herb WHERE id=? AND deleted_at IS NULL", (hid,))
            if herb is None:
                raise not_found("饮片不存在：%s" % hid)
            dose_g = it.get("dose_g")
            require(dose_g and float(dose_g) > 0, "饮片 %s 剂量必须大于 0" % herb["name"])
            price = it.get("price_milli_per_g") if it.get("price_milli_per_g") is not None \
                else herb["price_milli_per_g"]
            db.execute(conn, """
                INSERT INTO herb_prescription_item (prescription_id, line_no, herb_id,
                    herb_name_snap, dose_g, price_milli_per_g, foot_note, line_amount_fen,
                    is_toxic, remark, created_at, created_by, updated_at, updated_by)
                VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?)
            """, (rx_id, i + 1, hid, herb["name"], float(dose_g), price or 0,
                  it.get("foot_note") or "NORMAL", herb["is_toxic"], it.get("remark"),
                  tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            n += 1
        db.execute(conn, "UPDATE prescription SET prescription_type=?, updated_at=?, "
                         "updated_by=?, version=version+1 WHERE id=?",
                   (body.get("prescription_type") or "HERB", tu.now_str(), ctx.user_id, rx_id))
    return {"prescription_id": rx_id, "saved": n}


@route("/api/prescription/price", "POST", perm="prescription.edit", module="M06",
       action="prescription.price")
def price(ctx, body, q):
    """F112 计价（可单独调用；提交时也会跑）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx = _load(rx_id, editable=False)
    try:
        with db.tx() as conn:
            audit.audit_operation(conn, "M06", "prescription.price", "prescription", rx_id)
            return _price(conn, rx, ctx)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("E3043", "计价失败，拒绝提交：%s" % exc, retryable=True)


@route("/api/prescription/validate", "POST", perm="prescription.view", module="M06",
       action="prescription.validate")
def validate_api(ctx, body, q):
    """F35/F113 规则预检（供 AI 建议与界面实时校验，不写处方）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx = _load(rx_id, editable=False)
    with db.tx() as conn:
        blocks, warns, need_confirm = _validate(conn, rx, ctx, body)
    return {"blocks": blocks, "warns": warns, "need_confirm": need_confirm,
            "passed": not blocks and not need_confirm}


@route("/api/prescription/submit", "POST", perm="prescription.edit", module="M06",
       action="prescription.submit")
def submit(ctx, body, q):
    """F34.4 提交（DRAFT→PENDING）：先跑规则，再计价，任一失败整体回滚。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx = _load(rx_id)
    try:
        with db.tx() as conn:
            audit.audit_operation(conn, "M06", "prescription.submit", "prescription", rx_id)
            blocks, warns, need_confirm = _validate(conn, rx, ctx, body)
            if blocks:
                raise conflict("；".join([b["message"] for b in blocks]),
                               code=blocks[0].get("code", BIZ_STATE_INVALID),
                               detail={"blocks": blocks, "warns": warns})
            if need_confirm:
                raise conflict("存在需二次确认的规则项", code="E3040",
                               detail={"warns": warns})
            pricing = _price(conn, rx, ctx)
            if body.get("override_reason"):
                db.execute(conn, "UPDATE prescription SET override_flag=1, override_reason=? "
                                 "WHERE id=?", (body["override_reason"], rx_id))
                audit.audit_high_risk(conn, audit.RISK_OVERRIDE, "prescription", rx_id,
                                      "处方带强制放行提交：" + body["override_reason"])
            transition(conn, rx_id, PENDING, body.get("reason") or "提交处方", ctx)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("E3037", "规则校验服务异常，拒绝提交：%s" % exc, retryable=True)
    return {"prescription_id": rx_id, "status": PENDING, "pricing": pricing, "warns": warns}


@route("/api/prescription/confirm", "POST", perm="prescription.confirm", module="M06",
       action="prescription.confirm")
def confirm(ctx, body, q):
    """F37 处方确认签名：PENDING→CONFIRMED。确认时重跑规则（F35 R8 / F113 R8）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    require((body.get("password") or "").strip() or body.get("sign_method") == "CARD",
            "确认需要口令或刷卡签名")
    try:
        with db.tx() as conn:
            rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL",
                              (rx_id,))
            if rx is None:
                raise not_found("处方不存在")
            if rx["status"] != PENDING:
                raise conflict("只有 PENDING 状态的处方可以确认", code=BIZ_ALREADY_CONFIRMED)
            audit.audit_operation(conn, "M06", "prescription.confirm", "prescription", rx_id)
            blocks, warns, need_confirm = _validate(conn, rx, ctx, body)
            if blocks:
                raise conflict("；".join([b["message"] for b in blocks]),
                               code=blocks[0].get("code", BIZ_STATE_INVALID),
                               detail={"blocks": blocks})
            if body.get("password"):
                from core import security
                u = db.query_one("SELECT password_hash, password_salt FROM sys_user WHERE id=?",
                                 (ctx.user_id,))
                if not security.verify_password(body["password"], u["password_salt"],
                                                u["password_hash"]):
                    raise conflict("签名口令校验失败", code="E6003")
            pricing = _price(conn, rx, ctx)
            transition(conn, rx_id, CONFIRMED, body.get("reason") or "处方确认", ctx,
                       extra={"confirmed_by": ctx.user_id, "confirmed_at": tu.now_str(),
                              "sign_method": body.get("sign_method", "PASSWORD")})
    except AppError:
        raise
    except Exception as exc:
        raise AppError("E3037", "处方确认时校验异常：%s" % exc, retryable=True)
    return {"prescription_id": rx_id, "status": CONFIRMED, "pricing": pricing, "warns": warns}


@route("/api/prescription/void", "POST", perm="prescription.void", module="M06",
       action="prescription.void")
def void(ctx, body, q):
    """F38 作废：必须填原因；已发药拒绝（E3008）。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    reason = (body.get("reason") or "").strip()
    require(reason, "作废必须填写原因")
    with db.tx() as conn:
        rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
        if rx is None:
            raise not_found("处方不存在")
        audit.audit_operation(conn, "M06", "prescription.void", "prescription", rx_id,
                              detail={"reason": reason})
        audit.audit_high_risk(conn, audit.RISK_VOID, "prescription", rx_id,
                              "处方可废：%s / %s" % (rx["prescription_no"], reason))
        transition(conn, rx_id, VOID, reason, ctx, extra={"void_reason": reason})
    return {"prescription_id": rx_id, "status": VOID}


@route("/api/prescription/get", "GET", perm="prescription.view", module="M06",
       action="prescription.get")
def get(ctx, body, q):
    rx_id = q.get("id") or q.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
    if rx is None:
        raise not_found("处方不存在")
    out = dict(rx)
    out["items"] = db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                            "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
    out["herbs"] = db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                            "AND deleted_at IS NULL ORDER BY line_no", (rx_id,))
    out["pricing"] = db.query_one("SELECT * FROM prescription_pricing WHERE prescription_id=?",
                                  (rx_id,))
    out["rule_hits"] = db.query("SELECT * FROM prescription_rule_hit WHERE prescription_id=? "
                                "ORDER BY id DESC", (rx_id,))
    out["status_log"] = db.query("SELECT * FROM prescription_status_log WHERE prescription_id=? "
                                 "ORDER BY id", (rx_id,))
    out["total_yuan"] = money.fen_to_yuan(rx["total_amount"] or 0)
    return out


@route("/api/prescription/list", "GET", perm="prescription.view", module="M06",
       action="prescription.list")
def list_by_visit(ctx, body, q):
    vid = q.get("visit_id")
    pid = q.get("patient_id")
    require(vid or pid, "缺少 visit_id 或 patient_id")
    if vid:
        rows = db.query("SELECT * FROM prescription WHERE visit_id=? AND deleted_at IS NULL "
                        "ORDER BY id", (vid,))
    else:
        rows = db.query("SELECT * FROM prescription WHERE patient_id=? AND deleted_at IS NULL "
                        "ORDER BY id DESC LIMIT 100", (pid,))
    return {"items": rows}


# ---------- F36 皮试 ----------

@route("/api/skin_test/record", "POST", perm="skin_test.record", module="M06",
       action="skin_test.record")
def skin_test_record(ctx, body, q):
    require_keys(body, ["patient_id", "drug_id", "result"])
    require(body["result"] in ("NEGATIVE", "POSITIVE", "PENDING"), "皮试结果取值非法")
    d = db.query_one("SELECT * FROM drug WHERE id=?", (body["drug_id"],))
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "skin_test.record", "skin_test", None,
                              detail={"result": body["result"]})
        cur = db.execute(conn, """
            INSERT INTO skin_test (patient_id, visit_id, drug_id, drug_name, batch_no, result,
                performed_at, performed_by, observe_min, note, created_at, created_by,
                updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (body["patient_id"], body.get("visit_id"), body["drug_id"],
              (d["generic_name"] if d else body.get("drug_name")), body.get("batch_no"),
              body["result"], body.get("performed_at") or tu.now_str(), ctx.user_id,
              body.get("observe_min"), body.get("note"), tu.now_str(), ctx.user_id,
              tu.now_str(), ctx.user_id))
        sid = cur.lastrowid
    return {"id": sid, "result": body["result"]}


@route("/api/skin_test/list", "GET", perm="prescription.view", module="M06",
       action="skin_test.list")
def skin_test_list(ctx, body, q):
    pid = q.get("patient_id")
    require(pid, "缺少 patient_id")
    return {"items": db.query("SELECT * FROM skin_test WHERE patient_id=? AND deleted_at IS NULL "
                              "ORDER BY id DESC LIMIT 50", (pid,))}


# ---------- F39 用药指导 ----------

@route("/api/prescription/guide", "POST", perm="prescription.edit", module="M06",
       action="prescription.guide")
def guide(ctx, body, q):
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    content = (body.get("content") or "").strip()
    require(content, "用药指导内容不能为空")
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "prescription.guide", "medication_guide", None)
        cur = db.execute(conn, """
            INSERT INTO medication_guide (prescription_id, content, created_at, created_by)
            VALUES (?,?,?,?)
        """, (rx_id, content, tu.now_str(), ctx.user_id))
    return {"id": cur.lastrowid}


# ---------- F127 处方模板与经验方 ----------

@route("/api/prescription/templates", "GET", perm="prescription.view", module="M06",
       action="prescription.templates")
def templates(ctx, body, q):
    sql = """SELECT * FROM prescription_template WHERE deleted_at IS NULL AND is_active=1
             AND (scope='SYSTEM' OR scope='CLINIC' OR owner_id=?)"""
    params = [ctx.user_id]
    if q.get("template_type"):
        sql += " AND template_type=?"
        params.append(q["template_type"])
    if q.get("syndrome_name"):
        sql += " AND syndrome_name=?"
        params.append(q["syndrome_name"])
    if q.get("keyword"):
        sql += " AND (name LIKE ? OR indication LIKE ?)"
        like = "%%%s%%" % q["keyword"]
        params += [like, like]
    sql += " ORDER BY usage_count DESC, id DESC"
    return {"items": db.query(sql, tuple(params))}


@route("/api/prescription/template/create", "POST", perm="prescription.template.manage",
       module="M06", action="prescription.template.create")
def template_create(ctx, body, q):
    require(body.get("name"), "缺少模板名称")
    require(body.get("template_type") in ("WEST", "HERB", "GRANULE"), "template_type 取值非法")
    items = body.get("items") or []
    require(items, "模板明细不能为空")
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "prescription.template.create",
                              "prescription_template", None)
        cur = db.execute(conn, """
            INSERT INTO prescription_template (template_type, scope, owner_id, category, name,
                syndrome_code, syndrome_name, indication, doses_default, decoction_method,
                administration, usage_count, source_prescription_no, is_active,
                created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,1,?,?,?,?)
        """, (body["template_type"], body.get("scope", "USER"), ctx.user_id, body.get("category"),
              body["name"].strip(), body.get("syndrome_code"), body.get("syndrome_name"),
              body.get("indication"), body.get("doses_default"), body.get("decoction_method"),
              body.get("administration"), body.get("source_prescription_no"),
              tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        tid = cur.lastrowid
        for i, it in enumerate(items):
            kind = it.get("item_kind") or ("HERB" if body["template_type"] == "HERB" else "DRUG")
            tbl = "herb" if kind == "HERB" else "drug"
            ref = db.query_one("SELECT * FROM %s WHERE id=? AND deleted_at IS NULL" % tbl,
                               (it.get("ref_id"),))
            if ref is None:
                raise not_found("模板明细引用了不存在的%s：%s" % (tbl, it.get("ref_id")))
            db.execute(conn, """
                INSERT INTO prescription_template_item (template_id, item_kind, ref_id, name_snap,
                    dose_g, quantity, dosage, frequency, route, foot_note, seq_no,
                    created_at, created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (tid, kind, it["ref_id"],
                  ref["name"] if kind == "HERB" else ref["generic_name"],
                  it.get("dose_g"), it.get("quantity"), it.get("dosage"), it.get("frequency"),
                  it.get("route"), it.get("foot_note"), i + 1, tu.now_str(), ctx.user_id))
    return {"id": tid}


@route("/api/prescription/template/apply", "POST", perm="prescription.edit", module="M06",
       action="prescription.template.apply")
def template_apply(ctx, body, q):
    """F127 套用模板：复制明细到处方（R3 复制语义）；停用药品跳过并提示（R6）。"""
    rx_id = body.get("prescription_id")
    tid = body.get("template_id")
    require(rx_id and tid, "缺少 prescription_id / template_id")
    rx = _load(rx_id)
    tpl = db.query_one("SELECT * FROM prescription_template WHERE id=? AND deleted_at IS NULL",
                       (tid,))
    if tpl is None:
        raise not_found("模板不存在")
    items = db.query("SELECT * FROM prescription_template_item WHERE template_id=? ORDER BY seq_no",
                     (tid,))
    skipped = []
    drugs, herbs = [], []
    for it in items:
        if it["item_kind"] == "HERB":
            h = db.query_one("SELECT * FROM herb WHERE id=? AND deleted_at IS NULL",
                             (it["ref_id"],))
            if h is None or not h["is_active"]:
                skipped.append(it["name_snap"])
                continue
            herbs.append({"herb_id": h["id"], "dose_g": it["dose_g"] or h["usual_dose_g"] or 10,
                          "foot_note": it["foot_note"] or "NORMAL"})
        else:
            d = db.query_one("SELECT * FROM drug WHERE id=? AND deleted_at IS NULL", (it["ref_id"],))
            if d is None or not d["is_active"]:
                skipped.append(it["name_snap"])
                continue
            drugs.append({"drug_id": d["id"], "quantity": it["quantity"] or 1,
                          "dosage": it["dosage"], "frequency": it["frequency"],
                          "route": it["route"]})
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "prescription.template.apply", "prescription", rx_id,
                              detail={"template_id": tid, "skipped": skipped})
        db.execute(conn, "UPDATE prescription_template SET usage_count=usage_count+1 WHERE id=?",
                   (tid,))
        if tpl["template_type"] == "HERB":
            db.execute(conn, "UPDATE prescription SET prescription_type='HERB', doses=?, "
                             "decoction_method=COALESCE(?, decoction_method), "
                             "administration=COALESCE(?, administration), updated_at=?, "
                             "updated_by=? WHERE id=?",
                       (body.get("doses") or tpl["doses_default"]
                        or config.get_int("herb.default_doses", 7),
                        tpl["decoction_method"], tpl["administration"],
                        tu.now_str(), ctx.user_id, rx_id))
        if drugs:
            save_items(ctx, {"prescription_id": rx_id, "items": drugs}, q)
        if herbs:
            save_herbs(ctx, {"prescription_id": rx_id, "items": herbs,
                             "doses": body.get("doses") or tpl["doses_default"]}, q)
    return {"prescription_id": rx_id, "template_id": tid, "drugs": len(drugs),
            "herbs": len(herbs), "skipped": skipped}


@route("/api/prescription/template/save_from", "POST", perm="prescription.template.manage",
       module="M06", action="prescription.template.save_from")
def template_from_prescription(ctx, body, q):
    """R9：把一张已存在处方另存为模板。"""
    rx_id = body.get("prescription_id")
    require(rx_id, "缺少 prescription_id")
    require(body.get("name"), "缺少模板名称")
    rx = db.query_one("SELECT * FROM prescription WHERE id=? AND deleted_at IS NULL", (rx_id,))
    if rx is None:
        raise not_found("处方不存在")
    items = []
    for it in db.query("SELECT * FROM prescription_item WHERE prescription_id=? "
                       "AND deleted_at IS NULL ORDER BY line_no", (rx_id,)):
        items.append({"item_kind": "DRUG", "ref_id": it["drug_id"], "quantity": it["quantity"],
                      "dosage": it["dosage"], "frequency": it["frequency"], "route": it["route"]})
    for h in db.query("SELECT * FROM herb_prescription_item WHERE prescription_id=? "
                      "AND deleted_at IS NULL ORDER BY line_no", (rx_id,)):
        items.append({"item_kind": "HERB", "ref_id": h["herb_id"], "dose_g": h["dose_g"],
                      "foot_note": h["foot_note"]})
    require(items, "该处方没有明细，无法存为模板")
    return template_create(ctx, {
        "name": body["name"], "template_type": rx["prescription_type"],
        "scope": body.get("scope", "USER"), "category": body.get("category"),
        "syndrome_name": rx.get("tcm_syndrome"), "doses_default": rx.get("doses"),
        "decoction_method": rx.get("decoction_method"), "administration": rx.get("administration"),
        "source_prescription_no": rx["prescription_no"], "items": items,
    }, q)


# ---------- 对外服务接口（跨模块调用入口）----------

def void_by_visit(conn, visit_id, reason, ctx=None):
    """M02 F125 就诊作废时：作废该就诊下所有未确认处方（已确认的保留）。"""
    rows = db.query("SELECT id, status FROM prescription WHERE visit_id=? AND deleted_at IS NULL "
                    "AND status IN ('DRAFT','PENDING')", (visit_id,))
    n = 0
    for r in rows:
        transition(conn, r["id"], VOID, reason, ctx, extra={"void_reason": reason})
        audit.audit_high_risk(conn, audit.RISK_VOID, "prescription", r["id"],
                              "就诊作废联动作废处方：" + reason)
        n += 1
    return n


def reassign_patient(conn, old_patient_id, new_patient_id, reason=None):
    """M01 F04 患者合并改挂：只改 patient_id，患者快照字段不动（快照是历史事实）。"""
    cur = db.execute(conn, "UPDATE prescription SET patient_id=? WHERE patient_id=? "
                           "AND deleted_at IS NULL", (new_patient_id, old_patient_id))
    return cur.rowcount


def mark_dispensed(conn, rx_id, ctx=None):
    """M07 发药后调用：CONFIRMED → DISPENSED。禁止外部裸 UPDATE。"""
    return transition(conn, rx_id, DISPENSED, "发药", ctx,
                      extra={"dispensed_at": tu.now_str()})


def mark_returned(conn, rx_id, full=False, ctx=None):
    """M07 F46 退药后调用：DISPENSED → PARTIAL_RETURNED / FULLY_RETURNED。

    禁止外部裸 UPDATE prescription.status（R5）。
    """
    return transition(conn, rx_id, FULLY_RETURNED if full else PARTIAL_RETURNED,
                      "退药", ctx)


def prescribed_amount_for_bill(visit_id):
    """M09 F58 账单汇总用（只读）：该就诊下可计费的处方金额。

    只取已确认及之后的处方（DRAFT/PENDING/VOID 不计入），金额取 F112 计价结果。
    """
    rows = db.query("""
        SELECT p.id, p.prescription_no, p.prescription_type,
               COALESCE(pr.total_amount_fen, p.total_amount, 0) AS amount_fen
        FROM prescription p
        LEFT JOIN prescription_pricing pr ON pr.prescription_id = p.id
        WHERE p.visit_id=? AND p.deleted_at IS NULL
          AND p.status IN ('CONFIRMED','DISPENSED','PARTIAL_RETURNED','FULLY_RETURNED')
        ORDER BY p.id
    """, (visit_id,))
    return [dict(r) for r in rows]


def count_by_patient(patient_id):
    """M01 删除保护用：存在有效处方时不得删除患者（04 §3）。"""
    row = db.query_one("SELECT COUNT(*) c FROM prescription WHERE patient_id=? "
                       "AND deleted_at IS NULL AND status NOT IN ('VOID')", (patient_id,))
    return row["c"] if row else 0


def import_upsert(kind, rows):
    """对外服务接口（M14 F128 基础数据包导入用）：批量导入 DRUG / HERB。

    M14 不得直接写 drug / herb 表（04 §2 表所有权），必须经本接口。
    返回 (total, ok, failed, errors)。
    """
    ok, failed, errors = 0, 0, []

    def pick(row, *keys):
        for k in keys:
            if k in row and (row[k] or "").strip():
                return row[k].strip()
        return None

    for i, row in enumerate(rows, start=2):
        try:
            if kind == "DRUG":
                code = pick(row, "drug_code", "编码", "药品编码", "code")
                name = pick(row, "generic_name", "通用名", "名称", "药品名称", "name")
                if not (code and name):
                    failed += 1
                    errors.append({"line": i, "error": "缺少编码或通用名"})
                    continue
                price = pick(row, "price_yuan", "零售价", "单价", "价格")
                with db.tx() as conn:
                    db.execute(conn, """
                        INSERT INTO drug (drug_code, generic_name, trade_name, pinyin, spec,
                            dosage_form, unit, manufacturer, price_fen, drug_class, allergy_class,
                            is_antibiotic, is_injection, need_skin_test, max_single_dose,
                            max_daily_dose, is_active, created_at, created_by, updated_at,
                            updated_by)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,0,?,0)
                        ON CONFLICT(drug_code) DO UPDATE SET
                            generic_name=excluded.generic_name, trade_name=excluded.trade_name,
                            spec=excluded.spec, dosage_form=excluded.dosage_form,
                            unit=excluded.unit, price_fen=excluded.price_fen,
                            drug_class=excluded.drug_class, allergy_class=excluded.allergy_class,
                            is_antibiotic=excluded.is_antibiotic,
                            is_injection=excluded.is_injection,
                            need_skin_test=excluded.need_skin_test,
                            updated_at=excluded.updated_at
                    """, (code, name, pick(row, "trade_name", "商品名"),
                          pick(row, "pinyin", "拼音"), pick(row, "spec", "规格"),
                          pick(row, "dosage_form", "剂型"), pick(row, "unit", "单位") or "盒",
                          pick(row, "manufacturer", "厂家", "生产企业"),
                          money.to_fen(price) if price else 0,
                          pick(row, "drug_class", "药理分类"),
                          pick(row, "allergy_class", "过敏类别"),
                          int(pick(row, "is_antibiotic", "抗生素") or 0),
                          int(pick(row, "is_injection", "注射剂") or 0),
                          int(pick(row, "need_skin_test", "需皮试") or 0),
                          pick(row, "max_single_dose", "单次上限"),
                          pick(row, "max_daily_dose", "日上限"),
                          tu.now_str(), tu.now_str()))
            elif kind == "HERB":
                code = pick(row, "herb_code", "编码", "饮片编码", "code")
                name = pick(row, "name", "名称", "饮片名称", "品名")
                if not (code and name):
                    failed += 1
                    errors.append({"line": i, "error": "缺少编码或名称"})
                    continue
                price = pick(row, "price_yuan_per_g", "price_milli_per_g", "单价", "元/克")
                if pick(row, "price_milli_per_g"):
                    price_milli = int(float(pick(row, "price_milli_per_g")))
                elif price:
                    price_milli = money.to_milli(price)
                else:
                    price_milli = 0
                with db.tx() as conn:
                    db.execute(conn, """
                        INSERT INTO herb (herb_code, name, alias, pinyin, nature, flavor,
                            meridian, efficacy, indication, price_milli_per_g, usual_dose_g,
                            max_dose_g, is_toxic, toxic_limit_g, pregnancy_flag, is_active,
                            created_at, created_by, updated_at, updated_by)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,0,?,0)
                        ON CONFLICT(herb_code) DO UPDATE SET
                            name=excluded.name, alias=excluded.alias, pinyin=excluded.pinyin,
                            nature=excluded.nature, flavor=excluded.flavor,
                            meridian=excluded.meridian, efficacy=excluded.efficacy,
                            price_milli_per_g=excluded.price_milli_per_g,
                            usual_dose_g=excluded.usual_dose_g, max_dose_g=excluded.max_dose_g,
                            is_toxic=excluded.is_toxic, toxic_limit_g=excluded.toxic_limit_g,
                            pregnancy_flag=excluded.pregnancy_flag, updated_at=excluded.updated_at
                    """, (code, name, pick(row, "alias", "别名"), pick(row, "pinyin", "拼音"),
                          pick(row, "nature", "性"), pick(row, "flavor", "味"),
                          pick(row, "meridian", "归经"), pick(row, "efficacy", "功效"),
                          pick(row, "indication", "主治"), price_milli,
                          pick(row, "usual_dose_g", "常用量"), pick(row, "max_dose_g", "上限"),
                          int(pick(row, "is_toxic", "毒性") or 0),
                          pick(row, "toxic_limit_g", "毒性限量"),
                          pick(row, "pregnancy_flag", "妊娠"), tu.now_str(), tu.now_str()))
            else:
                failed += 1
                errors.append({"line": i, "error": "不支持的类型 %s" % kind})
                continue
            ok += 1
        except Exception as exc:
            failed += 1
            errors.append({"line": i, "error": str(exc)})
    return len(rows), ok, failed, errors


# ============================================================
# 易用性：开方检索目录 + 一键载入常用中药（2026-10-20）
#
# 背景：新装的库里没有药品目录，医生开方搜"当归"搜不到，只能先手工建档，
# 是"开方太麻烦"的第一道坎。这里提供：
#   1) 全量目录接口：前端一次拉走做本地检索，输入即出候选（不再每敲一个字打一次接口）
#   2) 一键载入：内置常用饮片 / 成药写入，**按正名去重，已存在的跳过，绝不覆盖**
# ============================================================

@route("/api/catalog/herbs", "GET", perm="herb.view", module="M06", action="catalog.herbs")
def catalog_herbs(ctx, body, q):
    """全量饮片目录（轻量字段），供前端本地检索 / 首字母匹配。"""
    rows = db.query("""
        SELECT id, herb_code, name, alias, pinyin, pinyin_initials,
               price_milli_per_g, usual_dose_g, is_toxic, pregnancy_flag
        FROM herb WHERE deleted_at IS NULL AND is_active=1
        ORDER BY name
    """)
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@route("/api/catalog/drugs", "GET", perm="drug.view", module="M06", action="catalog.drugs")
def catalog_drugs(ctx, body, q):
    """全量成药目录（轻量字段）。"""
    rows = db.query("""
        SELECT id, drug_code, generic_name, trade_name, alias, pinyin, pinyin_initials,
               spec, unit, price_fen, is_antibiotic, need_skin_test
        FROM drug WHERE deleted_at IS NULL AND is_active=1
        ORDER BY generic_name
    """)
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@route("/api/catalog/seed", "POST", perm="herb.manage", module="M06", action="catalog.seed")
def catalog_seed(ctx, body, q):
    """一键载入内置常用中药目录。幂等：按正名去重，已存在的跳过。

    body: {kind: "herb"|"drug"|"all"}  默认 all
    返回 {herb:{added,skipped}, drug:{added,skipped}}
    """
    from core import catalog_seed

    kind = (body.get("kind") or "all").lower()
    out = {}
    now = tu.now_str()

    if kind in ("all", "herb"):
        added = skipped = 0
        with db.tx() as conn:
            audit.audit_operation(conn, "M06", "catalog.seed.herb", "herb", None)
            seq = _seq_start(conn, "herb", "herb_code", "HS")
            for name, pinyin, initials, price_yuan, dose, flag in catalog_seed.herbs():
                # 关键：按**正名**去重。库里可能已有同名饮片（编目方式不同），
                # 重复写入会让医生在候选里看到两个"黄芪"，反而更难选。
                if _exists(conn, "herb", "name", name):
                    skipped += 1
                    continue
                db.execute(conn, """
                    INSERT INTO herb (herb_code, name, pinyin, pinyin_initials,
                        price_milli_per_g, usual_dose_g, is_toxic, pregnancy_flag,
                        stock_unit, is_active, created_at, created_by, updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?,'g',1,?,?,?,?)
                """, ("HS%04d" % seq, name, pinyin, initials,
                      money.to_milli(price_yuan), dose,
                      1 if "T" in flag else 0,
                      "CONTRAINDICATED" if "P" in flag else None,
                      now, ctx.user_id, now, ctx.user_id))
                seq += 1
                added += 1
        out["herb"] = {"added": added, "skipped": skipped}

    if kind in ("all", "drug"):
        added = skipped = 0
        with db.tx() as conn:
            audit.audit_operation(conn, "M06", "catalog.seed.drug", "drug", None)
            seq = _seq_start(conn, "drug", "drug_code", "DS")
            for name, pinyin, initials, price_yuan, unit, spec in catalog_seed.drugs():
                if _exists(conn, "drug", "generic_name", name):
                    skipped += 1
                    continue
                db.execute(conn, """
                    INSERT INTO drug (drug_code, generic_name, pinyin, pinyin_initials,
                        spec, unit, price_fen, is_active, created_at, created_by,
                        updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,1,?,?,?,?)
                """, ("DS%04d" % seq, name, pinyin, initials, spec, unit,
                      money.to_fen(price_yuan), now, ctx.user_id, now, ctx.user_id))
                seq += 1
                added += 1
        out["drug"] = {"added": added, "skipped": skipped}

    return out


@route("/api/catalog/frequent", "GET", perm="herb.view", module="M06",
       action="catalog.frequent")
def catalog_frequent(ctx, body, q):
    """开方常用品目（按历史开方次数排序），用于快捷面板一键加药。"""
    kind = (q.get("kind") or "herb").lower()
    try:
        limit = max(1, min(int(q.get("limit") or 30), 100))
    except ValueError:
        limit = 30
    if kind == "drug":
        rows = db.query("""
            SELECT d.id, d.generic_name AS name, d.pinyin_initials, d.unit, d.price_fen,
                   COUNT(*) c, MAX(i.quantity) usual
            FROM prescription_item i JOIN drug d ON d.id = i.drug_id
            WHERE i.deleted_at IS NULL AND d.deleted_at IS NULL AND d.is_active=1
            GROUP BY d.id ORDER BY c DESC, d.generic_name LIMIT ?
        """, (limit,))
    else:
        rows = db.query("""
            SELECT h.id, h.name, h.pinyin_initials, h.usual_dose_g AS usual,
                   h.price_milli_per_g, COUNT(*) c
            FROM herb_prescription_item i JOIN herb h ON h.id = i.herb_id
            WHERE i.deleted_at IS NULL AND h.deleted_at IS NULL AND h.is_active=1
            GROUP BY h.id ORDER BY c DESC, h.name LIMIT ?
        """, (limit,))
    return {"items": [dict(r) for r in rows]}


@route("/api/catalog/cleanup_test", "POST", perm="herb.manage", module="M06",
       action="catalog.cleanup_test")
def catalog_cleanup_test(ctx, body, q):
    """把自动化测试造出来的品目停用掉，避免它们混进开方候选里。

    特征是名称形如 "M06黄芪" / "M07饮片" / "M10饮片B"（模块号 + 品类名）。
    只置 is_active=0（软停用），不删数据 —— 随时可再启用，不做不可逆操作。
    """
    pats = ("M0%", "M1%")
    out = {}
    now = tu.now_str()
    with db.tx() as conn:
        audit.audit_operation(conn, "M06", "catalog.cleanup_test", "herb", None)
        n = 0
        for p in pats:
            rows = db.execute(conn, "SELECT id, name FROM herb WHERE deleted_at IS NULL "
                                    "AND is_active=1 AND name LIKE ?", (p,)).fetchall()
            for r in rows:
                db.execute(conn, "UPDATE herb SET is_active=0, updated_at=?, updated_by=? "
                                 "WHERE id=?", (now, ctx.user_id, r["id"]))
                n += 1
        out["herb"] = n
        n = 0
        for p in pats:
            rows = db.execute(conn, "SELECT id, generic_name FROM drug WHERE deleted_at IS NULL "
                                    "AND is_active=1 AND generic_name LIKE ?", (p,)).fetchall()
            for r in rows:
                db.execute(conn, "UPDATE drug SET is_active=0, updated_at=?, updated_by=? "
                                 "WHERE id=?", (now, ctx.user_id, r["id"]))
                n += 1
        out["drug"] = n
    return out


def _exists(conn, table, col, value):
    """事务内查重：必须用同一个 conn，走独立读连接看不到本事务未提交的行。"""
    cur = db.execute(conn, "SELECT id FROM %s WHERE %s=? AND deleted_at IS NULL"
                     % (table, col), (value,))
    return cur.fetchone() is not None


def _seq_start(conn, table, col, prefix):
    """同前缀编码的下一个序号。两位前缀（HS/DS）是为了和历史测试数据
    （H1642249588 这类时间戳编码）隔开，否则 MAX 会被天文数字带跑。"""
    cur = db.execute(conn, "SELECT MAX(CAST(SUBSTR(%s,%d) AS INTEGER)) m FROM %s "
                           "WHERE %s LIKE ?" % (col, len(prefix) + 1, table, col),
                     (prefix + "%",))
    row = cur.fetchone()
    return ((row["m"] if row and row["m"] else 0) or 0) + 1
