# -*- coding: utf-8 -*-
"""备份与完整性检查（F90 自动备份 / F92 手动备份 / F94 完整性检查）。

契约：05 §6
· 备份前强制 wal_checkpoint(TRUNCATE)
· 使用 VACUUM INTO 或 backup API，禁止直接 copy .db 文件
· 备份完成后立即 integrity_check，失败则标记该备份不可用并告警
· 保留：全量 30 天；每日最近 7 份；每月 1 份长期归档
"""

import hashlib
import os
import sqlite3
import time

from . import db
from . import db as _db
from . import timeutil as tu
from . import audit
from . import config
from . import ids
from .context import get_ctx
from .errors import AppError, SYSTEM_DB_IOERR
from .logging_setup import get

log = get("backup")

BACKUP_DIR = None


def init(backup_dir):
    global BACKUP_DIR
    BACKUP_DIR = backup_dir
    os.makedirs(backup_dir, exist_ok=True)


def _dir():
    d = config.get("backup.dir", "") or BACKUP_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def full_backup(reason="MANUAL", job_no=None, job_type="FULL", parent_job_no=None):
    """执行一次备份。job_type=FULL（全量）/ INCR（增量，F91）。

    增量与全量**同构**（都是一致快照），只是登记类型与所属链不同；
    增量必须挂在某个全量之后（parent_job_no）。失败写 audit_high_risk（05 §6）。
    """
    if not db.table_exists("backup_job"):
        log.info("备份表尚未创建（首次初始化），跳过本次备份")
        return {"skipped": True, "reason": "backup_job 表不存在"}

    ctx = get_ctx()
    started = tu.now()
    job_id = None
    suffix = "incr" if job_type == "INCR" else "full"
    # 精确到毫秒：同一秒内的两次备份不能撞同一个文件名（否则后者覆盖前者，
    # 前者的 SHA256 立刻失效，恢复时会被判成"文件已损坏"）
    file_name = "clinic_%s_%03d_%s.dbbak" % (started.strftime("%Y%m%d_%H%M%S"),
                                             started.microsecond // 1000, suffix)
    try:
        # 目录不可写必须在 try 内，否则裸 OSError 会绕过异常码体系（R6）
        target = os.path.join(_dir(), file_name)
        with db.tx() as conn:
            if job_no is None:
                job_no = ids.next_no(conn, "BACKUP_JOB")
            cur = db.execute(conn, """
                INSERT INTO backup_job (job_no, job_type, status, started_at, reason,
                                        parent_job_no, write_seq, created_by)
                VALUES (?,?,'RUNNING',?,?,?,?,?)
            """, (job_no, job_type, tu.now_str(), reason, parent_job_no,
                  db.write_seq(), ctx.user_id))
            job_id = cur.lastrowid

        # 备份前强制 checkpoint，保证一致性
        db.checkpoint_truncate()

        t0 = time.time()
        conn = db.write_conn()
        try:
            conn.execute("VACUUM INTO ?", (target,))
        except sqlite3.Error:
            # 降级：backup API
            out = sqlite3.connect(target)
            try:
                conn.backup(out)
            finally:
                out.close()
        size = os.path.getsize(target)
        checksum = _sha256(target)

        # 备份完成后立即校验
        ok = _verify_backup(target)
        with db.tx() as c:
            db.execute(c, """
                UPDATE backup_job SET status=?, finished_at=?, file_path=?, file_size=?,
                                     checksum=?, duration_ms=?
                WHERE id=?
            """, ("SUCCEEDED" if ok else "FAILED", tu.now_str(), target, size, checksum,
                  int((time.time() - t0) * 1000), job_id))
            db.execute(c, """
                INSERT INTO backup_file (job_id, file_name, file_path, file_size, checksum,
                                         status, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (job_id, file_name, target, size, checksum,
                  "VALID" if ok else "INVALID", tu.now_str()))
            audit.audit_operation(c, "M13", "backup.full", "backup_job", job_id,
                                  result="SUCCESS" if ok else "FAIL",
                                  detail={"reason": reason, "file": file_name, "size": size})
            if not ok:
                audit.audit_high_risk(c, "BACKUP_FAILED", "backup_job", job_id,
                                      "备份文件校验失败", {"file": file_name})
        cleanup()
        # 写序号在**全部收尾动作之后**记录，且**刻意不走 db.tx()**：
        # 事务提交会自增 write_seq，若用事务记账，本次记账就把序号顶高了，
        # F91 的"自上次备份以来无变化"会永远判成有变化。
        db.write_conn().execute("UPDATE backup_job SET write_seq=? WHERE id=?",
                                (db.write_seq(), job_id))
        return {"job_no": job_no, "job_id": job_id, "file": file_name, "size": size,
                "valid": ok, "write_seq": db.write_seq()}
    except Exception as exc:
        log.exception("全量备份失败 reason=%s", reason)
        try:
            with db.tx() as c:
                if job_id:
                    db.execute(c, "UPDATE backup_job SET status='FAILED', finished_at=?, "
                                  "error_message=? WHERE id=?",
                               (tu.now_str(), str(exc), job_id))
                audit.audit_high_risk(c, "BACKUP_FAILED", "backup_job", job_id,
                                      "备份失败：%s" % exc, {"reason": reason})
        except Exception:
            log.exception("记录备份失败状态时再次失败（已写日志）")
        if isinstance(exc, AppError):
            raise
        raise AppError(SYSTEM_DB_IOERR, "备份失败：%s" % exc, retryable=True,
                       http_status=500)


def _verify_backup(path):
    """备份完成后立即 integrity_check。"""
    conn = None
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        ok = row and row[0] == "ok"
    except Exception:
        ok = False
    finally:
        if conn is not None:
            conn.close()
    with db.tx() as c:
        db.execute(c, """
            INSERT INTO integrity_check (checked_at, check_type, target, result, detail_json,
                                         checked_by)
            VALUES (?,'BACKUP_VERIFY',?,?,?,?)
        """, (tu.now_str(), path, "OK" if ok else "FAIL", "integrity_check", get_ctx().user_id))
    return ok


def cleanup():
    """保留策略：全量 keep_days 天 + 每日最近 7 份 + 每月 1 份长期归档。

    **增量随所属全量级联删除**（F91 R4）：不允许出现"全量已删但增量还在"的孤儿链。
    """
    if not db.table_exists("backup_job"):
        return 0
    retention = (config.get_int("backup.keep_days", 0)
                 or config.get_int("backup.retention_days", 30))
    d = _dir()
    jobs = db.query("""SELECT id, job_no, job_type, parent_job_no, file_path
                       FROM backup_job
                       WHERE status='SUCCEEDED' AND file_path IS NOT NULL
                       ORDER BY id DESC""")
    path_of = {}
    for r in jobs:
        path_of[os.path.basename(r["file_path"])] = r
    ordered = sorted(path_of.items(), key=lambda kv: kv[0], reverse=True)  # 文件名含时间戳，新→旧
    cutoff = time.time() - retention * 86400
    keep_monthly = {}
    drop = set()
    for i, (fn, job) in enumerate(ordered):
        p = os.path.join(d, fn)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        ym = fn[7:13]  # clinic_YYYYMM...
        keep_monthly.setdefault(ym, job["job_no"])
        if i < 7 or mtime > cutoff or keep_monthly.get(ym) == job["job_no"]:
            continue
        drop.add(job["job_no"])

    all_no = {j["job_no"] for j in jobs}
    for fn, job in ordered:
        if job["job_type"] == "INCR":
            parent = job["parent_job_no"]
            if parent in drop or parent not in all_no:
                drop.add(job["job_no"])      # 孤儿增量：父全量不在 → 一并清理

    removed = 0
    for fn, job in ordered:
        if job["job_no"] not in drop:
            continue
        p = os.path.join(d, fn)
        try:
            os.remove(p)
            removed += 1
        except OSError:
            log.warning("删除过期备份失败：%s", p)
            continue
        try:
            with db.tx() as c:
                db.execute(c, "UPDATE backup_file SET status='DELETED' WHERE job_id=?",
                           (job["id"],))
        except Exception:
            log.warning("标记备份文件 DELETED 失败：job_id=%s", job["id"])
    if removed:
        log.info("清理过期备份 %d 个", removed)
    return removed


def list_backups(limit=50):
    return db.query("""
        SELECT b.id, b.job_no, b.job_type, b.status, b.started_at, b.finished_at,
               b.file_path, b.file_size, b.reason, f.status AS file_status
        FROM backup_job b LEFT JOIN backup_file f ON f.job_id=b.id
        ORDER BY b.id DESC LIMIT ?
    """, (limit,))


def integrity_check_now():
    """F94：对当前库做 integrity_check + foreign_key_check。"""
    ctx = get_ctx()
    t0 = time.time()
    res = db.integrity_check()
    fk = db.foreign_key_check()
    ok = (res == "ok") and not fk
    with db.tx() as c:
        db.execute(c, """
            INSERT INTO integrity_check (checked_at, check_type, target, result, detail_json,
                                         duration_ms, checked_by)
            VALUES (?,'DB',?,?,?,?,?)
        """, (tu.now_str(), db.path(), "OK" if ok else "FAIL",
              audit.dumps({"integrity_check": res, "foreign_key_check": fk}),
              int((time.time() - t0) * 1000), ctx.user_id))
    return {"integrity_check": res, "foreign_key_violations": fk, "ok": ok}
