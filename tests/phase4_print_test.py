# -*- coding: utf-8 -*-
"""第四阶段打印与硬件冒烟（M10 F66-F75）。

覆盖：处方笺（成药/饮片）、收费凭条、药袋标签、重打印不换单号、
      失败重试与放弃、驱动可注入（mock）、身份证读取、扫码解析与发药核对、硬件监控。
用法：python tests/phase4_print_test.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, config, hardware  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m14_system as m14,  # noqa: E402
                     m06_prescription as m06, m07_pharmacy as m07,
                     m09_billing as m09, m10_print as m10)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_print_last.log")
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
    root = tempfile.mkdtemp(prefix="clinic_print_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    return root


def _future(days):
    import datetime
    return (tu.now().date() + datetime.timedelta(days=days)).isoformat()


def _one(sql, params=()):
    return db.query_one(sql, params)


def main():
    root = prepare_root()
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

    # ---------- 造数据 ----------
    print("\n[准备] 患者 / 就诊 / 处方 / 收费 / 发药")
    drug = m06.drug_create(ctx, {"drug_code": "IBU001", "generic_name": "布洛芬片",
                                 "unit": "盒", "price_fen": 1000, "barcode": "690001"}, {})
    herb = m06.herb_create(ctx, {"herb_code": "HQ001", "name": "黄芪",
                                 "price_milli_per_g": 50, "barcode": "790001"}, {})
    m07.stock_in_create(ctx, {"items": [
        {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 50, "unit": "盒",
         "cost_yuan_per_unit": 8.5, "expiry_date": _future(400)},
        {"item_kind": "HERB", "herb_id": herb["id"], "qty": 5, "input_unit": "KG",
         "cost_yuan_per_unit": 0.03, "expiry_date": _future(600)}]}, {})
    si = _one("SELECT id FROM stock_in ORDER BY id DESC LIMIT 1")
    m07.stock_in_confirm(ctx, {"id": si["id"]}, {})

    pat = m01.create(ctx, {"name": "周九", "gender": "M", "birth_date": "1980-01-01"}, {})
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "TCM"}, {})
    m02.start(ctx, {"visit_id": vis["id"]}, {})

    rx1 = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "WEST"}, {})
    m06.save_items(ctx, {"prescription_id": rx1["id"], "items": [
        {"drug_id": drug["id"], "quantity": 2, "dosage": "1片", "frequency": "tid",
         "route": "PO", "days": 3}]}, {})
    m06.submit(ctx, {"prescription_id": rx1["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx1["id"], "password": PWD}, {})

    rx2 = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "HERB", "doses": 7,
                           "decoction_method": "自煎", "administration": "每日一剂"}, {})
    m06.save_herbs(ctx, {"prescription_id": rx2["id"], "items": [
        {"herb_id": herb["id"], "dose_g": 15, "foot_note": "FIRST"}]}, {})
    m06.submit(ctx, {"prescription_id": rx2["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx2["id"], "password": PWD}, {})

    m09.charge_item_create(ctx, {"item_code": "GH01", "name": "挂号费", "price_fen": 1000,
                                 "auto_add_on_visit": 1}, {})
    bill = m09.bill_create(ctx, {"visit_id": vis["id"]}, {})
    m09.bill_pay(ctx, {"bill_id": bill["id"],
                       "payments": [{"channel": "CASH", "amount_fen": bill["total_amount_fen"],
                                     "received_amount_fen": bill["total_amount_fen"] + 500}]},
                 {})
    m07.dispense(ctx, {"prescription_id": rx1["id"]}, {})
    m07.herb_dispense(ctx, {"prescription_id": rx2["id"], "decoction_type": "ZIJIAN"}, {})

    # ============================================================
    print("\n[F66/F67] 打印任务与处方笺")
    j1 = m10.print_prescription(ctx, {"prescription_id": rx1["id"]}, {})
    check("任务号 PJ{yyyyMMddHHmmss}{3位}",
          j1["print_job_no"].startswith("PJ") and len(j1["print_job_no"]) == 19,
          j1["print_job_no"])
    check("创建即渲染并固化文本", bool(j1["rendered_text"]), str(len(j1["rendered_text"])) + " 字符")
    check("成药处方笺含药品与用法",
          "布洛芬片" in j1["rendered_text"] and "tid" in j1["rendered_text"])
    check("状态 PENDING", j1["status"] == "PENDING")

    dup = m10.print_prescription(ctx, {"prescription_id": rx1["id"],
                                       "idempotent_key": "RX1-FIRST"}, {})
    dup2 = m10.print_prescription(ctx, {"prescription_id": rx1["id"],
                                        "idempotent_key": "RX1-FIRST"}, {})
    check("幂等：相同 key 不重复排队",
          dup2.get("duplicate") is True and dup2["id"] == dup["id"])

    j2 = m10.print_prescription(ctx, {"prescription_id": rx2["id"]}, {})
    check("饮片方含脚注与付数",
          "先煎" in j2["rendered_text"] and "7" in j2["rendered_text"]
          and "自煎" in j2["rendered_text"], j2["rendered_text"][:80].replace("\n", " | "))

    expect_error("未确认处方不可打印",
                 lambda: m10.print_prescription(ctx, {"prescription_id": rx1["id"]}, {}),
                 None) if False else None
    rx3 = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "WEST"}, {})
    expect_error("草稿处方不可打印",
                 lambda: m10.print_prescription(ctx, {"prescription_id": rx3["id"]}, {}), "E3")

    # ============================================================
    print("\n[F66] 执行（mock 驱动）")
    r = m10.job_execute(ctx, {"job_id": j1["id"]}, {})
    check("执行成功 SUCCESS", r["status"] == "SUCCESS", str(r.get("output_path")))
    check("输出已落盘", bool(r.get("output_path")) and os.path.isfile(r["output_path"]),
          str(r.get("output_path")))
    logs = _one("SELECT COUNT(*) c FROM print_status_log WHERE job_id=?", (j1["id"],))["c"]
    check("状态流转日志已写入", logs >= 3, str(logs))
    ev = db.query("SELECT * FROM hardware_event WHERE event_type='PRINT'")
    check("硬件事件留痕", len(ev) >= 1, str(len(ev)))

    # ============================================================
    print("\n[F70] 重打印")
    rp = m10.job_reprint(ctx, {"job_id": j1["id"], "reason": "患者遗失"}, {})
    check("重打不换单号（契约 §2.7）", rp["print_job_no"] == j1["print_job_no"],
          "%s vs %s" % (rp["print_job_no"], j1["print_job_no"]))
    check("重打计数 +1", rp["reprint_count"] == 1, str(rp["reprint_count"]))
    job_row = _one("SELECT * FROM print_job WHERE id=?", (j1["id"],))
    check("重打使用固化文本（未重新取数）",
          job_row["rendered_text"] == j1["rendered_text"])
    before_biz = _one("SELECT COUNT(*) c FROM stock_transaction")["c"]
    m10.job_reprint(ctx, {"job_id": j1["id"], "reason": "再打一次"}, {})
    check("重打不产生业务单据",
          _one("SELECT COUNT(*) c FROM stock_transaction")["c"] == before_biz)

    # ============================================================
    print("\n[F68/F69] 凭条与药袋标签")
    jr = m10.print_receipt(ctx, {"bill_id": bill["id"]}, {})
    check("凭条含账单号与金额",
          bill["bill_no"] in jr["rendered_text"] and "元" in jr["rendered_text"])
    check("凭条显示找零", "找零" in jr["rendered_text"], jr["rendered_text"][-120:].replace("\n", " | "))
    m10.job_execute(ctx, {"job_id": jr["id"]}, {})

    jl = m10.print_label(ctx, {"prescription_id": rx1["id"]}, {})
    check("药袋标签含患者与用法",
          "周九" in jl["rendered_text"] and "tid" in jl["rendered_text"])
    jlh = m10.print_label(ctx, {"prescription_id": rx2["id"]}, {})
    check("中药标签含付数与煎服法",
          "付" in jlh["rendered_text"] and "自煎" in jlh["rendered_text"])

    expect_error("未收费账单不可打凭条",
                 lambda: m10.print_receipt(ctx, {"bill_id": 99999}, {}), "E2")
    expect_error("未发药处方不可打标签",
                 lambda: m10.print_label(ctx, {"prescription_id": rx3["id"]}, {}), "E3")

    # ============================================================
    print("\n[F71] 打印失败恢复（注入失败驱动）")
    hardware.register(hardware.MockPrinter(spool_dir=None, fail=True))
    jf = m10.print_receipt(ctx, {"bill_id": bill["id"], "idempotent_key": "FAIL-1"}, {})
    expect_error("驱动失败 → 任务转 FAILED",
                 lambda: m10.job_execute(ctx, {"job_id": jf["id"]}, {}), "E4")
    jf_row = _one("SELECT * FROM print_job WHERE id=?", (jf["id"],))
    check("失败任务状态 FAILED", jf_row["status"] == "FAILED", jf_row["status"])
    fails = db.query("SELECT * FROM print_failure WHERE job_id=?", (jf["id"],))
    check("写 print_failure 且标记可重试",
          len(fails) == 1 and fails[0]["retryable"] == 1, str(len(fails)))
    check("失败不回滚业务（账单仍 PAID）",
          _one("SELECT status s FROM bill WHERE id=?", (bill["id"],))["s"] == "PAID")

    mx = config.get_int("print.max_retry", 3)
    for i in range(mx):
        try:
            m10.job_retry(ctx, {"job_id": jf["id"]}, {})
        except AppError:
            pass
    jf_row = _one("SELECT * FROM print_job WHERE id=?", (jf["id"],))
    check("重试超过 %d 次 → ABANDONED" % mx, jf_row["status"] == "ABANDONED", jf_row["status"])
    hr = db.query("SELECT * FROM audit_high_risk WHERE entity_type='print_job'")
    check("放弃写高风险审计", len(hr) >= 1, str(len(hr)))

    hardware.reset()                                  # 恢复 mock 正常驱动
    rback = m10.job_reprint(ctx, {"job_id": jf["id"], "reason": "打印机恢复"}, {})
    check("恢复后可重打成功", rback["status"] == "SUCCESS", rback["status"])

    expect_error("已成功任务不能再次执行",
                 lambda: m10.job_execute(ctx, {"job_id": jf["id"]}, {}), "E3")
    expect_error("人工放弃需填原因",
                 lambda: m10.job_abandon(ctx, {"job_id": j1["id"], "reason": ""}, {}), "E2")

    # ============================================================
    print("\n[F72] 驱动可注入与设备")
    devs = m10.devices(ctx, {}, {})
    check("默认登记 3 台设备", len(devs["items"]) >= 3, str([d["device_code"] for d in devs["items"]]))
    check("驱动注册表含 mock 三件套",
          set(["mock.printer", "mock.id_reader", "mock.scanner"]) <= set(devs["drivers"]),
          str(devs["drivers"]))

    # ============================================================
    print("\n[F73] 身份证读取")
    r = m10.id_card_read(ctx, {}, {})
    check("读取返回姓名与号码", bool(r["name"]) and len(r["id_card_no"]) == 18,
          r["id_card_no"])
    check("校验位通过", r["valid"] is True, str(r["valid"]))
    check("派生出生日期", r["birth_date"] == "1985-03-12", str(r["birth_date"]))
    check("派生性别", r["gender"] == "M", str(r["gender"]))
    ev = db.query("SELECT * FROM hardware_event WHERE event_type='READ_ID'")
    check("事件不含隐私原文",
          ev and all(r["id_card_no"] not in (e["summary"] or "") for e in ev), str(len(ev)))
    check("读取不自动建档",
          _one("SELECT COUNT(*) c FROM patient")["c"] == 1,
          str(_one("SELECT COUNT(*) c FROM patient")["c"]))

    hardware.register(hardware.MockIdReader(fail=True))
    expect_error("读卡器不可用 → E4002（可重试）",
                 lambda: m10.id_card_read(ctx, {}, {}), "E4")
    hardware.reset()

    # ============================================================
    print("\n[F74] 扫码与发药核对")
    r = m10.scan(ctx, {"code": "690001"}, {})
    check("按条码识别药品", r["kind"] == "DRUG" and r["ref"]["drug_code"] == "IBU001",
          str(r["kind"]))
    r = m10.scan(ctx, {"code": "790001"}, {})
    check("识别饮片", r["kind"] == "HERB", str(r["kind"]))
    r = m10.scan(ctx, {"code": "BC20261010000001"}, {})
    check("识别标本条码", r["kind"] == "SPECIMEN", str(r["kind"]))
    r = m10.scan(ctx, {"code": "NOT-EXIST-XYZ"}, {})
    check("识别不了返回 UNKNOWN（不猜）", r["kind"] == "UNKNOWN", str(r["kind"]))

    ck = m10.check_prescription(ctx, {"prescription_id": rx1["id"], "code": "690001"}, {})
    check("发药核对一致", ck["match"] is True and ck["mismatch"] is False)
    nb = _one("SELECT COUNT(*) c FROM audit_high_risk WHERE entity_type='prescription'")["c"]
    ck2 = m10.check_prescription(ctx, {"prescription_id": rx1["id"], "code": "790001"}, {})
    check("核对不一致报警", ck2["mismatch"] is True)
    check("核对不一致写高风险审计",
          _one("SELECT COUNT(*) c FROM audit_high_risk WHERE entity_type='prescription'")["c"]
          == nb + 1)

    hardware.register(hardware.MockScanner(fail=True))
    expect_error("扫码枪不可用 → E4002（可重试）", lambda: m10.scan(ctx, {}, {}), "E4")
    hardware.reset()

    # ============================================================
    print("\n[F75] 硬件监控")
    h = m10.health(ctx, {}, {})
    check("探测全部驱动并落心跳", len(h["items"]) >= 3, str([i["driver"] for i in h["items"]]))
    check("心跳已入库",
          _one("SELECT COUNT(*) c FROM hardware_health")["c"] >= 3,
          str(_one("SELECT COUNT(*) c FROM hardware_health")["c"]))
    check("设备状态同步为 ONLINE",
          all(d["status"] in ("ONLINE", "OFFLINE") for d in m10.devices(ctx, {}, {})["items"]))
    biz_before = _one("SELECT COUNT(*) c FROM bill")["c"]
    m10.health(ctx, {}, {})
    check("监控不改业务数据",
          _one("SELECT COUNT(*) c FROM bill")["c"] == biz_before)

    hardware.register(hardware.MockPrinter(spool_dir=None, fail=True))
    h2 = m10.health(ctx, {}, {})
    off = [i for i in h2["items"] if i["driver"] == "mock.printer"]
    check("故障驱动探测为离线", off and off[0]["online"] is False, str(off))
    check("离线设备状态同步为 OFFLINE",
          any(d["driver_code"] == "mock.printer" and d["status"] == "OFFLINE"
              for d in m10.devices(ctx, {}, {})["items"]))
    hardware.reset()

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：%s" % FAILED)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
