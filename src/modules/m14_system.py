# -*- coding: utf-8 -*-
"""M14 系统管理（F99 初始化 / F100 登录锁屏 / F101 配置 / F102 用户角色 /
F103 系统日志 / F104 诊断 / F105 健康检查 / F106 更新 / F107 安全退出 / F128 基础数据包导入）。

表所有权：sys_config / sys_user / sys_role / sys_user_role / sys_session /
         sys_sequence / sys_migration / sys_dict / sys_health_check /
         sys_diagnostic / sys_import_job
注意：登录日志归 M12（audit_login），本模块不写。
"""

import csv
import os

from core import db, audit, config, security, session as sess, ids
from core import timeutil as tu, backup, selftest, migrate
from core.api import route
from core.errors import require, require_keys, AppError, conflict, not_found

APP_VERSION = "1.0.0"

# 各数据类型对应的目标表与所属模块（F128）
IMPORT_TARGETS = {
    "ICD10":        ("sys_dict", "M14"),
    "DICT":         ("sys_dict", "M14"),
    "DRUG":         ("drug", "M06"),
    "HERB":         ("herb", "M06"),
    "CHARGE_ITEM":  ("charge_item", "M09"),
    "TREATMENT":    ("treatment_item", "M08"),
    "SUPPLIER":     ("supplier", "M07"),
}


def _user_count():
    row = db.query_one("SELECT COUNT(*) c FROM sys_user WHERE deleted_at IS NULL")
    return row["c"] if row else 0


@route("/api/system/status", "GET", module="M14", action="system.status", public=True)
def status(ctx, body, q):
    """F99/F105：系统状态。未初始化时引导初始化向导。"""
    try:
        applied = migrate.applied(db.write_conn())
        pending = [s[0] for s in migrate._scripts() if s[0] not in applied]
    except Exception:
        applied, pending = {}, []
    last = None
    if db.table_exists("backup_job"):
        last = db.query_one("SELECT started_at, status, file_path FROM backup_job "
                            "ORDER BY id DESC LIMIT 1")
    return {
        "version": APP_VERSION,
        "initialized": _user_count() > 0,
        "safe_mode": db.is_readonly(),
        "clinic_name": config.get("clinic.name", ""),
        "db_path": db.path(),
        "migrations_applied": len(applied),
        "migrations_pending": pending,
        "last_backup": last,
        "server_time": tu.now_str(),
    }


@route("/api/system/init", "POST", module="M14", action="system.init", public=True)
def init_system(ctx, body, q):
    """F99 系统初始化：仅在尚无用户时可执行一次。"""
    require_keys(body, ["admin_username", "admin_password", "admin_name"])
    if _user_count() > 0:
        raise conflict("系统已初始化", code="E3001")
    ok, msg = security.validate_password_policy(body["admin_password"])
    require(ok, msg)
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "system.init", "sys_user", None)
        salt = security.new_salt()
        cur = db.execute(conn, """
            INSERT INTO sys_user (username, display_name, password_hash, password_salt,
                is_active, pwd_changed_at, created_at, created_by, updated_at, updated_by)
            VALUES (?,?,?,?,1,?,?,?,?,?)
        """, (body["admin_username"].strip(), body["admin_name"].strip(),
              security.hash_password(body["admin_password"], salt), salt,
              tu.now_str(), tu.now_str(), 0, tu.now_str(), 0))
        uid = cur.lastrowid
        db.execute(conn, "INSERT INTO sys_user_role (user_id, role_code, created_at, created_by) "
                         "VALUES (?,'ADMIN',?,0)", (uid, tu.now_str()))
        if body.get("clinic_name"):
            db.execute(conn, "UPDATE sys_config SET cfg_value=?, updated_at=? WHERE cfg_key='clinic.name'",
                       (body["clinic_name"], tu.now_str()))
        config.reload_cache()
        audit.audit_high_risk(conn, audit.RISK_PERM_CHANGE, "sys_user", uid,
                              "系统初始化：创建管理员账号")
    return {"initialized": True, "admin_user_id": uid}


@route("/api/auth/login", "POST", module="M14", action="auth.login", public=True)
def login(ctx, body, q):
    """F100 登录。"""
    require_keys(body, ["username", "password", "role_code"])
    return sess.login(body["username"].strip(), body["password"], body["role_code"],
                      client_info={"ua": ctx.client_info.get("ua")})


@route("/api/auth/logout", "POST", module="M14", action="auth.logout",
       allow_locked=True)
def logout(ctx, body, q):
    token = body.get("token") or ctx.client_info.get("token")
    return sess.logout(token or _token_from_ctx(ctx))


@route("/api/auth/lock", "POST", module="M14", action="auth.lock")
def lock(ctx, body, q):
    return sess.lock(_token_from_ctx(ctx))


@route("/api/auth/unlock", "POST", module="M14", action="auth.unlock",
       allow_locked=True)
def unlock(ctx, body, q):
    """解锁。**必须 allow_locked**，否则锁屏后这个接口自己也被拦，等于把用户锁死。"""
    require_keys(body, ["password"])
    return sess.unlock(_token_from_ctx(ctx), body["password"])


def _token_from_ctx(ctx):
    ctx_token = getattr(ctx, "token", None)
    if ctx_token:
        return ctx_token
    row = db.query_one("SELECT token FROM sys_session WHERE id=?", (ctx.session_id,))
    return row["token"] if row else ""


@route("/api/system/config", "GET", perm="system.config", module="M14", action="system.config")
def get_config(ctx, body, q):
    return {"items": config.all_config(q.get("module"))}


@route("/api/system/config/set", "POST", perm="system.config", module="M14",
       action="system.config.set")
def set_config(ctx, body, q):
    require(body.get("key"), "缺少 key")
    before, after = config.set_value(body["key"], body.get("value"), body.get("reason"))
    return {"key": body["key"], "before": before, "after": after}


@route("/api/system/users", "GET", perm="system.user", module="M14", action="system.users")
def users(ctx, body, q):
    rows = db.query("""
        SELECT u.id, u.username, u.display_name, u.employee_no, u.title, u.is_active,
               u.failed_attempts, u.locked_until, u.last_login_at, u.created_at,
               (SELECT group_concat(role_code) FROM sys_user_role r
                 WHERE r.user_id=u.id AND r.deleted_at IS NULL) AS roles
        FROM sys_user u WHERE u.deleted_at IS NULL ORDER BY u.id
    """)
    return {"items": rows}


@route("/api/system/roles", "GET", perm="system.user", module="M14", action="system.roles")
def roles(ctx, body, q):
    return {"items": db.query("SELECT * FROM sys_role WHERE deleted_at IS NULL")}


@route("/api/system/user/create", "POST", perm="system.user", module="M14",
       action="system.user.create")
def user_create(ctx, body, q):
    require_keys(body, ["username", "display_name", "password"])
    ok, msg = security.validate_password_policy(body["password"])
    require(ok, msg)
    role_codes = body.get("roles") or []
    require(role_codes, "至少分配一个角色")
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "system.user.create", "sys_user", None,
                              detail={"username": body["username"], "roles": role_codes})
        salt = security.new_salt()
        cur = db.execute(conn, """
            INSERT INTO sys_user (username, display_name, password_hash, password_salt,
                employee_no, title, is_active, pwd_changed_at, created_at, created_by,
                updated_at, updated_by)
            VALUES (?,?,?,?,?,?,1,?,?,?,?,?)
        """, (body["username"].strip(), body["display_name"].strip(),
              security.hash_password(body["password"], salt), salt,
              body.get("employee_no"), body.get("title"),
              tu.now_str(), tu.now_str(), ctx.user_id, tu.now_str(), ctx.user_id))
        uid = cur.lastrowid
        for rc in role_codes:
            db.execute(conn, "INSERT OR IGNORE INTO sys_user_role (user_id, role_code, "
                             "created_at, created_by) VALUES (?,?,?,?)",
                       (uid, rc, tu.now_str(), ctx.user_id))
        audit.audit_high_risk(conn, audit.RISK_PERM_CHANGE, "sys_user", uid,
                              "创建用户并分配角色", {"roles": role_codes})
    return {"id": uid}


@route("/api/system/user/roles", "POST", perm="system.user", module="M14",
       action="system.user.roles")
def user_roles(ctx, body, q):
    """F102 角色分配：变更写高风险审计。"""
    uid = body.get("user_id")
    require(uid, "缺少 user_id")
    role_codes = body.get("roles") or []
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "system.user.roles", "sys_user", uid,
                              detail={"roles": role_codes})
        before = [r["role_code"] for r in db.query(
            "SELECT role_code FROM sys_user_role WHERE user_id=? AND deleted_at IS NULL", (uid,))]
        db.execute(conn, "UPDATE sys_user_role SET deleted_at=? WHERE user_id=?",
                   (tu.now_str(), uid))
        for rc in role_codes:
            # 先尝试复活已有的软删除行（ux_user_role 唯一索引不含 deleted_at，
            # 直接 INSERT OR IGNORE 会被软删除残留行挡掉，导致角色越配越少）
            cur = db.execute(conn, "UPDATE sys_user_role SET deleted_at=NULL, created_by=? "
                                   "WHERE user_id=? AND role_code=?", (ctx.user_id, uid, rc))
            if cur.rowcount == 0:
                db.execute(conn, "INSERT INTO sys_user_role (user_id, role_code, "
                                 "created_at, created_by) VALUES (?,?,?,?)",
                           (uid, rc, tu.now_str(), ctx.user_id))
        audit.audit_data_change(conn, "sys_user_role", uid, "roles", ",".join(before),
                                ",".join(role_codes), body.get("reason"))
        audit.audit_high_risk(conn, audit.RISK_PERM_CHANGE, "sys_user", uid,
                              "角色变更", {"before": before, "after": role_codes})
    return {"user_id": uid, "roles": role_codes}


@route("/api/system/user/disable", "POST", perm="system.user", module="M14",
       action="system.user.disable")
def user_disable(ctx, body, q):
    uid = body.get("user_id")
    require(uid, "缺少 user_id")
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "system.user.disable", "sys_user", uid)
        db.execute(conn, "UPDATE sys_user SET is_active=?, updated_at=?, updated_by=? "
                         "WHERE id=?", (0 if body.get("disable", True) else 1,
                                        tu.now_str(), ctx.user_id, uid))
        audit.audit_high_risk(conn, audit.RISK_PERM_CHANGE, "sys_user", uid,
                              "停用/启用账号")
    return {"user_id": uid, "is_active": 0 if body.get("disable", True) else 1}


@route("/api/system/user/reset_password", "POST", perm="system.user", module="M14",
       action="system.user.reset_password")
def reset_password(ctx, body, q):
    uid = body.get("user_id")
    new_pwd = body.get("new_password")
    require(uid and new_pwd, "缺少 user_id / new_password")
    ok, msg = security.validate_password_policy(new_pwd)
    require(ok, msg)
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "system.user.reset_password", "sys_user", uid)
        salt = security.new_salt()
        db.execute(conn, "UPDATE sys_user SET password_hash=?, password_salt=?, "
                         "pwd_changed_at=?, failed_attempts=0, locked_until=NULL, "
                         "updated_at=?, updated_by=? WHERE id=?",
                   (security.hash_password(new_pwd, salt), salt, tu.now_str(),
                    tu.now_str(), ctx.user_id, uid))
        audit.audit_high_risk(conn, audit.RISK_PERM_CHANGE, "sys_user", uid, "重置口令")
    return {"user_id": uid}


@route("/api/system/diagnose", "GET", perm="system.diagnose", module="M14",
       action="system.diagnose")
def diagnose(ctx, body, q):
    """F104 系统诊断：数据库 / 磁盘 / 备份 / 权限 / 运行时。"""
    from core.logging_setup import log_dir
    items = []
    # 数据库
    res = db.integrity_check()
    items.append({"diag_type": "DB", "target": db.path(),
                  "result": "OK" if res == "ok" else "FAIL", "detail": res})
    fk = db.foreign_key_check()
    items.append({"diag_type": "DB_FK", "target": db.path(),
                  "result": "OK" if not fk else "FAIL", "detail": "%d 条外键违规" % len(fk)})
    violations = selftest.check_invariants()
    items.append({"diag_type": "INVARIANT", "target": "cross-table",
                  "result": "OK" if not violations else "FAIL",
                  "detail": violations or "一致"})
    # 磁盘
    free = selftest._free_mb(os.path.dirname(os.path.abspath(db.path() or ".")))
    items.append({"diag_type": "DISK", "target": os.path.dirname(db.path() or "."),
                  "result": "OK" if (free or 0) >= selftest.DISK_FREE_WARN_MB else "WARN",
                  "detail": "剩余 %.0f MB" % (free or 0)})
    # 备份
    last = db.query_one("SELECT status, started_at FROM backup_job ORDER BY id DESC LIMIT 1")
    items.append({"diag_type": "BACKUP", "target": "backup",
                  "result": "OK" if last and last["status"] == "SUCCEEDED" else "WARN",
                  "detail": ("最近备份 %s (%s)" % (last["started_at"], last["status"]))
                            if last else "尚无备份"})
    with db.tx() as conn:
        audit.audit_operation(conn, "M14", "system.diagnose", "sys_diagnostic", None)
        for it in items:
            db.execute(conn, """
                INSERT INTO sys_diagnostic (diag_type, target, result, detail_json,
                                            created_at, created_by)
                VALUES (?,?,?,?,?,?)
            """, (it["diag_type"], it["target"], it["result"], audit.dumps(it["detail"]),
                  tu.now_str(), ctx.user_id))
    return {"items": items, "log_dir": log_dir()}


@route("/api/system/health", "GET", perm="system.health", module="M14", action="system.health")
def health(ctx, body, q):
    """F105 健康检查：最近一次启动自检结果 + 在线快检。"""
    latest = db.query("""
        SELECT check_item, result, detail, checked_at FROM sys_health_check
        WHERE id > (SELECT COALESCE(MAX(id),0)-20 FROM sys_health_check)
        ORDER BY id DESC LIMIT 20
    """)
    quick = {"db_writable": not db.is_readonly(), "server_time": tu.now_str()}
    return {"latest": latest, "quick": quick}


@route("/api/system/logs", "GET", perm="system.log", module="M14", action="system.logs")
def logs(ctx, body, q):
    """F103 系统日志：读取最近 N 行应用日志（日志可轮转，与审计区分）。"""
    from core.logging_setup import log_dir
    which = q.get("type") or "app"
    path = os.path.join(log_dir() or ".", "%s.log" % which)
    if not os.path.isfile(path):
        return {"items": [], "path": path}
    n = min(int(q.get("lines") or 200), 2000)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-n:]
    return {"items": [ln.rstrip("\n") for ln in lines], "path": path}


@route("/api/system/backup", "POST", perm="system.backup", module="M14", action="system.backup")
def do_backup(ctx, body, q):
    """F92 手动备份（F90 自动备份由启动流程触发）。"""
    return backup.full_backup(reason=body.get("reason") or "MANUAL")


@route("/api/system/backups", "GET", perm="system.backup", module="M14", action="system.backups")
def list_backups(ctx, body, q):
    return {"items": backup.list_backups(min(int(q.get("limit") or 50), 200))}


@route("/api/system/integrity", "POST", perm="system.health", module="M14",
       action="system.integrity")
def integrity(ctx, body, q):
    """F94 完整性检查。"""
    return backup.integrity_check_now()


@route("/api/system/update", "GET", perm="system.config", module="M14", action="system.update")
def update_info(ctx, body, q):
    """F106 系统更新：本报告版本与迁移状态（实际升级包分发不在本机范围）。"""
    applied = migrate.applied(db.write_conn())
    return {"current_version": APP_VERSION,
            "migrations_applied": sorted(applied.keys()),
            "migrations_pending": [s[0] for s in migrate._scripts() if s[0] not in applied]}


@route("/api/system/shutdown", "POST", perm="system.config", module="M14",
       action="system.shutdown")
def shutdown(ctx, body, q):
    """F107 安全退出：checkpoint → 可选备份 → 关闭连接 → 停止服务。"""
    from core import runtime
    return runtime.request_shutdown(backup_on_exit=body.get("backup", True))


# ---------- F128 基础数据包导入 ----------

# 标准模板：表头（英文 key / 中文别名都支持）+ 必填列 + 示例行（F128 R2）
IMPORT_TEMPLATE = {
    "DRUG": (["drug_code", "generic_name", "trade_name", "pinyin", "spec", "dosage_form",
              "unit", "manufacturer", "price_yuan", "drug_class", "is_antibiotic",
              "is_injection", "need_skin_test", "barcode"],
             ["drug_code", "generic_name", "spec", "dosage_form", "unit"],
             ["D0001", "布洛芬片", "芬必得", "buluofenpian", "0.1g×24片", "片剂", "盒",
              "某制药", "12.50", "解热镇痛", "0", "0", "0", "6900001"]),
    "HERB": (["herb_code", "name", "pinyin", "alias", "processed_spec", "unit",
              "price_milli_per_g", "is_toxic", "max_dose_g", "barcode"],
             ["herb_code", "name"],
             ["H0001", "黄芪", "huangqi", "绵黄芪", "生", "g", "50", "0", "30", "7900001"]),
    "CHARGE_ITEM": (["item_code", "name", "alias", "pinyin", "category", "unit",
                     "price_yuan", "auto_add_on_visit"],
                    ["item_code", "name", "price_yuan"],
                    ["GH01", "挂号费", "挂号", "guahaofei", "REGISTRATION", "次", "10.00", "1"]),
    "TREATMENT": (["item_code", "name", "pinyin", "category", "unit", "price_yuan",
                   "duration_minutes"],
                  ["item_code", "name"],
                  ["ZJ01", "针灸", "zhenjiu", "ACUPUNCTURE", "次", "60.00", "30"]),
    "SUPPLIER": (["supplier_code", "name", "contact", "phone", "license_no"],
                 ["supplier_code", "name"],
                 ["S001", "某药材公司", "张经理", "13800000000", "LIC-001"]),
    "ICD10": (["code", "name", "pinyin"], ["code", "name"],
              ["J06.9", "急性上呼吸道感染", "jixing"]),
    "DICT": (["dict_type", "dict_code", "dict_label"], ["dict_type", "dict_code",
                                                        "dict_label"],
             ["DEPARTMENT", "TCM", "中医科"]),
}

# 分批大小（F128 R6）
IMPORT_BATCH = 500
# 文件大小上限 20MB（F128 异常码 E2007）
IMPORT_MAX_BYTES = 20 * 1024 * 1024


def _read_rows(path, encoding="utf-8-sig"):
    """解析 CSV。首行为表头，返回 (rows, header)。"""
    size = os.path.getsize(path)
    if size > IMPORT_MAX_BYTES:
        raise AppError("E2007", "文件过大（%.1f MB > %d MB），请拆分后导入"
                       % (size / 1024.0 / 1024, IMPORT_MAX_BYTES // 1024 // 1024),
                       retryable=False, http_status=400)
    try:
        with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
            rd = csv.DictReader(f)
            header = rd.fieldnames or []
            rows = list(rd)
    except UnicodeDecodeError as exc:
        raise AppError("E2006", "文件编码无法解析（请用 UTF-8 CSV）：%s" % exc,
                       retryable=False, http_status=400)
    if not header:
        raise AppError("E2006", "文件为空或缺少表头", retryable=False, http_status=400)
    return rows, header


def _pick(row, *keys):
    for k in keys:
        if k in row and (row[k] or "").strip():
            return row[k].strip()
    return None


ALIASES = {
    "drug_code": ("编码", "药品编码", "code"),
    "generic_name": ("通用名", "药品名称", "名称", "name"),
    "spec": ("规格",), "dosage_form": ("剂型",), "unit": ("单位",),
    "herb_code": ("编码", "饮片编码", "code"), "name": ("名称", "饮片名称", "品名"),
    "item_code": ("项目编码", "编码", "code"), "price_yuan": ("单价", "价格", "零售价"),
    "supplier_code": ("编码", "供应商编码", "code"),
    "code": ("ICD10", "icd10", "编码"), "dict_type": ("类型",),
    "dict_code": ("编码",), "dict_label": ("名称", "标签"),
}


def _validate_rows(dtype, rows):
    """F128 R1 校验阶段：**只读不写**。返回错误清单（行号/字段/原因/原始值）。"""
    spec = IMPORT_TEMPLATE.get(dtype)
    errors = []
    if not spec:
        return errors
    _, required, _ = spec
    for i, row in enumerate(rows, start=2):
        for col in required:
            keys = (col,) + ALIASES.get(col, ())
            if _pick(row, *keys) is None:
                errors.append({"line": i, "field": col, "reason": "必填字段缺失",
                               "value": ""})
    return errors


def _write_rows(dtype, rows, batch_no=0):
    """把一批行交给**目标模块的 import 接口**写入（R2 表所有权）。"""
    if dtype in ("DRUG", "HERB"):
        from modules import m06_prescription          # 表所有权：M06
        return m06_prescription.import_upsert(dtype, rows)
    if dtype == "CHARGE_ITEM":
        from modules import m09_billing               # 表所有权：M09
        return m09_billing.import_upsert(dtype, rows)
    if dtype == "TREATMENT":
        from modules import m08_treatment             # 表所有权：M08
        return m08_treatment.import_upsert(dtype, rows)
    if dtype == "SUPPLIER":
        from modules import m07_pharmacy              # 表所有权：M07
        return m07_pharmacy.import_upsert(dtype, rows)
    ok = failed = 0
    errors = []
    for i, row in enumerate(rows, start=2 + batch_no * IMPORT_BATCH):
        try:
            if _import_row(dtype, row):
                ok += 1
            else:
                failed += 1
                errors.append({"line": i, "field": "", "reason": "无有效字段",
                               "value": ""})
        except Exception as exc:
            failed += 1
            errors.append({"line": i, "field": "", "reason": str(exc), "value": ""})
    return len(rows), ok, failed, errors


def _norm_errors(errors):
    """统一错误结构：line / field / reason / value（各模块返回格式可能不同）。"""
    out = []
    for e in errors or []:
        out.append({"line": e.get("line"), "field": e.get("field") or "",
                    "reason": e.get("reason") or e.get("error") or "",
                    "value": "" if e.get("value") is None else str(e.get("value"))})
    return out


def _file_sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def recover_stale_imports():
    """F128 AC15 崩溃恢复：启动时把残留 RUNNING 的导入任务标记为 FAILED。

    数据本身由事务保证（半导入不会落库）；这里只补任务状态，避免界面永远显示"进行中"。
    """
    if not db.table_exists("sys_import_job"):
        return 0
    from core import bizlock
    bizlock.end()
    with db.tx() as conn:
        cur = db.execute(conn, """
            UPDATE sys_import_job SET status='FAILED', finished_at=?,
                   error_file=COALESCE(error_file, '导入过程中进程中断，已整体回滚')
            WHERE status='RUNNING'
        """, (tu.now_str(),))
        return cur.rowcount


@route("/api/system/import/template", "GET", perm="system.config", module="M14",
       action="system.import.template")
def import_template(ctx, body, q):
    """F128 R2：标准 CSV 模板（含表头与示例行、必填列说明）。"""
    dtype = q.get("data_type") or "DRUG"
    if dtype not in IMPORT_TEMPLATE:
        raise AppError("E2001", "不支持的数据类型：%s" % dtype)
    cols, required, sample = IMPORT_TEMPLATE[dtype]
    buf = []
    buf.append(",".join(cols))
    buf.append(",".join(sample))
    return {"data_type": dtype, "filename": "import_template_%s.csv" % dtype.lower(),
            "content": "\n".join(buf), "columns": cols, "required": required,
            "sample": sample,
            "hint": "表头同时支持英文 key 与中文别名；金额写“元”，系统按契约 §3 转整数分。"}


@route("/api/system/import/preview", "POST", perm="system.config", module="M14",
       action="system.import.preview")
def import_preview(ctx, body, q):
    """F128 R1 校验阶段：**解析 + 校验，不写任何数据**（dry run）。"""
    require_keys(body, ["data_type", "file_path"])
    dtype = body["data_type"]
    require(dtype in IMPORT_TARGETS, "不支持的数据类型：%s" % dtype)
    table, owner = IMPORT_TARGETS[dtype]
    if not db.table_exists(table):
        raise AppError("E1007", "目标表 %s 尚未创建（归属 %s）" % (table, owner),
                       retryable=False, http_status=409)
    path = body["file_path"]
    require(os.path.isfile(path), "文件不存在：%s" % path)
    rows, header = _read_rows(path, body.get("encoding") or "utf-8-sig")
    errors = _validate_rows(dtype, rows)
    return {"data_type": dtype, "dry_run": True, "total": len(rows), "header": header,
            "error_count": len(errors), "errors": errors[:200],
            "batches": (len(rows) + IMPORT_BATCH - 1) // IMPORT_BATCH,
            "file_sha256": _file_sha(path),
            "writable": len(errors) == 0}


@route("/api/system/import", "POST", perm="system.config", module="M14", action="system.import")
def import_data(ctx, body, q):
    """F128 基础数据包导入（CSV/UTF-8，首行为表头）。

    · `dry_run=true` 只校验不写入（等价 preview）
    · 写入阶段：**整体一个事务**（R1），失败整体回滚（默认 `on_error=rollback`）
    · 分批 500 行执行并更新 `sys_import_job` 进度（R6）
    · 导入期间锁业务（R8，E3010）；结束写高风险审计（含文件哈希，R7）
    """
    from core import bizlock
    require_keys(body, ["data_type", "file_path"])
    dtype = body["data_type"]
    require(dtype in IMPORT_TARGETS, "不支持的数据类型：%s" % dtype)
    table, owner = IMPORT_TARGETS[dtype]
    if not db.table_exists(table):
        raise AppError("E1007", "目标表 %s 尚未创建（归属 %s，需先开发该模块）" % (table, owner),
                       retryable=False, http_status=409)
    path = body["file_path"]
    require(os.path.isfile(path), "文件不存在：%s" % path)
    rows, header = _read_rows(path, body.get("encoding") or "utf-8-sig")
    file_sha = _file_sha(path)

    if body.get("dry_run"):
        errors = _validate_rows(dtype, rows)
        return {"dry_run": True, "total": len(rows), "error_count": len(errors),
                "errors": errors[:200], "file_sha256": file_sha}

    # R1：先全量校验，有错直接返回（不写任何数据、不建任务）
    pre_errors = _validate_rows(dtype, rows)
    if pre_errors and body.get("on_error") != "force":
        return {"data_type": dtype, "total": len(rows), "success": 0,
                "failed": len(pre_errors), "status": "REJECTED",
                "errors": pre_errors[:200], "file_sha256": file_sha,
                "hint": "校验未通过，未写入任何数据；修正后可重新导入同一文件"}

    bizlock.require_free("导入")
    with db.tx() as conn:
        job_no = ids.next_no(conn, "IMPORT_JOB")
        audit.audit_operation(conn, "M14", "system.import", "sys_import_job", None,
                              detail={"data_type": dtype, "file": path,
                                      "file_sha256": file_sha})
        cur = db.execute(conn, """
            INSERT INTO sys_import_job (job_no, data_type, source_file, status,
                                        total_rows, started_at, created_at, created_by)
            VALUES (?,?,?,'RUNNING',?,?,?,?)
        """, (job_no, dtype, path, len(rows), tu.now_str(), tu.now_str(), ctx.user_id))
        job_id = cur.lastrowid

    bizlock.begin(job_no, dtype)
    total = ok = failed = 0
    errors = []
    status = "SUCCEEDED"
    try:
        # R6：分批 500 行；R1：整体仍在一个事务内（内层 db.tx() 退化为 SAVEPOINT）
        with db.tx() as conn:
            for b in range(0, max(len(rows), 1), IMPORT_BATCH):
                batch = rows[b:b + IMPORT_BATCH]
                if not batch:
                    break
                t, o, f, e = _write_rows(dtype, batch, batch_no=b // IMPORT_BATCH)
                total += t
                ok += o
                failed += f
                errors.extend(_norm_errors(e))
                db.execute(conn, "UPDATE sys_import_job SET success_rows=?, failed_rows=? "
                                 "WHERE id=?", (ok, failed, job_id))
            if failed and body.get("on_error") != "skip":
                # R1：失败整体回滚（AC2）
                raise AppError("E2002", "导入存在 %d 行错误，已整体回滚（第 %d 行：%s）"
                               % (failed, errors[0]["line"], errors[0].get("reason")),
                               retryable=False, http_status=409,
                               detail={"errors": errors[:50]})
            status = "SUCCEEDED" if failed == 0 else "PARTIAL"
    except AppError:
        status = "FAILED"
        raise
    except Exception as exc:
        status = "FAILED"
        if not errors:
            errors = [{"line": 0, "field": "", "reason": str(exc), "value": ""}]
        raise
    finally:
        bizlock.end()
        from core.logging_setup import log_dir
        err_file = None
        if errors:
            err_file = os.path.join(log_dir() or ".", "import_%s_errors.csv" % job_no)
            with open(err_file, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["line", "field", "reason", "value"])
                w.writeheader()
                w.writerows(errors)
        with db.tx() as conn:
            db.execute(conn, """
                UPDATE sys_import_job SET total_rows=?, success_rows=?, failed_rows=?,
                       status=?, finished_at=?, error_file=? WHERE id=?
            """, (total, ok, failed, status, tu.now_str(), err_file, job_id))
            # R7：导入必审计（只记统计与文件哈希，不记明细）
            audit.audit_high_risk(conn, "IMPORT", "sys_import_job", job_no,
                                  "基础数据导入 %s：总 %d / 成功 %d / 失败 %d"
                                  % (dtype, total, ok, failed),
                                  {"data_type": dtype, "file_sha256": file_sha,
                                   "total": total, "success": ok, "failed": failed,
                                   "status": status, "source_file": path})
    return {"job_no": job_no, "data_type": dtype, "total": total, "success": ok,
            "failed": failed, "status": status, "error_file": err_file,
            "file_sha256": file_sha, "errors": errors[:50]}


@route("/api/system/import/status", "GET", perm="system.config", module="M14",
       action="system.import.status")
def import_status(ctx, body, q):
    """F128 AC4：导入进度（分批执行时可轮询）。"""
    from core import bizlock
    job_no = q.get("job_no")
    sql = "SELECT * FROM sys_import_job"
    params = ()
    if job_no:
        sql += " WHERE job_no=?"
        params = (job_no,)
    sql += " ORDER BY id DESC LIMIT 20"
    return {"items": db.query(sql, params), "locked": bizlock.is_locked(),
            "current": bizlock.state()}


@route("/api/system/datapackage/status", "GET", perm="system.config", module="M14",
       action="system.datapackage.status")
def datapackage_status(ctx, body, q):
    """F128.5 数据包版本（记录在配置里，P2）。"""
    return {"version": config.get("datapackage.version"),
            "name": config.get("datapackage.name"),
            "imported_at": config.get("datapackage.imported_at"),
            "counts": {"drug": _count("drug"), "herb": _count("herb"),
                       "charge_item": _count("charge_item"),
                       "treatment_item": _count("treatment_item")}}


def _count(table):
    if not db.table_exists(table):
        return None
    return db.query_one("SELECT COUNT(*) c FROM %s WHERE deleted_at IS NULL" % table)["c"]


# 各基础数据表用于"名称比对"的列（表结构不同，不能统一写 name）
NAME_COL = {"drug": "generic_name", "herb": "name",
            "charge_item": "name", "treatment_item": "name"}


@route("/api/system/datapackage/check", "POST", perm="system.config", module="M14",
       action="system.datapackage.check")
def datapackage_check(ctx, body, q):
    """F128.5 AC11 版本比对：与**本地新版数据包清单文件**比对，输出新增/修改/停用。

    离线单机：不做联网下载；`manifest_path` 指向新版数据包清单 CSV（含 data_type + code
    + name + 可选 rev/停用标记）。返回差异条目数与类型，供增量导入决策。
    """
    path = body.get("manifest_path")
    require(path and os.path.isfile(path), "缺少 manifest_path 或文件不存在")
    rows, header = _read_rows(path, body.get("encoding") or "utf-8-sig")
    cur_ver = config.get("datapackage.version")
    new_ver = body.get("version") or os.path.basename(path)
    added = modified = disabled = 0
    samples = []
    for row in rows:
        dtype = _pick(row, "data_type", "类型") or "DRUG"
        code = _pick(row, "drug_code", "herb_code", "item_code", "code", "编码")
        if not code:
            continue
        table, keycol = {"DRUG": ("drug", "drug_code"), "HERB": ("herb", "herb_code"),
                         "CHARGE_ITEM": ("charge_item", "item_code"),
                         "TREATMENT": ("treatment_item", "item_code")}.get(dtype,
                                                                           (None, None))
        if not table or not db.table_exists(table):
            added += 1
            continue
        cur = db.query_one("SELECT %s AS nm FROM %s WHERE %s=?"
                           % (NAME_COL[table], table, keycol), (code,))
        if cur is None:
            added += 1
            if len(samples) < 20:
                samples.append({"change": "ADDED", "data_type": dtype, "code": code})
        else:
            new_name = _pick(row, "name", "generic_name", "名称")
            if new_name and (cur["nm"] or "") != new_name:
                modified += 1
                if len(samples) < 20:
                    samples.append({"change": "MODIFIED", "data_type": dtype, "code": code})
            if str(_pick(row, "is_active", "启用") or "1") in ("0", "false", "停用"):
                disabled += 1
                if len(samples) < 20:
                    samples.append({"change": "DISABLED", "data_type": dtype, "code": code})
    return {"current_version": cur_ver, "new_version": new_ver,
            "added": added, "modified": modified, "disabled": disabled,
            "samples": samples,
            "hint": "确认后对同一文件执行 /api/system/import 即为增量导入（upsert，不删既有数据）"}


@route("/api/system/datapackage/apply", "POST", perm="system.config", module="M14",
       action="system.datapackage.apply")
def datapackage_apply(ctx, body, q):
    """F128.5 应用数据包：导入 + 记录版本号。不删除既有数据（R9）。"""
    res = import_data(ctx, body, q)
    if res.get("status") in ("SUCCEEDED", "PARTIAL"):
        config.set_many({
            "datapackage.version": body.get("version") or "custom",
            "datapackage.name": body.get("name") or "本地数据包",
            "datapackage.imported_at": tu.now_str(),
        }, reason="基础数据包导入完成")
    res["datapackage_version"] = config.get("datapackage.version")
    return res


def _import_row(dtype, row):
    """单行导入。目前完整支持 ICD10 / DICT（写入 M14 自有的 sys_dict）。"""
    def pick(*keys):
        for k in keys:
            if k in row and (row[k] or "").strip():
                return row[k].strip()
        return None

    if dtype == "ICD10":
        code = pick("code", "编码", "icd10", "ICD10")
        name = pick("name", "名称", "疾病名称", "诊断名称")
        if not (code and name):
            return 0
        with db.tx() as conn:
            db.execute(conn, """
                INSERT INTO sys_dict (dict_type, dict_code, dict_label, is_active, created_at)
                VALUES ('ICD10',?,?,1,?)
                ON CONFLICT(dict_type, dict_code) DO UPDATE SET dict_label=excluded.dict_label
            """, (code, name, tu.now_str()))
        return 1

    if dtype == "DICT":
        dtype_name = pick("dict_type", "类型")
        code = pick("dict_code", "编码")
        label = pick("dict_label", "名称")
        if not (dtype_name and code and label):
            return 0
        with db.tx() as conn:
            db.execute(conn, """
                INSERT INTO sys_dict (dict_type, dict_code, dict_label, is_active, created_at)
                VALUES (?,?,?,1,?)
                ON CONFLICT(dict_type, dict_code) DO UPDATE SET dict_label=excluded.dict_label
            """, (dtype_name, code, label, tu.now_str()))
        return 1

    raise AppError("E1007", "数据类型 %s 的导入器尚未实现（归属模块未开发）" % dtype)
