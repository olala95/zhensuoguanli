# -*- coding: utf-8 -*-
"""SQLite 连接与事务。契约来源：05_RUNTIME_TOPOLOGY §3。

硬约定：
  · journal_mode = WAL
  · synchronous  = NORMAL
  · busy_timeout = 5000
  · foreign_keys = ON（每个连接强制）
  · 主进程持有唯一写连接 + 线程局部读连接；AI Worker / Hardware Agent 零连接（单写者原则）
"""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager

from .errors import AppError, SYSTEM_DB_BUSY, SYSTEM_DB_CORRUPT, SYSTEM_DB_IOERR, SYSTEM_DISK_FULL

_write_conn = None
_write_lock = threading.RLock()
_local = threading.local()
_db_path = None
_readonly_mode = False
_write_seq = 0          # 已提交写事务计数（F91：判断"上次备份后是否有变化"）


def init(db_path, readonly=False):
    """打开写连接并应用 PRAGMA。readonly=True 时为安全模式：禁止业务写。"""
    global _write_conn, _db_path, _readonly_mode
    _db_path = db_path
    _readonly_mode = readonly
    d = os.path.dirname(db_path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    _write_conn = conn
    return conn


def _apply_pragmas(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


def path():
    return _db_path


def is_readonly():
    return _readonly_mode


def write_seq():
    """已提交的写事务序号。备份用它判断"距上次备份有无实际变化"（F91 R3）。"""
    return _write_seq


def set_readonly(flag):
    global _readonly_mode
    _readonly_mode = flag


def write_conn():
    if _write_conn is None:
        raise AppError(SYSTEM_DB_BUSY, "数据库未初始化", retryable=True, http_status=500)
    return _write_conn


def read_conn():
    """线程局部读连接（WAL 下读写不互斥）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_db_path, timeout=5.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn)
        _local.conn = conn
    return conn


def close_all():
    global _write_conn
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None
    if _write_conn is not None:
        try:
            _write_conn.close()
        except Exception:
            pass
        _write_conn = None


def _in_tx():
    return getattr(_local, "tx_depth", 0) > 0


@contextmanager
def tx(force=False):
    """一个业务操作 = 一个事务。支持嵌套（内层退化为 SAVEPOINT）。

    事务内禁止：用户交互、网络调用、打印、AI 推理。

    force=True 只能在**恢复链路**（M13 数据恢复）里使用：系统因怀疑损坏而冻结为只读后，
    恢复动作本身还要写 restore_no / restore_log / 审计，否则"冻结后就再也恢复不了"。
    """
    global _write_seq
    if _readonly_mode and not force:
        raise AppError(SYSTEM_DB_CORRUPT, "系统处于安全模式（只读），禁止业务写入",
                       retryable=False, http_status=503)
    conn = write_conn()
    depth = getattr(_local, "tx_depth", 0)
    with _write_lock:
        # BEGIN/SAVEPOINT 也要翻译：否则写锁超时会裸抛 sqlite3.OperationalError，
        # 上层拿不到 E1001，无法区分"可重试"与"致命错误"（演练 10）
        try:
            if depth == 0:
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute("SAVEPOINT sp_%d" % (depth + 1))
        except sqlite3.Error as exc:
            raise _translate(exc)
        _local.tx_depth = depth + 1
        try:
            yield conn
        except Exception:
            try:
                if depth == 0:
                    conn.execute("ROLLBACK")
                else:
                    conn.execute("ROLLBACK TO SAVEPOINT sp_%d" % (depth + 1))
            except Exception:
                pass
            _local.tx_depth = depth
            raise
        else:
            try:
                if depth == 0:
                    conn.execute("COMMIT")
                    _write_seq += 1
                else:
                    conn.execute("RELEASE SAVEPOINT sp_%d" % (depth + 1))
            except sqlite3.Error as exc:
                _local.tx_depth = depth
                raise _translate(exc)
            except Exception:
                _local.tx_depth = depth
                raise
            _local.tx_depth = depth


def _translate(exc):
    """把 sqlite3 异常翻译成带异常码的 AppError（R6：禁止静默吞异常）。"""
    msg = str(exc)
    # 触发器 RAISE(ABORT, 'E2006: ...') 透传异常码，避免被统一包成 E1004
    m = re.match(r"^(E\d{4}):\s*(.+)$", msg.strip())
    if m:
        return AppError(m.group(1), m.group(2), retryable=False, http_status=400,
                        detail={"raw": msg})
    low = msg.lower()
    if "database is locked" in low or "database is busy" in low:
        return AppError(SYSTEM_DB_BUSY, "数据库忙，请稍后重试", retryable=True, http_status=503,
                        detail={"raw": msg})
    if "database disk image is malformed" in low or "corrupt" in low:
        return AppError(SYSTEM_DB_CORRUPT, "数据库文件损坏，已进入只读模式并触发恢复流程",
                        retryable=False, http_status=500, detail={"raw": msg})
    if "database or disk is full" in low or "disk i/o error" in low and "full" in low:
        return AppError(SYSTEM_DISK_FULL, "磁盘空间不足，禁止继续写入", retryable=False,
                        http_status=507, detail={"raw": msg})
    if "disk i/o error" in low:
        return AppError(SYSTEM_DB_IOERR, "磁盘 I/O 错误", retryable=True, http_status=500,
                        detail={"raw": msg})
    if "foreign key constraint failed" in low:
        return AppError("E2003", "存在关联数据，操作被拒绝（外键约束）", retryable=False,
                        http_status=400, detail={"raw": msg})
    if "unique constraint failed" in low:
        return AppError("E2004", "存在重复记录（唯一约束）", retryable=False, http_status=409,
                        detail={"raw": msg})
    return AppError(SYSTEM_DB_IOERR, "数据库错误：%s" % msg, retryable=True, http_status=500,
                    detail={"raw": msg})


def _conn_for_read():
    """事务内必须走写连接，否则读不到本事务未提交的写入（read-your-own-writes）。"""
    if getattr(_local, "tx_depth", 0) > 0:
        return write_conn()
    return read_conn()


def query(sql, params=()):
    """只读查询，返回 dict 列表。"""
    try:
        cur = _conn_for_read().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error as exc:
        raise _translate(exc)


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(conn, sql, params=()):
    """在给定写连接上执行（调用方须在 tx() 内）。返回 cursor。"""
    try:
        return conn.execute(sql, params)
    except sqlite3.Error as exc:
        raise _translate(exc)


def executemany(conn, sql, seq):
    try:
        return conn.executemany(sql, seq)
    except sqlite3.Error as exc:
        raise _translate(exc)


def checkpoint_truncate(conn=None):
    """备份前必做：PRAGMA wal_checkpoint(TRUNCATE)，防止 WAL 无限增长。"""
    conn = conn or write_conn()
    try:
        return conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        raise _translate(exc)


def integrity_check(conn=None):
    conn = conn or write_conn()
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "unknown"
    except sqlite3.Error as exc:
        raise _translate(exc)


def foreign_key_check(conn=None):
    conn = conn or write_conn()
    try:
        return [dict(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
    except sqlite3.Error as exc:
        raise _translate(exc)


def table_exists(name):
    row = query_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return row is not None
