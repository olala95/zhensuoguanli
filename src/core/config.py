# -*- coding: utf-8 -*-
"""系统配置读写（F101）。表所有权：M14 sys_config。

配置变更属高风险操作（03 §5.3），必须写 audit_data_change + audit_high_risk。
"""

from . import db
from . import timeutil as tu
from . import audit
from .context import get_ctx
from .errors import AppError, DATA_NOT_FOUND

_CACHE = {}


def get(key, default=None, value_type=None, refresh=False):
    if not refresh and key in _CACHE:
        return _CACHE[key]
    row = db.query_one("SELECT cfg_value, cfg_type FROM sys_config "
                       "WHERE cfg_key=? AND deleted_at IS NULL", (key,))
    if row is None:
        return default
    val = row["cfg_value"]
    t = value_type or row["cfg_type"]
    if t == "INT":
        try:
            val = int(val)
        except (TypeError, ValueError):
            val = default
    elif t == "BOOL":
        val = str(val) in ("1", "true", "True", "yes")
    elif t == "JSON":
        import json
        try:
            val = json.loads(val) if val else default
        except ValueError:
            val = default
    _CACHE[key] = val
    return val


def get_int(key, default=0):
    return get(key, default, value_type="INT")


def get_bool(key, default=False):
    return get(key, default, value_type="BOOL")


def all_config(module=None):
    sql = "SELECT cfg_key, cfg_value, cfg_type, module, description, is_secret, updated_at " \
          "FROM sys_config WHERE deleted_at IS NULL"
    params = ()
    if module:
        sql += " AND module=?"
        params = (module,)
    sql += " ORDER BY module, cfg_key"
    return db.query(sql, params)


def set_value(key, value, reason=None):
    """写入配置。返回 (before, after)。"""
    ctx = get_ctx()
    with db.tx() as conn:
        row = db.query_one("SELECT id, cfg_value FROM sys_config WHERE cfg_key=?", (key,))
        before = row["cfg_value"] if row else None
        after = "" if value is None else str(value)
        audit.audit_operation(conn, "M14", "config.set", "sys_config", key,
                              detail={"value": after, "reason": reason})
        if row is None:
            db.execute(conn, """
                INSERT INTO sys_config (cfg_key, cfg_value, cfg_type, module, description,
                                        created_at, created_by, updated_at, updated_by)
                VALUES (?,?,'STRING','M14',?,?,?,?,?)
            """, (key, after, reason, tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        else:
            db.execute(conn, "UPDATE sys_config SET cfg_value=?, updated_at=?, updated_by=? "
                             "WHERE id=?", (after, tu.now_str(), ctx.user_id, row["id"]))
            audit.audit_data_change(conn, "sys_config", row["id"], "cfg_value",
                                    before, after, reason)
        audit.audit_high_risk(conn, audit.RISK_CONFIG_CHANGE, "sys_config", key,
                              reason, {"before": before, "after": after})
    _CACHE.pop(key, None)
    return before, after


def set_many(items, reason=None):
    """批量写入配置（单事务）。items: {key: value}"""
    out = []
    ctx = get_ctx()
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "config.set_many", "sys_config", None,
                              detail={"keys": list(items.keys()), "reason": reason})
        for key, value in items.items():
            row = db.query_one("SELECT id, cfg_value FROM sys_config WHERE cfg_key=?", (key,))
            before = row["cfg_value"] if row else None
            after = "" if value is None else str(value)
            if row is None:
                db.execute(conn, """
                    INSERT INTO sys_config (cfg_key, cfg_value, cfg_type, module, description,
                                            created_at, created_by, updated_at, updated_by)
                    VALUES (?,?,'STRING','M14',?,?,?,?,?)
                """, (key, after, reason, tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
            else:
                db.execute(conn, "UPDATE sys_config SET cfg_value=?, updated_at=?, updated_by=? "
                                 "WHERE id=?", (after, tu.now_str(), ctx.user_id, row["id"]))
                audit.audit_data_change(conn, "sys_config", row["id"], "cfg_value",
                                        before, after, reason)
            audit.audit_high_risk(conn, audit.RISK_CONFIG_CHANGE, "sys_config", key, reason,
                                  {"before": before, "after": after})
            _CACHE.pop(key, None)
            out.append({"key": key, "before": before, "after": after})
    return out


def reload_cache():
    _CACHE.clear()
