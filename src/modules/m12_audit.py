# -*- coding: utf-8 -*-
"""M12 安全审计（F84-F89）。

表所有权：audit_operation / audit_data_change / audit_high_risk / audit_login /
         audit_time_anomaly / audit_permission —— **全部只允许 INSERT**
（数据库触发器 + 应用层双重约束，见 04 §3）。
本模块只提供"读"接口与内部写入工具，不提供任何修改接口。
"""

from core import db, audit, timeutil as tu
from core.api import route
from core.errors import require, AppError

_FILTERS = {
    "operations":  ("audit_operation",
                    ["module", "action", "entity_type", "entity_id", "user_id", "result"],
                    "occurred_at"),
    "data_changes": ("audit_data_change",
                     ["table_name", "record_id", "field_name", "user_id"],
                     "occurred_at"),
    "high_risk":   ("audit_high_risk", ["risk_type", "entity_type", "entity_id", "user_id"],
                    "occurred_at"),
    "logins":      ("audit_login", ["event_type", "user_id", "result"], "occurred_at"),
    "permission":  ("audit_permission", ["user_id", "required_permission"], "occurred_at"),
    "time_anomaly": ("audit_time_anomaly", ["anomaly_type"], "detected_at"),
}


def _query(kind, q):
    table, fields, time_col = _FILTERS[kind]
    sql = "SELECT * FROM %s WHERE 1=1" % table
    params = []
    for f in fields:
        if q.get(f):
            sql += " AND %s=?" % f
            params.append(q[f])
    if q.get("date_from"):
        sql += " AND %s>=?" % time_col
        params.append(q["date_from"])
    if q.get("date_to"):
        sql += " AND %s<=?" % time_col
        params.append(q["date_to"] + " 23:59:59")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(int(q.get("limit") or 100), 1000))
    return {"items": db.query(sql, tuple(params))}


@route("/api/audit/operations", "GET", perm="audit.view", module="M12",
       action="audit.operations")
def operations(ctx, body, q):
    return _query("operations", q)


@route("/api/audit/data_changes", "GET", perm="audit.view", module="M12",
       action="audit.data_changes")
def data_changes(ctx, body, q):
    return _query("data_changes", q)


@route("/api/audit/high_risk", "GET", perm="audit.view", module="M12",
       action="audit.high_risk")
def high_risk(ctx, body, q):
    return _query("high_risk", q)


@route("/api/audit/logins", "GET", perm="audit.view", module="M12", action="audit.logins")
def logins(ctx, body, q):
    return _query("logins", q)


@route("/api/audit/permission", "GET", perm="audit.view", module="M12",
       action="audit.permission")
def permission(ctx, body, q):
    return _query("permission", q)


@route("/api/audit/time_anomaly", "GET", perm="audit.view", module="M12",
       action="audit.time_anomaly")
def time_anomaly(ctx, body, q):
    return _query("time_anomaly", q)


@route("/api/audit/summary", "GET", perm="audit.view", module="M12", action="audit.summary")
def summary(ctx, body, q):
    """审计概览：各类审计条数 + 近 7 日趋势。"""
    def c(t):
        row = db.query_one("SELECT COUNT(*) c FROM %s" % t)
        return row["c"] if row else 0
    return {
        "counts": {k: c(v[0]) for k, v in _FILTERS.items()},
        "recent_high_risk": db.query(
            "SELECT * FROM audit_high_risk ORDER BY id DESC LIMIT 10"),
        "today_operations": db.query_one(
            "SELECT COUNT(*) c FROM audit_operation WHERE date(occurred_at)=date('now','localtime')")["c"],
    }


def check_time_anomaly():
    """F88：检测系统时间异常并落审计。启动与每次请求前调用。"""
    anomaly = tu.check_anomaly()
    if not anomaly:
        return None
    try:
        with db.tx() as conn:
            audit.audit_time_anomaly(conn, anomaly)
    except Exception:
        pass
    return anomaly
