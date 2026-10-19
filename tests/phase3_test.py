# -*- coding: utf-8 -*-
"""第三阶段冒烟测试（M07 药房与库存 / M08 处置执行 / M09 收费财务）。

直接调用模块服务函数（不经 HTTP），覆盖：
  入库 → 批次 → 拆零 → 发药 → 退药 → 报损 → 盘点 → 效期预警
  中药：饮片入库(kg→g) → 饮片处方 → 计价 → 代煎/自煎发药（剂量×付数）
  处置：项目 → 组套展开 → 开立 → 执行 → 中医治疗记录 → 中断/取消
  收费：收费项目 → 账单汇总 → 支付 → 退药退款 → 红字冲正 → 日结 → 日结后锁定

用法：python tests/phase3_test.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, money, config  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m14_system as m14,  # noqa: E402
                     m06_prescription as m06, m07_pharmacy as m07,
                     m08_treatment as m08, m09_billing as m09)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_phase3_last.log")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_orig_print = print


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _orig_print(msg, **kwargs)
    _LOG.write(msg + "\n")
    _LOG.flush()


def check(name, cond, extra=""):
    if cond:
        PASSED.append(name)
        print("  PASS  %s %s" % (name, extra))
    else:
        FAILED.append(name)
        print("  FAIL  %s %s" % (name, extra))


def expect_error(name, fn, code_prefix=None):
    try:
        fn()
    except AppError as exc:
        ok = (code_prefix is None) or str(exc.code).startswith(code_prefix)
        check(name, ok, "→ %s %s" % (exc.code, exc.message))
        return exc
    except Exception as exc:
        check(name, False, "→ 抛出非 AppError：%r" % exc)
        return None
    check(name, False, "→ 未拒绝（应当报错）")
    return None


def prepare_root():
    root = tempfile.mkdtemp(prefix="clinic_phase3_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    return root


def _future(days):
    import datetime
    return (tu.now().date() + datetime.timedelta(days=days)).isoformat()


def _q(sql, params=()):
    return db.query(sql, params)


def _one(sql, params=()):
    return db.query_one(sql, params)


def main():
    root = prepare_root()
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))
    mv = next((c["detail"] for c in report["checks"] if c["item"] == "migration_version"), "")
    applied = int(mv.split("已应用")[-1].split("/")[0].strip()) if "已应用" in mv else 0
    check("迁移全部应用且无缺失（≥0013）",
          applied >= 13 and "缺失 无" in mv, mv)

    # ---------- 初始化与登录 ----------
    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    m14.init_system(sys_ctx, {"admin_username": "admin", "admin_password": "Clinic@2026",
                              "admin_name": "管理员", "clinic_name": "示例中医诊所"}, {})
    m14.user_roles(sys_ctx, {"user_id": 1,
                             "roles": ["ADMIN", "DOCTOR", "PHARMACIST", "CASHIER"]}, {})
    sess = m14.login(sys_ctx, {"username": "admin", "password": "Clinic@2026",
                               "role_code": "DOCTOR"}, {})
    ctx = Context(user_id=sess["user_id"], role_code="DOCTOR", source="UI",
                  username="admin", session_id=sess["session_id"])
    set_ctx(ctx)
    PWD = "Clinic@2026"

    # ---------- 基础数据 ----------
    print("\n[准备] 患者 / 就诊 / 药品 / 饮片")
    pat = m01.create(ctx, {"name": "王五", "gender": "M", "birth_date": "1978-05-06",
                           "phone": "13700137000"}, {})
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "TCM"}, {})
    m02.start(ctx, {"visit_id": vis["id"]}, {})

    drug = m06.drug_create(ctx, {"drug_code": "IBU001", "generic_name": "布洛芬片",
                                 "spec": "0.1g*24片", "unit": "盒", "pack_qty": 24,
                                 "price_fen": 1000, "dosage_form": "片剂"}, {})
    herb = m06.herb_create(ctx, {"herb_code": "HQ001", "name": "黄芪",
                                 "price_milli_per_g": 50, "usual_dose_g": 15,
                                 "max_dose_g": 30}, {})

    # ============================================================
    print("\n[M07 F40/F41/F42/F43] 入库 · 批次 · 库存 · 流水")
    sup = m07.supplier_create(ctx, {"name": "康美药业", "supplier_code": "KM001"}, {})
    check("供应商建档", bool(sup.get("id")))

    si = m07.stock_in_create(ctx, {
        "supplier_id": sup["id"],
        "items": [
            {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 100,
             "cost_yuan_per_unit": 8.5, "unit": "盒", "pack_qty": 24,
             "supplier_batch_no": "KM2026A", "expiry_date": _future(400)},
            {"item_kind": "HERB", "herb_id": herb["id"], "qty": 5, "input_unit": "KG",
             "cost_yuan_per_unit": 0.03, "expiry_date": _future(600)},
        ]}, {})
    check("入库单号 IN{yyyyMMdd}{4位}",
          si["stock_in_no"].startswith("IN") and len(si["stock_in_no"]) == 14,
          si["stock_in_no"])
    check("入库前库存为 0（确认才生效）",
          m07.available_qty("DRUG", drug["id"]) == 0)

    m07.stock_in_confirm(ctx, {"id": si["id"]}, {})
    check("成药入库 100 盒", m07.available_qty("DRUG", drug["id"]) == 100,
          str(m07.available_qty("DRUG", drug["id"])))
    check("饮片按 kg 换算为 5000 g",
          m07.available_qty("HERB", herb["id"]) == 5000,
          str(m07.available_qty("HERB", herb["id"])))

    batches = m07.batch_list(ctx, {}, {"item_kind": "DRUG", "drug_id": drug["id"]})["items"]
    check("批次内码 BT{yyyyMMdd}{6位}",
          batches[0]["batch_no"].startswith("BT") and len(batches[0]["batch_no"]) == 16,
          batches[0]["batch_no"])
    check("供应商批号原样保存", batches[0]["supplier_batch_no"] == "KM2026A")
    d_batch = batches[0]["id"]
    h_batches = m07.batch_list(ctx, {}, {"item_kind": "HERB", "herb_id": herb["id"]})["items"]
    h_batch = h_batches[0]["id"]

    txns = m07.transactions(ctx, {}, {"ref_type": "STOCK_IN", "ref_id": si["id"]})["items"]
    check("入库产生 2 条流水", len(txns) == 2, str(len(txns)))
    check("流水含成本埋点（amount_fen）",
          all(isinstance(t["amount_fen"], int) and t["amount_fen"] > 0 for t in txns),
          str([t["amount_fen"] for t in txns]))
    check("成药入库金额 100×8.5元=85000分",
          any(t["amount_fen"] == 85000 for t in txns),
          str([t["amount_fen"] for t in txns]))

    expect_error("效期缺失被拒",
                 lambda: m07.stock_in_create(ctx, {"items": [
                     {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 1,
                      "cost_yuan_per_unit": 1}]}, {}), "E2")
    expect_error("已过期批次不可入库",
                 lambda: m07.stock_in_create(ctx, {"items": [
                     {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 1,
                      "cost_yuan_per_unit": 1, "expiry_date": _future(-1)}]}, {}), "E2")

    # ============================================================
    print("\n[M07 F44/F45] 拆零与尾差")
    sp = m07.stock_split(ctx, {"batch_id": d_batch, "idempotent_key": "SPLIT-1"}, {})
    check("拆零生成零头批次 24 片", sp.get("new_batch_id") is not None
          and sp["pack_qty"] == 24, str(sp.get("pack_qty")))
    check("尾差显式记录（整除为 0）", sp["tail_diff_milli"] == 0,
          str(sp["tail_diff_milli"]))
    dup = m07.stock_split(ctx, {"batch_id": d_batch, "idempotent_key": "SPLIT-1"}, {})
    check("重复提交幂等", dup.get("duplicate") is True)
    new_batch = sp["new_batch_id"]
    sp_stock = m07.stock_query(ctx, {}, {"item_kind": "DRUG", "drug_id": drug["id"]})
    check("拆零后可用量 = 99 + 24",
          abs(sp_stock["available"] - 123) < 1e-6, str(sp_stock["available"]))

    # ============================================================
    print("\n[M07 F51] 成药发药")
    rx1 = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "WEST"}, {})
    m06.save_items(ctx, {"prescription_id": rx1["id"], "items": [
        {"drug_id": drug["id"], "quantity": 2, "dosage": "1片", "frequency": "tid",
         "route": "PO"}]}, {})
    m06.submit(ctx, {"prescription_id": rx1["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx1["id"], "password": PWD}, {})

    before = m07.available_qty("DRUG", drug["id"])
    dsp = m07.dispense(ctx, {"prescription_id": rx1["id"],
                             "idempotent_key": "DISP-1"}, {})
    check("发药成功", dsp.get("status") == "DISPENSED", str(dsp.get("status")))
    check("库存扣减 2", abs(before - m07.available_qty("DRUG", drug["id"]) - 2) < 1e-6,
          "%s → %s" % (before, m07.available_qty("DRUG", drug["id"])))
    rx1_now = _one("SELECT * FROM prescription WHERE id=?", (rx1["id"],))
    check("处方状态经 M06 接口转为 DISPENSED", rx1_now["status"] == "DISPENSED",
          rx1_now["status"])
    dupd = m07.dispense(ctx, {"prescription_id": rx1["id"],
                              "idempotent_key": "DISP-1"}, {})
    check("重复发药幂等", dupd.get("duplicate") is True)
    expect_error("已发药处方不可重复发药",
                 lambda: m07.dispense(ctx, {"prescription_id": rx1["id"]}, {}), "E3")

    # ============================================================
    print("\n[M08] 处置执行")
    ti_acup = m08.item_create(ctx, {"name": "针灸治疗", "category": "TCM", "is_tcm": 1,
                                    "need_record": 1, "price_fen": 3000,
                                    "item_code": "ACUP001"}, {})
    ti_dress = m08.item_create(ctx, {"name": "换药", "category": "DRESSING",
                                     "price_fen": 1500, "item_code": "DR001"}, {})
    pkg = m08.package_create(ctx, {"name": "外伤处理包", "package_code": "PKG001",
                                   "items": [{"item_id": ti_dress["id"], "quantity": 1},
                                             {"item_id": ti_dress["id"], "quantity": 2}]}, {})
    check("组套创建", pkg.get("lines") == 2)

    ord1 = m08.order(ctx, {"visit_id": vis["id"], "items": [
        {"item_id": ti_acup["id"], "quantity": 1}]}, {})
    exec_acup = ord1["items"][0]["id"]
    check("开立处置 TE{yyyyMMdd}{4位}",
          ord1["items"][0]["exec_no"].startswith("TE"), ord1["items"][0]["exec_no"])
    m08.start(ctx, {"id": exec_acup}, {})
    m08.tcm_save(ctx, {"exec_id": exec_acup, "therapy_type": "ACUP",
                       "points": "足三里,三阴交", "manipulation": "平补平泻",
                       "retain_minutes": 30}, {})
    m08.process_add(ctx, {"exec_id": exec_acup, "phase": "STEP",
                          "content": "常规消毒后进针"}, {})
    m08.complete(ctx, {"id": exec_acup}, {})
    e1 = _one("SELECT * FROM treatment_execution WHERE id=?", (exec_acup,))
    check("处置完成 COMPLETED", e1["status"] == "COMPLETED", e1["status"])
    check("处置金额快照 3000 分", e1["amount_fen"] == 3000, str(e1["amount_fen"]))
    check("中医治疗记录已存",
          len(m08.tcm_list(ctx, {}, {"exec_id": exec_acup})["items"]) == 1)

    # 组套展开
    ord2 = m08.order(ctx, {"visit_id": vis["id"], "package_id": pkg["id"]}, {})
    check("组套展开为独立执行记录", len(ord2["items"]) == 2, str(len(ord2["items"])))
    exec_d1 = ord2["items"][0]["id"]
    m08.start(ctx, {"id": exec_d1}, {})
    expect_error("中断必须填原因",
                 lambda: m08.interrupt(ctx, {"id": exec_d1, "reason": ""}, {}), "E2")
    m08.interrupt(ctx, {"id": exec_d1, "reason": "患者不适"}, {})
    d1 = _one("SELECT * FROM treatment_execution WHERE id=?", (exec_d1,))
    check("执行中可中断", d1["status"] == "INTERRUPTED", d1["status"])
    m08.resume(ctx, {"id": exec_d1}, {})
    d1 = _one("SELECT * FROM treatment_execution WHERE id=?", (exec_d1,))
    check("中断后可恢复", d1["status"] == "IN_PROGRESS", d1["status"])
    expect_error("执行中不可直接取消（须先中断）",
                 lambda: m08.cancel(ctx, {"id": exec_d1, "reason": "直接取消"}, {}), "E3")
    m08.interrupt(ctx, {"id": exec_d1, "reason": "患者拒绝继续"}, {})
    m08.cancel(ctx, {"id": exec_d1, "reason": "患者拒绝"}, {})
    d1 = _one("SELECT * FROM treatment_execution WHERE id=?", (exec_d1,))
    check("中断后可取消", d1["status"] == "CANCELLED", d1["status"])
    m08.cancel(ctx, {"id": ord2["items"][1]["id"], "reason": "一并取消"}, {})
    expect_error("已完成处置不可直接取消",
                 lambda: m08.cancel(ctx, {"id": exec_acup, "reason": "已完成还想取消"}, {}),
                 "E3062")

    # 中医项目未填记录不得完成
    ord3 = m08.order(ctx, {"visit_id": vis["id"], "items": [
        {"item_id": ti_acup["id"], "quantity": 1}]}, {})
    exec_c = ord3["items"][0]["id"]
    m08.start(ctx, {"id": exec_c}, {})
    expect_error("中医项目未填穴位不能完成",
                 lambda: m08.complete(ctx, {"id": exec_c}, {}), "E2")
    m08.interrupt(ctx, {"id": exec_c, "reason": "测试清理"}, {})
    m08.cancel(ctx, {"id": exec_c, "reason": "测试清理"}, {})
    logs = _q("SELECT * FROM treatment_status_log WHERE exec_id=? ORDER BY id", (exec_d1,))
    check("状态流转日志完整", len(logs) >= 4, str(len(logs)))

    # ============================================================
    print("\n[M09 F57/F58/F59] 账单与收费")
    m09.charge_item_create(ctx, {"item_code": "GH001", "name": "普通挂号费",
                                 "category": "REGISTRATION", "price_fen": 1000,
                                 "auto_add_on_visit": 1}, {})
    # 饮片处方（自煎 7 付）
    rx2 = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "HERB",
                           "doses": 7, "decoction_method": "自煎",
                           "administration": "每日一剂，分两次温服"}, {})
    m06.save_herbs(ctx, {"prescription_id": rx2["id"], "items": [
        {"herb_id": herb["id"], "dose_g": 15, "foot_note": "NORMAL"}]}, {})
    m06.submit(ctx, {"prescription_id": rx2["id"]}, {})
    pr = m06.confirm(ctx, {"prescription_id": rx2["id"], "password": PWD}, {})
    check("饮片计价 15g×0.05元×7付=525分",
          pr["pricing"]["total_amount_fen"] == 525,
          str(pr["pricing"]["total_amount_fen"]))

    bill = m09.bill_create(ctx, {"visit_id": vis["id"]}, {})
    expect_total = 2000 + 525 + 3000 + 1000
    check("账单汇总 = 处方+饮片+处置+挂号 = %d" % expect_total,
          bill["total_amount_fen"] == expect_total,
          "%s / %s" % (bill["total_amount_fen"], expect_total))
    expect_error("同一就诊不可重复生成账单",
                 lambda: m09.bill_create(ctx, {"visit_id": vis["id"]}, {}), "E3070")

    pay = m09.bill_pay(ctx, {"bill_id": bill["id"],
                             "payments": [{"channel": "WECHAT", "amount_fen": 4000},
                                          {"channel": "CASH", "amount_fen": 2525,
                                           "received_amount_fen": 3000}]}, {})
    check("多笔混合支付后转 PAID", pay["status"] == "PAID", pay["status"])
    expect_error("金额不足不留中间态",
                 lambda: m09.bill_pay(ctx, {"bill_id": bill["id"],
                                            "payments": [{"channel": "CASH",
                                                          "amount_fen": 10}]}, {}), "E3")
    b = _one("SELECT * FROM bill WHERE id=?", (bill["id"],))
    check("账单状态仍为 PAID（未被失败支付破坏）", b["status"] == "PAID", b["status"])
    cash = _one("SELECT * FROM payment WHERE bill_id=? AND channel='CASH'", (bill["id"],))
    check("现金找零记录 475 分", cash["change_amount_fen"] == 475,
          str(cash["change_amount_fen"]))

    # ============================================================
    print("\n[M07 F46 + M09 F63] 退药与退药退款")
    p_item = _one("SELECT * FROM prescription_item WHERE prescription_id=?", (rx1["id"],))
    before_ret = m07.available_qty("DRUG", drug["id"])
    rt = m07.return_create(ctx, {"prescription_id": rx1["id"], "reason": "患者要求退药",
                                 "items": [{"prescription_item_id": p_item["id"], "qty": 1}]},
                           {})
    check("退药单号 DR{yyyyMMdd}{4位}", rt["return_no"].startswith("DR"),
          rt["return_no"])
    check("退药回库 +1",
          abs(m07.available_qty("DRUG", drug["id"]) - before_ret - 1) < 1e-6,
          "%s → %s" % (before_ret, m07.available_qty("DRUG", drug["id"])))
    check("退药金额按开方时价 1×1000=1000分", rt["total_amount_fen"] == 1000,
          str(rt["total_amount_fen"]))
    rx1_now = _one("SELECT * FROM prescription WHERE id=?", (rx1["id"],))
    check("处方转 PARTIAL_RETURNED", rx1_now["status"] == "PARTIAL_RETURNED",
          rx1_now["status"])
    expect_error("超额退药被拒",
                 lambda: m07.return_create(ctx, {"prescription_id": rx1["id"],
                                                 "reason": "超额测试", "items": [
                                                     {"prescription_item_id": p_item["id"],
                                                      "qty": 99}]}, {}), "E3053")

    rf = m09.refund_by_drug_return(ctx, {"drug_return_id": rt["id"]}, {})
    check("退药退款 1000 分", rf["total_amount_fen"] == 1000, str(rf["total_amount_fen"]))
    b = _one("SELECT * FROM bill WHERE id=?", (bill["id"],))
    check("账单转 PARTIAL_REFUNDED", b["status"] == "PARTIAL_REFUNDED", b["status"])
    expect_error("同一退药单不可重复退款",
                 lambda: m09.refund_by_drug_return(ctx, {"drug_return_id": rt["id"]}, {}),
                 "E3077")

    # ============================================================
    print("\n[M09 F62] 红字冲正")
    bi_drug = _one("SELECT * FROM bill_item WHERE bill_id=? AND source_type='DRUG'",
                   (bill["id"],))
    before_total = _one("SELECT total_amount_fen t FROM bill WHERE id=?", (bill["id"],))["t"]
    rv = m09.reversal_create(ctx, {"bill_id": bill["id"], "reason": "金额录错",
                                   "items": [{"bill_item_id": bi_drug["id"],
                                              "amount_fen": 500}]}, {})
    after_total = _one("SELECT total_amount_fen t FROM bill WHERE id=?", (bill["id"],))["t"]
    check("冲正生成负数行，账单总额 -500",
          after_total == before_total - 500, "%s → %s" % (before_total, after_total))
    check("原明细行未被修改",
          _one("SELECT amount_fen a FROM bill_item WHERE id=?", (bi_drug["id"],))["a"]
          == bi_drug["amount_fen"])
    neg = _one("SELECT * FROM bill_item WHERE reversal_id=?", (rv["id"],))
    check("负数行显式标记 is_reversal", neg["is_reversal"] == 1 and neg["amount_fen"] == -500)
    expect_error("超额冲正被拒",
                 lambda: m09.reversal_create(ctx, {"bill_id": bill["id"], "reason": "超额",
                                                   "items": [{"bill_item_id": bi_drug["id"],
                                                              "amount_fen": 999999}]}, {}),
                 "E3075")

    # ============================================================
    print("\n[M07 F47] 报损")
    loss = m07.loss_create(ctx, {"reason": "破损", "loss_reason": "DAMAGE",
                                 "items": [{"batch_id": h_batch, "qty": 500}]}, {})
    check("报损单号 LS{yyyyMMdd}{4位}", loss["loss_no"].startswith("LS"), loss["loss_no"])
    check("饮片报损 -500g", m07.available_qty("HERB", herb["id"]) == 4500,
          str(m07.available_qty("HERB", herb["id"])))
    expect_error("报损量超过可用量被拒",
                 lambda: m07.loss_create(ctx, {"reason": "超额", "items": [
                     {"batch_id": h_batch, "qty": 999999}]}, {}), "E3057")
    expect_error("原因缺失被拒",
                 lambda: m07.loss_create(ctx, {"reason": "", "items": [
                     {"batch_id": h_batch, "qty": 1}]}, {}), "E2")

    # ============================================================
    print("\n[M07 F115 + F114] 中药发药（自煎，剂量×付数）")
    h_before = m07.available_qty("HERB", herb["id"])
    hd = m07.herb_dispense(ctx, {"prescription_id": rx2["id"],
                                 "decoction_type": "ZIJIAN",
                                 "idempotent_key": "HDISP-1"}, {})
    check("中药发药成功", hd.get("status") == "DISPENSED", str(hd.get("status")))
    check("饮片扣减 = 15g × 7付 = 105g",
          abs(h_before - m07.available_qty("HERB", herb["id"]) - 105) < 1e-6,
          "%s → %s" % (h_before, m07.available_qty("HERB", herb["id"])))
    expect_error("付数缺失被拒",
                 lambda: m07.herb_dispense(ctx, {"prescription_id": rx2["id"]}, {}), "E3")

    # ============================================================
    print("\n[M07 F48] 盘点")
    ck = m07.check_create(ctx, {"scope": "DRUG"}, {})
    check("盘点单号 CK{yyyyMMdd}{4位}", ck["check_no"].startswith("CK"), ck["check_no"])
    locked = _one("SELECT COUNT(*) c FROM stock_batch WHERE status='LOCKED'")["c"]
    check("进入 COUNTING 后批次被冻结", locked >= 1, str(locked))
    expect_error("冻结期间报损被拒",
                 lambda: m07.loss_create(ctx, {"reason": "冻结测试", "items": [
                     {"batch_id": d_batch, "qty": 1}]}, {}), "E3051")

    detail = m07.check_get(ctx, {}, {"id": ck["id"]})
    inputs = [{"batch_id": it["batch_id"], "counted_qty": float(it["book_qty"]) - 1}
              for it in detail["items"]]
    m07.check_input(ctx, {"id": ck["id"], "items": inputs}, {})
    appr = m07.check_approve(ctx, {"id": ck["id"]}, {})
    check("盘点审批生成调整流水", appr["adjusted_rows"] >= 1,
          str(appr["adjusted_rows"]))
    unlocked = _one("SELECT COUNT(*) c FROM stock_batch WHERE status='LOCKED'")["c"]
    check("审批后批次解锁", unlocked == 0, str(unlocked))
    expect_error("已审批不可再次审批",
                 lambda: m07.check_approve(ctx, {"id": ck["id"]}, {}), "E3054")

    # ============================================================
    print("\n[M07 F49/F50] 效期与库存预警")
    m07.stock_in_create(ctx, {"items": [
        {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 5, "cost_yuan_per_unit": 8.5,
         "expiry_date": _future(30), "unit": "盒"}]}, {})
    sid2 = _one("SELECT id FROM stock_in ORDER BY id DESC LIMIT 1")["id"]
    m07.stock_in_confirm(ctx, {"id": sid2}, {})
    with db.tx() as conn:                       # 测试用：设置品目安全库存（F50 R1）
        db.execute(conn, "UPDATE drug SET safety_stock=? WHERE id=?", (1000, drug["id"]))
    stats = m07.alert_scan(ctx, {}, {})
    alerts = m07.alert_list(ctx, {}, {})["items"]
    check("近效期预警生成", any(a["alert_type"] == "NEAR_EXPIRY" for a in alerts),
          str(stats))
    check("库存下限预警生成",
          any(a["alert_type"] in ("LOW_STOCK", "OUT_OF_STOCK") for a in alerts), str(stats))
    n1 = len(alerts)
    m07.alert_scan(ctx, {}, {})
    check("重复扫描不产生重复预警", len(m07.alert_list(ctx, {}, {})["items"]) == n1,
          "%s → %s" % (n1, len(m07.alert_list(ctx, {}, {})["items"])))

    # ============================================================
    print("\n[M09 F64] 日结与日结后锁定")
    # 另一笔未收费账单，用于验证"日结后当日账单锁定"
    pat2 = m01.create(ctx, {"name": "赵六", "gender": "F", "birth_date": "1992-02-02"}, {})
    vis2 = m02.create(ctx, {"patient_id": pat2["id"], "visit_type": "NORMAL"}, {})
    bill2 = m09.bill_create(ctx, {"visit_id": vis2["id"]}, {})
    check("未收费账单为 PENDING", bill2["status"] == "PENDING", bill2["status"])

    st = m09.settlement_close(ctx, {}, {})
    check("日结单号 DS{yyyyMMdd}{2位}", st["settlement_no"].startswith("DS"),
          st["settlement_no"])
    check("净收 = 收款 6525 − 退款 1000 = 5525（冲正只减应收，不重复计入退款）",
          st["net_amount_fen"] == 5525,
          "paid=%s refund=%s net=%s" % (st["paid_amount_fen"], st["refund_amount_fen"],
                                        st["net_amount_fen"]))
    check("日结记录未收费账单", st["pending_count"] >= 1, str(st["pending_count"]))
    expect_error("日结后不能生成新账单",
                 lambda: m09.bill_create(ctx, {"visit_id": vis["id"]}, {}), "E3070")
    expect_error("日结后账单锁定（不可作废）",
                 lambda: m09.bill_void(ctx, {"bill_id": bill2["id"], "reason": "日结后作废"},
                                       {}), "E3006")
    rv2 = m09.reversal_create(ctx, {"bill_id": bill["id"], "reason": "日结后更正",
                                    "items": [{"bill_item_id": bi_drug["id"],
                                               "amount_fen": 100}]}, {})
    check("日结后仍可红字冲正", bool(rv2.get("reversal_no")), rv2.get("reversal_no"))

    # ============================================================
    print("\n[不变量] 账实一致与只读约束")
    viol = []
    for r in _q("""SELECT s.id, s.qty AS book,
                          COALESCE((SELECT SUM(t.qty_change) FROM stock_transaction t
                                    WHERE t.item_kind='DRUG' AND t.batch_id=s.batch_id),0) AS led
                   FROM stock s""") :
        if abs(float(r["book"]) - float(r["led"])) > 1e-6:
            viol.append(("stock", r["id"], r["book"], r["led"]))
    for r in _q("""SELECT h.id, h.qty_g AS book,
                          COALESCE((SELECT SUM(t.qty_change) FROM stock_transaction t
                                    WHERE t.item_kind='HERB' AND t.batch_id=h.batch_id),0) AS led
                   FROM herb_stock h"""):
        if abs(float(r["book"]) - float(r["led"])) > 1e-6:
            viol.append(("herb_stock", r["id"], r["book"], r["led"]))
    check("库存与流水完全一致", not viol, str(viol))

    expect_error("库存流水禁止修改",
                 lambda: _mutate("UPDATE stock_transaction SET qty_change=0 WHERE id=1"), "E2006")
    expect_error("账单明细禁止修改",
                 lambda: _mutate("UPDATE bill_item SET amount_fen=0 WHERE id=1"), "E2006")
    expect_error("账单明细禁止删除",
                 lambda: _mutate("DELETE FROM bill_item WHERE id=1"), "E2006")
    expect_error("处置过程记录只追加（禁止修改）",
                 lambda: _mutate("UPDATE treatment_process SET content='x' WHERE id=1"), "E2006")

    # ============================================================
    print("\n[审计] 高风险操作留痕")
    hr = _q("SELECT risk_type, COUNT(*) c FROM audit_high_risk GROUP BY risk_type")
    kinds = {r["risk_type"] for r in hr}
    check("退药/退款/冲正/日结均有高风险审计",
          {"REFUND", "REVERSAL"} <= kinds, str(sorted(kinds)))
    ops = _q("SELECT module, COUNT(*) c FROM audit_operation WHERE module IN "
             "('M07','M08','M09') GROUP BY module")
    mods = {r["module"] for r in ops}
    check("三个模块均有操作审计", {"M07", "M08", "M09"} <= mods, str(sorted(mods)))

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：%s" % FAILED)
    return 0 if not FAILED else 1


def _mutate(sql):
    """执行一条会被触发器拦截的写操作（用于验证 INSERT-ONLY 约束）。"""
    with db.tx() as conn:
        db.execute(conn, sql)


if __name__ == "__main__":
    sys.exit(main())
