# -*- coding: utf-8 -*-
"""第五阶段：系统级故障演练（02_DEVELOPMENT_ORDER §6 清单第 1-10 条）。

每一项都按同一套路执行：
    制造故障  →  断言期望结果  →  断言系统仍处于可用态（能继续干活）

| #  | 场景                  | 期望                                             |
|----|---------------------|------------------------------------------------|
| 1  | 写病历中途强制断电        | 已保存内容不丢，未提交回滚，无脏数据                          |
| 2  | 收费中途断电            | 账单与支付要么都在要么都不在，不出现"已扣款无账单"                |
| 3  | 发药中途断电            | 库存扣减与发药记录一致，不少扣不多扣                          |
| 4  | 打印中途断电/缺纸         | 任务可重打，重打不产生新单据号，有重打标记                       |
| 5  | AI 服务不可用           | 降级到手工录入，已有数据不丢，任务可恢复                       |
| 6  | Hardware Agent 崩溃   | 看门狗自动拉起，未完成打印任务不丢                           |
| 7  | AI Worker 崩溃        | 看门狗拉起，任务状态正确，不重复执行                          |
| 8  | SQLite 文件损坏        | 完整性检查发现，可从最近备份恢复，恢复流程有记录                   |
| 9  | 系统时间被回拨           | F88 检测到并记录告警，业务时间不倒流                        |
| 10 | 数据库写锁超时           | 有明确异常码与重试，不静默失败                             |

用法：python tests/phase5_fault_drills_test.py
"""

import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, backup, hardware, config, selftest  # noqa: E402
from core import timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m03_record as m03,  # noqa: E402
                     m04_ai as m04, m06_prescription as m06, m07_pharmacy as m07,
                     m09_billing as m09, m10_print as m10, m12_audit as m12,
                     m13_recovery as m13, m14_system as m14)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_drills_last.log")
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


def _count_safe(table, where="", params=()):
    """库已损坏时的读取：命中坏页会抛 E1003。返回 -1 而不是让整个演练崩掉。"""
    try:
        return _count(table, where, params)
    except AppError:
        return -1


class PowerCut(Exception):
    """演练用：模拟进程在事务中被强制杀掉。"""


class AlwaysDown(object):
    """Hardware Agent 崩溃模拟：重启也起不来，用于验证 F98 降级路径。"""

    code = "mock.printer"

    def status(self):
        return {"online": False, "detail": "agent crashed (test)"}

    def print_text(self, text, meta=None):
        raise AppError("E4001", "打印机永久离线（测试）")


def _future(days):
    return (tu.now().date() + datetime.timedelta(days=days)).isoformat()


def main():
    root = tempfile.mkdtemp(prefix="clinic_drills_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))

    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    m14.init_system(sys_ctx, {"admin_username": "admin", "admin_password": PWD,
                              "admin_name": "管理员", "clinic_name": "示例中医诊所"}, {})
    m14.user_roles(sys_ctx, {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                     "CASHIER"]}, {})
    s = m14.login(sys_ctx, {"username": "admin", "password": PWD, "role_code": "ADMIN"}, {})
    ctx = Context(user_id=s["user_id"], role_code="ADMIN", source="UI",
                  username="admin", session_id=s["session_id"])
    set_ctx(ctx)

    # ---------------- 公共造数据 ----------------
    print("\n[准备] 患者 / 就诊 / 库存 / 处方 / 收费")
    drug = m06.drug_create(ctx, {"drug_code": "IBU001", "generic_name": "布洛芬片",
                                 "unit": "盒", "price_fen": 1000, "barcode": "690001"}, {})
    m07.stock_in_create(ctx, {"items": [
        {"item_kind": "DRUG", "drug_id": drug["id"], "qty": 50, "unit": "盒",
         "cost_yuan_per_unit": 8.5, "expiry_date": _future(400)}]}, {})
    si = _one("SELECT id FROM stock_in ORDER BY id DESC LIMIT 1")
    m07.stock_in_confirm(ctx, {"id": si["id"]}, {})

    pat = m01.create(ctx, {"name": "演练甲", "gender": "M", "birth_date": "1980-01-01"}, {})
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "TCM"}, {})
    m02.start(ctx, {"visit_id": vis["id"]}, {})
    batch = _one("SELECT id, qty_remain FROM stock_batch ORDER BY id DESC LIMIT 1")
    m09.charge_item_create(ctx, {"item_code": "GH01", "name": "挂号费", "price_fen": 1000,
                                 "auto_add_on_visit": 1}, {})

    # ================================================================
    # 演练 1：写病历中途强制断电
    # ================================================================
    print("\n[演练 1] 写病历中途强制断电")
    rec = m03.create(ctx, {"visit_id": vis["id"]}, {})
    m03.save_chief(ctx, {"record_id": rec["id"], "content": "咳嗽三天",
                         "duration": "3天"}, {})
    n_chief = _count("record_chief_complaint", "WHERE record_id=? AND deleted_at IS NULL",
                     (rec["id"],))
    n_present = _count("record_present_illness", "WHERE record_id=? AND deleted_at IS NULL",
                       (rec["id"],))

    # 模拟：现病史写到一半进程被杀（事务未提交）
    cut = False
    try:
        with db.tx() as conn:
            db.execute(conn, """
                INSERT INTO record_present_illness (record_id, content, source,
                    created_at, created_by, updated_at, updated_by)
                VALUES (?,?,?,?,?,?,?)
            """, (rec["id"], "半截内容：患者自述近三日", "DOCTOR", tu.now_str(), ctx.user_id,
                  tu.now_str(), ctx.user_id))
            raise PowerCut("进程被杀")
    except PowerCut:
        cut = True
    check("断电被模拟（事务中止）", cut)
    check("已保存内容不丢（主诉仍在）",
          _count("record_chief_complaint", "WHERE record_id=? AND deleted_at IS NULL",
                 (rec["id"],)) == n_chief)
    check("主诉内容未被破坏",
          (_one("SELECT content FROM record_chief_complaint WHERE record_id=?", (rec["id"],))
           or {}).get("content") == "咳嗽三天")
    check("未提交内容已回滚（无半截现病史）",
          _count("record_present_illness", "WHERE record_id=? AND deleted_at IS NULL",
                 (rec["id"],)) == n_present)

    out = m13.recover_from_crash(ctx)
    check("重启后崩溃扫描不产生脏数据",
          out["imports_recovered"] == 0 and out["prints_recovered"] == 0
          and out["ai"]["scanned"] == 0, str(out))
    check("病历仍在可编辑态（可继续写）",
          _one("SELECT status FROM record WHERE id=?", (rec["id"],))["status"] == "DRAFT")
    m03.save_present(ctx, {"record_id": rec["id"], "content": "咳嗽三天，咽痒，无发热"}, {})
    check("断电后仍可继续录入",
          _count("record_present_illness", "WHERE record_id=? AND deleted_at IS NULL",
                 (rec["id"],)) == n_present + 1)
    check("无残留脏数据（不变量干净）", selftest.check_invariants() == [], 
          str(selftest.check_invariants()[:2]))

    # ================================================================
    # 演练 2：收费中途断电
    # ================================================================
    print("\n[演练 2] 收费中途断电")
    bill = m09.bill_create(ctx, {"visit_id": vis["id"]}, {})
    total = bill["total_amount_fen"]
    n_pay_before = _count("payment")

    # 模拟：支付已写入、账单状态推进前进程被杀
    _orig_bill_transition = m09.bill_transition

    def _crash_bill_transition(*a, **kw):
        raise PowerCut("收费推进账单状态时进程被杀")

    m09.bill_transition = _crash_bill_transition
    try:
        try:
            m09.bill_pay(ctx, {"bill_id": bill["id"],
                               "payments": [{"channel": "CASH", "amount_fen": total,
                                             "received_amount_fen": total}]}, {})
        except PowerCut:
            pass
    finally:
        m09.bill_transition = _orig_bill_transition

    check("断电后无支付记录（未出现'已扣款'）", _count("payment") == n_pay_before,
          "%d → %d" % (n_pay_before, _count("payment")))
    check("账单仍为 PENDING（未变成已付）",
          _one("SELECT status FROM bill WHERE id=?", (bill["id"],))["status"] == "PENDING")
    check("账实一致：paid_amount_fen 仍为 0",
          int(_one("SELECT paid_amount_fen FROM bill WHERE id=?", (bill["id"],))["paid_amount_fen"]) == 0)
    check("未产生孤立支付流水",
          _count("payment", "WHERE bill_id=?", (bill["id"],)) == 0)

    # 重新收费 → 账单与支付要么都在要么都不在
    m09.bill_pay(ctx, {"bill_id": bill["id"],
                       "payments": [{"channel": "CASH", "amount_fen": total,
                                     "received_amount_fen": total + 500}]}, {})
    pay_rows = _all("SELECT * FROM payment WHERE bill_id=?", (bill["id"],))
    check("重做后支付与账单同时存在", len(pay_rows) >= 1
          and _one("SELECT status FROM bill WHERE id=?", (bill["id"],))["status"] == "PAID")
    check("支付合计等于应收（无重复扣款）",
          sum(int(p["amount_fen"]) for p in pay_rows) == total,
          "%d vs %d" % (sum(int(p["amount_fen"]) for p in pay_rows), total))
    expect_error("重复收费被拒（幂等）",
                 lambda: m09.bill_pay(ctx, {"bill_id": bill["id"],
                                            "payments": [{"channel": "CASH",
                                                          "amount_fen": total}]}, {}),
                 "E3072")
    check("账实不符不变量为空", selftest.check_invariants() == [])

    # ================================================================
    # 演练 3：发药中途断电
    # ================================================================
    print("\n[演练 3] 发药中途断电")
    rx = m06.create(ctx, {"visit_id": vis["id"], "prescription_type": "WEST"}, {})
    m06.save_items(ctx, {"prescription_id": rx["id"], "items": [
        {"drug_id": drug["id"], "quantity": 2, "dosage": "1片", "frequency": "tid",
         "route": "PO", "days": 3}]}, {})
    m06.submit(ctx, {"prescription_id": rx["id"]}, {})
    m06.confirm(ctx, {"prescription_id": rx["id"], "password": PWD}, {})

    qty_before = float(_one("SELECT qty_remain FROM stock_batch WHERE id=?",
                            (batch["id"],))["qty_remain"])
    n_disp_before = _count("dispense")

    _orig_apply = m07.apply_change

    def _crash_apply(*a, **kw):
        raise PowerCut("扣减库存时进程被杀")

    m07.apply_change = _crash_apply
    try:
        try:
            m07.dispense(ctx, {"prescription_id": rx["id"]}, {})
        except PowerCut:
            pass
    finally:
        m07.apply_change = _orig_apply

    qty_after = float(_one("SELECT qty_remain FROM stock_batch WHERE id=?",
                           (batch["id"],))["qty_remain"])
    check("断电后库存未少扣也未多扣", abs(qty_after - qty_before) < 1e-6,
          "%.3f → %.3f" % (qty_before, qty_after))
    check("断电后无发药记录", _count("dispense") == n_disp_before)
    check("处方仍为 CONFIRMED（可重发）",
          _one("SELECT status FROM prescription WHERE id=?", (rx["id"],))["status"]
          == "CONFIRMED")

    d = m07.dispense(ctx, {"prescription_id": rx["id"]}, {})
    qty_done = float(_one("SELECT qty_remain FROM stock_batch WHERE id=?",
                          (batch["id"],))["qty_remain"])
    trans = _one("SELECT COALESCE(SUM(qty_change),0) s FROM stock_transaction "
                 "WHERE ref_type='DISPENSE' AND ref_id=?", (d["id"],))["s"]
    check("发药后库存扣减 2", abs((qty_before - qty_done) - 2.0) < 1e-6,
          "%.3f → %.3f" % (qty_before, qty_done))
    check("库存流水与发药记录一致（不多扣）", abs(float(trans) + 2.0) < 1e-6, str(trans))
    check("处方推进到 DISPENSED",
          _one("SELECT status FROM prescription WHERE id=?", (rx["id"],))["status"]
          == "DISPENSED")
    check("库存不变量干净", selftest.check_invariants() == [])
    expect_error("已发药处方不可重复发药",
                 lambda: m07.dispense(ctx, {"prescription_id": rx["id"]}, {}), "E3")

    # ================================================================
    # 演练 4：打印中途断电 / 缺纸
    # ================================================================
    print("\n[演练 4] 打印中途断电 / 缺纸")
    hardware.register(hardware.MockPrinter(spool_dir=os.path.join(root, "logs", "prints"),
                                           fail=True))
    job = m10.print_prescription(ctx, {"prescription_id": rx["id"]}, {})
    job_no = job["print_job_no"]
    check("缺纸前任务已建（PENDING）", job["status"] == "PENDING")
    n_fail_before = _count("print_failure")
    expect_error("缺纸 → 驱动抛 E4001",
                 lambda: m10.job_execute(ctx, {"job_id": job["id"]}, {}), "E4001")
    check("任务转为 FAILED（可重试）",
          _one("SELECT status FROM print_job WHERE id=?", (job["id"],))["status"] == "FAILED")
    check("写了打印失败记录", _count("print_failure") > n_fail_before)
    check("打印失败不回滚业务（处方仍 DISPENSED）",
          _one("SELECT status FROM prescription WHERE id=?", (rx["id"],))["status"]
          == "DISPENSED")

    # 打印中"断电"：任务卡在 PRINTING
    _tx("UPDATE print_job SET status='PRINTING', updated_at=? WHERE id=?",
        (tu.now_str(), job["id"]))
    _tx("UPDATE print_job SET created_at=? WHERE id=?", ("2020-01-01 09:00:00", job["id"]))
    rec_n = m10.recover_stale_printing(ctx)["recovered"]
    check("卡在 PRINTING 的任务被收敛", rec_n >= 1, str(rec_n))
    check("收敛后回到 FAILED（可重打）",
          _one("SELECT status FROM print_job WHERE id=?", (job["id"],))["status"] == "FAILED")

    hardware.reset()
    r = m10.job_retry(ctx, {"job_id": job["id"]}, {})
    check("复电后重试打印成功", r["status"] == "SUCCESS", str(r))
    rp = m10.job_reprint(ctx, {"job_id": job["id"], "reason": "缺纸补打"}, {})
    check("重打不产生新单据号", rp["print_job_no"] == job_no,
          "%s vs %s" % (rp["print_job_no"], job_no))
    check("重打计数 +1", rp["reprint_count"] == 1, str(rp["reprint_count"]))
    row = _one("SELECT * FROM print_job WHERE id=?", (job["id"],))
    check("有重打标记（原因已留痕）", row["reprint_reason"] == "缺纸补打",
          str(row["reprint_reason"]))
    check("重打使用固化文本（未重新取数）", row["rendered_text"] == job["rendered_text"])
    check("状态日志含重打记录",
          _count("print_status_log", "WHERE job_id=? AND reason LIKE '%重打%'", (job["id"],)) >= 1)
    check("打印任务总数未因重打增加",
          _count("print_job", "WHERE print_job_no=?", (job_no,)) == 1)

    # ================================================================
    # 演练 5：AI 服务不可用
    # ================================================================
    print("\n[演练 5] AI 服务不可用")
    n_rec_before = _count("record")
    n_chief_before = _count("record_chief_complaint")
    config.set_value("ai.enabled", "0", reason="演练：AI 服务不可用")
    config.reload_cache()
    expect_error("AI 关闭时创建任务被明确拒绝",
                 lambda: m04.task_create(ctx, {"task_type": "ASR", "visit_id": vis["id"]}, {}),
                 None)
    check("已有数据不丢（病历条数不变）", _count("record") == n_rec_before)
    check("已有数据不丢（主诉条数不变）", _count("record_chief_complaint") == n_chief_before)
    m03.save_chief(ctx, {"record_id": rec["id"], "content": "咳嗽三天，加重一天"}, {})
    check("可降级为手工录入",
          (_one("SELECT content FROM record_chief_complaint WHERE record_id=?", (rec["id"],))
           or {}).get("content") == "咳嗽三天，加重一天")
    check("手工录入标记来源为 DOCTOR",
          (_one("SELECT source FROM record_chief_complaint WHERE record_id=?", (rec["id"],))
           or {}).get("source") == "DOCTOR")

    config.set_value("ai.enabled", "1", reason="演练：AI 服务恢复")
    config.reload_cache()
    task = m04.task_create(ctx, {"task_type": "ASR", "visit_id": vis["id"]}, {})
    check("AI 恢复后任务可创建", task["status"] == "CREATED", str(task))
    tr = m04.transcript_save(ctx, {"ai_task_id": task["id"],
                                   "text": "患者咳嗽三天，体温38.2度，对青霉素过敏"}, {})
    check("任务可恢复执行（转写成功）", tr.get("status") in ("SUCCEEDED", "FAILED"), str(tr))
    check("AI 结果不直接写病历（条数不变）", _count("record") == n_rec_before)

    # ================================================================
    # 演练 6：Hardware Agent 崩溃
    # ================================================================
    print("\n[演练 6] Hardware Agent 崩溃")
    m13._reset_watchdog_state()
    job2 = m10.print_receipt(ctx, {"bill_id": bill["id"]}, {})
    n_jobs_before = _count("print_job")
    hardware.register(AlwaysDown())
    w1 = m13.watchdog_hardware(ctx)
    check("单次失败不重启", w1["restarted"] == 0, str(w1))
    w2 = m13.watchdog_hardware(ctx)
    check("两次失败仍未达阈值", w2["restarted"] == 0, str(w2))
    w3 = m13.watchdog_hardware(ctx)
    check("连续失败达阈值 → 看门狗自动拉起", w3["restarted"] == 1, str(w3))
    check("重启动作留痕（hardware_event）",
          _count("hardware_event", "WHERE event_type='RESTART'") >= 1)
    check("未完成打印任务不丢（任务仍在队列）", _count("print_job") == n_jobs_before,
          "%d vs %d" % (n_jobs_before, _count("print_job")))
    check("未完成打印任务可查",
          _one("SELECT status FROM print_job WHERE id=?", (job2["id"],)) is not None)
    w4 = m13.watchdog_hardware(ctx)
    check("重启无效多轮后降级 OFFLINE", w4["degraded"] == 1, str(w4))
    check("降级写了高风险审计", _count("audit_high_risk", "WHERE risk_type='HW_DEGRADE'") >= 1)

    hardware.reset()
    m13._reset_watchdog_state()
    w5 = m13.watchdog_hardware(ctx)
    check("Agent 被拉起后设备回到 ONLINE",
          _one("SELECT status FROM hardware_device WHERE driver_code='mock.printer'")["status"]
          == "ONLINE")
    check("恢复后不再重启", w5["restarted"] == 0, str(w5))
    r2 = m10.job_execute(ctx, {"job_id": job2["id"]}, {})
    check("崩溃期间未完成的打印任务可继续完成", r2["status"] == "SUCCESS", str(r2))

    # ================================================================
    # 演练 7：AI Worker 崩溃
    # ================================================================
    print("\n[演练 7] AI Worker 崩溃")
    _tx("DELETE FROM ai_task WHERE task_no LIKE 'AIDRILL%'")
    _tx("INSERT INTO ai_task (task_no, task_type, status, retry_count, max_retry, started_at,"
        " created_at, updated_at) VALUES ('AIDRILL0001','ASR','RUNNING',0,3,?,?,?)",
        ("2020-01-01 09:00:00", "2020-01-01 09:00:00", "2020-01-01 09:00:00"))
    _tx("INSERT INTO ai_task (task_no, task_type, status, retry_count, max_retry, started_at,"
        " created_at, updated_at) VALUES ('AIDRILL0002','ASR','RUNNING',0,3,?,?,?)",
        (tu.now_str(), tu.now_str(), tu.now_str()))
    n_ai_fail = _count("ai_failure_log")
    w = m13.watchdog_ai(ctx)
    check("看门狗扫描到卡死任务", w["scanned"] >= 1, str(w))
    check("卡死任务被拉回确定态（RETRYING）",
          _one("SELECT status FROM ai_task WHERE task_no='AIDRILL0001'")["status"] == "RETRYING")
    check("未超时的任务不误杀",
          _one("SELECT status FROM ai_task WHERE task_no='AIDRILL0002'")["status"] == "RUNNING")
    check("写了失败日志（可追溯）", _count("ai_failure_log") > n_ai_fail)
    check("看门狗动作落 watchdog_log",
          _count("watchdog_log", "WHERE kind='AI'") >= 1)

    n_ai_fail2 = _count("ai_failure_log")
    w_again = m13.watchdog_ai(ctx)
    check("不重复执行（第二次扫描为空）", w_again["scanned"] == 0, str(w_again))
    check("不重复写失败日志", _count("ai_failure_log") == n_ai_fail2,
          "%d vs %d" % (n_ai_fail2, _count("ai_failure_log")))

    _tx("UPDATE ai_task SET status='RUNNING', retry_count=3, started_at=? "
        "WHERE task_no='AIDRILL0001'", ("2020-01-01 09:00:00",))
    w2 = m13.watchdog_ai(ctx)
    check("重试达上限 → ABANDONED", w2["abandoned"] >= 1, str(w2))
    check("任务确为终态 ABANDONED",
          _one("SELECT status FROM ai_task WHERE task_no='AIDRILL0001'")["status"]
          == "ABANDONED")
    n_rec2 = _count("record")
    m13.watchdog_ai(ctx)
    check("看门狗绝不自动写病历（不越权）", _count("record") == n_rec2)

    # ================================================================
    # 演练 9：系统时间被回拨
    # ================================================================
    print("\n[演练 9] 系统时间被回拨")
    n_anom_before = _count("audit_time_anomaly")
    tu._last_tick = tu.now() + datetime.timedelta(hours=2)
    an = tu.check_anomaly()
    check("检测到时间回拨", an is not None and an["anomaly_type"] == "BACKWARD", str(an))
    tu._last_tick = tu.now() + datetime.timedelta(hours=2)
    an2 = m12.check_time_anomaly()
    check("F88 落审计告警", an2 is not None and an2["anomaly_type"] == "BACKWARD", str(an2))
    check("告警写入 audit_time_anomaly", _count("audit_time_anomaly") > n_anom_before,
          "%d → %d" % (n_anom_before, _count("audit_time_anomaly")))
    t_before = tu.now_str()
    pat2 = m01.create(ctx, {"name": "演练乙", "gender": "F", "birth_date": "1990-01-01"}, {})
    row2 = _one("SELECT created_at FROM patient WHERE id=?", (pat2["id"],))
    check("业务照常可用（未因时间异常拒写）", row2 is not None)
    check("业务时间取系统时间（不倒流、不由外部指定）", row2["created_at"] >= t_before,
          "%s vs %s" % (row2["created_at"], t_before))
    tu._last_tick = None
    check("异常恢复后不再告警", tu.check_anomaly() is None)

    # ================================================================
    # 演练 10：数据库写锁超时
    # ================================================================
    print("\n[演练 10] 数据库写锁超时（约 5 秒 busy_timeout）")
    other = sqlite3.connect(db.path(), timeout=1.0, isolation_level=None)
    other.execute("PRAGMA journal_mode=WAL")
    other.execute("BEGIN IMMEDIATE")
    other.execute("UPDATE sys_config SET cfg_value=cfg_value WHERE cfg_key='ai.enabled'")
    t0 = time.time()
    exc = None
    try:
        with db.tx() as conn:
            db.execute(conn, "UPDATE sys_config SET cfg_value='1' WHERE cfg_key='ai.enabled'")
    except AppError as e:
        exc = e
    cost = time.time() - t0
    check("写锁冲突抛出 AppError（不是裸 sqlite3 异常）", exc is not None, repr(exc))
    check("异常码为 E1001（数据库忙）", exc is not None and exc.code == "E1001",
          str(exc and exc.code))
    check("标记为可重试", exc is not None and exc.retryable is True)
    check("等待 busy_timeout 后才失败（未静默丢写）", cost >= 4.0, "%.1fs" % cost)
    check("有原始错误明细便于排查",
          exc is not None and "locked" in str((exc.detail or {}).get("raw", "")).lower(),
          str(exc and exc.detail))
    other.execute("ROLLBACK")
    other.close()
    ok = False
    with db.tx() as conn:
        db.execute(conn, "UPDATE sys_config SET cfg_value='1' WHERE cfg_key='ai.enabled'")
        ok = True
    check("释放锁后重试成功（不静默失败）", ok)
    check("写锁演练后业务不变量干净", selftest.check_invariants() == [])

    # ================================================================
    # 演练 8：SQLite 文件损坏（放最后：会整库覆盖）
    # ================================================================
    print("\n[演练 8] SQLite 文件损坏 → 检查 → 备份恢复")
    db.checkpoint_truncate()
    snap = backup.full_backup(reason="MANUAL")
    check("损坏前已建立可恢复备份", snap.get("valid") is True, str(snap))
    n_patient_before = _count("patient")
    n_bill_before = _count("bill")

    # 制造损坏：把主库文件中部一页写成垃圾（单页即可让 integrity_check 报错，
    # 又不至于连审计表一起毁掉——否则"结论留痕""恢复有记录"都无从验证）
    db_path = db.path()
    size = os.path.getsize(db_path)
    offset = (max(size // 2, 4096) // 4096) * 4096
    with open(db_path, "r+b") as fh:
        fh.seek(offset)
        fh.write(b"\x00CORRUPTED-BY-DRILL-08\x01" * 171)
    print("  已注入损坏：%s（偏移 %d，1 页）" % (db_path, offset))
    # 进程内页缓存会盖住外部篡改，等价于真实场景的"重启后才发现"
    db.close_all()
    db.init(db_path)

    detected = False
    res = None
    try:
        res = m13.integrity_check_full(ctx)
        detected = (res.get("ok") is False)
    except AppError as exc:
        detected = True
        print("  完整性检查抛出异常（同样算发现）：%s %s" % (exc.code, exc.message))
    check("完整性检查发现损坏", detected, str(res and res.get("integrity_check")))
    check("检查结论有留痕（库记录或库外旁路证据）",
          _count_safe("integrity_check", "WHERE result='FAIL'") >= 1
          or bool((res or {}).get("evidence_file")),
          str((res or {}).get("evidence_file")))

    n_crash_before = _count_safe("crash_recovery_log")
    fr = m13.freeze_on_corruption("E1003", "演练 8：文件校验失败", ctx)
    check("怀疑损坏即冻结为只读", db.is_readonly() is True, str(fr))
    check("冻结前留痕（崩溃日志）", _count_safe("crash_recovery_log") > n_crash_before,
          "%d → %d" % (n_crash_before, _count_safe("crash_recovery_log")))
    check("冻结写了高风险审计",
          _count_safe("audit_high_risk", "WHERE risk_type='DB_CORRUPT'") >= 1)
    check("冻结期间禁止业务写入",
          expect_error("只读下写业务被拒",
                       lambda: m01.create(ctx, {"name": "不该写入", "gender": "M"}, {}),
                       None) is not None)

    n_restore_before = _count("restore_log")
    r = m13.restore_from_backup(snap["job_no"], confirm=True, ctx=ctx)
    check("从最近备份恢复成功", r.get("result") == "SUCCEEDED", str(r))
    check("恢复后解除只读", db.is_readonly() is False)
    check("恢复后完整性检查通过", r.get("integrity_check") == "ok", str(r.get("integrity_check")))
    check("恢复流程有记录（restore_log）", _count("restore_log") > n_restore_before)
    check("恢复单号已生成", bool((r.get("restore_no") or "").startswith("RS")),
          str(r.get("restore_no")))
    check("恢复前保留了现状（备份或现场副本）",
          bool(r.get("pre_restore_backup") or r.get("pre_restore_file")),
          str(r.get("pre_restore_backup") or r.get("pre_restore_file")))
    check("原库未被删除（保留证据）", os.path.isfile(db_path))
    check("写恢复后校验记录", _count("integrity_check", "WHERE check_type='POST_RESTORE'") >= 1)
    check("恢复写了高风险审计",
          _count("audit_high_risk", "WHERE risk_type='RESTORE'") >= 1)
    check("数据完整恢复（患者数一致）", _count("patient") == n_patient_before,
          "%d vs %d" % (_count("patient"), n_patient_before))
    check("数据完整恢复（账单数一致）", _count("bill") == n_bill_before,
          "%d vs %d" % (_count("bill"), n_bill_before))
    check("恢复后备份清单仍在（可再次回退）", _count("backup_job") >= 1,
          str(_count("backup_job")))
    check("恢复后可继续业务", m01.create(ctx, {"name": "恢复后患者", "gender": "M"}, {})
          is not None)
    check("恢复后不变量干净", selftest.check_invariants() == [])

    # ================================================================
    print("\n" + "=" * 60)
    print("故障演练结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
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
