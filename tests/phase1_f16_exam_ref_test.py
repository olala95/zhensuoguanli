# -*- coding: utf-8 -*-
"""F16 辅助检查记录（M03）功能验收。

AC1 引用外送结果（只存引用不复制全文）
AC2 源数据变更时提示，已确认病历内容不自动改
AC3 院内即时检查直接记录，不走 M05
AC4 已开单未回报显示为"已开单待回报"
AC5 删除引用不影响 M05 原始结果
AC6 非必填（无辅助检查也能确认病历）
AC7 修改引用/摘要留 before/after 审计

用法：python tests/phase1_f16_exam_ref_test.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m03_record as m03,  # noqa: E402
                     m05_lab as m05, m14_system as m14)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_f16_last.log")
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


def _one(sql, params=()):
    return db.query_one(sql, params)


def _all(sql, params=()):
    return db.query(sql, params)


def _tx(sql, params=()):
    with db.tx() as conn:
        return db.execute(conn, sql, params)


def _count(table, where="", params=()):
    return _one("SELECT COUNT(*) c FROM %s %s" % (table, where), params)["c"]


def _make_reported_lab(ctx, vis_id):
    """走真实 M05 流程造一份"已回报"的血常规结果，返回 (order_id, result_id)。"""
    ag = m05.agency_create(ctx, {"agency_code": "AG01", "name": "示例检验所",
                                 "report_days_default": 2}, {})
    item = m05.item_create(ctx, {"agency_id": ag["id"], "item_code": "CBC",
                                 "name": "血常规", "price_fen": 3000,
                                 "settle_price_fen": 1800, "report_days": 1,
                                 "specimen_type": "BLOOD"}, {})
    order = m05.order_create(ctx, {"visit_id": vis_id, "clinical_note": "咳嗽待查"}, {})
    m05.order_save_items(ctx, {"lab_order_id": order["id"],
                               "items": [{"lab_item_id": item["id"]}]}, {})
    m05.order_submit(ctx, {"lab_order_id": order["id"]}, {})
    m05.specimen_collect(ctx, {"lab_order_id": order["id"]}, {})
    dv = m05.delivery_create(ctx, {"lab_order_id": order["id"], "agency_id": ag["id"]}, {})
    m05.result_save(ctx, {"lab_order_id": order["id"], "items": [
        {"item_name": "白细胞", "result_text": "11.2", "unit": "10^9/L",
         "ref_range": "3.5-9.5", "abnormal_flag": "HIGH",
         "result_summary": "白细胞升高，提示感染"}]}, {})
    res = _one("SELECT id FROM lab_result WHERE lab_order_id=? AND deleted_at IS NULL "
               "ORDER BY id DESC LIMIT 1", (order["id"],))
    return order["id"], res["id"]


def main():
    root = tempfile.mkdtemp(prefix="clinic_f16_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))
    check("迁移已应用到 0019",
          "0019" in __import__("core.migrate", fromlist=["migrate"]).applied(db.write_conn()),
          str(sorted(__import__("core.migrate", fromlist=["migrate"])
                     .applied(db.write_conn()))[-2:]))

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

    pat = m01.create(ctx, {"name": "检验甲", "gender": "M", "birth_date": "1985-03-03"}, {})
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "WEST"}, {})
    m02.start(ctx, {"visit_id": vis["id"]}, {})
    rec = m03.create(ctx, {"visit_id": vis["id"]}, {})
    rid = rec["id"]

    print("\n[准备] 走真实 M05 流程造一份已回报的血常规")
    oid, result_id = _make_reported_lab(ctx, vis["id"])
    check("M05 已回报结果就位", result_id is not None, "order=%s result=%s" % (oid, result_id))

    # ================================================================
    print("\n[AC1] 引用外送结果：只存引用 + 摘要，不复制全文")
    r = m03.save_exam_ref(ctx, {"record_id": rid, "items": [
        {"ref_type": "LAB", "lab_result_id": result_id,
         "summary": "血常规示白细胞升高"}]}, {})
    check("引用保存成功", r["saved"] == 1, str(r))
    ref = _one("SELECT * FROM record_exam_ref WHERE record_id=? AND deleted_at IS NULL "
               "ORDER BY id DESC LIMIT 1", (rid,))
    check("存的是引用（lab_result_id 指向 M05）", ref["lab_result_id"] == result_id)
    check("只存摘要不复制结果全文", ref["summary"] == "血常规示白细胞升高"
          and "11.2" not in str(ref["summary"]), str(ref["summary"]))
    check("记录了溯源快照 source_updated_at", bool(ref["source_updated_at"]),
          str(ref["source_updated_at"]))
    check("来源标记为 M05", ref["source"] == "M05", str(ref["source"]))
    view = m03._exam_ref_view(rid)
    check("展示为已回报", view[0]["display_status"] == "已回报", str(view[0]["display_status"]))
    check("M03 未复制 M05 结果文本（表内无 result_text 列）",
          "result_text" not in ref.keys())

    # ================================================================
    print("\n[AC2] 源数据被更正 → 提示已更新，但病历内容不自动改")
    summary_before = _one("SELECT summary FROM record_exam_ref WHERE id=?", (ref["id"],))["summary"]
    _tx("UPDATE lab_result SET result_text='12.0', result_summary='白细胞明显升高', "
        "updated_at=?, updated_by=? WHERE id=?",
        ("2030-01-01 09:00:00", ctx.user_id, result_id))
    view2 = m03._exam_ref_view(rid)
    check("提示源数据已更新", view2[0].get("source_changed") is True,
          str(view2[0].get("display_status")))
    check("提示里带源更新时间", "2030-01-01" in str(view2[0].get("source_changed_at")),
          str(view2[0].get("source_changed_at")))
    check("病历里的摘要不被自动改写",
          _one("SELECT summary FROM record_exam_ref WHERE id=?", (ref["id"],))["summary"]
          == summary_before, summary_before)

    # ================================================================
    print("\n[AC3] 院内即时检查：直接记录，不走 M05")
    r2 = m03.save_exam_ref(ctx, {"record_id": rid, "items": [
        {"ref_type": "POINT", "exam_name": "即时血糖", "exam_value": "8.5",
         "unit": "mmol/L", "abnormal_flag": "HIGH"}]}, {})
    check("即时检查保存成功", r2["saved"] == 1, str(r2))
    pt = _one("SELECT * FROM record_exam_ref WHERE record_id=? AND ref_type='POINT' "
              "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (rid,))
    check("即时检查不挂 M05 单据",
          pt["lab_order_id"] is None and pt["lab_result_id"] is None)
    check("即时检查结果值已记录", pt["exam_value"] == "8.5" and pt["unit"] == "mmol/L",
          "%s %s" % (pt["exam_value"], pt["unit"]))
    check("来源标记为 POINT", pt["source"] == "POINT", str(pt["source"]))
    view3 = m03._exam_ref_view(rid)
    point_view = [x for x in view3 if x["ref_type"] == "POINT"][0]
    check("展示为即时检查", point_view["display_status"] == "即时检查",
          str(point_view["display_status"]))
    check("展示文本含值与单位", "8.5" in point_view["display_text"]
          and "mmol/L" in point_view["display_text"], point_view["display_text"])

    # ================================================================
    print("\n[AC4] 已开单未回报 → 显示「已开单待回报」")
    oid2 = m05.order_create(ctx, {"visit_id": vis["id"]}, {})["id"]
    m03.save_exam_ref(ctx, {"record_id": rid, "items": [
        {"ref_type": "IMAGING", "lab_order_id": oid2}]}, {})
    view4 = m03._exam_ref_view(rid)
    pend = [x for x in view4 if x["lab_order_id"] == oid2 and not x["lab_result_id"]]
    check("待回报条目存在", len(pend) == 1, str(len(pend)))
    check("显示为已开单待回报", pend and pend[0]["display_status"] == "已开单待回报",
          str(pend and pend[0]["display_status"]))
    check("不显示为空白", pend and bool(pend[0]["display_text"]),
          str(pend and pend[0]["display_text"]))

    # ================================================================
    print("\n[AC5] 删除引用不影响 M05 原始结果")
    n_res_before = _count("lab_result", "WHERE deleted_at IS NULL")
    m03.save_exam_ref(ctx, {"record_id": rid, "items": [{"id": ref["id"], "deleted": True}],
                            "reason": "引用错了，移除"}, {})
    check("引用已移除",
          _one("SELECT deleted_at FROM record_exam_ref WHERE id=?", (ref["id"],))["deleted_at"]
          is not None)
    check("M05 原始结果仍在且未被删改", _count("lab_result", "WHERE deleted_at IS NULL")
          == n_res_before, "%d vs %d" % (n_res_before, _count("lab_result",
                                                              "WHERE deleted_at IS NULL")))
    check("M05 结果内容原封不动",
          _one("SELECT result_text FROM lab_result WHERE id=?", (result_id,))["result_text"]
          == "12.0")

    # ================================================================
    print("\n[AC7] 修改引用/摘要留 before/after 审计")
    n_dc = _count("audit_data_change", "WHERE table_name='record_exam_ref'")
    m03.save_exam_ref(ctx, {"record_id": rid, "items": [
        {"id": pt["id"], "exam_value": "7.9", "summary": "复查后下降"}],
        "reason": "复查更正"}, {})
    dc = _all("SELECT * FROM audit_data_change WHERE table_name='record_exam_ref' "
              "AND record_id=? ORDER BY id", (str(pt["id"]),))
    check("变更审计已落表",
          _count("audit_data_change", "WHERE table_name='record_exam_ref'") > n_dc)
    check("记录了 before/after",
          any(str(d.get("before_value")) == "8.5" and str(d.get("after_value")) == "7.9"
              for d in dc),
          str([(d["field_name"], d["before_value"], d["after_value"]) for d in dc]))
    check("记录了原因", all(bool(d.get("reason")) for d in dc),
          str(dc and dc[0].get("reason")))

    # ================================================================
    print("\n[异常] 非法输入一律明确拒绝")
    expect_error("引用不存在的结果 → E3021",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rid, "items": [
                     {"ref_type": "LAB", "lab_result_id": 999999}]}, {}), "E3021")
    expect_error("引用不存在的申请单 → E3021",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rid, "items": [
                     {"ref_type": "LAB", "lab_order_id": 999999}]}, {}), "E3021")
    expect_error("即时检查缺结果值被拒",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rid, "items": [
                     {"ref_type": "POINT", "exam_name": "即时血糖"}]}, {}), None)
    expect_error("即时检查带 M05 单据被拒",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rid, "items": [
                     {"ref_type": "POINT", "exam_name": "血糖", "exam_value": "6",
                      "lab_order_id": oid}]}, {}), None)
    expect_error("ref_type 非法被拒",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rid, "items": [
                     {"ref_type": "XXX", "lab_result_id": result_id}]}, {}), None)
    expect_error("既无 order 又无 result 被拒",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rid, "items": [
                     {"ref_type": "LAB"}]}, {}), None)

    # ================================================================
    print("\n[AC6] 非必填：无辅助检查也能确认病历")
    pat2 = m01.create(ctx, {"name": "无检查乙", "gender": "F", "birth_date": "1990-01-01"}, {})
    vis2 = m02.create(ctx, {"patient_id": pat2["id"], "visit_type": "WEST"}, {})
    m02.start(ctx, {"visit_id": vis2["id"]}, {})
    rec2 = m03.create(ctx, {"visit_id": vis2["id"]}, {})
    m03.save_chief(ctx, {"record_id": rec2["id"], "content": "头痛两天"}, {})
    m03.save_diagnosis(ctx, {"record_id": rec2["id"], "items": [
        {"icd10_code": "R51", "icd10_name": "头痛", "is_primary": 1}]}, {})
    cf = m03.confirm(ctx, {"record_id": rec2["id"], "password": PWD}, {})
    check("无辅助检查仍可确认", cf["to"] == "CONFIRMED", str(cf))
    expect_error("已确认病历不能直接改辅助检查",
                 lambda: m03.save_exam_ref(ctx, {"record_id": rec2["id"], "items": [
                     {"ref_type": "POINT", "exam_name": "血糖", "exam_value": "6"}]}, {}),
                 "E3007")

    # ================================================================
    print("\n[接口] 展示视图与只读列表")
    lst = m03.exam_ref_list(ctx, {}, {"record_id": rid})
    check("exam_ref_list 返回条目", len(lst["items"]) >= 2, str(len(lst["items"])))
    g = m03.get(ctx, {}, {"record_id": rid})
    check("病历 get 里带辅助检查", len(g["exam_refs"]) >= 2, str(len(g["exam_refs"])))
    check("get 里的条目带展示状态",
          all("display_status" in x for x in g["exam_refs"]))

    print("\n" + "=" * 60)
    print("F16 验收结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
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
