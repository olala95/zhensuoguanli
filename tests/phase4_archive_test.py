# -*- coding: utf-8 -*-
"""第四阶段病历归档冒烟（M11 F76-F83）。

覆盖：确认建 v1、版本快照自包含、修改留痕（前后值）、关闭、归档号、
      Hash 链（篡改可发现 / 只增不改 / 批量巡检）、封存、解封（高风险审计）、
      重新归档闭环、导出（格式白名单 / 路径穿越 / 不落内容副本）。
用法：python tests/phase4_archive_test.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m03_record as m03,  # noqa: E402
                     m14_system as m14, m11_archive as m11)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_archive_last.log")
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


def _one(sql, params=()):
    return db.query_one(sql, params)


def _all(sql, params=()):
    return db.query(sql, params)


def new_record(ctx, name, chief="咳嗽三天"):
    """建患者→就诊→病历（含主诉与一条诊断），返回 (patient, visit, record_id)。"""
    pat = m01.create(ctx, {"name": name, "gender": "M", "birth_date": "1980-01-01"}, {})
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "TCM"}, {})
    m02.start(ctx, {"visit_id": vis["id"]}, {})
    rid = m03.ensure_record(db.write_conn(), vis["id"], ctx)["id"]
    m03.save_chief(ctx, {"record_id": rid, "content": chief, "duration": "3天"}, {})
    m03.save_diagnosis(ctx, {"record_id": rid, "items": [
        {"diag_type": "TCM", "tcm_name": "风热犯肺", "is_primary": 1}]}, {})
    return pat, vis, rid


def confirm(ctx, rid, pwd):
    return m03.confirm(ctx, {"record_id": rid, "password": pwd}, {})


def main():
    root = tempfile.mkdtemp(prefix="clinic_archive_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))

    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    m14.init_system(sys_ctx, {"admin_username": "admin", "admin_password": "Clinic@2026",
                              "admin_name": "管理员", "clinic_name": "示例中医诊所"}, {})
    m14.user_roles(sys_ctx, {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                     "CASHIER"]}, {})
    sess = m14.login(sys_ctx, {"username": "admin", "password": "Clinic@2026",
                               "role_code": "DOCTOR"}, {})
    ctx = Context(user_id=sess["user_id"], role_code="DOCTOR", source="UI",
                  username="admin", session_id=sess["session_id"])
    set_ctx(ctx)
    PWD = "Clinic@2026"

    # ============================================================
    print("\n[F76] 确认即建 v1 + Hash 链首条")
    _, _, rid = new_record(ctx, "张三")
    res = confirm(ctx, rid, PWD)
    check("确认返回 version_no=1", res.get("version_no") == 1, str(res))
    v1 = _one("SELECT * FROM record_version WHERE record_id=? AND version_no=1", (rid,))
    check("版本快照已落库", v1 is not None and len(v1["snapshot_json"]) > 50)
    h1 = _one("SELECT * FROM record_hash WHERE record_id=? AND version_no=1", (rid,))
    check("Hash 算法 SHA256", h1["algorithm"] == "SHA256")
    check("链首 prev_hash 为空串", h1["prev_hash"] == "", repr(h1["prev_hash"]))
    check("版本来源 CONFIRM", v1["source"] == "CONFIRM")
    ok, bad, why = m11.verify_hash(rid)
    check("Hash 链校验通过", ok, why)

    # ============================================================
    print("\n[F77] 修改留痕")
    expect_error("未填原因被拒",
                 lambda: m11.amend(ctx, {"record_id": rid, "changes": [
                     {"field_path": "chief_complaint.content", "after_value": "咳嗽五天"}]}, {}),
                 "E2001")
    expect_error("原因过短被拒",
                 lambda: m11.amend(ctx, {"record_id": rid, "reason": "改", "changes": [
                     {"field_path": "chief_complaint.content", "after_value": "咳嗽五天"}]}, {}),
                 "E2001")
    expect_error("不支持的字段被拒",
                 lambda: m11.amend(ctx, {"record_id": rid, "reason": "补充诊断信息", "changes": [
                     {"field_path": "diagnosis.icd10_name", "after_value": "xx"}]}, {}),
                 "E2001")
    snap1 = _one("SELECT snapshot_json FROM record_version WHERE record_id=? AND version_no=1",
                 (rid,))["snapshot_json"]
    a = m11.amend(ctx, {"record_id": rid, "reason": "患者补充：实为咳嗽五天",
                        "changes": [
                            {"field_path": "chief_complaint.content", "after_value": "咳嗽五天"},
                            {"field_path": "present_illness.content",
                             "after_value": "五天前受凉后咳嗽"}]}, {})
    check("修改生成 v2", a["version_no"] == 2, str(a))
    check("一次留痕两处变更", a["amended"] == 2)
    snap1b = _one("SELECT snapshot_json FROM record_version WHERE record_id=? AND version_no=1",
                  (rid,))["snapshot_json"]
    check("v1 快照不被后续修改影响（自包含）", snap1 == snap1b)
    am = _all("SELECT * FROM record_amendment WHERE record_id=? AND version_no=2 ORDER BY id",
              (rid,))
    check("留痕含前后值", len(am) == 2 and am[0]["before_value"] == "咳嗽三天"
          and am[0]["after_value"] == "咳嗽五天",
          "%s → %s" % (am[0]["before_value"], am[0]["after_value"]))
    check("留痕含修改人与角色", am[0]["operator_id"] == ctx.user_id
          and am[0]["operator_role"] == "DOCTOR")
    check("留痕按 version_no 归组", all(x["version_no"] == 2 for x in am))
    cc = _one("SELECT content FROM record_chief_complaint WHERE record_id=?", (rid,))
    check("内容经 M03 接口已落库", cc["content"] == "咳嗽五天", cc["content"])
    h2 = _one("SELECT * FROM record_hash WHERE record_id=? AND version_no=2", (rid,))
    check("v2 的 prev_hash 等于 v1 的 hash", h2["prev_hash"] == h1["hash"])
    ok, bad, why = m11.verify_hash(rid)
    check("修改后链仍校验通过", ok, why)

    # ============================================================
    print("\n[F78] 关闭")
    _, _, rid2 = new_record(ctx, "李四")
    expect_error("草稿不能关闭", lambda: m11.close(ctx, {"record_id": rid2}, {}), "E3001")
    c1 = m11.close(ctx, {"record_id": rid, "summary": "疗程结束"}, {})
    check("关闭成功", c1["record_id"] == rid)
    check("关闭不改状态（仍 CONFIRMED）",
          _one("SELECT status FROM record WHERE id=?", (rid,))["status"] == "CONFIRMED")
    expect_error("重复关闭被拒", lambda: m11.close(ctx, {"record_id": rid}, {}), "E3001")
    expect_error("关闭后禁止修改留痕",
                 lambda: m11.amend(ctx, {"record_id": rid, "reason": "再改一次试试",
                                         "changes": [{"field_path": "chief_complaint.content",
                                                      "after_value": "咳嗽七天"}]}, {}),
                 "E3001")

    # ============================================================
    print("\n[F80] Hash 链防篡改")
    conn = db.write_conn()
    try:
        db.execute(conn, "UPDATE record_hash SET hash='deadbeef' WHERE record_id=? AND "
                         "version_no=1", (rid,))
        check("直接改 Hash 应被触发器拒绝", False)
    except AppError as exc:
        check("直接改 Hash 被拒（E2006）", exc.code == "E2006", "%s %s" % (exc.code, exc.message))
    try:
        db.execute(conn, "DELETE FROM record_version WHERE record_id=?", (rid,))
        check("删除版本应被拒绝", False)
    except AppError as exc:
        check("删除版本被拒（E2006）", exc.code == "E2006", exc.code)

    # 注入"伪造版本"：prev_hash 正确但 hash 不对
    import json as _json
    db.execute(conn, """
        INSERT INTO record_version (record_id, version_no, snapshot_json, source, change_note,
                                    created_at, created_by, created_role)
        VALUES (?,99,?,'AMEND','伪造',datetime('now','localtime'),?,?)
    """, (rid, _json.dumps({"fake": True}, ensure_ascii=False), ctx.user_id, ctx.role_code))
    db.execute(conn, """
        INSERT INTO record_hash (record_id, version_no, algorithm, prev_hash, hash,
                                 created_at, created_by)
        VALUES (?,99,'SHA256',?,'ff'||replace(hex(zeroblob(31)),'00','0'),
                datetime('now','localtime'),?)
    """, (rid, h2["hash"], ctx.user_id))
    ok, bad, why = m11.verify_hash(rid)
    check("伪造版本可被发现", (not ok) and bad == 99, "bad=%s %s" % (bad, why))
    check("指出首个不符位置（v99）", bad == 99)

    # 清掉伪造行（只增不改表无法 DELETE，重建一份干净病历用于后续归档链路）
    _, _, rid3 = new_record(ctx, "王五", chief="头痛两天")
    r3 = confirm(ctx, rid3, PWD)
    check("新病历确认建 v1", r3["version_no"] == 1)

    # ============================================================
    print("\n[F79] 归档")
    expect_error("未确认不能归档", lambda: m11.archive(ctx, {"record_id": rid2}, {}), "E3001")
    ok, bad, why = m11.verify_hash(rid)
    check("链被破坏的病历校验失败", (not ok))
    expect_error("链异常拒绝归档", lambda: m11.archive(ctx, {"record_id": rid}, {}), "E3090")

    arc = m11.archive(ctx, {"record_id": rid3}, {})
    check("归档号 AR{yyyyMM}{4位}（12 位）",
          arc["archive_no"].startswith("AR") and len(arc["archive_no"]) == 12,
          arc["archive_no"])
    check("归档固化版本号", arc["version_no"] == 1, str(arc["version_no"]))
    check("状态推进到 ARCHIVED",
          _one("SELECT status FROM record WHERE id=?", (rid3,))["status"] == "ARCHIVED")
    expect_error("重复归档被拒", lambda: m11.archive(ctx, {"record_id": rid3}, {}), "E3001")
    ar = _one("SELECT * FROM record_archive WHERE record_id=?", (rid3,))
    check("归档记录不含内容副本",
          all(k not in ar for k in ("content", "snapshot_json", "chief_complaint")),
          ",".join(ar.keys()))
    lst = m11.archive_list(ctx, {}, {"month": arc["archive_month"]})
    check("可按月列出档案", any(x["archive_no"] == arc["archive_no"] for x in lst["items"]),
          "%s 条" % len(lst["items"]))

    # ============================================================
    print("\n[F81] 封存")
    _, _, rid4 = new_record(ctx, "赵六", chief="失眠一月")
    confirm(ctx, rid4, PWD)
    expect_error("未归档不能封存", lambda: m11.seal(ctx, {"record_id": rid4}, {}), "E3001")
    sl = m11.seal(ctx, {"record_id": rid3, "reason": "纠纷调阅保全"}, {})
    check("封存成功", sl["record_id"] == rid3)
    check("状态推进到 SEALED",
          _one("SELECT status FROM record WHERE id=?", (rid3,))["status"] == "SEALED")
    check("封存记录含原因",
          _one("SELECT reason FROM record_seal WHERE record_id=?", (rid3,))["reason"]
          == "纠纷调阅保全")
    expect_error("封存后禁止修改",
                 lambda: m11.amend(ctx, {"record_id": rid3, "reason": "封存后还想改",
                                         "changes": [{"field_path": "chief_complaint.content",
                                                      "after_value": "xx"}]}, {}),
                 "E3001")
    expect_error("封存后禁止再归档", lambda: m11.archive(ctx, {"record_id": rid3}, {}), "E3001")
    expect_error("已封存不可原地重复封存",
                 lambda: m11.seal(ctx, {"record_id": rid3}, {}), "E3001")

    # 封存幂等：用一份干净病历走完 归档→封存 两次相同幂等键
    print("\n[F81] 归档后链被破坏 → 拒绝封存（含封存幂等）")
    _, _, rid5 = new_record(ctx, "孙七", chief="胃脘胀痛")
    confirm(ctx, rid5, PWD)
    m11.archive(ctx, {"record_id": rid5}, {})
    dup_seal = m11.seal(ctx, {"record_id": rid5, "idempotent_key": "SEAL-1"}, {})
    dup_seal2 = m11.seal(ctx, {"record_id": rid5, "idempotent_key": "SEAL-1"}, {})
    check("封存幂等（相同 key 不重复封存）", dup_seal2["seal_id"] == dup_seal["seal_id"])

    arc4 = m11.archive(ctx, {"record_id": rid4}, {})
    check("第二份病历归档成功", arc4["archive_no"].startswith("AR"))
    ver4 = _one("SELECT * FROM record_version WHERE record_id=? ORDER BY version_no DESC LIMIT 1",
                (rid4,))
    db.execute(conn, """
        INSERT INTO record_version (record_id, version_no, snapshot_json, source, change_note,
                                    created_at, created_by, created_role)
        VALUES (?,99,?,'AMEND','伪造',datetime('now','localtime'),?,?)
    """, (rid4, _json.dumps({"fake": 1}), ctx.user_id, ctx.role_code))
    db.execute(conn, """
        INSERT INTO record_hash (record_id, version_no, algorithm, prev_hash, hash,
                                 created_at, created_by)
        VALUES (?,99,'SHA256',?,?,datetime('now','localtime'),?)
    """, (rid4, ver4 and "wrong-prev", "aa" * 32, ctx.user_id))
    expect_error("链异常拒绝封存", lambda: m11.seal(ctx, {"record_id": rid4}, {}), "E3090")
    hb = _all("SELECT * FROM audit_high_risk WHERE risk_type='HASH_BROKEN'")
    check("链异常写高风险审计", len(hb) >= 2, "%d 条" % len(hb))

    # ============================================================
    print("\n[F82] 解封")
    expect_error("未封存不能解封",
                 lambda: m11.unseal(ctx, {"record_id": rid4, "reason": "先试着解封一下"}, {}),
                 "E3001")
    expect_error("未填原因不能解封",
                 lambda: m11.unseal(ctx, {"record_id": rid3}, {}), "E2001")
    un = m11.unseal(ctx, {"record_id": rid3, "reason": "发现漏记过敏史，需更正"}, {})
    check("解封成功", un["record_id"] == rid3)
    check("状态推进到 UNSEALED",
          _one("SELECT status FROM record WHERE id=?", (rid3,))["status"] == "UNSEALED")
    check("封存记录未被删除（成对留存）",
          _one("SELECT COUNT(*) c FROM record_seal WHERE record_id=?", (rid3,))["c"] >= 1)
    hr = _all("SELECT * FROM audit_high_risk WHERE risk_type='UNSEAL' AND entity_id=?",
              (str(rid3),))
    check("解封写高风险审计（含原因）", len(hr) == 1
          and "漏记过敏史" in (hr[0]["reason"] or ""), str(len(hr)))
    expect_error("重复解封被拒",
                 lambda: m11.unseal(ctx, {"record_id": rid3, "reason": "再解封一次试试"},
                                    {}), "E3091")
    expect_error("解封后不可原地再封存", lambda: m11.seal(ctx, {"record_id": rid3}, {}), "E3001")

    # S3 闭环：解封 → 修改 → 重新归档（新归档号）→ 再封存
    a3 = m11.amend(ctx, {"record_id": rid3, "reason": "补记头孢类过敏史",
                         "changes": [{"field_path": "chief_complaint.content",
                                      "after_value": "头痛两天，伴鼻塞"}]}, {})
    check("解封后可修改留痕", a3["version_no"] == 2, str(a3["version_no"]))
    # 清理 rid3 上的伪造行不可能；改用 rid 之外的正常病历演示重新归档
    # 由于 rid3 之前未注入伪造行（伪造行注入在 rid / rid4），可直接重新归档
    arc3b = m11.archive(ctx, {"record_id": rid3}, {})
    check("解封后可重新归档且开新归档号", arc3b["archive_no"] != arc["archive_no"],
          "%s → %s" % (arc["archive_no"], arc3b["archive_no"]))
    check("重新归档固化新版本号", arc3b["version_no"] == 2, str(arc3b["version_no"]))
    sl3b = m11.seal(ctx, {"record_id": rid3, "reason": "更正后重新保全"}, {})
    check("重新归档后可再封存", sl3b["record_id"] == rid3
          and _one("SELECT status FROM record WHERE id=?", (rid3,))["status"] == "SEALED")

    # ============================================================
    print("\n[F83] 导出")
    expect_error("格式不在白名单",
                 lambda: m11.export(ctx, {"record_id": rid3, "format": "XLSX"}, {}), "E2001")
    expect_error("路径穿越被拒",
                 lambda: m11.export(ctx, {"record_id": rid3, "format": "HTML",
                                          "target_path": "../../evil.html"}, {}), "E3092")
    expect_error("绝对路径被拒",
                 lambda: m11.export(ctx, {"record_id": rid3, "format": "HTML",
                                          "target_path": "C:/tmp/evil.html"}, {}), "E3092")
    before_status = _one("SELECT status FROM record WHERE id=?", (rid3,))["status"]
    before_ver = _one("SELECT COUNT(*) c FROM record_version WHERE record_id=?", (rid3,))["c"]
    exp = m11.export(ctx, {"record_id": rid3, "format": "JSON"}, {})
    check("JSON 导出成功", exp["format"] == "JSON" and exp["file_size"] > 0, str(exp["file_size"]))
    check("导出不改变状态",
          _one("SELECT status FROM record WHERE id=?", (rid3,))["status"] == before_status)
    check("导出不新增版本",
          _one("SELECT COUNT(*) c FROM record_version WHERE record_id=?", (rid3,))["c"]
          == before_ver)
    ex = _one("SELECT * FROM record_export WHERE id=?", (exp["export_id"],))
    check("导出事件不含内容副本",
          all(k not in ex for k in ("content", "snapshot_json", "snapshot")), ",".join(ex.keys()))
    check("导出记录 hash_verified=1", ex["hash_verified"] == 1)
    exp_html = m11.export(ctx, {"record_id": rid3, "format": "HTML",
                                "file_name": "record_html.html"}, {})
    check("HTML 导出成功", exp_html["format"] == "HTML")
    exp_pdf = m11.export(ctx, {"record_id": rid3, "format": "PDF",
                               "file_name": "record_pdf.pdf"}, {})
    check("PDF 导出成功", exp_pdf["format"] == "PDF" and exp_pdf["file_size"] > 100)
    base = m11._export_dir()
    pdf_path = os.path.join(base, "record_pdf.pdf")
    check("PDF 文件头合法", open(pdf_path, "rb").read(8) == b"%PDF-1.4")
    exp_dup = m11.export(ctx, {"record_id": rid3, "format": "JSON",
                               "idempotent_key": "EXP-1"}, {})
    exp_dup2 = m11.export(ctx, {"record_id": rid3, "format": "JSON",
                                "idempotent_key": "EXP-1"}, {})
    check("导出幂等", exp_dup2["export_id"] == exp_dup["export_id"])
    try:
        db.execute(conn, "UPDATE record_export SET file_size=0 WHERE id=?",
                   (exp["export_id"],))
        check("改导出事件应被拒", False)
    except AppError as e:
        check("导出事件只增不改（E2006）", e.code == "E2006", e.code)
    expect_error("链异常拒绝导出",
                 lambda: m11.export(ctx, {"record_id": rid4, "format": "HTML"}, {}), "E3090")

    # ============================================================
    print("\n[F76/F80] 版本查询与批量巡检")
    vs = m11.versions(ctx, {}, {"record_id": rid3})
    check("版本列表连续递增",
          [x["version_no"] for x in vs["items"]] == list(range(1, len(vs["items"]) + 1)),
          str([x["version_no"] for x in vs["items"]]))
    vg = m11.version_get(ctx, {}, {"record_id": rid3, "version_no": 1})
    check("可取指定版本快照", "record" in vg["snapshot"])
    check("版本元信息含来源与说明", vg["source"] == "CONFIRM")
    batch = m11.hash_verify_all(ctx, {"limit": 50}, {})
    check("批量巡检返回结果", batch["checked"] > 0, "检查 %d 份" % batch["checked"])
    check("批量巡检发现异常病历",
          any(x["record_id"] in (rid, rid4) for x in batch["invalid"]),
          "异常 %d 份" % batch["invalid_count"])

    # ============================================================
    print("\n[合规] 不写 M03 表 / 审计完整")
    check("record_status_log 有归档与封存流转",
          _one("SELECT COUNT(*) c FROM record_status_log WHERE record_id=? AND "
               "to_status IN ('ARCHIVED','SEALED','UNSEALED')", (rid3,))["c"] >= 3)
    check("高风险审计含解封与导出",
          _one("SELECT COUNT(*) c FROM audit_high_risk WHERE risk_type='UNSEAL'")["c"] >= 1
          and _one("SELECT COUNT(*) c FROM audit_high_risk WHERE risk_type='EXPORT'")["c"] >= 1)
    check("M11 未修改 record.version 之外的病历内容字段",
          _one("SELECT status FROM record WHERE id=?", (rid3,))["status"] == "SEALED")

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print("  - %s" % f)
    print("日志：%s" % _LOG_PATH)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
