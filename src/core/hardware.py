# -*- coding: utf-8 -*-
"""硬件驱动注册表（F72）。契约：00_AI_RULES R8（外部依赖必须有可注入的 mock）。

设计约束：
  · 业务代码**只依赖本注册表**，不直接 import 任何具体驱动
  · 内置 mock 驱动（打印落盘 / 身份证模拟 / 扫码模拟），真实驱动可运行时注入替换
  · 驱动失败一律转成 E4xxx（retryable=True），业务层不得因硬件失败回滚
  · Hardware Agent 若独立进程运行，不得直连业务库（05 §2 单写者原则）
"""

import os
import threading
import time

from .errors import AppError, HW_PRINTER_OFFLINE, HW_AGENT_UNREACHABLE
from .logging_setup import get

log = get("hardware")

DEFAULT_TIMEOUT = 5.0
_DRIVERS = {}
_LOCK = threading.RLock()
_SPOOL_DIR = None


class DriverError(AppError):
    """驱动层统一异常：携带驱动码与原因，便于上层翻译成 E4xxx。"""

    def __init__(self, code, message, retryable=True, detail=None):
        super().__init__(code, message, retryable=retryable, http_status=503,
                         detail=detail or {})


# ---------------- mock 驱动（默认，可注入替换）----------------

class MockPrinter:
    """mock 打印机：把渲染结果写到 spool 目录，可配置失败/超时。"""

    code = "mock.printer"

    def __init__(self, spool_dir=None, fail=False, delay=0.0):
        self.spool_dir = spool_dir
        self.fail = fail
        self.delay = delay

    def status(self):
        return {"online": not self.fail, "detail": "mock printer"}

    def print_text(self, text, meta=None):
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise DriverError(HW_PRINTER_OFFLINE, "打印机离线或缺纸（mock）",
                              retryable=True)
        if not self.spool_dir:
            return {"ok": True, "sink": "memory"}
        os.makedirs(self.spool_dir, exist_ok=True)
        name = "print_%s_%d.txt" % (time.strftime("%Y%m%d_%H%M%S"), int(time.time() * 1000) % 100000)
        path = os.path.join(self.spool_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text or "")
        return {"ok": True, "sink": "file", "path": path}


class MockIdReader:
    """mock 身份证读卡器：返回一条固定的模拟身份信息。"""

    code = "mock.id_reader"

    def __init__(self, fail=False, id_no="110105198503120012", name="张三"):
        self.fail = fail
        self.id_no = id_no
        self.name = name

    def status(self):
        return {"online": not self.fail, "detail": "mock id reader"}

    def read(self):
        if self.fail:
            raise DriverError(HW_AGENT_UNREACHABLE, "读卡器不可达（mock）", retryable=True)
        return {"name": self.name, "id_card_no": self.id_no}


class MockScanner:
    """mock 扫码枪：返回预设的条码串（可注入）。"""

    code = "mock.scanner"

    def __init__(self, fail=False, payload="IBU001"):
        self.fail = fail
        self.payload = payload

    def status(self):
        return {"online": not self.fail, "detail": "mock scanner"}

    def scan(self):
        if self.fail:
            raise DriverError(HW_AGENT_UNREACHABLE, "扫码枪不可达（mock）", retryable=True)
        return {"code": self.payload}


# ---------------- 注册表 ----------------

def init(spool_dir=None, drivers=None):
    """装配默认驱动。spool_dir 为 mock 打印落盘目录（logs/prints）。"""
    global _SPOOL_DIR
    _SPOOL_DIR = spool_dir
    with _LOCK:
        _DRIVERS.clear()
        _DRIVERS[MockPrinter.code] = MockPrinter(spool_dir=spool_dir)
        _DRIVERS[MockIdReader.code] = MockIdReader()
        _DRIVERS[MockScanner.code] = MockScanner()
        for d in (drivers or []):
            register(d)
    return sorted(_DRIVERS)


def register(driver):
    """注入驱动（按 driver.code 覆盖同名驱动）。"""
    with _LOCK:
        _DRIVERS[getattr(driver, "code", driver.__class__.__name__)] = driver
    return True


def get(code):
    with _LOCK:
        d = _DRIVERS.get(code)
    if d is None:
        raise AppError(HW_AGENT_UNREACHABLE, "未注册的设备驱动：%s" % code,
                       retryable=True, http_status=503)
    return d


def reset():
    """恢复为默认 mock 驱动（测试用）。"""
    return init(spool_dir=_SPOOL_DIR)


def codes():
    with _LOCK:
        return sorted(_DRIVERS)


def call(code, method, *args, **kwargs):
    """统一驱动调用：异常归一为 AppError（E4xxx，retryable），并计时。

    返回 (result, elapsed_ms)；失败抛 AppError。
    """
    t0 = time.time()
    d = get(code)
    fn = getattr(d, method, None)
    if fn is None:
        raise AppError(HW_AGENT_UNREACHABLE, "驱动 %s 不支持方法 %s" % (code, method),
                       retryable=True, http_status=503)
    try:
        r = fn(*args, **kwargs)
    except AppError:
        raise
    except Exception as exc:
        log.warning("驱动调用失败 %s.%s：%s", code, method, exc)
        raise AppError(HW_AGENT_UNREACHABLE, "硬件调用失败（%s.%s）：%s"
                       % (code, method, exc), retryable=True, http_status=503,
                       detail={"driver": code, "method": method})
    return r, int((time.time() - t0) * 1000)


def status_all():
    """探测所有驱动状态（F75 消费）。"""
    out = []
    for c in codes():
        try:
            st, ms = call(c, "status")
            out.append({"driver": c, "online": bool(st.get("online")),
                        "detail": st.get("detail", ""), "elapsed_ms": ms})
        except AppError as exc:
            out.append({"driver": c, "online": False, "detail": exc.message,
                        "elapsed_ms": 0, "error_code": exc.code})
    return out


def restart(code):
    """重新初始化驱动（F98 看门狗调用）：mock 驱动重建即恢复。"""
    with _LOCK:
        old = _DRIVERS.get(code)
    if old is None:
        return {"restarted": False, "reason": "驱动未注册"}
    new = old.__class__.__new__(old.__class__)
    try:
        new.__dict__.update({k: v for k, v in old.__dict__.items()})
        for k in ("fail",):
            if hasattr(new, k):
                setattr(new, k, False)     # 复位故障标记，模拟"拉起"
    except Exception:
        new = old
    register(new)
    return {"restarted": True, "driver": code}
