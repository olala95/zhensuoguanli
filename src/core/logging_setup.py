# -*- coding: utf-8 -*-
"""日志约定。契约：05 §7。

应用日志 logs/app_{date}.log（30 天）；错误日志 logs/error_{date}.log（90 天）。
**日志 ≠ 审计**：日志可轮转，审计不可删（审计走数据库 audit_* 表）。
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

_initialized = False
_LOG_DIR = None


def init(log_dir):
    global _initialized, _LOG_DIR
    _LOG_DIR = log_dir
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-16s | %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    app_h = TimedRotatingFileHandler(os.path.join(log_dir, "app.log"),
                                     when="midnight", backupCount=30, encoding="utf-8")
    app_h.suffix = "%Y%m%d"
    app_h.setLevel(logging.INFO)
    app_h.setFormatter(fmt)

    err_h = TimedRotatingFileHandler(os.path.join(log_dir, "error.log"),
                                     when="midnight", backupCount=90, encoding="utf-8")
    err_h.suffix = "%Y%m%d"
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
