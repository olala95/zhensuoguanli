# -*- coding: utf-8 -*-
"""M12 安全审计写入层。契约：03 §5.3，表所有权：04 §2（audit_* 只允许 INSERT）。

调用约定：
  · 必须在 db.tx() 事务内调用，保证"业务写入 + 审计"原子性
  · 时间一律取系统时间，不接受外部传入
  · 审计表有数据库触发器兜底拦截 UPDATE/DELETE
"""

from . import db
from . import timeutil as tu
from .context import get_ctx, dumps

# 高风险操作类型（03 §5.3）
RISK_VOID = "VOID"                  # 作废
RISK_DELETE = "DELETE"              # 删除
RISK_REFUND = "REFUND"              # 退款
RISK_REVERSAL = "REVERSAL"          # 冲正
RISK_UNSEAL = "UNSEAL"              # 解封
RISK_RESTORE = "RESTORE"            # 数据恢复
RISK_PERM_CHANGE = "PERM_CHANGE"    # 权限变更
RISK_CONFIG_CHANGE = "CONFIG_CHANGE"  # 配置变更
RISK_OVERRIDE = "OVERRIDE"          # 规则强制放行（如毒性饮片超量、配伍禁忌确认）
RISK_MERGE = "MERGE"                # 患者合并
RISK_BACKUP_RESTORE = "BACKUP_RESTORE"


def audit_operation(conn, module, action, entity_type=None, entity_id=None,
                    result="SUCCESS", error_code=None, detail=None, ctx=None,
                    occurred_at=None):
    """F84 操作审计。返回新记录 ID（供 audit_data_change 关联）。"""
    ctx = ctx or get_ctx()
    cur = db.execute(conn, """
        INSERT INTO audit_operation
          (occurred_at, module, action, entity_type, entity_id, user_id, role_code,
           source, result, error_code, request_id, session_id, client_info, detail_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        occurred_at or tu.now_str(), module, action, entity_type,
        None if entity_id is None else str(entity_id),
        ctx.user_id, ctx.role_code, ctx.source, result, error_code,
        ctx.request_id, ctx.session_id, dumps(ctx.client_info), dumps(detail),
    ))
    op_id = cur.lastrowid
    if ctx is get_ctx():
        ctx.operation_id = op_id
    return op_id


def audit_data_change(conn, table_name, record_id, field_name, before_value, after_value,
                      reason=None, ctx=None, operation_id=None):
    """F85 字段级变更审计（含 before/after）。值相等时跳过，避免噪声。"""
    if before_value == after_value:
        return None
    ctx = ctx or get_ctx()
    cur = db.execute(conn, """
        INSERT INTO audit_data_change
          (occurred_at, table_name, record_id, field_name, before_value, after_value,
           user_id, role_code, source, reason, operation_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        tu.now_str(), table_name, None if record_id is None else str(record_id), field_name,
        None if before_value is None else str(before_value),
        None if after_value is None else str(after_value),
        ctx.user_id, ctx.role_code, ctx.source, reason,
        operation_id or ctx.operation_id,
    ))
    return cur.lastrowid


def audit_high_risk(conn, risk_type, entity_type=None, entity_id=None, reason=None,
                    detail=None, ctx=None):
    """F86 高风险操作审计。"""
    ctx = ctx or get_ctx()
    cur = db.execute(conn, """
        INSERT INTO audit_high_risk
          (occurred_at, risk_type, entity_type, entity_id, user_id, role_code,
           source, reason, detail_json, operation_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        tu.now_str(), risk_type, entity_type,
        None if entity_id is None else str(entity_id),
        ctx.user_id, ctx.role_code, ctx.source, reason, dumps(detail), ctx.operation_id,
    ))
    return cur.lastrowid


def audit_login(conn, event_type, user_id=None, username=None, role_code=None,
                result="SUCCESS", fail_reason=None, client_info=None, ctx=None):
    """F87 登录 / 登出 / 锁屏 / 解锁 / 登录失败。"""
    ctx = ctx or get_ctx()
    cur = db.execute(conn, """
        INSERT INTO audit_login
          (occurred_at, event_type, user_id, username, role_code, result,
           fail_reason, client_info, session_id)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        tu.now_str(), event_type, user_id, username, role_code, result,
        fail_reason, dumps(client_info or ctx.client_info), ctx.session_id,
    ))
    return cur.lastrowid


def audit_permission(conn, required_permission, resource=None, action=None,
                     detail=None, ctx=None):
    """F89 权限拒绝。越权一律拒绝并留痕，不静默放行。"""
    ctx = ctx or get_ctx()
    cur = db.execute(conn, """
        INSERT INTO audit_permission
          (occurred_at, user_id, role_code, required_permission, resource, action,
           result, detail_json, request_id)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        tu.now_str(), ctx.user_id, ctx.role_code, required_permission, resource,
        action, "DENIED", dumps(detail), ctx.request_id,
    ))
    return cur.lastrowid


def audit_time_anomaly(conn, anomaly):
    """F88 系统时间异常。"""
    cur = db.execute(conn, """
        INSERT INTO audit_time_anomaly
          (detected_at, anomaly_type, last_known_at, current_at, diff_seconds, detail_json)
        VALUES (?,?,?,?,?,?)
    """, (
        tu.now_str(), anomaly.get("anomaly_type"), anomaly.get("last_known_at"),
        anomaly.get("current_at"), anomaly.get("diff_seconds"), dumps(anomaly),
    ))
    return cur.lastrowid


def record_status_log(conn, table, entity_col, entity_id, from_status, to_status,
                      reason=None, ctx=None):
    """通用状态流转日志（03 §2.1：每次转换写入 *_status_log）。"""
    ctx = ctx or get_ctx()
    db.execute(conn, """
        INSERT INTO {t} (entity_type, {col}, from_status, to_status, operator_id,
                         role_code, occurred_at, reason, operation_id)
        VALUES (?,?,?,?,?,?,?,?,?)
    """.format(t=table, col=entity_col), (
        table.replace("_status_log", ""), entity_id, from_status, to_status,
        ctx.user_id, ctx.role_code, tu.now_str(), reason, ctx.operation_id,
    ))
