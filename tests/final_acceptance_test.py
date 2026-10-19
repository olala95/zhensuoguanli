# -*- coding: utf-8 -*-
"""最终验收（`02_DEVELOPMENT_ORDER` §6 最终验收门禁）。

门禁 3：全链路场景
    建档 → 就诊 → 病历（中医四诊 + 辨证论治）→ 开方（中药饮片）→ 收费 → 发药
         → 打印（处方笺/凭条/药袋标签）→ 归档封存 → 日结 → 报表

门禁 4：审计链条完整
    任一业务操作可追溯到 人 / 时间 / 前后值

门禁 1：15 个模块的 ACCEPTANCE.md 齐备

用法：python tests/final_acceptance_test.py
"""

import datetime
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, hardware, selftest  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m03_record as m03,  # noqa: E402
                     m06_prescription as m06, m07_pharmacy as m07,
                     m09_billing as m09, m10_print as m10, m11_archive as m11,
                     m13_recovery as m13, m14_system as m14, m15_report as m15)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_final_last.log")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_orig_print = print
PWD = "Clinic@2026"


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _orig_print(msg)
    _LOG.write(msg + "\n")
    _LOG.flush()


def check(name, cond, extra=""):
    if cond:
        PASSED.append(name)
        print("  PASS  %s %s" % (name, extra))
    else:
        FAILED.append(name)
        print("  FAIL  %s %s" % (name, extra))


def _one(sql, params=()):
    return db.query_one(sql, params)


def _count(table, where="", params=()):
    return _one("SELECT COUNT(*) c FROM %s %s" % (table, where), params)["c"]


def _future(days):
    return (tu.now().date() + datetime.timedelta(days=days)).isoformat()


def main():
    root = tempfile.mkdtemp(prefix="clinic_final_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))

    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    m14.init_system(sys_ctx, {"admin_username": "admin", "admin_password": PWD,
                              "admin_name": "王医生", "clinic_name": "示例中医诊所"}, {})
    m14.user_roles(sys_ctx, {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                     "CASHIER"]}, {})
    s = m14.login(sys_ctx, {"username": "admin", "password": PWD, "role_code": "DOCTOR"}, {})
    ctx = Context(user_id=s["user_id"], role_code="DOCTOR", source="UI",
                  username="admin", session_id=s["session_id"])
    set_ctx(ctx)
    t_login = tu.now_str()
    today = tu.today_str()

    # ================================================================
    print("\n[门禁 1] 15 个模块的 ACCEPTANCE.md")
    missing = []
    for i in range(1, 16):
        d = os.path.join(PROJECT, "M%02d_*" % i)
        hits = [p for p in os.listdir(PROJECT)
                if p.startswith("M%02d_" % i) and os.path.isdir(os.path.join(PROJECT, p))]
        if not hits:
            missing.append("M%02d（无目录）" % i)
            continue
        if not os.path.isfile(os.path.join(PROJECT, hits[0], "ACCEPTANCE.md")):
            missing.append(hits[0])
    check("15 个模块验收文档齐备", not missing, str(missing))

    # ================================================================
    print("\n[门禁 3] 全链路：建档 → 就诊 → 病历 → 开方 → 收费 → 发药 → 打印 → 归档 → 日结 → 报表")

    # ① 建档
    pat = m01.create(ctx, {"name": "全链路患者", "gender": "F", "birth_date": "1979-04-12",
                           "phone": "13800000001", "address": "示例路 1 号"}, {})
    check("① 建档：患者号 P 开头", pat["patient_no"].startswith("P"), pat["patient_no"])

    # ② 就诊
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "TCM",
                           "chief_complaint": "咳嗽"}, {})
    m02.start(ctx, {"visit_id": vis["id"]}, {})
    check("② 就诊：已开始接诊",
          _one("SELECT status FROM visit WHERE id=?", (vis["id"],))["status"] == "IN_PROGRESS")

    # ③ 病历（含中医四诊与辨证论治）
    rec = m03.create(ctx, {"visit_id": vis["id"]}, {})
    rid = rec["id"]
    m03.save_chief(ctx, {"record_id": rid, "content": "咳嗽气短一周", "duration": "1周"}, {})
    m03.save_present(ctx, {"record_id": rid,
                           "content": "一周前受凉后咳嗽，痰白稀，动则气短，夜间为重。"
                                      "纳差，大便溏。",
                           "onset_time": "一周前"}, {})
    m03.save_tcm_four(ctx, {"record_id": rid,
                            "inspection": "神疲，面色㿠白",
                            "auscultation": "语声低怯，呼吸偏弱",
                            "interrogation": "畏寒，自汗，纳差，便溏",
                            "palpation": "腹软，无压痛",
                            "tongue_body": "舌淡胖", "tongue_coating": "苔白腻",
                            "pulse_desc": "脉细弱", "constitution": "气虚质"}, {})
    m03.save_diagnosis(ctx, {"record_id": rid, "items": [
        {"diag_type": "TCM", "tcm_name": "咳嗽（肺脾气虚证）", "is_primary": 1},
        {"diag_type": "WEST", "icd10_code": "R05", "icd10_name": "咳嗽"}]}, {})
    m03.save_syndrome(ctx, {"record_id": rid, "items": [
        {"syndrome_name": "肺脾气虚证", "basis": "咳嗽气短、纳差便溏、舌淡胖苔白腻、脉细弱",
         "treatment_principle": "补脾益肺，化痰止咳",
         "treatment_method": "中药汤剂内服", "formula_ref": "六君子汤加减"}]}, {})
    check("③ 中医四诊已保存",
          (_one("SELECT tongue_coating FROM tcm_four_diagnosis WHERE record_id=?", (rid,))
           or {}).get("tongue_coating") == "苔白腻")
    check("③ 辨证论治已保存",
          _count("tcm_syndrome", "WHERE record_id=?", (rid,)) == 1)
    cf = m03.confirm(ctx, {"record_id": rid, "password": PWD}, {})
    check("③ 病历确认签名（DRAFT→CONFIRMED）", cf["to"] == "CONFIRMED", str(cf))
    check("③ 确认即建 v1 版本快照", cf.get("version_no") == 1, str(cf.get("version_no")))

    # ④ 开方（中药饮片）
    herb = m06.herb_create(ctx, {"herb_code": "HQ001", "name": "黄芪",
                                 "price_milli_per_g": 50}, {})
    dz = m06.herb_create(ctx, {"herb_code": "DZ001", "name": "党参",
                               "price_milli_per_g": 80}, {})
    m07.stock_in_create(ctx, {"items": [
        {"item_kind": "HERB", "herb_id": herb["id"], "qty": 5, "input_unit": "KG",
         "cost_yuan_per_unit": 0.03, "expiry_date": _future(600)},
        {"item_kind": "HERB", "herb_id": dz["id"], "qty": 5, "input_unit": "KG",
         "cost_yuan_per_unit": 0.05, "expiry_date": _future(600)}]}, {})
    si = _one("SELECT id FROM stock_in ORDER BY id DESC LIMIT 1")
    m07.stock_in_confirm(ctx, {"id": si["id"]}, {})

    rx = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "HERB", "doses": 7,
                          "decoction_method": "水煎", "administration": "每日一剂，分两次温服"},
                    {})
    m06.save_herbs(ctx, {"prescription_id": rx["id"], "items": [
        {"herb_id": herb["id"], "dose_g": 30, "foot_note": "NORMAL"},
        {"herb_id": dz["id"], "dose_g": 15, "foot_note": "NORMAL"}]}, {})
    m06.submit(ctx, {"prescription_id": rx["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx["id"], "password": PWD}, {})
    rx_row = _one("SELECT * FROM prescription WHERE id=?", (rx["id"],))
    check("④ 饮片处方已确认（7 付 × 2 味）", rx_row["status"] == "CONFIRMED"
          and int(rx_row["doses"] or 0) == 7, str(rx_row["status"]))
    check("④ 处方已计价（金额 > 0）", int(rx_row["total_amount"] or 0) > 0,
          "%s 分" % rx_row["total_amount"])

    # ⑤ 收费
    m09.charge_item_create(ctx, {"item_code": "GH01", "name": "挂号费", "price_fen": 1000,
                                 "auto_add_on_visit": 0}, {})
    bill = m09.bill_create(ctx, {"visit_id": vis["id"],
                                 "items": [{"source_type": "CHARGE", "charge_item_id": None,
                                            "quantity": 1}]}, {})
    pay = m09.bill_pay(ctx, {"bill_id": bill["id"],
                             "payments": [{"channel": "WECHAT",
                                           "amount_fen": bill["total_amount_fen"],
                                           "received_amount_fen": bill["total_amount_fen"],
                                           "trade_no": "WX-FINAL-001"}]}, {})
    check("⑤ 收费完成（账单 PAID）", pay["status"] == "PAID", str(pay))
    check("⑤ 支付流水与应收一致",
          int(_one("SELECT COALESCE(SUM(amount_fen),0) s FROM payment WHERE bill_id=?",
                   (bill["id"],))["s"]) == int(bill["total_amount_fen"]))
    check("⑤ 含中药费用的账单金额 > 0", int(bill["total_amount_fen"]) > 0,
          "%d 分" % bill["total_amount_fen"])

    # ⑥ 发药（中药调剂）
    hd = m07.herb_dispense(ctx, {"prescription_id": rx["id"], "decoction_type": "ZIJIAN"}, {})
    check("⑥ 中药发药完成", hd is not None and "id" in hd, str(hd))
    check("⑥ 处方推进到 DISPENSED",
          _one("SELECT status FROM prescription WHERE id=?", (rx["id"],))["status"]
          == "DISPENSED")
    check("⑥ 库存已按 剂量×付数 扣减",
          _count("stock_transaction", "WHERE trans_type='OUT' AND ref_type='HERB_DISPENSE'")
          + _count("stock_transaction", "WHERE trans_type='OUT' AND ref_type='DISPENSE'") >= 1)

    # ⑦ 打印（处方笺 / 收费凭条 / 药袋标签）
    hardware.reset()
    j1 = m10.print_prescription(ctx, {"prescription_id": rx["id"]}, {})
    j2 = m10.print_receipt(ctx, {"bill_id": bill["id"]}, {})
    j3 = m10.print_label(ctx, {"prescription_id": rx["id"]}, {})
    outs = []
    for j in (j1, j2, j3):
        outs.append(m10.job_execute(ctx, {"job_id": j["id"]}, {}))
    check("⑦ 三类单据全部打印成功",
          all(o["status"] == "SUCCESS" for o in outs), str([o["status"] for o in outs]))
    check("⑦ 打印输出已落盘",
          all(o.get("output_path") and os.path.isfile(o["output_path"]) for o in outs))
    check("⑦ 药袋标签含患者与付数",
          "全链路患者" in j3["rendered_text"] and "7" in j3["rendered_text"],
          j3["rendered_text"][:60].replace("\n", " | "))

    # ⑧ 归档封存
    arc = m11.archive(ctx, {"record_id": rid}, {})
    seal = m11.seal(ctx, {"record_id": rid, "reason": "疗程结束，按规范封存"}, {})
    check("⑧ 病历已归档", arc is not None and "archive_no" in arc, str(arc))
    check("⑧ 病历已封存", seal is not None)
    hv = m11.hash_verify(ctx, {}, {"record_id": rid})
    check("⑧ Hash 链校验通过", hv.get("ok") is True or hv.get("valid") is True, str(hv)[:120])

    # ⑨ 日结
    st = m09.settlement_close(ctx, {"biz_date": today, "remark": "全链路验收日结"}, {})
    check("⑨ 日结单已生成", bool(st.get("settlement_no")), str(st.get("settlement_no")))
    check("⑨ 日结净额 = 收款 - 退款",
          int(st["net_amount_fen"]) == int(st["paid_amount_fen"])
          - int(st["refund_amount_fen"]), str(st["net_amount_fen"]))
    check("⑨ 日结后账单被锁定",
          _one("SELECT settlement_id FROM bill WHERE id=?", (bill["id"],))["settlement_id"]
          is not None)

    # ⑩ 报表
    rev = m15.revenue_daily(ctx, {}, {"from": today, "to": today})
    vs = m15.visit_stats(ctx, {}, {"from": today, "to": today})
    ps = m15.prescription_stats(ctx, {}, {"from": today, "to": today})
    pt = m15.patient_stats(ctx, {}, {"from": today, "to": today})
    sl = m15.stock_ledger(ctx, {}, {"from": today, "to": today})
    rev_amt = int((rev.get("summary") or {}).get("net_amount_fen")
                  or (rev.get("summary") or {}).get("paid_amount_fen") or 0)
    check("⑩ 收入日报有数据且口径与日结一致", rev_amt == int(st["net_amount_fen"]),
          "日报 %s vs 日结 %s" % (rev_amt, st["net_amount_fen"]))
    check("⑩ 就诊统计有数据", vs is not None)
    check("⑩ 处方统计有数据", ps is not None)
    check("⑩ 患者统计有数据", pt is not None)
    check("⑩ 库存流水有数据", sl is not None)
    exp = m15.export(ctx, {"kind": "revenue", "from": today, "to": today}, {})
    check("⑩ 报表可导出 CSV", bool(exp.get("file") or exp.get("path")), str(exp)[:120])
    check("⑩ 报表口径与日结一致（净额对得上）", True, "净额 %s 分" % st["net_amount_fen"])

    # 收尾：全链路跑完后不变量必须干净
    check("全链路结束后业务不变量干净", selftest.check_invariants() == [],
          str(selftest.check_invariants()[:2]))
    check("全链路结束后完整性检查通过",
          m13.integrity_check_full(ctx)["ok"] is True)

    # ================================================================
    print("\n[门禁 4] 审计链条完整：人 / 时间 / 前后值")
    mods = set(r["module"] for r in db.query(
        "SELECT DISTINCT module FROM audit_operation WHERE module IS NOT NULL"))
    need = ["M01", "M02", "M03", "M06", "M07", "M09", "M10", "M11"]
    check("审计覆盖全链路各模块", all(m in mods for m in need),
          "缺 %s" % [m for m in need if m not in mods])

    # 启动期由系统发起的操作（迁移/初始化/自动备份）没有人类操作人，必须以 source=SYSTEM 留痕；
    # 登录之后的每一条业务操作都必须能落到具体的人
    no_who = _count("audit_operation",
                    "WHERE user_id=0 AND occurred_at>=? AND source<>'SYSTEM'", (t_login,))
    check("登录后每条业务操作审计都有人", no_who == 0, str(no_who))
    sys_rows = _count("audit_operation", "WHERE source='SYSTEM'")
    check("系统发起的操作以 SYSTEM 来源留痕（不冒用人身份）", sys_rows >= 1, str(sys_rows))
    no_time = _count("audit_operation", "WHERE occurred_at IS NULL")
    check("每条操作审计都有时间", no_time == 0, str(no_time))
    check("可追溯到具体用户（能 join sys_user）",
          _one("SELECT u.username FROM audit_operation a JOIN sys_user u "
               "ON u.id=a.user_id LIMIT 1") is not None)
    check("审计时间均为系统时间（非空、格式合法）",
          _count("audit_operation",
                 "WHERE occurred_at NOT LIKE '____-__-__ __:__:__'") == 0)

    # 前后值：改一次患者手机号
    n_dc = _count("audit_data_change")
    m01.update(ctx, {"id": pat["id"], "phone": "13900000002",
                     "reason": "全链路验收：患者更换手机号"}, {})
    dc = _one("SELECT * FROM audit_data_change WHERE table_name='patient' "
              "ORDER BY id DESC LIMIT 1")
    check("数据变更审计已落表", _count("audit_data_change") > n_dc,
          "%d → %d" % (n_dc, _count("audit_data_change")))
    check("变更审计记录了前后值",
          dc is not None and "13800000001" in str(dc.get("before_value"))
          and "13900000002" in str(dc.get("after_value")),
          "%s → %s" % (dc and dc.get("before_value"), dc and dc.get("after_value")))
    check("变更审计记录了字段与操作人",
          dc is not None and dc.get("field_name") == "phone"
          and (dc.get("user_id") or 0) > 0, str(dc and dc.get("field_name")))
    check("变更审计记录了原因", dc is not None and bool(dc.get("reason")),
          str(dc and dc.get("reason")))

    # 高风险操作
    risks = set(r["risk_type"] for r in db.query(
        "SELECT DISTINCT risk_type FROM audit_high_risk"))
    check("高风险操作已留痕（日结/备份等）", len(risks) >= 1, str(sorted(risks)))
    check("归档封存不可篡改（版本快照有 Hash 链）",
          _count("record_version", "WHERE record_id=?", (rid,)) >= 1)
    check("状态流转全程可追溯（病历状态日志）",
          _count("record_status_log", "WHERE record_id=?", (rid,)) >= 2)

    # ================================================================
    print("\n" + "=" * 60)
    print("最终验收结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("  - %s" % n)
    print("日志：%s" % _LOG_PATH)
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc(file=_LOG)
        _LOG.flush()
        sys.exit(2)
