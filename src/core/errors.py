# -*- coding: utf-8 -*-
"""异常码体系。契约来源：03_GLOBAL_CONTRACTS §6。

格式 E{大类}{3位}：
  E1xxx 系统 | E2xxx 数据校验 | E3xxx 业务规则 | E4xxx 硬件
  E5xxx AI    | E6xxx 权限     | E7xxx 打印
约定：E4xxx/E5xxx retryable=True；E2xxx/E6xxx retryable=False。
"""

SYSTEM_DB_UNAVAILABLE = "E1001"
SYSTEM_DB_BUSY = "E1001"
SYSTEM_DISK_FULL = "E1002"
SYSTEM_DB_CORRUPT = "E1003"
SYSTEM_DB_IOERR = "E1004"
SYSTEM_READONLY = "E1005"
SYSTEM_PROCESS_ABNORMAL = "E1006"
SYSTEM_NOT_INITIALIZED = "E1007"
SYSTEM_MIGRATION_FAILED = "E1008"

DATA_REQUIRED = "E2001"
DATA_FORMAT = "E2002"
DATA_FK_NOT_FOUND = "E2003"
DATA_DUPLICATE = "E2004"
DATA_NOT_FOUND = "E2005"
DATA_IMMUTABLE_TABLE = "E2006"
DATA_VERSION_CONFLICT = "E2007"

BIZ_STATE_INVALID = "E3001"
BIZ_STOCK_INSUFFICIENT = "E3002"
BIZ_OVER_DOSE = "E3003"
BIZ_SKIN_TEST_REQUIRED = "E3004"
BIZ_COMPATIBILITY = "E3005"
BIZ_SETTLED_LOCKED = "E3006"
BIZ_ALREADY_CONFIRMED = "E3007"
BIZ_DISPENSED_NOT_VOIDABLE = "E3008"
BIZ_PATIENT_HAS_DATA = "E3009"
BIZ_IMPORT_LOCKED = "E3010"        # 基础数据导入中，业务暂时锁定（F128 R8，可重试）

HW_PRINTER_OFFLINE = "E4001"
HW_AGENT_UNREACHABLE = "E4002"

AI_FAILED = "E5001"
AI_TIMEOUT = "E5002"
AI_UNPARSABLE = "E5003"

PERM_NOT_LOGIN = "E6001"
PERM_DENIED = "E6002"
PERM_SIGN_INVALID = "E6003"
PERM_ACCOUNT_LOCKED = "E6004"

PRINT_TEMPLATE_MISSING = "E7001"
PRINT_RENDER_FAILED = "E7002"


class AppError(Exception):
    """所有业务异常的载体。禁止静默吞异常（R6）：要么抛这个，要么记录日志。"""

    def __init__(self, code, message, retryable=False, http_status=400, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.detail = detail or {}

    def to_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "detail": self.detail,
        }


def bad_request(message, code=DATA_FORMAT, detail=None):
    return AppError(code, message, retryable=False, http_status=400, detail=detail)


def not_found(message, detail=None):
    return AppError(DATA_NOT_FOUND, message, retryable=False, http_status=404, detail=detail)


def forbidden(message="无权执行该操作", detail=None):
    return AppError(PERM_DENIED, message, retryable=False, http_status=403, detail=detail)


def conflict(message, code=BIZ_STATE_INVALID, detail=None):
    return AppError(code, message, retryable=False, http_status=409, detail=detail)


def require(cond, message, code=DATA_REQUIRED, detail=None):
    """参数校验统一入口，避免散落的 if-raise。"""
    if not cond:
        raise AppError(code, message, retryable=False, http_status=400, detail=detail)
    return True


def require_keys(body, keys):
    for k in keys:
        v = body.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise AppError(DATA_REQUIRED, "缺少必填项：%s" % k, retryable=False, http_status=400)
    return True
