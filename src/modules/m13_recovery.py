# -*- coding: utf-8 -*-
"""M13 数据安全与恢复（F90-F98）。

表所有权：backup_job / backup_file / restore_log / integrity_check /
         crash_recovery_log / watchdog_log
跨模块：只调对方服务函数（m04_ai.recover_stale_running / m10_print.recover_stale_printing /
       m10_print.probe_and_record / m10_print.set_device_status / m14_system.recover_stale_imports），
       **绝不直接写对方业务表**（04 §2 R2）。

设计要点：
  · 增量 = 快照式（F91 R1），任一备份点都能独立恢复，不做脆弱的 WAL 重放
  · 怀疑损坏即只读，且只读**不自动解除**（F95 R6）
  · 崩溃恢复只做"收敛到确定态"，资金/库存相关一律交人工（R-M13-7）
"""

import datetime
import os
import shutil
import sqlite3

from core import db, audit, backup, config, ids, timeutil as tu
from core import hardware
from core.api import route
from core.context import get_ctx
from core.errors import (AppError, require, not_found, DATA_NOT_FOUND, DATA_REQUIRED,
                         SYSTEM_DB_CORRUPT, SYSTEM_NOT_INITIALIZED, SYSTEM_READONLY,
                         SYSTEM_DB_IOERR)
from core.logging_setup import get

MODULE = "M13"
log = get("m13")

RESTORE_POST_CHECK_FAILED = "E3093"     # 恢复后完整性检查未通过
WATCHDOG_RESTART_EXHAUSTED = "E3094"    # 看门狗连续重启超限
CRASH_NEED_MANUAL_REVIEW = "E3095"      # 崩溃恢复需人工复核

# 看门狗内存态（进程内累计，重启清零；测试可用 _reset_watchdog_state 复位）
_HW_RESTART_FAIL = {}


def _reset_watchdog_state():
    _HW_RESTART_FAIL.clear()


# ---------------- F90 自动备份 ----------------

def auto_backup_if_needed(force=False, ctx=None):
    """F90 R1/R2：每日首次启动自动全量；当天已有成功全量则跳过。"""
    ctx = ctx or get_ctx()
    if not db.table_exists("backup_job"):
        return {"skipped": True, "reason": "备份表尚未创建"}
    if not force:
        today = tu.today_str()
        row = db.query_one("SELECT COUNT(*) c FROM backup_job "
                           "WHERE date(started_at)=? AND job_type='FULL' AND status='SUCCEEDED'",
                           (today,))
        if row and row["c"] > 0:
            return {"skipped": True, "reason": "当天已有成功的全量备份（每日只做一次）",
                    "date": today}
    return backup.full_backup(reason="SCHEDULED")


def wal_residue():
    """上次是否非正常退出（-wal 有残留）。只读判定，不动文件。"""
    p = db.path()
    if not p:
        return False
    wal = p + "-wal"
    try:
        return os.path.exists(wal) and os.path.getsize(wal) > 0
    except OSError:
        return False


# ---------------- F91 增量备份 ----------------

def incremental_backup(reason="SCHEDULED", force=False, ctx=None):
    """F91：在最近一次成功全量之后新增一个恢复点。

    R2 无全量 → E1007；R3 无变化 → skipped；R5 未到周期且非强制 → skipped。
    """
    ctx = ctx or get_ctx()
    if not db.table_exists("backup_job"):
        raise AppError(SYSTEM_NOT_INITIALIZED, "备份表尚未创建，请先执行初始化迁移")
    full = db.query_one("SELECT id, job_no FROM backup_job "
                        "WHERE job_type='FULL' AND status='SUCCEEDED' "
                        "ORDER BY id DESC LIMIT 1")
    if full is None:
        raise AppError(SYSTEM_NOT_INITIALIZED,
                       "尚无成功的全量备份，增量备份必须先有全量作为起点（F91 R2）")

    last = db.query_one("SELECT started_at, write_seq FROM backup_job "
                        "WHERE status='SUCCEEDED' ORDER BY id DESC LIMIT 1")
    if not force and last and last["started_at"]:
        minutes = max(config.get_int("backup.incremental_minutes", 60), 0)
        dt = tu.parse(last["started_at"])
        if minutes > 0 and dt and \
                (tu.now() - dt).total_seconds() < minutes * 60:
            return {"skipped": True, "reason": "周期未到（%d 分钟内已有备份）" % minutes}

    seq = db.write_seq()
    if last and last["write_seq"] is not None and last["write_seq"] == seq:
        return {"skipped": True, "reason": "自上次备份以来无已提交的写入",
                "write_seq": seq}

    r = backup.full_backup(reason=reason, job_type="INCR",
                           parent_job_no=full["job_no"])
    r["parent_job_no"] = full["job_no"]
    r["incremental"] = True
    return r


def list_restores(limit=50):
    return db.query("SELECT * FROM restore_log ORDER BY id DESC LIMIT ?",
                    (min(int(limit or 50), 200),))


# ---------------- F93 数据恢复 ----------------

def _find_backup(ref):
    """按 job_no 或文件路径定位一条成功的备份记录。"""
    row = db.query_one("SELECT * FROM backup_job WHERE job_no=?", (ref,))
    if row is None:
        row = db.query_one("SELECT * FROM backup_job WHERE file_path=?", (ref,))
    if row is None or not row.get("file_path"):
        return None
    if row["status"] != "SUCCEEDED":
        return None
    return row


REGISTRY_TABLES = ("backup_job", "backup_file", "restore_log", "integrity_check",
                   "crash_recovery_log")
REGISTRY_KEEP = 500


def _snapshot_registry():
    """恢复前导出备份/恢复注册表。

    **为什么必须做**：`backup_job` / `restore_log` 就在被恢复的库里，整库覆盖后它们会
    一起消失——用户恢复完会发现"备份清单空了"，也就无法再回退。这些注册表必须与业务
    数据脱钩保存，恢复后回填。
    """
    out = {}
    for t in REGISTRY_TABLES:
        if not db.table_exists(t):
            continue
        out[t] = db.query("SELECT * FROM %s ORDER BY id DESC LIMIT %d" % (t, REGISTRY_KEEP))
    if db.table_exists("sys_sequence"):
        out["sys_sequence"] = db.query(
            "SELECT biz_date, seq_type, current_val FROM sys_sequence")
    return out


def _restore_registry(snapshot, ctx=None):
    """恢复后回填注册表（按 id 去重，已存在则跳过）。"""
    back = 0
    snap = dict(snapshot)
    seq_rows = snap.pop("sys_sequence", None)
    with db.tx() as conn:
        # 编号序列取 MAX 合并：恢复会把序列退回到备份时点，
        # 不合并的话下次取号会撞上恢复后又存在的旧编号（UNIQUE 冲突）。
        for r in (seq_rows or []):
            cur = db.execute(conn, "UPDATE sys_sequence SET current_val=MAX(current_val,?) "
                                   "WHERE biz_date=? AND seq_type=?",
                             (r["current_val"], r["biz_date"], r["seq_type"]))
            if cur.rowcount == 0:
                db.execute(conn, "INSERT INTO sys_sequence (biz_date, seq_type, current_val,"
                                 " updated_at) VALUES (?,?,?,?)",
                           (r["biz_date"], r["seq_type"], r["current_val"], tu.now_str()))
        for t, rows in snap.items():
            if not db.table_exists(t):
                continue
            for r in reversed(rows):          # 升序回填，保持原顺序
                cols = [c for c in r.keys()]
                sql = "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (
                    t, ",".join(cols), ",".join("?" * len(cols)))
                db.execute(conn, sql, tuple(r[c] for c in cols))
                back += 1
        audit.audit_operation(conn, MODULE, "restore.registry", "database", None,
                              detail={"tables": sorted(snap.keys()), "rows": back})
    return back


def _copy_current_db(restore_no):
    """原样复制当前库文件（可能已损坏）留作现场证据。返回副本路径或 None。"""
    src = db.path()
    if not src or not os.path.isfile(src):
        return None
    dst = "%s.pre_restore_%s.corrupt" % (src, restore_no)
    try:
        shutil.copyfile(src, dst)
        return dst
    except Exception:
        log.exception("复制现场库文件失败：%s", src)
        return None


def restore_from_backup(backup_ref, confirm=False, ctx=None):
    """F93：把库整体还原到某个备份点。**高风险**。"""
    ctx = ctx or get_ctx()
    require(confirm is True, "恢复将覆盖当前全部数据，必须传 confirm=true 二次确认",
            code=DATA_REQUIRED)

    row = _find_backup(backup_ref)
    if row is None:
        raise AppError(DATA_NOT_FOUND, "未找到可用的备份记录：%s" % backup_ref)
    src = row["file_path"]
    if not os.path.isfile(src):
        raise AppError(DATA_NOT_FOUND, "备份文件不存在：%s" % src)

    # R2 校验 SHA256
    actual = backup._sha256(src)
    if row.get("checksum") and actual != row["checksum"]:
        with db.tx(force=True) as conn:      # 恢复链路必须在只读态下也能留痕
            db.execute(conn, """
                INSERT INTO restore_log (restored_at, source_file, target_path, result,
                                         detail_json, restored_by)
                VALUES (?,?,?,?,?,?)
            """, (tu.now_str(), src, db.path(), "FAILED",
                  audit.dumps({"reason": "SHA256 校验失败", "expected": row["checksum"],
                               "actual": actual}), ctx.user_id))
            audit.audit_high_risk(conn, "RESTORE", "backup_job", row["id"],
                                  "恢复被拒：备份文件校验失败",
                                  {"file": src, "checksum": row.get("checksum")})
        raise AppError(SYSTEM_DB_CORRUPT,
                       "备份文件已损坏（SHA256 不符），拒绝恢复，当前数据未受影响")

    # R8 恢复单号（关闭连接前取号）
    with db.tx(force=True) as conn:
        restore_no = ids.next_no(conn, "RESTORE")
        audit.audit_operation(conn, MODULE, "restore.begin", "backup_job", row["id"],
                              detail={"file": src, "restore_no": restore_no})

    # R1 恢复前先备份现状，防止"恢复错了想退回"
    pre = None
    try:
        pre = backup.full_backup(reason="PRE_RESTORE")
    except Exception:
        log.exception("恢复前备份失败（库可能已损坏，退化为原样文件副本）")
    # 库已坏到 VACUUM 不出来时也要留住现场：整文件原样复制一份，
    # **绝不**改写或删除原库（F95 R2），这份副本只作取证，不作为可恢复点
    pre_file = None if pre else _copy_current_db(restore_no)

    target = db.path()
    registry = _snapshot_registry()
    # R4 恢复期间禁止业务
    db.set_readonly(True)
    db.close_all()
    ok = False
    detail = {"source": src, "restore_no": restore_no, "pre_restore": pre,
              "pre_restore_file": pre_file}
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            p = target + suffix
            if os.path.exists(p):
                os.remove(p)
        shutil.copyfile(src, target)
        db.init(target)          # 重新打开（init 会把 readonly 置回 False）
    except Exception as exc:
        log.exception("恢复过程失败")
        try:
            db.init(target)
        except Exception:
            pass
        detail["error"] = str(exc)
        _finish_restore(restore_no, src, False, detail, ctx, row)
        raise AppError(SYSTEM_DB_IOERR, "恢复失败：%s（原库已保留）" % exc, retryable=True)

    # R5 恢复后强制完整性检查
    res = db.integrity_check()
    ok = (res == "ok")
    detail["integrity_check"] = res
    if not ok:
        # 回滚到 PRE_RESTORE 备份，绝不留一个坏库
        if pre and pre.get("file"):
            try:
                db.close_all()
                shutil.copyfile(os.path.join(os.path.dirname(src) or ".", pre["file"]),
                                target)
                db.init(target)
            except Exception:
                log.exception("回滚到 PRE_RESTORE 备份失败")
        detail["rolled_back_to"] = (pre or {}).get("file")
        _finish_restore(restore_no, src, False, detail, ctx, row)
        raise AppError(RESTORE_POST_CHECK_FAILED,
                       "恢复后完整性检查未通过（%s），已回退到恢复前备份" % res)

    db.execute(db.write_conn(), "PRAGMA foreign_keys=ON")
    # 回填备份/恢复注册表：否则整库覆盖后备份清单会一起消失，用户将无法再次回退
    try:
        detail["registry_rows"] = _restore_registry(registry, ctx)
    except Exception:
        log.exception("回填备份注册表失败（备份列表可能不完整）")
    # R5 落一条恢复后校验记录，便于事后追溯
    with db.tx() as c:
        db.execute(c, """
            INSERT INTO integrity_check (checked_at, check_type, target, result, detail_json,
                                         checked_by)
            VALUES (?,'POST_RESTORE',?,?,?,?)
        """, (tu.now_str(), db.path(), "OK" if ok else "FAIL",
              audit.dumps({"restore_no": restore_no}), ctx.user_id))
    _finish_restore(restore_no, src, True, detail, ctx, row)
    return {"restore_no": restore_no, "source_file": src, "result": "SUCCEEDED",
            "pre_restore_backup": (pre or {}).get("file"), "pre_restore_file": pre_file,
            "integrity_check": res}


def _finish_restore(restore_no, src, ok, detail, ctx, row):
    """写 restore_log + 高风险审计（无论成败都要留痕）。"""
    try:
        with db.tx(force=True) as conn:
            db.execute(conn, """
                INSERT INTO restore_log (restore_no, restored_at, source_file, target_path,
                                         result, before_backup, detail_json, restored_by)
                VALUES (?,?,?,?,?,?,?,?)
            """, (restore_no, tu.now_str(), src, db.path(),
                  "SUCCEEDED" if ok else "FAILED",
                  (detail.get("pre_restore") or {}).get("file"),
                  audit.dumps(detail), ctx.user_id))
            audit.audit_high_risk(conn, "RESTORE", "backup_job", row["id"],
                                  "数据恢复%s：%s" % ("成功" if ok else "失败", src), detail)
    except Exception:
        log.exception("写恢复日志失败")


# ---------------- F94 完整性检查 ----------------

def integrity_check_full(ctx=None):
    """F94：页结构 + 外键 + 业务不变量三层检查。"""
    from core import selftest
    ctx = ctx or get_ctx()
    t0 = datetime.datetime.now()
    # 页结构层：库真损坏时 integrity_check 本身就会抛 E1003，
    # 此时不能让整个检查函数崩掉——它同时是启动自检的健康探针
    err = None
    try:
        base = backup.integrity_check_now()
    except AppError as exc:
        err = {"code": exc.code, "message": exc.message}
        base = {"integrity_check": "ERROR", "foreign_key_violations": [], "ok": False}
    # 业务不变量层：文件已损坏时跑查询只会得到更多无意义的报错，直接跳过
    violations = []
    if base.get("ok"):
        try:
            violations = selftest.check_invariants()
        except AppError as exc:
            err = {"code": exc.code, "message": exc.message}
    ok = base["ok"] and not violations
    detail = {"invariants": violations}
    if err:
        detail["layer_error"] = err
    duration = int((datetime.datetime.now() - t0).total_seconds() * 1000)
    try:
        with db.tx() as conn:
            db.execute(conn, """
                INSERT INTO integrity_check (checked_at, check_type, target, result,
                                             detail_json, duration_ms, checked_by)
                VALUES (?,'FULL',?,?,?,?,?)
            """, (tu.now_str(), db.path(), "OK" if ok else "FAIL", audit.dumps(detail),
                  duration, ctx.user_id))
    except (AppError, sqlite3.Error):
        # 库已坏到写不进去了：结论仍然要返回，不能让"检查"本身变成新的故障
        log.exception("完整性检查结果落表失败（库可能已损坏）")
    return {"integrity_check": base["integrity_check"],
            "foreign_key_violations": base["foreign_key_violations"],
            "invariants": violations, "layer_error": err, "ok": ok}


# ---------------- F95 SQLite 异常恢复 ----------------

def freeze_on_corruption(error_code, raw_message, ctx=None):
    """F95 R2：捕获损坏类异常后冻结现场。只读 + 崩溃日志 + 高风险审计。

    **绝不**删除或改写原库文件，为后续 salvage / 恢复保留证据。
    """
    ctx = ctx or get_ctx()
    # 先留痕（此刻仍可写），再冻结：否则只读后连审计都写不进去
    try:
        with db.tx() as conn:
            db.execute(conn, """
                INSERT INTO crash_recovery_log (detected_at, issue, action_taken, result,
                                                detail_json)
                VALUES (?,?,?,?,?)
            """, (tu.now_str(), "SQLite 异常：%s" % (raw_message or error_code),
                  "进入只读模式，保留现场等待人工恢复",
                  "NEED_MANUAL_REVIEW",
                  audit.dumps({"error_code": error_code, "raw": raw_message})))
            audit.audit_high_risk(conn, "DB_CORRUPT", "database", None,
                                  "数据库异常，已冻结现场并进入只读",
                                  {"error_code": error_code, "raw": raw_message})
    except Exception:
        log.exception("写崩溃日志失败（异常绝不静默丢失，已进应用日志）")
    db.set_readonly(True)
    return {"readonly": True, "error_code": error_code,
            "message": "数据库异常，已进入只读模式，请使用最近备份恢复"}


def salvage(confirm=False, ctx=None):
    """F95 R3：逐表导出抢救到独立库，**不覆盖原库**，结果需人工核对。"""
    ctx = ctx or get_ctx()
    require(confirm is True, "抢救会读取可能损坏的库，必须传 confirm=true", code=DATA_REQUIRED)
    src_path = db.path()
    ts = tu.now().strftime("%Y%m%d_%H%M%S")
    dst_path = os.path.join(os.path.dirname(os.path.abspath(src_path)),
                            "clinic_salvage_%s.db" % ts)
    report = {"source": src_path, "target": dst_path, "tables": [], "skipped": []}
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        rows = src.execute("SELECT name, sql FROM sqlite_master "
                           "WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for name, sql in rows:
            try:
                data = src.execute("SELECT * FROM \"%s\"" % name).fetchall()
                cols = [d[0] for d in src.execute(
                    "SELECT * FROM \"%s\" LIMIT 1" % name).description] \
                    if False else None
            except sqlite3.Error as exc:
                report["skipped"].append({"table": name, "error": str(exc)})
                continue
            try:
                dst.execute(sql)
            except sqlite3.Error:
                report["skipped"].append({"table": name, "error": "目标建表失败"})
                continue
            if not data:
                report["tables"].append({"table": name, "rows": 0})
                continue
            ncol = len(data[0])
            ph = ",".join(["?"] * ncol)
            try:
                dst.executemany("INSERT INTO \"%s\" VALUES (%s)" % (name, ph), data)
                report["tables"].append({"table": name, "rows": len(data)})
            except sqlite3.Error as exc:
                report["skipped"].append({"table": name, "error": str(exc)})
        dst.commit()
    finally:
        src.close()
        dst.close()
    # 抢救结果校验
    conn = sqlite3.connect(dst_path)
    try:
        res = conn.execute("PRAGMA integrity_check").fetchone()
        report["integrity_check"] = res[0] if res else "unknown"
    finally:
        conn.close()
    try:
        with db.tx() as c:
            audit.audit_high_risk(c, "DB_SALVAGE", "database", None,
                                  "数据库抢救：导出到 %s" % dst_path, report)
    except Exception:
        # 抢救常在只读态下执行，写不进审计也要留下应用日志
        log.exception("抢救审计写入失败（已记应用日志）：%s", dst_path)
    report["note"] = "抢救库可能缺行，必须人工核对后再使用；原库未被改动"
    return report


def release_readonly(reason=None, ctx=None):
    """F95 R6：只读只能由人解除（成功恢复后也走这里）。"""
    ctx = ctx or get_ctx()
    was = db.is_readonly()
    db.set_readonly(False)
    with db.tx() as conn:
        audit.audit_high_risk(conn, "READONLY_RELEASE", "database", None,
                              "管理员解除只读：%s" % (reason or "未填写原因"),
                              {"was_readonly": was})
    return {"readonly": False, "was_readonly": was}


# ---------------- F96 崩溃恢复 ----------------

def _manual_review_items():
    """资金/库存相关：只报告，绝不自动补偿（R-M13-7）。"""
    items = []
    today = tu.today_str()
    if db.table_exists("bill"):
        for r in db.query("SELECT id, bill_no, status, biz_date FROM bill "
                          "WHERE status='PENDING' AND deleted_at IS NULL "
                          "AND COALESCE(biz_date, '') < ?", (today,)):
            items.append({"kind": "BILL_PENDING", "ref": r["bill_no"],
                          "detail": "账单仍处于待收费（营业日 %s）" % r["biz_date"]})
    if db.table_exists("dispense") and db.table_exists("prescription"):
        for r in db.query("""
            SELECT p.id, p.prescription_no
            FROM prescription p
            WHERE p.status='CONFIRMED' AND p.deleted_at IS NULL
              AND EXISTS (SELECT 1 FROM dispense d WHERE d.prescription_id=p.id
                          AND d.deleted_at IS NULL)
        """):
            items.append({"kind": "DISPENSE_UNFINISHED", "ref": r["prescription_no"],
                          "detail": "已有发药记录但处方未推进到 DISPENSED"})
    return items


def recover_from_crash(ctx=None):
    """F96：启动时收敛崩溃遗留。数据由事务保证，这里只收敛"卡住的任务"。"""
    ctx = ctx or get_ctx()
    from modules import m14_system, m10_print, m04_ai
    out = {"wal_residue": False, "imports_recovered": 0, "prints_recovered": 0,
           "ai": {"scanned": 0, "retried": 0, "abandoned": 0}, "manual_review": []}

    out["wal_residue"] = wal_residue()
    out["imports_recovered"] = m14_system.recover_stale_imports() or 0
    out["prints_recovered"] = m10_print.recover_stale_printing(ctx)["recovered"]
    out["ai"] = m04_ai.recover_stale_running(ctx)
    out["manual_review"] = _manual_review_items()

    with db.tx() as conn:
        db.execute(conn, """
            INSERT INTO crash_recovery_log (detected_at, issue, action_taken, result,
                                            detail_json)
            VALUES (?,?,?,?,?)
        """, (tu.now_str(), "崩溃恢复扫描",
              "收敛导入/打印/AI 任务；资金与库存项转人工复核",
              "NEED_MANUAL_REVIEW" if out["manual_review"] else "RECOVERED",
              audit.dumps(out)))
        audit.audit_operation(conn, MODULE, "crash.recovery", "database", None,
                              detail=out)
        if out["manual_review"]:
            audit.audit_high_risk(conn, "CRASH_MANUAL_REVIEW", "database", None,
                                  "崩溃恢复发现需人工复核的项（不自动补偿）",
                                  {"items": out["manual_review"]})
    return out


# ---------------- F97 AI 看门狗 ----------------

def watchdog_ai(ctx=None):
    ctx = ctx or get_ctx()
    from modules import m04_ai
    r = m04_ai.recover_stale_running(ctx)
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "watchdog.ai", "ai_task", None, detail=r)
        for it in r.get("items", []):
            db.execute(conn, """
                INSERT INTO watchdog_log (kind, target, action, result, detail_json, occurred_at)
                VALUES ('AI',?,?,?,?,?)
            """, (it["task_no"], it["action"], "SUCCESS", audit.dumps(it), tu.now_str()))
    r["kind"] = "AI"
    return r


# ---------------- F98 硬件看门狗 ----------------

def _consecutive_fail(driver_code, limit=30):
    if not db.table_exists("hardware_health"):
        return 0
    rows = db.query("SELECT result FROM hardware_health WHERE driver_code=? "
                    "ORDER BY id DESC LIMIT ?", (driver_code, limit))
    n = 0
    for r in rows:
        if r["result"] == "FAIL":
            n += 1
        else:
            break
    return n


def watchdog_hardware(ctx=None):
    """F98：探测 → 连续失败达阈值则重启驱动 → 仍失败则降级设备。"""
    ctx = ctx or get_ctx()
    from modules import m10_print
    threshold = max(config.get_int("hardware.watchdog_fail_threshold", 3), 1)
    degrade_rounds = max(config.get_int("hardware.watchdog_degrade_rounds", 2), 1)

    probe = m10_print.probe_and_record(ctx)
    out = {"checked": len(probe["items"]), "restarted": 0, "degraded": 0,
           "jobs_recovered": 0, "items": []}

    for r in probe["items"]:
        if r.get("online"):
            _HW_RESTART_FAIL.pop(r["driver"], None)
            continue
        n = _consecutive_fail(r["driver"])
        if n < threshold:
            out["items"].append({"driver": r["driver"], "action": "NONE",
                                 "consecutive_fail": n})
            continue
        # R3 重启驱动并复测
        hardware.restart(r["driver"])
        out["restarted"] += 1
        m10_print.record_event(r["driver"], "RESTART", "SUCCESS",
                               "看门狗：连续失败 %d 次触发重启" % n, ctx=ctx)
        again = m10_print.probe_and_record(ctx)
        still_down = any((x["driver"] == r["driver"] and not x.get("online"))
                         for x in again["items"])
        if still_down:
            _HW_RESTART_FAIL[r["driver"]] = _HW_RESTART_FAIL.get(r["driver"], 0) + 1
            out["items"].append({"driver": r["driver"], "action": "RESTART",
                                 "recovered": False,
                                 "restart_fail_rounds": _HW_RESTART_FAIL[r["driver"]]})
            if _HW_RESTART_FAIL[r["driver"]] >= degrade_rounds:
                dev = db.query_one("SELECT id FROM hardware_device WHERE driver_code=? "
                                   "AND deleted_at IS NULL", (r["driver"],))
                if dev:
                    m10_print.set_device_status(dev["id"], "OFFLINE", ctx)
                    out["degraded"] += 1
                _HW_RESTART_FAIL.pop(r["driver"], None)
                with db.tx() as conn:
                    audit.audit_high_risk(conn, "HW_DEGRADE", "hardware_device",
                                          dev["id"] if dev else None,
                                          "驱动 %s 重启无效，设备降级为 OFFLINE" % r["driver"])
        else:
            _HW_RESTART_FAIL.pop(r["driver"], None)
            dev = db.query_one("SELECT id FROM hardware_device WHERE driver_code=? "
                               "AND deleted_at IS NULL", (r["driver"],))
            if dev:
                m10_print.set_device_status(dev["id"], "ONLINE", ctx)
            out["items"].append({"driver": r["driver"], "action": "RESTART",
                                 "recovered": True})

    out["jobs_recovered"] = m10_print.recover_stale_printing(ctx)["recovered"]
    with db.tx() as conn:
        audit.audit_operation(conn, MODULE, "watchdog.hardware", "hardware_health", None,
                              detail=out)
        for it in out["items"]:
            db.execute(conn, """
                INSERT INTO watchdog_log (kind, target, action, result, detail_json, occurred_at)
                VALUES ('HARDWARE',?,?,?,?,?)
            """, (it["driver"], it["action"],
                  "FAIL" if it.get("recovered") is False else "SUCCESS",
                  audit.dumps(it), tu.now_str()))
    out["kind"] = "HARDWARE"
    return out


def watchdog_all(ctx=None):
    ctx = ctx or get_ctx()
    ai = watchdog_ai(ctx)
    hw = watchdog_hardware(ctx)
    return {"ai": ai, "hardware": hw}


# ---------------- 路由 ----------------

@route("/api/system/backup/incremental", "POST", perm="system.backup", module=MODULE,
       action="backup.incremental")
def api_incremental(ctx, body, q):
    return incremental_backup(reason=body.get("reason") or "SCHEDULED",
                              force=bool(body.get("force")), ctx=ctx)


@route("/api/system/restore", "POST", perm="system.restore", module=MODULE,
       action="system.restore")
def api_restore(ctx, body, q):
    ref = body.get("job_no") or body.get("file_path") or body.get("backup_ref")
    require(ref, "缺少备份标识（job_no 或 file_path）")
    return restore_from_backup(ref, confirm=bool(body.get("confirm")), ctx=ctx)


@route("/api/system/restores", "GET", perm="system.restore", module=MODULE,
       action="system.restores")
def api_restores(ctx, body, q):
    return {"items": list_restores(q.get("limit"))}


@route("/api/system/integrity/full", "POST", perm="system.health", module=MODULE,
       action="system.integrity.full")
def api_integrity_full(ctx, body, q):
    return integrity_check_full(ctx)


@route("/api/system/recovery/crash", "POST", perm="system.recovery", module=MODULE,
       action="system.crash.recovery")
def api_crash(ctx, body, q):
    return recover_from_crash(ctx)


@route("/api/system/watchdog", "POST", perm="system.watchdog", module=MODULE,
       action="system.watchdog")
def api_watchdog(ctx, body, q):
    kind = (body.get("kind") or "all").lower()
    if kind == "ai":
        return watchdog_ai(ctx)
    if kind == "hardware":
        return watchdog_hardware(ctx)
    return watchdog_all(ctx)


@route("/api/system/watchdog/log", "GET", perm="system.watchdog", module=MODULE,
       action="system.watchdog.log")
def api_watchdog_log(ctx, body, q):
    sql = "SELECT * FROM watchdog_log WHERE 1=1"
    params = ()
    if q.get("kind"):
        sql += " AND kind=?"
        params = (q["kind"].upper(),)
    sql += " ORDER BY id DESC LIMIT ?"
    params += (min(int(q.get("limit") or 100), 500),)
    return {"items": db.query(sql, params)}


@route("/api/system/readonly/release", "POST", perm="system.restore", module=MODULE,
       action="system.readonly.release")
def api_release_readonly(ctx, body, q):
    return release_readonly(reason=body.get("reason"), ctx=ctx)


@route("/api/system/salvage", "POST", perm="system.restore", module=MODULE,
       action="system.salvage")
def api_salvage(ctx, body, q):
    return salvage(confirm=bool(body.get("confirm")), ctx=ctx)
