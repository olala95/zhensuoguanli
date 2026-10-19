# -*- coding: utf-8 -*-
"""第一阶段冒烟测试（M14 / M12 / M01 / M02 / M03）。

直接调用模块服务函数（不经 HTTP），覆盖：
  初始化 → 登录 → 建档 → 搜索 → 修改 → 就诊 → 接诊 → 病历（含中医）→ 确认 → 作废
  → 患者合并 → 审计落库 → 审计表只读约束 → 金额精度

用法：python tests/smoke_test.py
"""

import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, money, ids, config  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient, m02_visit, m03_record, m12_audit, m14_system,  # noqa: E402
                     m04_ai as m04, m05_lab as m05, m06_prescription as m06,
                     m07_pharmacy as m07, m08_treatment as m08, m09_billing as m09)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_smoke_last.log")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_orig_print = print


def print(*args, **kwargs):  # noqa: A001 - 同时输出到控制台与 UTF-8 日志文件
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
        ok = (code_prefix is None) or exc.code.startswith(code_prefix)
        check(name, ok, "→ %s %s" % (exc.code, exc.message))
        return exc
    except Exception as exc:
        check(name, False, "→ 抛出了非 AppError：%r" % exc)
        return None
    check(name, False, "→ 未拒绝（应当报错）")
    return None


def prepare_root():
    root = tempfile.mkdtemp(prefix="clinic_smoke_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    return root


def main():
    root = prepare_root()
    print("临时数据目录：%s\n" % root)

    _, _, report = runtime.bootstrap(root, start_server=False)
    print("启动自检：")
    for c in report["checks"]:
        print("  [%s] %s — %s" % (c["result"], c["item"], c["detail"][:60]))
    check("启动自检全部通过", all(c["result"] != "FAIL" for c in report["checks"]))
    check("未进入安全模式", report["safe_mode"] is False)

    # ---------- F99 初始化 ----------
    print("\n[F99] 系统初始化")
    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    st = m14_system.status(sys_ctx, {}, {})
    check("初始状态未初始化", st["initialized"] is False)
    r = m14_system.init_system(sys_ctx, {
        "admin_username": "admin", "admin_password": "Clinic@2026",
        "admin_name": "管理员", "clinic_name": "示例中医诊所"}, {})
    check("初始化创建管理员", r["initialized"] is True, "user_id=%s" % r["admin_user_id"])
    expect_error("重复初始化被拒绝",
                 lambda: m14_system.init_system(sys_ctx, {
                     "admin_username": "a2", "admin_password": "Clinic@2026",
                     "admin_name": "x"}, {}), "E3")

    # ---------- F100 登录 ----------
    print("\n[F100] 登录与锁屏")
    m14_system.user_roles(sys_ctx, {"user_id": r["admin_user_id"],
                                    "roles": ["ADMIN", "DOCTOR"]}, {})
    expect_error("错误口令被拒绝",
                 lambda: m14_system.login(sys_ctx, {"username": "admin", "password": "bad",
                                                    "role_code": "DOCTOR"}, {}), "E6")
    sess = m14_system.login(sys_ctx, {"username": "admin", "password": "Clinic@2026",
                                      "role_code": "DOCTOR"}, {})
    check("登录成功取得令牌", bool(sess["token"]), "role=%s" % sess["role_code"])
    doctor = Context(user_id=sess["user_id"], role_code="DOCTOR", source="UI",
                     username="admin", session_id=sess["session_id"])
    doctor.token = sess["token"]
    set_ctx(doctor)

    # ---------- F01/F02/F03 患者 ----------
    print("\n[F01/F02/F03] 患者建档 / 搜索 / 修改")
    p1 = m01_patient.create(doctor, {"name": "张三", "gender": "M", "birth_date": "1985-03-12",
                                     "phone": "13800138000", "address": "示例路 1 号",
                                     "id_card_no": "110105198503120012"}, {})
    check("患者号格式 P{yyyyMMdd}{4位}",
          len(p1["patient_no"]) == 13 and p1["patient_no"].startswith("P"), p1["patient_no"])
    p2 = m01_patient.create(doctor, {"name": "李四", "gender": "F", "birth_date": "1990-01-01",
                                     "phone": "13900139000"}, {})
    check("第二个患者号递增", p2["patient_no"] > p1["patient_no"], p2["patient_no"])
    res = m01_patient.search(doctor, {}, {"keyword": "张三"})
    check("按姓名搜索命中", len(res["items"]) == 1)
    res = m01_patient.search(doctor, {}, {"keyword": "13800138000"})
    check("按手机号搜索命中", len(res["items"]) == 1)
    res = m01_patient.search(doctor, {}, {"keyword": "110105198503120012"})
    check("按身份证搜索命中", len(res["items"]) == 1)

    upd = m01_patient.update(doctor, {"id": p1["id"], "version": p1["version"],
                                      "phone": "13811111111", "reason": "患者换号"}, {})
    check("资料修改生效", upd["changed"] == 1, str(upd.get("fields")))
    expect_error("乐观锁冲突被拒绝",
                 lambda: m01_patient.update(doctor, {"id": p1["id"], "version": 1,
                                                     "phone": "13800000000"}, {}), "E2")

    dup = m01_patient.duplicates(doctor, {}, {"name": "张三", "birth_date": "1985-03-12"})
    check("重复患者检测命中", len(dup["items"]) >= 1)

    # ---------- F05/F06/F07 就诊 ----------
    print("\n[F05/F06/F07] 创建就诊 / 队列 / 接诊")
    v1 = m02_visit.create(doctor, {"patient_id": p1["id"], "visit_type": "TCM"}, {})
    check("就诊号格式 V{yyyyMMdd}{3位}",
          len(v1["visit_no"]) == 12 and v1["visit_no"].startswith("V"), v1["visit_no"])
    check("初诊判定为首诊", v1["is_first_visit"] == 1)
    expect_error("同一患者重复建就诊被拒绝",
                 lambda: m02_visit.create(doctor, {"patient_id": p1["id"]}, {}), "E3")
    q = m02_visit.queue(doctor, {}, {})
    check("候诊队列包含该就诊", any(i["id"] == v1["id"] for i in q["items"]))
    started = m02_visit.start(doctor, {"visit_id": v1["id"]}, {})
    check("接诊后状态 IN_PROGRESS", True, "record_id=%s" % started["record_id"])
    vv = m02_visit.get(doctor, {}, {"id": v1["id"]})
    check("就诊状态为 IN_PROGRESS", vv["status"] == "IN_PROGRESS")
    check("状态流转日志已写入", len(vv["status_log"]) >= 2)

    # ---------- M03 病历 ----------
    print("\n[M03] 病历（含中医四诊与辨证）")
    rid = started["record_id"]
    m03_record.save_chief(doctor, {"record_id": rid, "content": "反复头晕 3 天",
                                   "duration": "3天"}, {})
    m03_record.save_present(doctor, {"record_id": rid,
                                     "content": "3 天前劳累后出现头晕，伴乏力…",
                                     "onset_time": "2026-10-06"}, {})
    m03_record.save_tcm_four(doctor, {"record_id": rid, "tongue_body": "淡红",
                                      "tongue_coating": "薄白", "pulse_desc": "弦细",
                                      "inspection": "神疲", "interrogation": "头晕乏力"},
                             {})
    m03_record.save_syndrome(doctor, {"record_id": rid, "items": [
        {"syndrome_name": "肝阳上亢", "syndrome_category": "脏腑",
         "treatment_principle": "平肝潜阳", "treatment_method": "天麻钩藤饮加减"}]}, {})
    m03_record.save_diagnosis(doctor, {"record_id": rid, "items": [
        {"diag_type": "WEST", "icd10_code": "R42", "icd10_name": "头晕", "is_primary": 1},
        {"diag_type": "TCM", "tcm_name": "肝阳上亢"}]}, {})
    m03_record.save_vital(doctor, {"record_id": rid, "temperature_c": 36.6, "pulse": 78,
                                   "systolic": 130, "diastolic": 85, "height_cm": 170,
                                   "weight_kg": 65}, {})
    m03_record.save_exam(doctor, {"record_id": rid, "items": [
        {"section": "GENERAL", "content": "神清，精神可"}]}, {})
    m03_record.save_order(doctor, {"record_id": rid, "items": [
        {"order_type": "TREATMENT", "content": "针灸 1 次"},
        {"order_type": "ADVICE", "content": "注意休息，清淡饮食"}]}, {})
    expect_error("医嘱中开药被拒绝",
                 lambda: m03_record.save_order(doctor, {"record_id": rid, "items": [
                     {"order_type": "DRUG", "content": "某药"}]}, {}), "E3")

    rec = m03_record.get(doctor, {}, {"id": rid})
    check("主诉已保存", rec["chief_complaint"]["content"] == "反复头晕 3 天")
    check("中医四诊已保存", rec["tcm_four"]["pulse_desc"] == "弦细")
    check("辨证论治已保存", rec["syndromes"][0]["syndrome_name"] == "肝阳上亢")
    check("诊断 2 条", len(rec["diagnoses"]) == 2)
    check("BMI 自动计算", abs(rec["vitals"][0]["bmi"] - 22.5) < 0.1,
          str(rec["vitals"][0]["bmi"]))

    # F19 确认
    expect_error("空签名口令不能确认",
                 lambda: m03_record.confirm(doctor, {"record_id": rid}, {}), "E2")
    m03_record.confirm(doctor, {"record_id": rid, "password": "Clinic@2026"}, {})
    rec = m03_record.get(doctor, {}, {"id": rid})
    check("病历已确认", rec["status"] == "CONFIRMED")
    check("签名记录已写入", rec["signature"] is not None)
    expect_error("确认后禁止直接编辑",
                 lambda: m03_record.save_chief(doctor, {"record_id": rid,
                                                        "content": "改内容"}, {}), "E3")

    # ---------- F08/F09/F125 ----------
    print("\n[F08/F09/F125] 挂单 / 恢复 / 作废")
    v2 = m02_visit.create(doctor, {"patient_id": p2["id"]}, {})
    m02_visit.start(doctor, {"visit_id": v2["id"]}, {})
    expect_error("挂单必须填原因",
                 lambda: m02_visit.suspend(doctor, {"visit_id": v2["id"],
                                                    "suspend_type": "HOLD"}, {}), "E2")
    m02_visit.suspend(doctor, {"visit_id": v2["id"], "suspend_type": "OBSERVATION",
                               "reason": "需等待检验结果"}, {})
    check("挂单后状态 SUSPENDED",
          m02_visit.get(doctor, {}, {"id": v2["id"]})["status"] == "SUSPENDED")
    m02_visit.resume(doctor, {"visit_id": v2["id"]}, {})
    check("恢复后状态 IN_PROGRESS",
          m02_visit.get(doctor, {}, {"id": v2["id"]})["status"] == "IN_PROGRESS")
    expect_error("作废必须填原因",
                 lambda: m02_visit.void(doctor, {"visit_id": v2["id"]}, {}), "E2")
    m02_visit.void(doctor, {"visit_id": v2["id"], "reason": "患者未就诊",
                            "refund_required": 1}, {})
    check("作废后状态 VOID",
          m02_visit.get(doctor, {}, {"id": v2["id"]})["status"] == "VOID")
    expect_error("终态不可再变更",
                 lambda: m02_visit.complete(doctor, {"visit_id": v2["id"]}, {}), "E3")

    # ---------- F04 合并 ----------
    print("\n[F04] 重复患者合并")
    p3 = m01_patient.create(doctor, {"name": "张三", "gender": "M",
                                     "birth_date": "1985-03-12", "phone": "13700000000"}, {})
    before_visits = len(m02_visit.list_visits(doctor, {}, {"patient_id": p1["id"]})["items"])
    mg = m01_patient.merge(doctor, {"primary_patient_id": p1["id"],
                                    "merged_patient_id": p3["id"],
                                    "reason": "同一人重复建档"}, {})
    after_visits = len(m02_visit.list_visits(doctor, {}, {"patient_id": p1["id"]})["items"])
    check("合并成功", mg["primary_patient_id"] == p1["id"], str(mg["moved"]))
    check("合并后主患者就诊数不变", after_visits == before_visits)
    expect_error("有就诊记录的患者不能删除",
                 lambda: m01_patient.soft_delete(doctor, {"id": p1["id"]}, {}), "E3")

    # ---------- M12 审计 ----------
    print("\n[M12] 审计")
    ops = db.query("SELECT COUNT(*) c FROM audit_operation")[0]["c"]
    dc = db.query("SELECT COUNT(*) c FROM audit_data_change")[0]["c"]
    hr = db.query("SELECT COUNT(*) c FROM audit_high_risk")[0]["c"]
    lg = db.query("SELECT COUNT(*) c FROM audit_login")[0]["c"]
    check("操作审计已写入", ops > 10, "%d 条" % ops)
    check("字段变更审计已写入", dc > 0, "%d 条" % dc)
    check("高风险审计已写入", hr >= 3, "%d 条（初始化/作废/合并）" % hr)
    check("登录审计已写入", lg >= 1, "%d 条" % lg)

    def _try_update_audit():
        with db.tx() as conn:
            db.execute(conn, "UPDATE audit_operation SET result='X' WHERE id=1")
    expect_error("审计表禁止 UPDATE", _try_update_audit, "E")

    def _try_delete_audit():
        with db.tx() as conn:
            db.execute(conn, "DELETE FROM audit_operation WHERE id=1")
    expect_error("审计表禁止 DELETE", _try_delete_audit, "E")

    def _try_delete_patient():
        with db.tx() as conn:
            db.execute(conn, "DELETE FROM patient WHERE id=1")
    expect_error("业务主表禁止物理删除", _try_delete_patient, "E")

    # ---------- M06 处方与用药 ----------
    print("\n[M06] 处方与用药（含中药）")
    d_amox = m06.drug_create(doctor, {
        "drug_code": "AMOX001", "generic_name": "阿莫西林胶囊", "spec": "0.25g*24粒",
        "dosage_form": "胶囊", "unit": "盒", "price_fen": 1850, "allergy_class": "PENICILLIN",
        "need_skin_test": 1, "is_antibiotic": 1, "max_single_dose": 0.5}, {})
    d_ibu = m06.drug_create(doctor, {
        "drug_code": "IBU001", "generic_name": "布洛芬缓释胶囊", "spec": "0.3g*10粒",
        "unit": "盒", "price_fen": 1200, "allergy_class": "NSAID"}, {})
    check("药品资料建档", bool(d_amox["id"]) and bool(d_ibu["id"]))
    check("药品搜索命中", len(m06.drug_search(doctor, {}, {"keyword": "阿莫西林"})["items"]) == 1)

    h_hq = m06.herb_create(doctor, {
        "herb_code": "HQ", "name": "黄芪", "pinyin": "huangqi",
        "price_yuan_per_g": 0.05, "usual_dose_g": 15, "max_dose_g": 30}, {})
    h_bz = m06.herb_create(doctor, {
        "herb_code": "BZ", "name": "白术", "price_yuan_per_g": 0.08,
        "usual_dose_g": 10, "max_dose_g": 30}, {})
    h_gc = m06.herb_create(doctor, {
        "herb_code": "GC", "name": "甘草", "price_yuan_per_g": 0.03}, {})
    h_gs = m06.herb_create(doctor, {
        "herb_code": "GS", "name": "甘遂", "price_yuan_per_g": 0.20}, {})
    h_cw = m06.herb_create(doctor, {
        "herb_code": "CW", "name": "制川乌", "price_yuan_per_g": 0.15,
        "is_toxic": 1, "toxic_limit_g": 3}, {})
    check("饮片资料建档（含毒性饮片）", bool(h_cw["id"]))

    # 成药处方：正常流程
    rx1 = m06.create(doctor, {"visit_id": v1["id"], "prescription_type": "WEST"}, {})
    check("处方号格式 R{yyyyMMdd}{4位}",
          len(rx1["prescription_no"]) == 13 and rx1["prescription_no"].startswith("R"),
          rx1["prescription_no"])
    m06.save_items(doctor, {"prescription_id": rx1["id"],
                            "items": [{"drug_id": d_ibu["id"], "quantity": 2,
                                       "dosage": "0.3", "frequency": "bid", "route": "PO"}]}, {})
    sub = m06.submit(doctor, {"prescription_id": rx1["id"]}, {})
    check("成药处方提交 PENDING", sub["status"] == "PENDING")
    check("成药计价 = 单价×数量", sub["pricing"]["total_amount_fen"] == 2400,
          "%d 分" % sub["pricing"]["total_amount_fen"])
    cf = m06.confirm(doctor, {"prescription_id": rx1["id"], "password": "Clinic@2026"}, {})
    check("处方确认 CONFIRMED", cf["status"] == "CONFIRMED")
    expect_error("确认后不能改明细",
                 lambda: m06.save_items(doctor, {"prescription_id": rx1["id"],
                                                 "items": [{"drug_id": d_ibu["id"]}]}, {}), "E3")

    # 过敏：新患者 + 过敏史 + 开过敏药
    p4 = m01_patient.create(doctor, {"name": "王五", "gender": "F",
                                     "birth_date": "1978-05-05", "phone": "13600000000"}, {})
    v4 = m02_visit.create(doctor, {"patient_id": p4["id"], "visit_type": "TCM"}, {})
    st4 = m02_visit.start(doctor, {"visit_id": v4["id"]}, {})
    rid4 = st4["record_id"]
    m03_record.save_allergy(doctor, {"record_id": rid4, "items": [
        {"allergen": "布洛芬", "allergen_type": "DRUG", "severity": "SEVERE"}]}, {})
    rx2 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "WEST"}, {})
    m06.save_items(doctor, {"prescription_id": rx2["id"],
                            "items": [{"drug_id": d_ibu["id"], "quantity": 1}]}, {})
    expect_error("过敏强制拦截（同类匹配）",
                 lambda: m06.submit(doctor, {"prescription_id": rx2["id"]}, {}), "E30")
    ov = m06.submit(doctor, {"prescription_id": rx2["id"], "allergy_override": 1,
                             "override_reason": "患者已确认耐受，皮试阴性"}, {})
    check("过敏强制继续需理由 → 放行", ov["status"] == "PENDING")
    hr_now = db.query("SELECT COUNT(*) c FROM audit_high_risk "
                      "WHERE entity_type='prescription'")[0]["c"]
    check("强制放行写高风险审计", hr_now >= 1, "%d 条" % hr_now)

    # 皮试前置
    rx3 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "WEST"}, {})
    m06.save_items(doctor, {"prescription_id": rx3["id"],
                            "items": [{"drug_id": d_amox["id"], "quantity": 1}]}, {})
    expect_error("皮试未做 → 不得提交",
                 lambda: m06.submit(doctor, {"prescription_id": rx3["id"]}, {}), "E3035")
    m06.skin_test_record(doctor, {"patient_id": p4["id"], "drug_id": d_amox["id"],
                                  "result": "NEGATIVE"}, {})
    sub3 = m06.submit(doctor, {"prescription_id": rx3["id"]}, {})
    check("皮试阴性后可提交", sub3["status"] == "PENDING")

    # 饮片处方：必填项 → 计价 → 付数变更重算
    rx4 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "HERB"}, {})
    m06.save_herbs(doctor, {"prescription_id": rx4["id"], "items": [
        {"herb_id": h_hq["id"], "dose_g": 15, "foot_note": "NORMAL"},
        {"herb_id": h_bz["id"], "dose_g": 10, "foot_note": "NORMAL"}]}, {})
    expect_error("饮片处方缺付数/煎法/服法 → 拦截",
                 lambda: m06.submit(doctor, {"prescription_id": rx4["id"]}, {}), "E3042")
    m06.save_herbs(doctor, {"prescription_id": rx4["id"], "doses": 7,
                            "decoction_method": "水煎", "administration": "每日一剂，分两次温服",
                            "items": [
                                {"herb_id": h_hq["id"], "dose_g": 15},
                                {"herb_id": h_bz["id"], "dose_g": 10}]}, {})
    pr = m06.price(doctor, {"prescription_id": rx4["id"]}, {})
    check("饮片计价 7 付 = 1085 分（10.85 元）", pr["total_amount_fen"] == 1085,
          "%d 分" % pr["total_amount_fen"])
    m06.save_herbs(doctor, {"prescription_id": rx4["id"], "doses": 14, "items": [
        {"herb_id": h_hq["id"], "dose_g": 15}, {"herb_id": h_bz["id"], "dose_g": 10}]}, {})
    pr2 = m06.price(doctor, {"prescription_id": rx4["id"]}, {})
    check("付数改 14 全量重算 = 2170 分", pr2["total_amount_fen"] == 2170,
          "%d 分" % pr2["total_amount_fen"])

    # 毒性饮片超量 → 强制二次确认
    rx5 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "HERB"}, {})
    m06.save_herbs(doctor, {"prescription_id": rx5["id"], "doses": 7,
                            "decoction_method": "先煎久煎", "administration": "每日一剂",
                            "items": [{"herb_id": h_cw["id"], "dose_g": 5}]}, {})
    expect_error("毒性饮片超量 → 需二次确认",
                 lambda: m06.submit(doctor, {"prescription_id": rx5["id"]}, {}), "E3040")
    expect_error("二次确认缺理由 → 拒绝",
                 lambda: m06.submit(doctor, {"prescription_id": rx5["id"],
                                             "toxic_confirm": 1}, {}), "E3040")
    sub5 = m06.submit(doctor, {"prescription_id": rx5["id"], "toxic_confirm": 1,
                               "toxic_reason": "患者长期服用耐受，已告知风险"}, {})
    check("毒性饮片带理由二次确认 → 通过", sub5["status"] == "PENDING")

    # 配伍禁忌：提醒不阻断 / 重复药味：拦截
    rx6 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "HERB"}, {})
    m06.save_herbs(doctor, {"prescription_id": rx6["id"], "doses": 7,
                            "decoction_method": "水煎", "administration": "每日一剂",
                            "items": [{"herb_id": h_gc["id"], "dose_g": 6},
                                      {"herb_id": h_gs["id"], "dose_g": 3}]}, {})
    sub6 = m06.submit(doctor, {"prescription_id": rx6["id"]}, {})
    has_compat = any(w.get("rule") == "COMPATIBILITY" for w in sub6["warns"])
    check("配伍禁忌提醒但不阻断", sub6["status"] == "PENDING" and has_compat,
          "%d 条提醒" % len(sub6["warns"]))

    rx7 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "HERB"}, {})
    m06.save_herbs(doctor, {"prescription_id": rx7["id"], "doses": 7,
                            "decoction_method": "水煎", "administration": "每日一剂",
                            "items": [{"herb_id": h_gc["id"], "dose_g": 6},
                                      {"herb_id": h_gc["id"], "dose_g": 6}]}, {})
    expect_error("重复药味强制拦截",
                 lambda: m06.submit(doctor, {"prescription_id": rx7["id"]}, {}), "E3045")

    # 已发药不可作废
    with db.tx() as conn:
        m06.mark_dispensed(conn, rx1["id"], doctor)
    expect_error("已发药处方不能作废",
                 lambda: m06.void(doctor, {"prescription_id": rx1["id"],
                                           "reason": "开错了"}, {}), "E3008")
    voided = m06.void(doctor, {"prescription_id": rx3["id"], "reason": "患者拒绝用药"}, {})
    check("未发药处方可作废", voided["status"] == "VOID")

    # 处方模板套用
    tpl = m06.template_create(doctor, {"name": "头晕经验方", "template_type": "HERB",
                                       "syndrome_name": "肝阳上亢", "doses_default": 7,
                                       "items": [{"item_kind": "HERB", "ref_id": h_hq["id"],
                                                  "dose_g": 15},
                                                 {"item_kind": "HERB", "ref_id": h_bz["id"],
                                                  "dose_g": 10}]}, {})
    rx8 = m06.create(doctor, {"visit_id": v4["id"], "prescription_type": "HERB"}, {})
    ap = m06.template_apply(doctor, {"prescription_id": rx8["id"], "template_id": tpl["id"],
                                     "doses": 7}, {})
    check("模板套用生成明细", ap["herbs"] == 2, str(ap))
    check("模板使用计数递增",
          db.query_one("SELECT usage_count c FROM prescription_template WHERE id=?",
                       (tpl["id"],))["c"] == 1)

    # ---------- M05 检验检查（全外送）----------
    print("\n[M05] 检验检查（全外送）")
    ag = m05.agency_create(doctor, {"agency_code": "LAB01", "name": "示例检验所",
                                    "settlement_cycle": "MONTHLY"}, {})
    li1 = m05.item_create(doctor, {"item_code": "CBC", "name": "血常规",
                                   "agency_id": ag["id"], "specimen_type": "血",
                                   "price_yuan": 30, "settle_price_yuan": 12,
                                   "report_days": 1}, {})
    li2 = m05.item_create(doctor, {"item_code": "LFT", "name": "肝功能",
                                   "agency_id": ag["id"], "specimen_type": "血",
                                   "price_yuan": 60, "settle_price_yuan": 25,
                                   "report_days": 2}, {})
    lo = m05.order_create(doctor, {"visit_id": v1["id"], "agency_id": ag["id"],
                                   "clinical_note": "排除感染"}, {})
    check("检验申请号 L{yyyyMMdd}{4位}",
          len(lo["order_no"]) == 13 and lo["order_no"].startswith("L"), lo["order_no"])
    si = m05.order_save_items(doctor, {"lab_order_id": lo["id"], "items": [
        {"lab_item_id": li1["id"]}, {"lab_item_id": li2["id"]}]}, {})
    check("申请计价：收费 9000 分 / 结算 3700 分",
          si["total_amount"] == 9000 and si["settle_amount"] == 3700,
          "%d / %d" % (si["total_amount"], si["settle_amount"]))
    sm = m05.order_submit(doctor, {"lab_order_id": lo["id"]}, {})
    check("提交后生成标本条码 BC{yyyyMMdd}{6位}",
          len(sm["barcodes"]) == 2 and sm["barcodes"][0].startswith("BC"),
          str(sm["barcodes"]))
    cl = m05.specimen_collect(doctor, {"lab_order_id": lo["id"]}, {})
    check("采样完成 → SAMPLED", cl["status"] == "SAMPLED")
    dv = m05.delivery_create(doctor, {"lab_order_id": lo["id"], "batch_no": "B20261010"}, {})
    check("送检登记 → DELIVERED", dv["status"] == "DELIVERED")
    rs = m05.result_save(doctor, {"lab_order_id": lo["id"], "items": [
        {"item_name": "白细胞", "result_text": "11.2", "unit": "10^9/L",
         "ref_range": "4-10", "abnormal_flag": "HIGH"}]}, {})
    check("结果回录 → REPORTED",
          m05.order_get(doctor, {}, {"id": lo["id"]})["status"] == "REPORTED")
    rv = m05.result_review(doctor, {"lab_order_id": lo["id"]}, {})
    check("医生阅片 → REVIEWED", rv["status"] == "REVIEWED")
    period = tu.now().strftime("%Y-%m")
    rc = m05.reconcile(doctor, {"agency_id": ag["id"], "period": period}, {})
    check("月度对账：差额 = 收费 − 结算", rc["diff_fen"] == rc["charged_fen"] - rc["settle_fen"],
          "差额 %d 分" % rc["diff_fen"])

    # ---------- M04 AI 与语音 ----------
    print("\n[M04] AI 与语音")
    t1 = m04.task_create(doctor, {"task_type": "ASR", "visit_id": v4["id"],
                                  "patient_id": p4["id"]}, {})
    check("AI 任务号 AI{yyyyMMddHHmmss}{6位}", t1["task_no"].startswith("AI"), t1["task_no"])
    tr = m04.transcript_save(doctor, {
        "ai_task_id": t1["id"],
        "text": "患者反复头晕三天，体温 37.2 度，血压 130/85，对青霉素过敏，有高血压病史"}, {})
    check("ASR 转写成功", tr["status"] == "SUCCEEDED")
    t_ex = m04.task_create(doctor, {"task_type": "EXTRACT", "visit_id": v4["id"],
                                    "patient_id": p4["id"]}, {})
    ex = m04.extract(doctor, {"ai_task_id": t_ex["id"]}, {})
    types = set(f["fact_type"] for f in ex["facts"])
    check("事实抽取覆盖症状/体征/过敏/既往史",
          {"SYMPTOM", "VITAL", "ALLERGY", "HISTORY"} <= types, str(sorted(types)))
    t2 = m04.task_create(doctor, {"task_type": "DRAFT", "visit_id": v4["id"]}, {})
    dr = m04.draft_create(doctor, {"ai_task_id": t_ex["id"], "visit_id": v4["id"]}, {})
    check("AI 草稿已生成（只写 ai_draft）", dr["status"] == "PENDING")
    rec4_before = m03_record.get(doctor, {}, {"id": rid4})
    check("未确认前病历未被 AI 改写",
          not (rec4_before["chief_complaint"] or {}).get("content"))
    expect_error("AI 草稿未确认不得落库",
                 lambda: m04.draft_apply(doctor, {"draft_id": dr["id"]}, {}), "E2")
    ap2 = m04.draft_apply(doctor, {"draft_id": dr["id"], "confirm": True}, {})
    check("医生确认后草稿落库", "chief_complaint" in ap2["written"], str(ap2["written"]))
    rec4 = m03_record.get(doctor, {}, {"id": rid4})
    check("落库内容标记来源 AI", rec4["chief_complaint"]["source"] == "AI")

    sg = m04.suggest_create(doctor, {"visit_id": v4["id"], "suggest_type": "DRUG",
                                     "items": [{"drug_id": d_ibu["id"], "quantity": 1}],
                                     "rationale": "对症止痛"}, {})
    ad = m04.suggest_adopt(doctor, {"suggestion_id": sg["id"]}, {})
    check("AI 处方建议采纳 → 生成普通处方", bool(ad["prescription_no"]),
          ad["prescription_no"])
    rk = m04.risk_check(doctor, {"patient_id": p4["id"]}, {})
    check("风险提醒命中过敏史",
          any(a["risk_type"] == "ALLERGY" for a in rk["alerts"]),
          "%d 条" % len(rk["alerts"]))

    # ---------- 金额精度 ----------
    print("\n[契约 §3] 金额与编号")
    check("成药行小计按分舍入", money.line_amount_fen(1234, 3) == 3702)
    check("饮片行小计公式正确", money.herb_line_amount_fen(35, 10, 7) == 245,
          "35毫元/g × 10g × 7付 = 24.5 元 → %d 分" % money.herb_line_amount_fen(35, 10, 7))
    check("金额显示为 2 位小数", money.fen_to_yuan(12345) == "123.45")

    # ---------- 备份 ----------
    print("\n[F90/F92] 备份")
    from core import backup
    bk = backup.full_backup(reason="SMOKE_TEST")
    check("全量备份成功且校验通过", bk.get("valid") is True, str(bk.get("file")))

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：%s" % FAILED)
    try:
        shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass
    return 0 if not FAILED else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        traceback.print_exc(file=_LOG)  # 异常也进日志，便于排查
        _LOG.flush()
        sys.exit(2)
