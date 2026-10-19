# -*- coding: utf-8 -*-
"""基础数据导入锁（F128 R8）。

导入执行期间禁止开处方 / 收费等一线业务，避免读到"半导入状态"的基础数据。
单机单用户场景下用进程内标记即可；锁状态同时落 `sys_config`，崩溃重启后可自检。

被拒的业务操作返回 **E3010（retryable=true）**，提示稍后重试。
"""

import threading

from .errors import AppError, BIZ_IMPORT_LOCKED

_lock = threading.RLock()
_state = {"active": False, "job_no": None, "data_type": None, "started_at": None}


def begin(job_no, data_type):
    with _lock:
        _state.update({"active": True, "job_no": job_no, "data_type": data_type,
                       "started_at": None})
        from . import timeutil as tu
        _state["started_at"] = tu.now_str()
    return dict(_state)


def end():
    with _lock:
        _state.update({"active": False, "job_no": None, "data_type": None,
                       "started_at": None})


def is_locked():
    return bool(_state.get("active"))


def state():
    return dict(_state)


def require_free(action="业务操作"):
    """业务入口调用：导入进行中 → 拒绝（E3010，可重试）。"""
    if is_locked():
        raise AppError(BIZ_IMPORT_LOCKED,
                       "基础数据导入中（%s），%s暂不可用，请稍后重试"
                       % (_state.get("data_type"), action),
                       retryable=True, http_status=409,
                       detail={"job_no": _state.get("job_no")})
    return True
