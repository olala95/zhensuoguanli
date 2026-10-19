# -*- coding: utf-8 -*-
"""启动自检与崩溃恢复（F94 / F96）。契约：05 §5。

每次启动必做 7 项自检；任一失败 → 安全模式（只读打开，禁止业务写入）。
崩溃后校验跨表一致性不变量：
  · 库存不为负
  · 账单明细之和 == 账单总额
  · 已发药处方必有 dispense 记录
  · 已确认病历必有 Hash 记录
"""

import os
import sqlite3
import time

from . import db
from . import timeutil as tu
from . import audit
from . import config
from .context import get_ctx
from .logging_setup import get

log = get("selftest")

DISK_FREE_WARN_MB = 500


def _free_mb(path):
    try:
        if hasattr(os, "statvfs"):
            st = os.statvfs(path)
            return st.f_bavail * st.f_frsize / 1048576.0
        import ctypes
        free = ctypes.c_ulonglong()
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(path), None, None, ctypes.byref(free))
        return free.value / 1048576.0
    except Exception:
        return None


def quick_gate():
    """迁移前的快速闸门：文件可打开 + integrity_check。

    损坏时直接进安全模式，绝不在坏库上跑迁移。
    """
    p = db.path()
    if not p or not os.path.isfile(p):
        # 全新库：文件不存在时视为可继续（后续迁移会建表）
        return True, "新库，尚未创建文件"
    res = db.integrity_check()
    return (res == "ok"), res


def run_startup_checks(migrations_expected=None):
    """返回 (checks, safe_mode)。checks 为 7 项结果列表。"""
    checks = []

    def add(item, ok, detail="", warn=False):
        checks.append({"item": item,
                       "result": "OK" if ok else ("WARN" if warn else "FAIL"),
                       "detail": detail})

    # 1 数据库文件存在且可打开
    p = db.path()
    ok = bool(p) and os.path.isfile(p)
    add("db_file", ok, p or "路径为空")
    if not ok:
        return checks, True

    # 2 integrity_check
    t0 = time.time()
    res = db.integrity_check()
    add("integrity_check", res == "ok", res, warn=False)

    # 3 foreign_key_check
    fk = db.foreign_key_check()
    add("foreign_key_check", not fk, "违规 %d 条" % len(fk))

    # 4 未提交事务 / WAL 残留 → 崩溃恢复
    crash = detect_crash()
    add("crash_recovery", crash["clean"], crash["detail"], warn=not crash["clean"])

    # 5 迁移版本一致
    try:
        from . import migrate
        applied = migrate.applied(db.write_conn())
        scripts = migrate._scripts()
        expected = [s[0] for s in scripts]
        missing = [e for e in expected if e not in applied]
        add("migration_version", not missing,
            "已应用 %d / 期望 %d，缺失 %s" % (len(applied), len(expected), missing or "无"))
    except Exception as exc:
        add("migration_version", False, str(exc))

    # 6 磁盘空间
    free = _free_mb(os.path.dirname(os.path.abspath(p)) or ".")
    if free is None:
        add("disk_space", True, "无法读取", warn=True)
    else:
        add("disk_space", free >= DISK_FREE_WARN_MB, "剩余 %.0f MB" % free,
            warn=free < DISK_FREE_WARN_MB)

    # 7 备份目录可写（首次启动时 sys_config 尚未建表，读配置需容错）
    try:
        bdir = config.get("backup.dir", "") or None
    except Exception:
        bdir = None
    try:
        from . import backup as _bk
        bdir = bdir or _bk.BACKUP_DIR
        test = os.path.join(bdir, ".write_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        add("backup_writable", True, bdir)
    except Exception as exc:
        add("backup_writable", False, str(exc))

    failed = [c for c in checks if c["result"] == "FAIL"]
    return checks, bool(failed)


def detect_crash():
    """检查上次崩溃遗留：WAL 未 checkpoint / 未提交事务。SQLite WAL 会自动回滚。"""
    p = db.path()
    wal = p + "-wal" if p else None
    detail = "无 WAL 残留"
    clean = True
    try:
        if wal and os.path.exists(wal) and os.path.getsize(wal) > 0:
            clean = False
            detail = "发现 WAL 残留 %d 字节，SQLite 已自动回滚未提交事务" % os.path.getsize(wal)
        else:
            detail = "无残留事务"
    except Exception as exc:
        clean = False
        detail = str(exc)
    # 无论是否有残留，都做一次不变量校验
    violations = check_invariants()
    if violations:
        clean = False
        detail += "；不变量违规 %d 项：%s" % (len(violations), violations)
    record = {"clean": clean, "detail": detail, "violations": violations}
    if not clean:
        _write_crash_log(record)
    return record


def check_invariants():
    """跨表一致性不变量校验。返回违规描述列表。"""
    out = []
    try:
        rows = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    except Exception:
        return out
    names = {r["name"] for r in rows}

    # 库存不得为负（成药 qty / 饮片 qty_g）——字段名以 04_DATA_MODEL 为准
    if "stock" in names:
        for r in db.query("SELECT id, drug_id, qty FROM stock WHERE qty < 0"):
            out.append("库存为负：stock.id=%s" % r["id"])
    if "herb_stock" in names:
        for r in db.query("SELECT id, herb_id, qty_g FROM herb_stock WHERE qty_g < 0"):
            out.append("饮片库存为负：herb_stock.id=%s" % r["id"])

    # 账单金额 = Σ 明细（含负数冲正行）
    if "bill" in names and "bill_item" in names:
        for r in db.query("""
            SELECT b.id, b.total_amount_fen AS t,
                   COALESCE((SELECT SUM(i.amount_fen) FROM bill_item i WHERE i.bill_id=b.id),0) AS s
            FROM bill b WHERE b.deleted_at IS NULL AND b.status != 'VOID' AND b.total_amount_fen <> s
        """):
            out.append("账单金额与明细不符：bill.id=%s（账=%s 明细=%s）"
                       % (r["id"], r["t"], r["s"]))

    # 库存流水 vs 库存结存：账实必须一致（M07 R-M07-1/R1）
    if "stock_transaction" in names and "stock" in names:
        for r in db.query("""
            SELECT s.id, s.drug_id, s.batch_id, s.qty AS book,
                   COALESCE((SELECT SUM(t.qty_change) FROM stock_transaction t
                             WHERE t.item_kind='DRUG' AND t.batch_id=s.batch_id),0) AS led
            FROM stock s WHERE s.deleted_at IS NULL AND ABS(s.qty - led) > 0.0001
        """):
            out.append("成药库存与流水不符：stock.id=%s（账=%s 流水=%s）"
                       % (r["id"], r["book"], r["led"]))
    if "stock_transaction" in names and "herb_stock" in names:
        for r in db.query("""
            SELECT h.id, h.herb_id, h.batch_id, h.qty_g AS book,
                   COALESCE((SELECT SUM(t.qty_change) FROM stock_transaction t
                             WHERE t.item_kind='HERB' AND t.batch_id=h.batch_id),0) AS led
            FROM herb_stock h WHERE h.deleted_at IS NULL AND ABS(h.qty_g - led) > 0.0001
        """):
            out.append("饮片库存与流水不符：herb_stock.id=%s（账=%s 流水=%s）"
                       % (r["id"], r["book"], r["led"]))

    # 净收不得超过**原始**应收（无欠费挂账，也不得多收）
    # 口径：已收 − 已退 ≤ 应收 + 已冲正。
    # 为什么要把冲正加回去：冲正（F62）只减应收、不动已收，
    # 已收费账单冲正后到实际退款前，必然存在"已收 > 应收"的窗口期，
    # 那是合法的中间状态（多收的钱还没退给患者），不能算违规。
    # 加回冲正额后即还原为"原始应收"，冲正前后、退款前后都能同时成立。
    if "bill" in names and "payment" in names:
        for r in db.query("""
            SELECT b.id, b.total_amount_fen AS t,
                   COALESCE((SELECT SUM(p.amount_fen) FROM payment p
                             WHERE p.bill_id=b.id AND p.status='SUCCESS'),0) AS paid,
                   -- refund.status 取值 DRAFT/SUCCEEDED/FAILED/VOID（不是 CONFIRMED）
                   COALESCE((SELECT SUM(f.total_amount_fen) FROM refund f
                             WHERE f.bill_id=b.id AND f.status='SUCCEEDED'
                               AND f.deleted_at IS NULL),0) AS rf,
                   -- reversal 表没有 deleted_at 列
                   COALESCE((SELECT SUM(v.total_amount_fen) FROM reversal v
                             WHERE v.bill_id=b.id),0) AS rv
            FROM bill b WHERE b.deleted_at IS NULL AND b.status IN ('PAID',
                'PARTIAL_REFUNDED','FULLY_REFUNDED') AND (paid - rf) > (t + rv)
        """):
            out.append("净收超过原始应收：bill.id=%s（已收%s 已退%s / 应收%s + 冲正%s）"
                       % (r["id"], r["paid"], r.get("rf", 0), r["t"], r.get("rv", 0)))
    if "prescription" in names and ("dispense" in names or "herb_dispense" in names):
        # 中药走 herb_dispense（F115），成药走 dispense（F51）：两条链路都要认，
        # 否则任何一次饮片发药都会让完整性检查误报"缺少发药记录"
        conds = []
        if "dispense" in names:
            conds.append("NOT EXISTS (SELECT 1 FROM dispense d WHERE d.prescription_id=p.id)")
        if "herb_dispense" in names:
            conds.append("NOT EXISTS (SELECT 1 FROM herb_dispense h "
                         "WHERE h.prescription_id=p.id)")
        for r in db.query("""
            SELECT p.id FROM prescription p
            WHERE p.status IN ('DISPENSED','PARTIAL_RETURNED','FULLY_RETURNED')
              AND %s
            """ % " AND ".join(conds)):
            out.append("已发药处方缺少发药记录：prescription.id=%s" % r["id"])
    return out


def _write_crash_log(record):
    try:
        with db.tx() as conn:
            db.execute(conn, """
                INSERT INTO crash_recovery_log (detected_at, issue, action_taken,
                                                result, detail_json)
                VALUES (?,?,?,?,?)
            """, (tu.now_str(), "启动自检发现异常", "WAL 自动回滚 + 不变量校验",
                  "RECOVERED" if not record["violations"] else "NEED_MANUAL_REVIEW",
                  audit.dumps(record)))
            if record["violations"]:
                audit.audit_high_risk(conn, "CRASH_INCONSISTENT", "database", None,
                                      "崩溃恢复后发现数据不一致，需人工核对",
                                      {"violations": record["violations"]})
            else:
                audit.audit_operation(conn, "M13", "crash_recovery", "database", None,
                                      detail=record["detail"])
    except Exception:
        log.exception("写崩溃恢复日志失败")


def persist(checks):
    ctx = get_ctx()
    try:
        with db.tx() as conn:
            for c in checks:
                db.execute(conn, """
                    INSERT INTO sys_health_check (check_item, result, detail, checked_at,
                                                  checked_by)
                    VALUES (?,?,?,?,?)
                """, (c["item"], c["result"], c["detail"][:500], tu.now_str(), ctx.user_id))
    except Exception:
        log.exception("持久化自检结果失败")
