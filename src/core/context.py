# -*- coding: utf-8 -*-
"""请求上下文（审计埋点的载体）。

审计字段标准见 03 §5：每次写操作都要能回答"谁、以什么角色、从哪来、做了什么"。
该上下文是线程局部的，由 API 层在每次请求开始时装载。
"""

import itertools
import json
import threading

_local = threading.local()
_req_counter = itertools.count(1)

SYSTEM_USER_ID = 0
SYSTEM_ROLE = "SYSTEM"


class Context:
    """操作上下文。user_id=0 / role=SYSTEM 表示系统内部操作。"""

    def __init__(self, user_id=SYSTEM_USER_ID, role_code=SYSTEM_ROLE, source="SYSTEM",
                 username=None, request_id=None, session_id=None, client_info=None):
        self.user_id = user_id
        self.role_code = role_code
        self.source = source  # UI / AI / HARDWARE / SYSTEM
        self.username = username
        self.request_id = request_id or ("R%08d" % next(_req_counter))
        self.session_id = session_id
        self.client_info = client_info or {}
        self.operation_id = None  # 当前 audit_operation 记录 ID，供 audit_data_change 关联

    def is_system(self):
        return self.user_id == SYSTEM_USER_ID

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "role_code": self.role_code,
            "source": self.source,
            "username": self.username,
            "request_id": self.request_id,
        }


def set_ctx(ctx):
    _local.ctx = ctx


def get_ctx():
    return getattr(_local, "ctx", None) or Context()


def clear_ctx():
    _local.ctx = None


def dumps(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def new_request_id():
    return "R%08d" % next(_req_counter)
