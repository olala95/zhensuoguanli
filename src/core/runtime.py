# -*- coding: utf-8 -*-
"""运行时装配：启动自检 → 迁移 → 每日首次启动备份 → 服务；退出时安全收尾。

对应 05 §5（启动自检/崩溃恢复）、05 §6（备份）、05 §8（安全退出）。
"""

import atexit
import datetime
import os
import sys
import threading

from . import db, migrate, selftest, backup, config, api, logging_setup
from .context import Context, set_ctx
from .logging_setup import get

log = get("runtime")

ROOT = None
DATA_DIR = None
_shutdown_event = threading.Event()
_httpd = None


def bootstrap(root, port=8787, open_browser=False, safe_mode_override=None,
              start_server=True):
    """启动装配。返回 (httpd, url, report)。report 含自检与迁移结果。"""
    global ROOT, DATA_DIR, _httpd
    ROOT = root
    db_path = os.path.join(root, "data", "clinic.db")
    DATA_DIR = os.path.join(root, "data")

    logging_setup.init(os.path.join(root, "logs"))
    log.info("=" * 60)
    log.info("系统启动 version=phase1 db=%s", db_path)

    db.init(db_path)
    set_ctx(Context(source="SYSTEM"))

    migrate.init(os.path.join(root, "migrations"))
    backup.init(os.path.join(root, "backups"))

    # 硬件驱动注册表（F72）：默认 mock 驱动，真实驱动可注入替换
    try:
        from . import hardware
        hardware.init(os.path.join(root, "logs", "prints"))
    except Exception:
        log.exception("硬件驱动初始化失败（打印功能将不可用）")

    # 0) 快速闸门：绝不在损坏的库上跑迁移
    gate_ok, gate_detail = selftest.quick_gate()
    if safe_mode_override is not None:
        db.set_readonly(bool(safe_mode_override))
    elif not gate_ok:
        db.set_readonly(True)

    # 1) 崩溃恢复检测（WAL 残留 + 跨表不变量，05 §5.2）
    if not db.is_readonly():
        try:
            crash = selftest.detect_crash()
            if not crash["clean"]:
                log.warning("崩溃恢复：%s", crash["detail"])
        except Exception:
            log.exception("崩溃恢复检测失败")

    # 2) 时间异常检测（F88）
    try:
        from modules import m12_audit
        anomaly = m12_audit.check_time_anomaly()
        if anomaly:
            log.warning("检测到系统时间异常：%s", anomaly)
    except Exception:
        log.exception("时间异常检测失败")

    if db.is_readonly():
        log.error("进入安全模式（只读），禁止业务写入")
        checks = [{"item": "integrity_check", "result": "FAIL", "detail": gate_detail},
                  {"item": "migration", "result": "SKIPPED", "detail": "安全模式跳过迁移"}]
    else:
        # 3) 迁移（执行前自动备份，05 §6）
        try:
            done = migrate.run(auto_backup=True)
            if done:
                log.info("执行迁移 %d 个：%s", len(done), [d["filename"] for d in done])
        except Exception as exc:
            log.exception("迁移失败")
            db.set_readonly(True)
            checks = [{"item": "migration", "result": "FAIL", "detail": str(exc)}]

        # 4) 完整启动自检（05 §5.1 七项）
        if not db.is_readonly():
            checks, need_safe = selftest.run_startup_checks()
            if need_safe:
                db.set_readonly(True)
                log.error("自检未通过，进入安全模式（只读）")
            try:
                selftest.persist(checks)
            except Exception:
                log.exception("持久化自检结果失败")

        # 5) 每日首次启动全量备份（F90：当天已有成功全量则跳过）
        try:
            from modules import m13_recovery
            r = m13_recovery.auto_backup_if_needed()
            if r.get("skipped"):
                log.info("跳过启动备份：%s", r.get("reason"))
            else:
                log.info("每日首次启动备份完成：%s", r)
        except Exception:
            log.exception("每日启动备份失败（不阻断启动）")

    # 6) 崩溃恢复（F96）：收敛上次崩溃遗留的导入/打印/AI 任务；
    #    资金与库存相关项只报告不自动补偿（R-M13-7）
    if not db.is_readonly():
        try:
            from modules import m13_recovery
            r = m13_recovery.recover_from_crash()
            bits = ["导入 %d" % r["imports_recovered"], "打印 %d" % r["prints_recovered"],
                    "AI 重试 %d/放弃 %d" % (r["ai"]["retried"], r["ai"]["abandoned"])]
            if r["manual_review"]:
                bits.append("需人工复核 %d 项" % len(r["manual_review"]))
                log.warning("崩溃恢复发现需人工复核：%s", r["manual_review"])
            log.info("崩溃恢复完成：%s", "，".join(bits))
        except Exception:
            log.exception("崩溃恢复失败（不阻断启动）")

    # 7) 数据库异常兜底：启动期若已进只读，明确告知并保留现场（F95 R2）
    if db.is_readonly():
        try:
            from modules import m13_recovery
            m13_recovery.freeze_on_corruption("E1003", "启动自检判定库不可用")
        except Exception:
            log.exception("冻结现场失败")

    api.init(os.path.join(root, "web"))
    url = "http://127.0.0.1:%d/" % port
    if start_server:
        _httpd, url = api.serve(port=port, open_browser=open_browser)
        atexit.register(_atexit)
    report = {"checks": checks, "safe_mode": db.is_readonly(), "url": url,
              "db_path": db_path, "version": "1.0.0"}
    return _httpd, url, report


def request_shutdown(backup_on_exit=True):
    """F107 安全退出：checkpoint → 可选备份 → 关闭连接 → 停止服务。"""
    log.info("收到安全退出请求")
    result = {"checkpoint": False, "backup": None, "db_closed": False}
    try:
        db.checkpoint_truncate()
        result["checkpoint"] = True
    except Exception:
        log.exception("退出前 checkpoint 失败")
    if backup_on_exit and config.get_bool("backup.on_exit", True):
        try:
            result["backup"] = backup.full_backup(reason="ON_EXIT")
        except Exception:
            log.exception("退出前备份失败")
    try:
        db.close_all()
        result["db_closed"] = True
    except Exception:
        log.exception("关闭数据库连接失败")
    _shutdown_event.set()
    threading.Timer(1.0, _stop_server).start()
    return result


def _stop_server():
    global _httpd
    if _httpd is not None:
        try:
            _httpd.shutdown()
        except Exception:
            pass
    os._exit(0)


def _atexit():
    if not _shutdown_event.is_set():
        try:
            db.checkpoint_truncate()
            db.close_all()
        except Exception:
            pass


def wait():
    """阻塞等待服务结束。"""
    if _httpd is None:
        return
    try:
        _httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断信号，开始安全退出")
        request_shutdown(backup_on_exit=True)
