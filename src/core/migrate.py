# -*- coding: utf-8 -*-
"""迁移执行器。契约：04 §5。

· 脚本放 migrations/，命名 {4位序号}_{简述}.sql
· 只增不改：已执行脚本禁止修改，需要修正就新增下一个序号
· 迁移在事务内执行，失败整体回滚
· 执行前强制触发一次全量备份（05 §6）
"""

import hashlib
import os
import sqlite3
import time

from . import db
from . import timeutil as tu
from .errors import AppError, SYSTEM_MIGRATION_FAILED
from .context import get_ctx

MIGRATIONS_DIR = None


def init(migrations_dir):
    global MIGRATIONS_DIR
    MIGRATIONS_DIR = migrations_dir


def _ensure_meta(conn):
    db.execute(conn, """
        CREATE TABLE IF NOT EXISTS sys_migration (
            id INTEGER PRIMARY KEY, seq_no TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
            checksum TEXT NOT NULL, executed_at TEXT NOT NULL, execution_ms INTEGER,
            executed_by INTEGER)
    """)


def applied(conn):
    _ensure_meta(conn)
    rows = db.query("SELECT seq_no, checksum FROM sys_migration ORDER BY seq_no")
    return {r["seq_no"]: r["checksum"] for r in rows}


def split_sql(script):
    """把脚本切成语句列表（不用 executescript —— 它会隐式提交，破坏事务原子性）。

    使用 sqlite3.complete_statement 逐行累积，可正确处理 CREATE TRIGGER 体内的分号。
    """
    stmts = []
    buf = ""
    for line in script.splitlines(keepends=True):
        if not line.strip():
            buf += line
            continue
        buf += line
        try:
            done = sqlite3.complete_statement(buf)
        except Exception:
            done = buf.rstrip().endswith(";")
        if done:
            s = buf.strip()
            if s:
                stmts.append(s)
            buf = ""
    if buf.strip():
        stmts.append(buf.strip())
    return stmts


def _scripts():
    if not MIGRATIONS_DIR or not os.path.isdir(MIGRATIONS_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(MIGRATIONS_DIR)):
        if fn.endswith(".sql"):
            out.append((fn.split("_")[0], fn))
    return out


def run(auto_backup=True):
    """执行所有未应用的迁移。返回本次执行的脚本清单。"""
    if auto_backup:
        try:
            from . import backup
            applied_now = applied(db.write_conn())
            if _scripts() and len(applied_now) < len(_scripts()):
                backup.full_backup(reason="BEFORE_MIGRATION")
        except Exception:
            # 备份失败不阻断迁移，但必须留痕（R6）
            from .logging_setup import get
            get("migrate").exception("迁移前备份失败，继续迁移（已记录）")

    done = []
    conn = db.write_conn()
    already = applied(conn)
    for seq_no, fn in _scripts():
        full = os.path.join(MIGRATIONS_DIR, fn)
        with open(full, "r", encoding="utf-8") as f:
            sql = f.read()
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if seq_no in already:
            if already[seq_no] != checksum:
                raise AppError(SYSTEM_MIGRATION_FAILED,
                               "迁移脚本已被修改：%s（禁止修改已执行脚本）" % fn,
                               retryable=False, http_status=500)
            continue
        t0 = time.time()
        try:
            with db.tx() as c:
                for stmt in split_sql(sql):
                    db.execute(c, stmt)
                db.execute(c, """
                    INSERT INTO sys_migration (seq_no, filename, checksum, executed_at,
                                               execution_ms, executed_by)
                    VALUES (?,?,?,?,?,?)
                """, (seq_no, fn, checksum, tu.now_str(),
                      int((time.time() - t0) * 1000), get_ctx().user_id))
        except Exception as exc:
            if isinstance(exc, AppError):
                raise
            raise AppError(SYSTEM_MIGRATION_FAILED, "迁移失败：%s（%s）" % (fn, exc),
                           retryable=False, http_status=500, detail={"file": fn})
        done.append({"seq_no": seq_no, "filename": fn})
    return done
