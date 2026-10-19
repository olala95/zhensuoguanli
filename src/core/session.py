# -*- coding: utf-8 -*-
"""登录 / 会话 / 锁屏（F100）。表所有权：M14 sys_session。

登录事件审计归 M12（audit_login），不写 M14 日志表。
"""

import datetime

from . import db
from . import timeutil as tu
from . import audit
from . import security
from . import config
from .context import get_ctx
from .errors import AppError, PERM_NOT_LOGIN, PERM_ACCOUNT_LOCKED, DATA_NOT_FOUND

# 第五阶段（M13 数据安全与恢复）新增权限点：
#   system.restore（恢复/抢救/解除只读，最危险） system.recovery（崩溃恢复扫描）
#   system.watchdog（AI/硬件看门狗手动触发与查看）
# 第四阶段（M10 打印 / M11 病历归档 / M15 报表）新增权限点：
#   print.create print.view hardware.view hardware.use hardware.manage
#   report.view report.export
#   record.close record.amend record.archive record.seal record.unseal
#   record.export record.hash.verify
# 第三阶段（M07 药房 / M08 处置 / M09 收费）新增权限点：
#   stock.view stock.in stock.manage stock.check stock.loss stock.return dispense.do
#   treatment.view treatment.manage treatment.order treatment.execute
#   finance.view bill.create bill.pay refund.create reversal.create
#   settlement.close charge_item.manage
ROLE_PERMISSIONS = {
    "DOCTOR": {
        "patient.create", "patient.update", "patient.view", "patient.merge",
        "visit.create", "visit.start", "visit.suspend", "visit.resume", "visit.complete",
        "visit.view", "visit.void",
        "record.edit", "record.confirm", "record.view", "record.template.manage",
        "prescription.create", "prescription.edit", "prescription.confirm",
        "prescription.void", "prescription.view", "prescription.template.manage",
        "drug.view", "drug.manage", "herb.view", "herb.manage", "skin_test.record",
        "lab.order.create", "lab.view", "lab.result.entry", "lab.agency.manage",
        "ai.use", "ai.view",
        # M08：医生开立并执行处置（含中医治疗）
        "treatment.view", "treatment.manage", "treatment.order", "treatment.execute",
        # 只读：查库存与账单状态
        "stock.view", "finance.view",
        # M15：医生可看经营报表（单机场景医生多为经营者），导出限管理员
        "report.view",
        # M10：开方后打印处方笺；建档/发药时读身份证与扫码
        "print.create", "print.view", "hardware.view", "hardware.use",
        # M11：医生关闭/修改留痕/归档自己的病历；封存解封与导出只给管理员
        "record.close", "record.amend", "record.archive", "record.hash.verify",
    },
    "PHARMACIST": {
        "patient.view", "visit.view", "record.view",
        "prescription.view", "drug.view", "drug.manage", "herb.view", "herb.manage",
        "lab.view",
        # M07：发药、入库、退药、报损、盘点、拆零
        "stock.view", "stock.in", "stock.manage", "stock.check", "stock.loss",
        "stock.return", "dispense.do",
        # M10：发药后打印处方笺与药袋标签、扫码核对
        "print.create", "print.view", "hardware.view", "hardware.use",
        # M08：皮试等可由药师执行
        "treatment.view", "treatment.execute",
        "finance.view",
    },
    "CASHIER": {
        "patient.view", "patient.create", "patient.update", "visit.view", "record.view",
        "prescription.view", "drug.view", "herb.view", "lab.view",
        # M09：账单、收费、退款、冲正、日结
        "finance.view", "bill.create", "bill.pay", "refund.create", "reversal.create",
        "settlement.close", "charge_item.manage",
        "stock.view", "treatment.view",
        # M10：收费后打印凭条
        "print.create", "print.view", "hardware.view", "hardware.use",
    },
    "ADMIN": {
        "patient.view", "patient.merge", "visit.view", "record.view",
        "prescription.view", "drug.view", "drug.manage", "herb.view", "herb.manage",
        "lab.view", "lab.agency.manage", "ai.view",
        "system.init", "system.config", "system.user", "system.log",
        "system.diagnose", "system.health", "system.backup",
        "system.restore", "system.recovery", "system.watchdog",
        "audit.view",
        # 只读与基础数据维护（不含发药/收费等一线操作）
        "stock.view", "finance.view", "treatment.view", "treatment.manage",
        "charge_item.manage", "settlement.close",
        # M15：报表查看 + 导出（导出属数据外流，只给管理员）
        "report.view", "report.export",
        # M10：硬件登记与管理
        "print.create", "print.view", "hardware.view", "hardware.use", "hardware.manage",
        # M11：管理员可封存/解封/导出，并做 Hash 链巡检；但**无权修改已确认病历内容**
        "record.archive", "record.seal", "record.unseal", "record.export",
        "record.hash.verify",
    },
}


def has_permission(role_code, permission):
    return permission in ROLE_PERMISSIONS.get(role_code, set())


def user_roles(user_id):
    """该账号被授予的全部角色（未删除的）。"""
    if not user_id:
        return []
    try:
        rows = db.query("SELECT role_code FROM sys_user_role "
                        "WHERE user_id=? AND deleted_at IS NULL ORDER BY id", (user_id,))
    except Exception:
        return []
    return [r["role_code"] for r in rows]


def user_permissions(user_id):
    """账号权限 = 其全部角色的并集。

    为什么必须取并集（而不是登录时选定的单一角色）：
      本系统是**单机单用户**，一台电脑一名操作员，一人兼任医生/药师/收费员/管理员。
      早期实现按会话里那个单一 role_code 校验，导致选「医生」就收不了费、发不了药，
      选「管理员」就连建档/写病历都被拒 —— 无论选哪个角色都走不完一次完整门诊。
      「登录角色」只决定**审计默认身份**，不决定能不能干这个活。
    """
    perms = set()
    for rc in user_roles(user_id):
        perms |= ROLE_PERMISSIONS.get(rc, set())
    return perms


def acting_role(user_id, preferred_role, permission):
    """返回行使该权限的角色：优先用登录时选的身份，否则用真正有权的那个角色。

    审计要记录"以什么身份操作"。如果当前身份没这项权限、靠另一个角色才做得了，
    就应当记录那个真正行使职责的角色，而不是记一个无权角色。
    """
    if permission is None:
        return preferred_role
    if has_permission(preferred_role, permission):
        return preferred_role
    for rc in user_roles(user_id):
        if has_permission(rc, permission):
            return rc
    return preferred_role


def has_permission_for_user(user_id, permission):
    return permission in user_permissions(user_id)


def _idle_timeout():
    return config.get_int("session.idle_timeout", 900)


def login(username, password, role_code, client_info=None):
    """登录并创建会话。失败写 audit_login(LOGIN_FAILED)，达阈值锁定账号。"""
    from .logging_setup import get
    log = get("session")
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM sys_user WHERE username=? AND deleted_at IS NULL",
                           (username,))
        if row is None:
            audit.audit_login(conn, "LOGIN_FAILED", username=username, role_code=role_code,
                              result="FAIL", fail_reason="用户不存在", client_info=client_info)
            raise AppError(PERM_NOT_LOGIN, "用户名或口令错误", retryable=False, http_status=401)

        if row["locked_until"] and tu.parse(row["locked_until"]) and \
                tu.parse(row["locked_until"]) > tu.now():
            audit.audit_login(conn, "LOGIN_FAILED", user_id=row["id"], username=username,
                              role_code=role_code, result="FAIL", fail_reason="账号已锁定",
                              client_info=client_info)
            raise AppError(PERM_ACCOUNT_LOCKED,
                           "账号已锁定，解锁时间 %s" % row["locked_until"],
                           retryable=False, http_status=423)

        if not row["is_active"]:
            audit.audit_login(conn, "LOGIN_FAILED", user_id=row["id"], username=username,
                              role_code=role_code, result="FAIL", fail_reason="账号已停用",
                              client_info=client_info)
            raise AppError(PERM_NOT_LOGIN, "账号已停用", retryable=False, http_status=403)

        if not security.verify_password(password, row["password_salt"], row["password_hash"]):
            attempts = (row["failed_attempts"] or 0) + 1
            max_attempts = config.get_int("login.max_attempts", 5)
            locked_until = None
            if attempts >= max_attempts:
                locked_until = tu.fmt(tu.now() + datetime.timedelta(minutes=15))
            db.execute(conn, "UPDATE sys_user SET failed_attempts=?, locked_until=?, "
                             "updated_at=? WHERE id=?",
                       (attempts, locked_until, tu.now_str(), row["id"]))
            audit.audit_login(conn, "LOGIN_FAILED", user_id=row["id"], username=username,
                              role_code=role_code, result="FAIL",
                              fail_reason="口令错误（第 %d 次）" % attempts,
                              client_info=client_info)
            left = max_attempts - attempts
            raise AppError(PERM_NOT_LOGIN,
                           "用户名或口令错误，剩余尝试次数 %d" % max(0, left),
                           retryable=False, http_status=401)

        if role_code not in [r["role_code"] for r in
                             db.query("SELECT role_code FROM sys_user_role "
                                      "WHERE user_id=? AND deleted_at IS NULL", (row["id"],))]:
            # 注：audit_permission 签名为 (conn, required_permission, resource, action, detail, ctx)
            # 早期版本在此多传了一个位置参数，导致 ctx 被 dict 覆盖而抛 AttributeError（500）。
            audit.audit_permission(conn, "role.assign", "sys_user", "login.role",
                                   {"requested": role_code, "user_id": row["id"]})
            raise AppError(PERM_ACCOUNT_LOCKED if False else "E6002",
                           "该用户未被授予角色：%s" % role_code, retryable=False, http_status=403)

        token = security.new_token()
        now = tu.now()
        expires = tu.fmt(now + datetime.timedelta(seconds=_idle_timeout()))
        cur = db.execute(conn, """
            INSERT INTO sys_session (token, user_id, role_code, status, login_at,
                                     last_seen_at, expires_at, idle_timeout, client_info,
                                     created_at)
            VALUES (?,?,?,'ACTIVE',?,?,?,?,?,?)
        """, (token, row["id"], role_code, tu.now_str(), tu.now_str(), expires,
              _idle_timeout(), audit.dumps(client_info or {}), tu.now_str()))
        db.execute(conn, "UPDATE sys_user SET failed_attempts=0, locked_until=NULL, "
                         "last_login_at=?, updated_at=? WHERE id=?",
                   (tu.now_str(), tu.now_str(), row["id"]))
        audit.audit_login(conn, "LOGIN", user_id=row["id"], username=username,
                          role_code=role_code, client_info=client_info)
        log.info("用户登录成功 user=%s role=%s", username, role_code)
        return {
            "token": token,
            "session_id": cur.lastrowid,
            "user_id": row["id"],
            "username": username,
            "display_name": row["display_name"],
            "role_code": role_code,
            "roles": user_roles(row["id"]),
            # 权限按该账号全部角色取并集：单机单用户下一人兼多职，
            # 否则选「医生」就收不了费、选「管理员」就建不了档。
            "permissions": sorted(user_permissions(row["id"])),
            "expires_at": expires,
        }


def resolve(token, allow_locked=False):
    """校验令牌并返回会话信息；过期/登出均拒绝，锁定默认也拒绝。

    allow_locked=True 只给「解锁 / 退出」这类**认证自身**的端点用。
    否则锁屏后连解锁请求都会被 E6005 拦下 → 用户只能重启浏览器，
    锁屏功能等于"把人锁在门外"（这是真 bug，不是设计）。
    """
    if not token:
        raise AppError(PERM_NOT_LOGIN, "未登录", retryable=False, http_status=401)
    row = db.query_one("SELECT * FROM sys_session WHERE token=?", (token,))
    if row is None or row["status"] in ("LOGGED_OUT",):
        raise AppError(PERM_NOT_LOGIN, "会话无效，请重新登录", retryable=False, http_status=401)
    if row["status"] == "LOCKED" and not allow_locked:
        raise AppError("E6005", "会话已锁定，请解锁", retryable=False, http_status=423)
    if row["expires_at"] and tu.parse(row["expires_at"]) and \
            tu.parse(row["expires_at"]) < tu.now():
        with db.tx() as conn:
            db.execute(conn, "UPDATE sys_session SET status='EXPIRED' WHERE id=?", (row["id"],))
            audit.audit_login(conn, "SESSION_EXPIRED", user_id=row["user_id"],
                              role_code=row["role_code"], result="FAIL",
                              fail_reason="空闲超时")
        raise AppError(PERM_NOT_LOGIN, "会话已超时，请重新登录", retryable=False, http_status=401)
    return row


def touch(conn, session_id, token):
    """刷新 last_seen_at 与 expires_at（每次请求调用）。"""
    expires = tu.fmt(tu.now() + datetime.timedelta(seconds=_idle_timeout()))
    db.execute(conn, "UPDATE sys_session SET last_seen_at=?, expires_at=? WHERE id=?",
               (tu.now_str(), expires, session_id))


def lock(token, client_info=None):
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM sys_session WHERE token=?", (token,))
        if row is None:
            raise AppError(PERM_NOT_LOGIN, "会话无效", retryable=False, http_status=401)
        db.execute(conn, "UPDATE sys_session SET status='LOCKED', locked_at=? WHERE id=?",
                   (tu.now_str(), row["id"]))
        audit.audit_login(conn, "LOCK", user_id=row["user_id"], role_code=row["role_code"],
                          client_info=client_info)
    return {"locked": True}


def unlock(token, password):
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM sys_session WHERE token=?", (token,))
        if row is None:
            raise AppError(PERM_NOT_LOGIN, "会话无效", retryable=False, http_status=401)
        user = db.query_one("SELECT * FROM sys_user WHERE id=?", (row["user_id"],))
        if not security.verify_password(password, user["password_salt"], user["password_hash"]):
            audit.audit_login(conn, "LOGIN_FAILED", user_id=user["id"],
                              username=user["username"], role_code=row["role_code"],
                              result="FAIL", fail_reason="解锁口令错误")
            raise AppError(PERM_NOT_LOGIN, "解锁口令错误", retryable=False, http_status=401)
        expires = tu.fmt(tu.now() + datetime.timedelta(seconds=_idle_timeout()))
        db.execute(conn, "UPDATE sys_session SET status='ACTIVE', locked_at=NULL, "
                         "last_seen_at=?, expires_at=? WHERE id=?",
                   (tu.now_str(), expires, row["id"]))
        audit.audit_login(conn, "UNLOCK", user_id=row["user_id"], role_code=row["role_code"])
    return {"unlocked": True}


def logout(token):
    with db.tx() as conn:
        row = db.query_one("SELECT * FROM sys_session WHERE token=?", (token,))
        if row is None:
            return {"logged_out": False}
        db.execute(conn, "UPDATE sys_session SET status='LOGGED_OUT' WHERE id=?", (row["id"],))
        audit.audit_login(conn, "LOGOUT", user_id=row["user_id"], role_code=row["role_code"])
    return {"logged_out": True}
