# -*- coding: utf-8 -*-
"""编号生成器。契约：03 §1。

· 编号 = 前缀 + 日期 + 流水，字符串，全局唯一
· 在事务内取号（防止重号）；允许跳号（回滚时），绝不允许重号
· 编号一经生成不可修改
"""

from . import db
from . import timeutil as tu

# (前缀, 日期格式, 流水位数)
SEQ_SPEC = {
    "PATIENT":        ("P",  "%Y%m%d",     4),
    "VISIT":          ("V",  "%Y%m%d",     3),
    "PRESCRIPTION":   ("R",  "%Y%m%d",     4),
    "LAB_ORDER":      ("L",  "%Y%m%d",     4),
    "SPECIMEN":       ("BC", "%Y%m%d",     6),
    "BILL":           ("B",  "%Y%m%d",     4),
    "REFUND":         ("RF", "%Y%m%d",     4),
    "REVERSAL":       ("RV", "%Y%m%d",     4),
    "DAY_SETTLEMENT": ("DS", "%Y%m%d",     2),
    "STOCK_IN":       ("IN", "%Y%m%d",     4),
    "BATCH":          ("BT", "%Y%m%d",     6),
    "STOCK_CHECK":    ("CK", "%Y%m%d",     4),
    "STOCK_LOSS":     ("LS", "%Y%m%d",     4),
    "DRUG_RETURN":    ("DR", "%Y%m%d",     4),
    "TREATMENT_EXEC": ("TE", "%Y%m%d",     4),
    "PRINT_JOB":      ("PJ", "%Y%m%d%H%M%S", 3),
    "ARCHIVE":        ("AR", "%Y%m",       4),
    "AI_TASK":        ("AI", "%Y%m%d%H%M%S", 6),
    "IMPORT_JOB":     ("IM", "%Y%m%d",     4),
    "BACKUP_JOB":     ("BK", "%Y%m%d",     4),
    "RESTORE":        ("RS", "%Y%m%d",     4),
}


def next_no(conn, seq_type, biz_date=None):
    """在事务内取号。conn 必须是 db.tx() 提供的写连接。"""
    if seq_type not in SEQ_SPEC:
        raise KeyError("未定义的编号类型：%s" % seq_type)
    prefix, date_fmt, width = SEQ_SPEC[seq_type]
    d = biz_date or tu.now().strftime(date_fmt)
    cur = db.execute(conn,
                     "UPDATE sys_sequence SET current_val = current_val + 1 "
                     "WHERE biz_date=? AND seq_type=?", (d, seq_type))
    if cur.rowcount == 0:
        db.execute(conn,
                   "INSERT INTO sys_sequence (biz_date, seq_type, current_val, updated_at) "
                   "VALUES (?,?,1,?)", (d, seq_type, tu.now_str()))
        val = 1
    else:
        # db.query 在事务内走写连接，可直接读到刚 UPDATE 的值
        row = db.query_one("SELECT current_val FROM sys_sequence "
                           "WHERE biz_date=? AND seq_type=?", (d, seq_type))
        val = row["current_val"]
    return "%s%s%s" % (prefix, d, str(val).zfill(width))


def peek_current(seq_type, biz_date=None):
    """只读查看当前流水值（不取号）。"""
    prefix, date_fmt, _ = SEQ_SPEC[seq_type]
    d = biz_date or tu.now().strftime(date_fmt)
    row = db.query_one("SELECT current_val FROM sys_sequence WHERE biz_date=? AND seq_type=?",
                       (d, seq_type))
    return row["current_val"] if row else 0
