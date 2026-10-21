# -*- coding: utf-8 -*-
"""日志约定。契约：05 §7。

应用日志 logs/app.log（按天轮转，保留 30 天）；错误日志 logs/error.log（保留 90 天）。
**日志 ≠ 审计**：日志可轮转，审计不可删（审计走数据库 audit_* 表）。

轮转是「按天 + 按大小」双条件：只按天的话，一天内出现异常刷屏（曾经一天 219MB）
当天就不会切分，直接把磁盘写满；磁盘一满备份失败、系统随即进只读。
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

_initialized = False
_LOG_DIR = None


class SizedTimedRotatingFileHandler(TimedRotatingFileHandler):
    """按天轮转，同时限制单文件大小（默认 32MB）。

    同一自然日多次滚动时用 %H%M%S 区分，避免后续滚动覆盖前一个切片。
    """

    def __init__(self, filename, max_bytes=32 * 1024 * 1024, **kw):
        self.max_bytes = max_bytes
        super().__init__(filename, **kw)

    def shouldRollover(self, record):
        if super().shouldRollover(record):
            return True
        if not self.max_bytes:
            return False
        try:
            return os.path.getsize(self.baseFilename) >= self.max_bytes
        except OSError:
            return False


def init(log_dir):
    global _initialized, _LOG_DIR
    _LOG_DIR = log_dir
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-16s | %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    app_h = SizedTimedRotatingFileHandler(os.path.join(log_dir, "app.log"),
                                          when="midnight", backupCount=30,
                                          encoding="utf-8")
    app_h.suffix = "%Y%m%d_%H%M%S"
    app_h.setLevel(logging.INFO)
    app_h.setFormatter(fmt)

    err_h = SizedTimedRotatingFileHandler(os.path.join(log_dir, "error.log"),
                                          when="midnight", backupCount=90,
                                          encoding="utf-8")
    err_h.suffix = "%Y%m%d_%H%M%S"
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(fmt)

    root.addHandler(app_h)
    root.addHandler(err_h)

    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    _initialized = True
    return root


def get(name):
    if not _initialized:
        logging.basicConfig(level=logging.INFO)
    return logging.getLogger(name)


def log_dir():
    return _LOG_DIR
