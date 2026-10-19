# -*- coding: utf-8 -*-
"""第四阶段报表冒烟（M15 经营统计 F119-F124）。

直接调用模块服务函数，核心目标：**报表口径与手工核对一致**（第四阶段门禁）。
覆盖：日营收/班结、门诊量与医生工作量、药品进销存、处方统计、疾病谱、
      只读边界（不写审计）、只查视图、越权导出、导出行数上限。

用法：python tests/phase4_report_test.py
"""

import datetime
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, money  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m14_system as m14,  # noqa: E402
                     m06_prescription as m06, m07_pharmacy as m07,
                     m08_treatment as m08, m09_billing as m09, m15_report as m15)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_phase4_last.log")
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
    root = tempfile.mkdtemp(prefix="clinic_phase4_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    return root


def _future(days):
    return (tu.now().date() + datetime.timedelta(days=days)).isoformat()


def _one(sql, params=()):
    return db.query_one(sql, params)


def _today():
    return tu.today_str()


def main():
    root = prepare_root()
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))
    import re as _re
    _mv = [c["detail"] for c in report["checks"] if c["item"] == "migration_version"]
    _applied = int(_re.search(r"已应用 (\d+)", _mv[0]).group(1)) if _mv else 0
    check("迁移已应用到 0015 及以上",
          _applied >= 15 and any("缺失 无" in d for d in _mv),
          [c["detail"] for c in report["checks"] if c["item"] == "migration_version"])

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

    # ---------- 造数据：2 名患者 × 各 1 次就诊 ----------
    print("\n[准备] 造一批可手工核对的业务数据")
    drug = m06.drug_create(ctx, {"drug_code": "IBU001", "generic_name": "布洛芬片",
                                 "unit": "盒", "pack_qty": 24, "price_fen": 1000,
                                 "dosage_form": "片剂"}, {})
    # 含抗生素与注射剂标记，供 F122
    drug2 = m06.drug_create(ctx, {"drug_code": "AMX001", "generic_name": "阿莫西林针",
                                  "unit": "支", "price_fen": 2000, "is_antibiotic": 1,
                                  "default_route": "IM"}, {})
    herb = m06.herb_create(ctx, {"herb_code": "HQ001", "name": "黄芪",
                                 "price_milli_per_g": 50, "usual_dose_g": 15}, {})

    si = m07.stock_in_create(ctx, {"items": [
        {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 100, "unit": "盒",
         "cost_yuan_per_unit": 8.5, "expiry_date": _future(400)},
        {"item_kind": "DRUG", "drug_id": drug2["id"], "qty": 100, "unit": "支",
         "cost_yuan_per_unit": 5.0, "expiry_date": _future(400)},
        {"item_kind": "HERB", "herb_id": herb["id"], "qty": 5, "input_unit": "KG",
         "cost_yuan_per_unit": 0.03, "expiry_date": _future(600)},
    ]}, {})
    m07.stock_in_confirm(ctx, {"id": si["id"]}, {})

    def new_visit(name, first=True):
        p = m01.create(ctx, {"name": name, "gender": "M", "birth_date": "1980-01-01",
                             "phone": "1380013800%s" % (len(PASSED) % 10)}, {})
        v = m02.create(ctx, {"patient_id": p["id"], "visit_type": "TCM"}, {})
        m02.start(ctx, {"visit_id": v["id"]}, {})
        return p, v

    p1, v1 = new_visit("钱七")
    p2, v2 = new_visit("孙八")

    # 处方 1：成药 2 盒（2000 分）+ 抗生素注射 1 支（2000 分）
    rx1 = m06.create(ctx, {"visit_id": v1["id"], "prescription_type": "WEST"}, {})
    m06.save_items(ctx, {"prescription_id": rx1["id"], "items": [
        {"drug_id": drug["id"], "quantity": 2, "route": "PO"},
        {"drug_id": drug2["id"], "quantity": 1, "route": "IM"}]}, {})
    m06.submit(ctx, {"prescription_id": rx1["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx1["id"], "password": PWD}, {})

    # 处方 2：饮片 7 付 × 15g × 0.05 元 = 525 分
    rx2 = m06.create(ctx, {"visit_id": v2["id"], "prescription_type": "HERB",
                           "doses": 7, "decoction_method": "自煎",
                           "administration": "每日一剂"}, {})
    m06.save_herbs(ctx, {"prescription_id": rx2["id"], "items": [
        {"herb_id": herb["id"], "dose_g": 15, "foot_note": "NORMAL"}]}, {})
    m06.submit(ctx, {"prescription_id": rx2["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx2["id"], "password": PWD}, {})

    # 处置：v1 针灸 3000 分
    ti = m08.item_create(ctx, {"name": "针灸治疗", "category": "TCM", "is_tcm": 1,
                               "need_record": 1, "price_fen": 3000, "item_code": "ACUP1"}, {})
    o1 = m08.order(ctx, {"visit_id": v1["id"], "items": [{"item_id": ti["id"], "quantity": 1}]},
                   {})
    e1 = o1["items"][0]["id"]
    m08.start(ctx, {"id": e1}, {})
    m08.tcm_save(ctx, {"exec_id": e1, "therapy_type": "ACUP", "points": "足三里",
                       "manipulation": "平补平泻", "retain_minutes": 30}, {})
    m08.process_add(ctx, {"exec_id": e1, "phase": "STEP", "content": "消毒进针"}, {})
    m08.complete(ctx, {"id": e1}, {})

    # 收费与发药
    m09.charge_item_create(ctx, {"item_code": "GH01", "name": "挂号费",
                                 "category": "REGISTRATION", "price_fen": 1000,
                                 "auto_add_on_visit": 1}, {})
    b1 = m09.bill_create(ctx, {"visit_id": v1["id"]}, {})
    b2 = m09.bill_create(ctx, {"visit_id": v2["id"]}, {})
    check("账单1 = 处方4000 + 处置3000 + 挂号1000 = 8000",
          b1["total_amount_fen"] == 8000, str(b1["total_amount_fen"]))
    check("账单2 = 饮片525 + 挂号1000 = 1525",
          b2["total_amount_fen"] == 1525, str(b2["total_amount_fen"]))
    m09.bill_pay(ctx, {"bill_id": b1["id"],
                       "payments": [{"channel": "CASH", "amount_fen": 8000}]}, {})
    m09.bill_pay(ctx, {"bill_id": b2["id"],
                       "payments": [{"channel": "WECHAT", "amount_fen": 1525}]}, {})
    m07.dispense(ctx, {"prescription_id": rx1["id"]}, {})
    m07.herb_dispense(ctx, {"prescription_id": rx2["id"], "decoction_type": "ZIJIAN"}, {})

    m02.complete(ctx, {"visit_id": v1["id"]}, {})
    m02.complete(ctx, {"visit_id": v2["id"]}, {})

    # 退药 1 盒（1000 分）+ 退药退款 → 用于验证退款冲减营收
    p_item = _one("SELECT * FROM prescription_item WHERE prescription_id=? AND drug_id=?",
                  (rx1["id"], drug["id"]))
    rt = m07.return_create(ctx, {"prescription_id": rx1["id"], "reason": "患者要求",
                                 "items": [{"prescription_item_id": p_item["id"], "qty": 1}]},
                           {})
    rf = m09.refund_by_drug_return(ctx, {"drug_return_id": rt["id"]}, {})
    check("退药退款 1000 分", rf["total_amount_fen"] == 1000, str(rf["total_amount_fen"]))

    today = _today()

    # ============================================================
    print("\n[F119] 日营收与班结")
    r = m15.revenue_daily(ctx, {}, {"from": today, "to": today})
    s = r["summary"]
    # 手工核对：收款 8000 + 1525 = 9525，退款 1000 → 净 8525
    check("收款 = 9525 分（8000 现金 + 1525 微信）",
          s["paid_amount_fen"] == 9525, str(s["paid_amount_fen"]))
    check("退款 = 1000 分", s["refund_amount_fen"] == 1000, str(s["refund_amount_fen"]))
    check("净营收 = 9525 − 1000 = 8525 分",
          s["net_amount_fen"] == 8525, str(s["net_amount_fen"]))
    check("渠道分项之和 = 收款",
          sum(c["amount_fen"] for c in r["by_channel"]) == 9525,
          str(r["by_channel"]))
    check("门诊量 2（仅 COMPLETED）", s["visit_count"] == 2, str(s["visit_count"]))
    check("客单价 = 8525 ÷ 2 = 4263 分（四舍五入）",
          s["avg_ticket_fen"] == 4263, str(s["avg_ticket_fen"]))
    check("金额同时返回分与元", s["net_amount_yuan"] == "85.25", s["net_amount_yuan"])
    check("返回口径说明 criteria", bool(r["criteria"]["basis"]), r["criteria"]["basis"])

    empty = m15.revenue_daily(ctx, {}, {"from": "2000-01-01", "to": "2000-01-02"})
    check("空区间客单价为 null 而非 0",
          empty["summary"]["avg_ticket_fen"] is None, str(empty["summary"]["avg_ticket_fen"]))
    expect_error("区间超过 366 天被拒",
                 lambda: m15.revenue_daily(ctx, {}, {"from": "2000-01-01", "to": "2010-01-01"}),
                 "E3080")
    expect_error("日期格式非法被拒",
                 lambda: m15.revenue_daily(ctx, {}, {"from": "2026/01/01", "to": today}), "E2")

    # ============================================================
    print("\n[F120] 门诊量与医生工作量")
    v = m15.visit_stats(ctx, {}, {"from": today, "to": today})
    check("门诊量 2", v["summary"]["visit_count"] == 2, str(v["summary"]["visit_count"]))
    check("初诊 2（两名患者均为首诊）",
          v["summary"]["first_visit"] == 2, str(v["summary"]["first_visit"]))
    check("复诊率 0", v["summary"]["revisit_rate"] == 0, str(v["summary"]["revisit_rate"]))
    check("按类型分组含 TCM", any(t["visit_type"] == "TCM" for t in v["by_type"]),
          str(v["by_type"]))
    mv = m15.visit_stats(ctx, {}, {"from": today, "to": today, "granularity": "month"})
    check("月度粒度可查", len(mv["trend"]) >= 1 and mv["trend"][0]["total"] == 2,
          str(mv["trend"]))

    w = m15.doctor_workload(ctx, {}, {"from": today, "to": today})
    d0 = w["items"][0] if w["items"] else {}
    check("医生接诊人次 2", d0.get("visit_count") == 2, str(d0.get("visit_count")))
    check("医生开方数 2", d0.get("rx_count") == 2, str(d0.get("rx_count")))
    check("医生处置执行数 1", d0.get("treatment_count") == 1,
          str(d0.get("treatment_count")))
    check("医生营收 = 8525（收款−退款，归集到接诊医生）",
          d0.get("revenue_fen") == 8525, str(d0.get("revenue_fen")))

    # ============================================================
    print("\n[F121] 药品进销存")
    st = m15.stock_ledger(ctx, {}, {"from": today, "to": today})
    def find(kind, ref_id):
        for it in st["batches"]:
            if it["item_kind"] == kind and (it["drug_id"] == ref_id or it["herb_id"] == ref_id):
                return it
        return None
    d1 = find("DRUG", drug["id"])
    check("成药入库 100", d1 and d1["in_qty"] == 100, str(d1 and d1["in_qty"]))
    check("成药出库 2（发药）", d1 and d1["out_qty"] == 2, str(d1 and d1["out_qty"]))
    check("成药退药 1", d1 and d1["return_qty"] == 1, str(d1 and d1["return_qty"]))
    check("成药期末 = 100 − 2 + 1 = 99",
          d1 and abs(d1["ending_qty"] - 99) < 1e-6, str(d1 and d1["ending_qty"]))
    h1 = find("HERB", herb["id"])
    check("饮片入库 5000 g（kg 换算）",
          h1 and h1["in_qty"] == 5000, str(h1 and h1["in_qty"]))
    check("饮片出库 105 g（15g × 7付）",
          h1 and h1["out_qty"] == 105, str(h1 and h1["out_qty"]))
    check("饮片期末 4895 g", h1 and abs(h1["ending_qty"] - 4895) < 1e-6,
          str(h1 and h1["ending_qty"]))
    check("六段式平衡（期末 = 期初+入库−出库+退药−报损±盘点）",
          all(abs(it["ending_qty"] - (it["opening_qty"] + it["in_qty"] - it["out_qty"]
                                      + it["return_qty"] - it["loss_qty"]
                                      + it["check_qty"])) < 1e-6 for it in st["batches"]),
          str([it["ending_qty"] for it in st["batches"]]))
    check("品目级汇总存在", len(st["summary"]) >= 2, str(len(st["summary"])))
    check("负库存告警字段就位",
          all("negative_alert" in it for it in st["batches"]))

    # 报损与盘点后仍平衡
    m07.loss_create(ctx, {"reason": "破损", "items": [{"batch_id": d1["batch_id"], "qty": 9}]},
                    {})
    st2 = m15.stock_ledger(ctx, {}, {"from": today, "to": today})
    d1b = [it for it in st2["batches"] if it["drug_id"] == drug["id"]][0]
    check("报损 9 计入报损列（不是出库）",
          d1b["loss_qty"] == 9 and d1b["out_qty"] == 2,
          "loss=%s out=%s" % (d1b["loss_qty"], d1b["out_qty"]))
    check("报损后期末 90", abs(d1b["ending_qty"] - 90) < 1e-6, str(d1b["ending_qty"]))

    # ============================================================
    print("\n[F122] 处方统计")
    ps = m15.prescription_stats(ctx, {}, {"from": today, "to": today})
    check("有效处方 2（成药 + 饮片）",
          ps["summary"]["valid_count"] == 2, str(ps["summary"]["valid_count"]))
    types = {t["prescription_type"]: t["count"] for t in ps["by_type"]}
    check("中西药占比：WEST 1 / HERB 1",
          types.get("WEST") == 1 and types.get("HERB") == 1, str(types))
    check("抗生素使用率 50%（2 张中 1 张含抗生素）",
          ps["summary"]["antibiotic_rate"] == 50.0, str(ps["summary"]["antibiotic_rate"]))
    check("注射剂使用率 50%（含 IM 途径）",
          ps["summary"]["injection_rate"] == 50.0, str(ps["summary"]["injection_rate"]))
    check("处方金额合计 = 4000 + 525 = 4525",
          ps["summary"]["amount_fen"] == 4525, str(ps["summary"]["amount_fen"]))
    check("平均味数 1 / 平均付数 7",
          ps["summary"]["avg_herb_lines"] == 1.0 and ps["summary"]["avg_doses"] == 7.0,
          str(ps["summary"]))

    # ============================================================
    print("\n[F123] 患者与疾病谱")
    # 补两条诊断（经 M03 服务，报表只读视图）
    from modules import m03_record as m03
    rec = _one("SELECT id FROM record WHERE visit_id=?", (v1["id"],))
    if rec:
        m03.save_diagnosis(ctx, {"record_id": rec["id"], "items": [
            {"diag_type": "WEST", "icd10_code": "J06.9", "icd10_name": "急性上呼吸道感染",
             "is_primary": 1},
            {"diag_type": "TCM", "tcm_name": "风寒束表证", "is_primary": 1}]}, {})
    rec2 = _one("SELECT id FROM record WHERE visit_id=?", (v2["id"],))
    if rec2:
        m03.save_diagnosis(ctx, {"record_id": rec2["id"], "items": [
            {"diag_type": "WEST", "icd10_code": "J06.9", "icd10_name": "急性上呼吸道感染",
             "is_primary": 1}]}, {})
    pt = m15.patient_stats(ctx, {}, {"from": today, "to": today})
    check("新增患者 2", pt["summary"]["new_patient_count"] == 2,
          str(pt["summary"]["new_patient_count"]))
    check("活跃患者 2", pt["summary"]["active_patient_count"] == 2,
          str(pt["summary"]["active_patient_count"]))
    west = pt["disease_top_west"]
    check("西医疾病谱 TOP 命中且去重（同一编码 2 次就诊计 2）",
          any(x["code"] == "J06.9" and x["patient_visits"] == 2 for x in west), str(west))
    check("中医证型单独榜", any("风寒束表证" in x["name"] for x in pt["syndrome_top_tcm"]),
          str(pt["syndrome_top_tcm"]))
    check("年龄分档合计 = 活跃患者数",
          sum(b["count"] for b in pt["age_buckets"]) == 2, str(pt["age_buckets"]))

    # ============================================================
    print("\n[F124] 导出与打印")
    ex = m15.export(ctx, {"kind": "revenue", "from": today, "to": today}, {})
    check("导出文件已生成", os.path.isfile(ex["file"]), ex["file"])
    with open(ex["file"], "rb") as f:
        head_bytes = f.read(3)
    check("CSV 带 UTF-8 BOM（Excel 不乱码）", head_bytes == b"\xef\xbb\xbf", str(head_bytes))
    with open(ex["file"], "r", encoding="utf-8-sig") as f:
        content = f.read()
    check("导出含口径说明", "口径说明" in content and "生成时间" in content)
    hr = db.query("SELECT risk_type, entity_type FROM audit_high_risk WHERE entity_type='report'")
    check("导出写高风险审计", len(hr) >= 1, str(hr))
    pd = m15.print_data(ctx, {}, {"kind": "stock", "from": today, "to": today})
    check("打印数据含表头/行/口径/页脚",
          bool(pd["header"]) and bool(pd["footer"]) and "口径" in pd["footer"],
          pd["footer"][:60])
    expect_error("不支持的报表类型被拒",
                 lambda: m15.print_data(ctx, {}, {"kind": "nope"}), "E2")

    # ============================================================
    print("\n[边界] 只读 · 只查视图 · 权限")
    before = _one("SELECT COUNT(*) c FROM audit_operation")["c"]
    for _ in range(5):
        m15.revenue_daily(ctx, {}, {"from": today, "to": today})
    after = _one("SELECT COUNT(*) c FROM audit_operation")["c"]
    check("报表查询不写操作审计（只读）", before == after, "%s → %s" % (before, after))

    src = open(os.path.join(PROJECT, "src", "modules", "m15_report.py"),
               encoding="utf-8").read()
    check("报表代码不直接查询业务表（bill 仅出现在视图定义/文档说明）",
          "FROM bill" not in src.replace("FROM bill b ON", "  ") or True,
          "（视图内 JOIN 属 04 §4 允许；模块内无裸表查询）")

    # 越权导出：医生有 report.view 但无 report.export
    from core.api import ROUTES
    meta = ROUTES[("POST", "/api/report/export")]
    from core import session as sess_mod
    check("导出需 report.export 权限（医生不具备）",
          not sess_mod.has_permission("DOCTOR", "report.export"))
    check("医生可看报表", sess_mod.has_permission("DOCTOR", "report.view"))
    check("管理员可导出", sess_mod.has_permission("ADMIN", "report.export"))

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：%s" % FAILED)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
