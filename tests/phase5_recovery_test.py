# -*- coding: utf-8 -*-
"""第五阶段数据安全与恢复冒烟（M13 F90-F98）。

覆盖：自动备份每日一次、快照式增量（挂链/无变化跳过/无全量拒绝）、
      恢复（校验拦截/恢复前备份/恢复后校验/高风险审计）、完整性检查三层、
      SQLite 异常冻结与抢救、崩溃收敛（导入/打印/AI，资金库存不自动补偿）、
      AI 看门狗、硬件看门狗（重启→复测→降级）。
用法：python tests/phase5_recovery_test.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime, backup, hardware, config, selftest  # noqa: E402
from core import migrate, timeutil as tu  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from core import session as sess_mod  # noqa: E402
from modules import (m01_patient as m01, m02_visit as m02, m14_system as m14,  # noqa: E402
                     m04_ai as m04, m10_print as m10, m13_recovery as m13)

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_phase5_last.log")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_orig_print = print


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


def _drop_all_backups():
    """清空备份记录（测试用：临时摘掉禁止删除触发器）。"""
    with db.tx() as conn:
        db.execute(conn, "DROP TRIGGER IF EXISTS trg_backup_job_no_delete")
        db.execute(conn, "DELETE FROM backup_file")
        db.execute(conn, "DELETE FROM backup_job")
        db.execute(conn, """
            CREATE TRIGGER trg_backup_job_no_delete
            BEFORE DELETE ON backup_job
            BEGIN SELECT RAISE(ABORT, 'E2006: backup_job 禁止删除'); END;
        """)


def _count(table, where="", params=()):
    sql = "SELECT COUNT(*) c FROM %s %s" % (table, where)
    return _one(sql, params)["c"]


class AlwaysDown(object):
    """测试用永不上线驱动：重启也无法恢复（验证 F98 降级路径）。"""

    code = "mock.printer"

    def status(self):
        return {"online": False, "detail": "always down (test)"}

    def print_text(self, text, meta=None):
        raise AppError("E4001", "打印机永久离线（测试）")


def main():
    root = tempfile.mkdtemp(prefix="clinic_phase5_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))
    check("迁移已应用到 0018",
          "0018" in migrate.applied(db.write_conn()),
          "applied=%s" % sorted(migrate.applied(db.write_conn()))[-3:])

    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    m14.init_system(sys_ctx, {"admin_username": "admin", "admin_password": "Clinic@2026",
                              "admin_name": "管理员", "clinic_name": "示例中医诊所"}, {})
    m14.user_roles(sys_ctx, {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                     "CASHIER"]}, {})
    s = m14.login(sys_ctx, {"username": "admin", "password": "Clinic@2026",
                            "role_code": "ADMIN"}, {})
    ctx = Context(user_id=s["user_id"], role_code="ADMIN", source="UI",
                  username="admin", session_id=s["session_id"])
    set_ctx(ctx)

    # ============================================================
    print("\n[权限] M13 权限点分配")
    check("管理员有 system.restore", sess_mod.has_permission("ADMIN", "system.restore"))
    check("管理员有 system.watchdog", sess_mod.has_permission("ADMIN", "system.watchdog"))
    check("医生无 system.restore", not sess_mod.has_permission("DOCTOR", "system.restore"))
    check("医生无 system.watchdog", not sess_mod.has_permission("DOCTOR", "system.watchdog"))

    # ============================================================
    print("\n[F90] 自动备份")
    sched = _all("SELECT * FROM backup_job WHERE reason='SCHEDULED' AND job_type='FULL'")
    check("启动已自动全量备份", len(sched) >= 1, "%d 条" % len(sched))
    r2 = m13.auto_backup_if_needed()
    check("当天已有全量则跳过", r2.get("skipped") is True, str(r2))
    bf = _one("SELECT * FROM backup_file WHERE status='VALID' ORDER BY id DESC LIMIT 1")
    check("备份文件登记了大小", bf is not None and (bf["file_size"] or 0) > 0,
          str(bf and bf["file_size"]))
    check("备份文件登记了 SHA256", bf is not None and len(bf["checksum"] or "") == 64)
    check("备份时记录了写事务序号",
          _one("SELECT write_seq FROM backup_job WHERE write_seq IS NOT NULL LIMIT 1")
          is not None)
    check("备份后做了校验", _count("integrity_check", "WHERE check_type='BACKUP_VERIFY'") >= 1)

    n_risk_before = _count("audit_high_risk", "WHERE risk_type='BACKUP_FAILED'")
    _orig_dir = backup._dir
    backup._dir = lambda: (_ for _ in ()).throw(OSError("mock: 备份目录不可写"))
    expect_error("备份目录不可写 → E1004",
                 lambda: backup.full_backup(reason="MANUAL"), "E1004")
    backup._dir = _orig_dir
    check("备份失败写了高风险审计",
          _count("audit_high_risk", "WHERE risk_type='BACKUP_FAILED'") > n_risk_before)

    # ============================================================
    print("\n[F91] 增量备份（快照式）")
    _drop_all_backups()
    expect_error("无全量时增量被拒 → E1007",
                 lambda: m13.incremental_backup(force=True), "E1007")

    f1 = backup.full_backup(reason="MANUAL")
    check("先建全量 F1", f1.get("valid") is True, str(f1))
    pat = m01.create(ctx, {"name": "李四", "gender": "F", "birth_date": "1990-05-05"}, {})
    vis = m02.create(ctx, {"patient_id": pat["id"], "visit_type": "TCM"}, {})
    inc = m13.incremental_backup(reason="SCHEDULED", force=True)
    check("有写入时生成增量", inc.get("skipped") is not True, str(inc))
    check("增量挂在 F1 之下", inc.get("parent_job_no") == f1["job_no"],
          "%s vs %s" % (inc.get("parent_job_no"), f1["job_no"]))
    check("增量登记为 INCR",
          _one("SELECT job_type FROM backup_job WHERE job_no=?", (inc["job_no"],))["job_type"]
          == "INCR")
    check("增量文件名带 incr", "_incr" in (inc.get("file") or ""), str(inc.get("file")))

    skip = m13.incremental_backup(reason="SCHEDULED", force=True)
    check("无变化则跳过", skip.get("skipped") is True, str(skip))

    f2 = backup.full_backup(reason="MANUAL")
    m01.create(ctx, {"name": "王五", "gender": "M", "birth_date": "1975-02-02"}, {})
    inc2 = m13.incremental_backup(reason="SCHEDULED", force=True)
    check("新全量后增量挂到 F2", inc2.get("parent_job_no") == f2["job_no"],
          "%s vs %s" % (inc2.get("parent_job_no"), f2["job_no"]))

    noforce = m13.incremental_backup(reason="SCHEDULED", force=False)
    check("周期未到非强制则跳过", noforce.get("skipped") is True, str(noforce))

    m01.create(ctx, {"name": "赵六", "gender": "M", "birth_date": "1982-03-03"}, {})
    _orig_dir = backup._dir
    backup._dir = lambda: (_ for _ in ()).throw(OSError("mock: 备份目录不可写"))
    expect_error("增量失败抛 E1004",
                 lambda: m13.incremental_backup(reason="SCHEDULED", force=True), "E1004")
    backup._dir = _orig_dir
    check("增量失败不进只读", db.is_readonly() is False)

    print("\n[F91 AC6] 清理不产生孤儿增量")
    for i in range(6):
        fn = "clinic_2020010%d_010101_full.dbbak" % i
        p = os.path.join(root, "backups", fn)
        with open(p, "wb") as fh:
            fh.write(b"x" * 128)
        os.utime(p, (1000000000, 1000000000))     # 很久以前
        _tx("INSERT INTO backup_job (job_no, job_type, status, started_at, reason, file_path,"
            " file_size, checksum, write_seq, created_by) VALUES (?,'FULL','SUCCEEDED',"
            "?,?,?,128,'deadbeef',0,?)",
            ("BK2020010%d0001" % i, "2020-01-0%d 01:01:01" % i, "MANUAL", p, ctx.user_id))
    orphan_parent = "BK202001010001"
    pf = os.path.join(root, "backups", "clinic_20200105_020202_incr.dbbak")
    with open(pf, "wb") as fh:
        fh.write(b"y" * 128)
    os.utime(pf, (1000000000, 1000000000))
    _tx("INSERT INTO backup_job (job_no, job_type, status, started_at, reason, parent_job_no,"
        " file_path, file_size, checksum, write_seq, created_by) VALUES (?,'INCR','SUCCEEDED',"
        "?,?,?,?,128,'deadbeef',0,?)",
        ("BK202001050002", "2020-01-05 02:02:02", "MANUAL", orphan_parent, pf, ctx.user_id))
    removed = backup.cleanup()
    check("过期备份被清理", removed >= 1, "removed=%d" % removed)
    check("孤儿增量被级联清理", not os.path.exists(pf), pf)

    # ============================================================
    print("\n[F94] 完整性检查三层")
    res = m13.integrity_check_full(ctx)
    check("返回页结构结果", "integrity_check" in res, str(res.get("integrity_check")))
    check("返回外键结果", "foreign_key_violations" in res)
    check("返回不变量结果", "invariants" in res)
    check("正常库 ok=True", res["ok"] is True, str(res))
    n_ic_before = _count("integrity_check")
    m13.integrity_check_full(ctx)
    check("检查结果落表", _count("integrity_check") > n_ic_before)

    conn = db.write_conn()
    conn.execute("PRAGMA foreign_keys=OFF")
    _tx("INSERT INTO backup_file (job_id, file_name, file_path, file_size, checksum, status,"
        " created_at) VALUES (999999,'orphan','/tmp/orphan',1,'x','VALID',?)",
        (tu.now_str(),))
    conn.execute("PRAGMA foreign_keys=ON")
    res2 = m13.integrity_check_full(ctx)
    check("发现外键违规", len(res2["foreign_key_violations"]) >= 1,
          "%d 条" % len(res2["foreign_key_violations"]))
    _tx("DELETE FROM backup_file WHERE job_id=999999")
    check("清理后恢复干净", m13.integrity_check_full(ctx)["ok"] is True)

    # 不变量：人为制造账实不符（用一条已作废的账单，测完立刻恢复）
    _tx("""INSERT INTO bill (bill_no, visit_id, patient_id, patient_name_snap, status,
           total_amount_fen, paid_amount_fen, refunded_amount_fen, line_count, biz_date,
           version, created_at)
           VALUES ('B209901010001',?,?,?,'PENDING',10000,0,0,1,'2099-01-01',1,?)""",
        (vis["id"], pat["id"], pat["name"], "2099-01-01 10:00:00"))
    bid = _one("SELECT id FROM bill WHERE bill_no='B209901010001'")["id"]
    _tx("INSERT INTO bill_item (bill_id, source_type, source_id, name_snap, quantity,"
        " unit_price_fen, amount_fen, created_at) VALUES (?,'CHARGE',NULL,'测试项目',1,5000,5000,?)",
        (bid, "2099-01-01 10:00:00"))
    v = selftest.check_invariants()
    check("发现账实不符", any("账单金额与明细不符" in x for x in v), str(v[:2]))
    check("不变量违规不进只读", db.is_readonly() is False)
    _tx("UPDATE bill SET total_amount_fen=5000 WHERE id=?", (bid,))
    check("修正后不变量恢复",
          not any("账单金额与明细不符" in x for x in selftest.check_invariants()))
    _tx("UPDATE bill SET deleted_at=? WHERE id=?", ("2099-01-01 11:00:00", bid))

    n_before = _count("patient")
    m13.integrity_check_full(ctx)
    check("检查本身不改数据", _count("patient") == n_before)

    # ============================================================
    print("\n[F95] SQLite 异常：冻结与抢救")
    exc = db._translate(sqlite3.OperationalError("database is locked"))
    check("BUSY 翻译为 E1001 且可重试", exc.code == "E1001" and exc.retryable is True)
    exc2 = db._translate(sqlite3.OperationalError("database disk image is malformed"))
    check("CORRUPT 翻译为 E1003", exc2.code == "E1003")

    size_before = os.path.getsize(db.path())
    r = m13.freeze_on_corruption("E1003", "mock: 页结构损坏")
    check("损坏即进只读", db.is_readonly() is True, str(r))
    check("原库文件未被删除", os.path.exists(db.path()))
    check("原库大小未变", os.path.getsize(db.path()) == size_before)
    check("写了高风险审计 DB_CORRUPT",
          _count("audit_high_risk", "WHERE risk_type='DB_CORRUPT'") >= 1)
    check("崩溃日志标记需人工复核",
          _count("crash_recovery_log", "WHERE result='NEED_MANUAL_REVIEW'") >= 1)
    expect_error("只读期间业务写入被拒", lambda: db.tx().__enter__(), "E1003")

    sv = m13.salvage(confirm=True, ctx=ctx)
    check("抢救生成独立库", os.path.isfile(sv["target"]), sv["target"])
    check("抢救不覆盖原库", os.path.getsize(db.path()) == size_before)
    check("抢救库完整性检查 ok", sv.get("integrity_check") == "ok", str(sv.get("integrity_check")))
    check("抢救导出了表", len(sv["tables"]) >= 5, "%d 张" % len(sv["tables"]))
    m13.release_readonly(reason="测试复位", ctx=ctx)
    check("只读由人工解除", db.is_readonly() is False)

    expect_error("INSERT-ONLY 表 E2006 透传",
                 lambda: _tx("UPDATE crash_recovery_log SET issue='x' WHERE id=1"), "E2006")

    # ============================================================
    print("\n[F96] 崩溃恢复：任务收敛")
    # WAL 残留判定（用隔离路径，避免动真库）
    _orig_path = db._db_path
    tmpd = tempfile.mkdtemp(prefix="clinic_wal_")
    with open(os.path.join(tmpd, "x.db-wal"), "wb") as fh:
        fh.write(b"0" * 64)
    db._db_path = os.path.join(tmpd, "x.db")
    check("识别出 WAL 残留", m13.wal_residue() is True)
    os.remove(os.path.join(tmpd, "x.db-wal"))
    check("无 WAL 时判定干净", m13.wal_residue() is False)
    db._db_path = _orig_path

    _tx("INSERT INTO sys_import_job (job_no, data_type, source_file, status, started_at,"
        " created_at) VALUES ('IM209901010001','DRUG','/tmp/x.csv','RUNNING',?,?)",
        ("2099-01-01 09:00:00", "2099-01-01 09:00:00"))
    # 注意：超时判定是"早于 now - timeout"，所以卡住的时间必须填**过去**
    _tx("""INSERT INTO print_job (print_job_no, template_code, biz_type, driver_code, status,
           rendered_text, attempt_count, created_at)
           VALUES ('PJ209901010101001','RX_WEST','PRESCRIPTION','mock.printer','PRINTING',
                   'x',1,?)""", ("2020-01-01 09:00:00",))
    _tx("INSERT INTO ai_task (task_no, task_type, status, retry_count, max_retry, started_at,"
        " created_at) VALUES ('AI20990101010101000001','ASR','RUNNING',0,3,?,?)",
        ("2020-01-01 09:00:00", "2020-01-01 09:00:00"))
    _tx("""INSERT INTO bill (bill_no, visit_id, patient_id, patient_name_snap, status,
           total_amount_fen, paid_amount_fen, refunded_amount_fen, line_count, biz_date,
           version, created_at)
           VALUES ('B209901020002',?,?,?,'PENDING',0,0,0,0,'2020-01-01',1,?)""",
        (vis["id"], pat["id"], pat["name"], "2020-01-01 10:00:00"))
    pending_bill = _one("SELECT id FROM bill WHERE bill_no='B209901020002'")["id"]

    out = m13.recover_from_crash(ctx)
    check("残留导入任务被收敛", out["imports_recovered"] >= 1, str(out["imports_recovered"]))
    check("残留打印任务被收敛", out["prints_recovered"] >= 1, str(out["prints_recovered"]))
    check("残留 AI 任务被收敛", out["ai"]["retried"] + out["ai"]["abandoned"] >= 1,
          str(out["ai"]))
    check("资金项列入人工复核",
          any(x["kind"] == "BILL_PENDING" for x in out["manual_review"]),
          str(out["manual_review"]))
    check("人工复核项未被动状态",
          _one("SELECT status FROM bill WHERE id=?", (pending_bill,))["status"] == "PENDING")
    check("崩溃恢复留痕", _count("crash_recovery_log") >= 1)
    clean = m13.recover_from_crash(ctx)
    check("再次收敛是幂等的", clean["imports_recovered"] == 0
          and clean["prints_recovered"] == 0 and clean["ai"]["scanned"] == 0, str(clean))

    # ============================================================
    print("\n[F97] AI 看门狗")
    _tx("DELETE FROM ai_task WHERE task_no LIKE 'AI2099%'")
    _tx("INSERT INTO ai_task (task_no, task_type, status, retry_count, max_retry, started_at,"
        " created_at) VALUES ('AI20990101010101000002','ASR','RUNNING',0,3,?,?)",
        ("2020-01-01 09:00:00", "2020-01-01 09:00:00"))
    _tx("INSERT INTO ai_task (task_no, task_type, status, retry_count, max_retry, started_at,"
        " created_at) VALUES ('AI20990101010101000003','ASR','RUNNING',0,3,?,?)",
        ("2020-01-01 09:00:00", "2020-01-01 09:00:00"))
    _tx("UPDATE ai_task SET started_at=? WHERE task_no='AI20990101010101000003'",
        (db.query_one("SELECT datetime('now','localtime') t")["t"],))
    n_fail_before = _count("ai_failure_log")
    w = m13.watchdog_ai(ctx)
    check("看门狗返回可量化结果",
          all(k in w for k in ("scanned", "retried", "abandoned")), str(w))
    check("未超时的任务不误杀",
          _one("SELECT status FROM ai_task WHERE task_no='AI20990101010101000003'")["status"]
          == "RUNNING")
    check("超时任务转为 RETRYING",
          _one("SELECT status FROM ai_task WHERE task_no='AI20990101010101000002'")["status"]
          == "RETRYING")
    check("写了 AI 失败日志", _count("ai_failure_log") > n_fail_before)
    n_fail_2 = _count("ai_failure_log")
    m13.watchdog_ai(ctx)
    check("幂等：不产生重复失败日志", _count("ai_failure_log") == n_fail_2)
    check("看门狗动作落 watchdog_log",
          _count("watchdog_log", "WHERE kind='AI'") >= 1)

    # 达上限 → ABANDONED
    _tx("UPDATE ai_task SET status='RUNNING', retry_count=3 WHERE "
        "task_no='AI20990101010101000002'")
    w2 = m13.watchdog_ai(ctx)
    check("达重试上限转 ABANDONED", w2["abandoned"] >= 1, str(w2))
    check("任务确为 ABANDONED",
          _one("SELECT status FROM ai_task WHERE task_no='AI20990101010101000002'")["status"]
          == "ABANDONED")
    n_rec = _count("record")
    m13.watchdog_ai(ctx)
    check("看门狗绝不自动写病历", _count("record") == n_rec)

    # ============================================================
    print("\n[F98] 硬件看门狗")
    m13._reset_watchdog_state()
    hardware.reset()
    n_hh_before = _count("hardware_health")
    w = m13.watchdog_hardware(ctx)
    check("探测写入硬件健康表", _count("hardware_health") > n_hh_before)
    check("在线驱动不重启", w["restarted"] == 0, str(w))

    hardware.register(AlwaysDown())
    w1 = m13.watchdog_hardware(ctx)
    check("单次失败不重启", w1["restarted"] == 0, str(w1))
    w2 = m13.watchdog_hardware(ctx)
    check("两次失败仍未达阈值", w2["restarted"] == 0, str(w2))
    w3 = m13.watchdog_hardware(ctx)
    check("连续失败达阈值触发重启", w3["restarted"] == 1, str(w3))
    check("重启无效未立即降级", w3["degraded"] == 0, str(w3))
    w4 = m13.watchdog_hardware(ctx)
    check("重启无效轮次达标后降级", w4["degraded"] == 1, str(w4))
    check("设备状态变为 OFFLINE",
          _one("SELECT status FROM hardware_device WHERE driver_code='mock.printer'")["status"]
          == "OFFLINE")
    check("降级写了高风险审计",
          _count("audit_high_risk", "WHERE risk_type='HW_DEGRADE'") >= 1)
    check("写了 RESTART 硬件事件",
          _count("hardware_event", "WHERE event_type='RESTART'") >= 1)

    # 恢复默认驱动 → 看门狗应把设备拉回 ONLINE
    hardware.reset()
    w5 = m13.watchdog_hardware(ctx)
    check("驱动恢复后设备回到 ONLINE",
          _one("SELECT status FROM hardware_device WHERE driver_code='mock.printer'")["status"]
          == "ONLINE")
    check("恢复后不再重启", w5["restarted"] == 0, str(w5))
    check("硬件看门狗落 watchdog_log",
          _count("watchdog_log", "WHERE kind='HARDWARE'") >= 1)
    check("看门狗不进只读", db.is_readonly() is False)

    # 打印任务收敛（F98 R5）
    _tx("""INSERT INTO print_job (print_job_no, template_code, biz_type, driver_code, status,
           rendered_text, attempt_count, created_at)
           VALUES ('PJ209901010101002','RX_WEST','PRESCRIPTION','mock.printer','PRINTING',
                   'x',1,?)""", ("2020-01-01 09:00:00",))
    rec = m10.recover_stale_printing(ctx)
    check("卡住的打印任务被收敛", rec["recovered"] >= 1, str(rec))
    check("打印任务转为 FAILED",
          _one("SELECT status FROM print_job WHERE print_job_no='PJ209901010101002'")["status"]
          == "FAILED")

    # ============================================================
    print("\n[F93] 数据恢复")
    # 建立一个"恢复点"：此时库里有 N 个患者
    m01.create(ctx, {"name": "恢复点前患者", "gender": "M", "birth_date": "1988-08-08"}, {})
    rp = backup.full_backup(reason="MANUAL")
    n_at_backup = _count("patient")
    m01.create(ctx, {"name": "恢复点后患者", "gender": "M", "birth_date": "1988-08-08"}, {})
    check("恢复点后数据有变化", _count("patient") == n_at_backup + 1)

    expect_error("未确认被拒 → E2001",
                 lambda: m13.restore_from_backup(rp["job_no"], confirm=False, ctx=ctx), "E2001")
    expect_error("备份不存在 → E2005",
                 lambda: m13.restore_from_backup("NOPE", confirm=True, ctx=ctx), "E2005")

    # 改坏备份文件 → 拒绝
    bad = backup.full_backup(reason="MANUAL")
    with open(os.path.join(root, "backups", bad["file"]), "ab") as fh:
        fh.write(b"tampered")
    n_patient_before_bad = _count("patient")
    expect_error("被改坏的备份拒绝恢复 → E1003",
                 lambda: m13.restore_from_backup(bad["job_no"], confirm=True, ctx=ctx), "E1003")
    check("拒绝后数据未受影响", _count("patient") == n_patient_before_bad)
    check("失败的恢复也留痕",
          _count("restore_log", "WHERE result='FAILED'") >= 1)

    n_pre = _count("backup_job", "WHERE reason='PRE_RESTORE'")
    r = m13.restore_from_backup(rp["job_no"], confirm=True, ctx=ctx)
    check("恢复成功", r["result"] == "SUCCEEDED", str(r))
    check("生成恢复单号", (r.get("restore_no") or "").startswith("RS"), str(r.get("restore_no")))
    check("恢复前自动备份了现状",
          _count("backup_job", "WHERE reason='PRE_RESTORE'") > n_pre)
    check("数据回到恢复点", _count("patient") == n_at_backup,
          "%d vs %d" % (_count("patient"), n_at_backup))
    check("恢复后完整性检查通过", r.get("integrity_check") == "ok")
    check("写了恢复后校验记录",
          _count("integrity_check", "WHERE check_type='POST_RESTORE'") >= 1)
    check("写了高风险审计 RISK_RESTORE",
          _count("audit_high_risk", "WHERE risk_type='RESTORE'") >= 1)
    check("restore_log 有记录", _count("restore_log", "WHERE result='SUCCEEDED'") >= 1)
    check("恢复后可正常读写", _count("patient") >= 1 and db.is_readonly() is False)

    # 增量文件独立可恢复（F91 AC8 / F93 AC4）
    m01.create(ctx, {"name": "增量前患者", "gender": "F", "birth_date": "1992-02-02"}, {})
    inc_r = m13.incremental_backup(reason="MANUAL", force=True)
    n_at_inc = _count("patient")
    m01.create(ctx, {"name": "增量后患者", "gender": "F", "birth_date": "1992-02-02"}, {})
    check("增量之后又有新数据", _count("patient") == n_at_inc + 1)
    r2 = m13.restore_from_backup(inc_r["job_no"], confirm=True, ctx=ctx)
    check("增量可独立恢复", r2["result"] == "SUCCEEDED", str(r2))
    check("增量恢复后数据正确", _count("patient") == n_at_inc,
          "%d vs %d" % (_count("patient"), n_at_inc))
    check("恢复记录可列表", len(m13.list_restores(10)) >= 2)

    # ============================================================
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
