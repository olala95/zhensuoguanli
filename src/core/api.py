# -*- coding: utf-8 -*-
"""极简 HTTP API 框架（仅标准库）。

设计约束：
· 所有写操作在 db.tx() 内完成，审计与业务同事务（成功一起提交，失败一起回滚）
· 失败时业务事务回滚，再由本层在独立事务中补写 audit_operation(result=FAIL)
· 权限：路由声明 perm，越权一律拒绝并写 audit_permission，不静默放行（03 §9）
· 静态资源从 web/ 目录提供；仅监听 127.0.0.1
"""

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db
from . import audit
from . import session as sess
from . import timeutil as tu
from .context import Context, set_ctx, clear_ctx, get_ctx
from .errors import AppError
from .logging_setup import get

log = get("api")

ROUTES = {}          # (method, path) -> meta
WEB_DIR = None
MAX_BODY = 8 * 1024 * 1024


def route(path, method="POST", perm=None, roles=None, module=None, action=None,
          public=False, allow_locked=False):
    """注册路由。perm: 权限点；roles: 允许角色集合（与 perm 二选一或同时使用）。

    allow_locked=True：允许在会话已锁定时调用（只给解锁/退出这类认证自身端点，
    否则锁屏后无法解锁，只能重启浏览器）。
    """

    def deco(fn):
        ROUTES[(method.upper(), path)] = {
            "fn": fn, "perm": perm, "roles": set(roles) if roles else None,
            "module": module or "M00", "action": action or fn.__name__, "public": public,
            "allow_locked": allow_locked,
        }
        return fn

    return deco


def init(web_dir):
    global WEB_DIR
    WEB_DIR = web_dir


def _json_resp(handler, code, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _read_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY:
        raise AppError("E2002", "请求体过大", retryable=False, http_status=413)
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise AppError("E2002", "请求体不是合法 JSON", retryable=False, http_status=400)


def _token(handler, query):
    auth = handler.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return handler.headers.get("X-Token") or query.get("_token") or ""


def _dispatch(handler, method, path, query, body):
    meta = ROUTES.get((method, path))
    if meta is None:
        raise AppError("E2005", "接口不存在：%s %s" % (method, path),
                       retryable=False, http_status=404)

    token = _token(handler, query)
    srow = None
    if meta["public"] and not token:
        ctx = Context(source="UI", client_info={"ua": handler.headers.get("User-Agent", "")})
    else:
        srow = sess.resolve(token, allow_locked=bool(meta.get("allow_locked")))
        ctx = Context(user_id=srow["user_id"], role_code=srow["role_code"], source="UI",
                      request_id=None, session_id=srow["id"],
                      client_info={"ua": handler.headers.get("User-Agent", "")})
        ctx.username = _username(srow["user_id"])
        try:
            with db.tx() as conn:
                sess.touch(conn, srow["id"], token)
        except Exception:
            log.warning("刷新会话心跳失败")
    set_ctx(ctx)

    # 权限校验（03 §9：越权拒绝并留痕）
    # 判定用「账号全部角色的并集」：单机单用户下一人兼多职，
    # 登录时选的角色只决定审计默认身份，不限制实际能做的操作。
    if meta["perm"] and not meta["public"]:
        if not sess.has_permission_for_user(ctx.user_id, meta["perm"]):
            try:
                with db.tx() as conn:
                    audit.audit_permission(conn, meta["perm"], path, meta["action"])
            except Exception:
                log.exception("写权限审计失败")
            raise AppError("E6002", "当前账号（%s）无权执行：%s" % (ctx.username or ctx.user_id,
                                                             meta["perm"]),
                           retryable=False, http_status=403)
        # 审计记「实际行使的角色」：若登录身份无此权限、由另一角色完成，
        # 就记为那个有权角色，避免审计里出现"无权角色干了这件事"的假记录。
        ctx.role_code = sess.acting_role(srow["user_id"], srow["role_code"], meta["perm"])
    if meta["roles"] and srow is not None:
        allowed = set(meta["roles"])
        if srow["role_code"] not in allowed and \
                not any(rc in allowed for rc in sess.user_roles(srow["user_id"])):
            try:
                with db.tx() as conn:
                    audit.audit_permission(conn, "role:%s" % ",".join(sorted(meta["roles"])),
                                           path, meta["action"])
            except Exception:
                log.exception("写权限审计失败")
            raise AppError("E6002", "当前账号无权访问该接口（需要角色 %s）"
                           % ",".join(sorted(meta["roles"])),
                           retryable=False, http_status=403)

    result = meta["fn"](ctx, body or {}, query)

    # 写操作成功审计（失败路径在 _handle 中补写 FAIL）
    if method != "GET":
        try:
            with db.tx() as conn:
                audit.audit_operation(conn, meta["module"], meta["action"],
                                      entity_type=_entity_type(meta["action"]),
                                      entity_id=_entity_id(result),
                                      result="SUCCESS",
                                      detail={"path": path})
        except Exception:
            log.exception("写操作审计失败（不阻断业务）")
    return result


def _username(user_id):
    row = db.query_one("SELECT username FROM sys_user WHERE id=?", (user_id,))
    return row["username"] if row else None


def _entity_type(action):
    return action.split(".")[0] if "." in action else None


def _entity_id(result):
    if isinstance(result, dict):
        for k in ("id", "patient_id", "visit_id", "record_id"):
            if k in result:
                return result[k]
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "ClinicSystem/1.0"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _handle(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        body = {}
        try:
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                body = _read_body(self)
            if path.startswith("/api/"):
                result = _dispatch(self, method, path, query, body)
                _json_resp(self, 200, {"ok": True, "data": result if result is not None else {}})
            else:
                self._static(path)
        except AppError as exc:
            self._fail_audit(method, path, exc)
            _json_resp(self, exc.http_status,
                       {"ok": False, "error": exc.to_dict()})
        except Exception as exc:  # R6：禁止静默吞异常
            log.exception("未处理异常 %s %s", method, path)
            err = AppError("E1006", "服务器内部错误：%s" % exc, retryable=True, http_status=500)
            self._fail_audit(method, path, err)
            _json_resp(self, 500, {"ok": False, "error": err.to_dict()})
        finally:
            clear_ctx()

    def _fail_audit(self, method, path, exc):
        meta = ROUTES.get((method, path))
        if not meta or method == "GET":
            return
        try:
            with db.tx() as conn:
                audit.audit_operation(conn, meta["module"], meta["action"],
                                      result="FAIL", error_code=exc.code,
                                      detail={"path": path, "message": exc.message})
        except Exception:
            log.exception("写失败审计时再次失败")

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        # 浏览器会无条件请求 /favicon.ico；没有就返回 204，
        # 否则控制台永远挂着一条红色 404，看着像系统出错。
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not os.path.abspath(full).startswith(os.path.abspath(WEB_DIR)) or \
                not os.path.isfile(full):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return
        ext = os.path.splitext(full)[1].lower()
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
                 ".ico": "image/x-icon"}.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # no-store：改了前端必须立刻能看到。用 no-cache 时浏览器在没有
        # ETag/Last-Modified 的情况下仍可能复用旧副本，导致"改了但界面没变"。
        self.send_header("Cache-Control", "no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET,POST,PUT,DELETE,OPTIONS")
        self.end_headers()


class _ExclusiveServer(ThreadingHTTPServer):
    """单实例 HTTP 服务。

    Windows 上 HTTPServer 默认 allow_reuse_address=1，即 SO_REUSEADDR，
    它允许**两个进程同时 bind 同一个端口**。后果非常隐蔽：
    用户重复双击 start.bat 起了两个实例，请求被随机分给新/旧进程，
    表现为"一会儿正常、一会儿报接口不存在/数据不对"。
    这里改成独占绑定：端口已被占用时直接失败，宁可启动不起来也绝不双实例。
    """
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            try:
                import socket as _s
                self.socket.setsockopt(_s.SOL_SOCKET, _s.SO_EXCLUSIVEADDRUSE, 1)
            except Exception:
                pass
        super().server_bind()


def serve(host="127.0.0.1", port=8787, open_browser=False):
    try:
        httpd = _ExclusiveServer((host, port), Handler)
    except OSError as exc:
        raise AppError("E1004",
                       "端口 %d 已被占用，可能已经有一个诊所在运行。"
                       "请先在浏览器打开 http://%s:%d/ 确认；"
                       "确实要重开请先关闭旧窗口/任务管理器里结束 python 进程。（%s）"
                       % (port, host, port, exc),
                       retryable=False, http_status=500) from exc
    httpd.daemon_threads = True
    url = "http://%s:%d/" % (host, port)
    log.info("服务启动：%s", url)
    if open_browser:
        threading.Timer(0.8, _open, args=(url,)).start()
    return httpd, url


def _open(url):
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        log.warning("无法自动打开浏览器，请手动访问 %s", url)
