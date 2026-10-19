# -*- coding: utf-8 -*-
"""时间工具。契约：03 §4.1。

· 存储格式：本地时间字符串 'YYYY-MM-DD HH:MM:SS'（单机单时区）
· 审计时间一律取系统时间，不接受外部传入
· 时间回拨或跳变 > 5 分钟 → 记录 audit_time_anomaly（F88）
"""

import datetime
import threading

_FMT = "%Y-%m-%d %H:%M:%S"
_last_tick = None
_last_lock = threading.Lock()
ANOMALY_THRESHOLD_SECONDS = 300


def now():
    return datetime.datetime.now()


def now_str():
    return now().strftime(_FMT)


def today_str():
    return now().strftime("%Y-%m-%d")


def parse(s):
    if not s:
        return None
    if isinstance(s, datetime.datetime):
        return s
    for f in (_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def fmt(dt):
    return dt.strftime(_FMT) if dt else None


def calc_age(birth_date_str, ref=None):
    """返回 (years, months, days)；解析失败返回 None。"""
    b = parse(birth_date_str)
    if b is None:
        return None
    ref = ref or now()
    years = ref.year - b.year
    months = ref.month - b.month
    days = ref.day - b.day
    if days < 0:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    return years, months, days


def age_text(birth_date_str, ref=None):
    """年龄文本快照（用于处方/检验单据上的患者快照字段）。

    不满 1 岁按月显示，其余按岁；解析失败返回 None。
    """
    t = calc_age(birth_date_str, ref)
    if t is None:
        return None
    years, months, _ = t
    if years <= 0:
        return "%d个月" % max(0, months)
    if years < 3 and months:
        return "%d岁%d个月" % (years, months)
    return "%d岁" % years


def check_anomaly():
    """检测系统时间异常。返回 None 或异常字典。

    F88：回拨（当前时间早于上次记录）或跳变超过 5 分钟 → 记录并告警。
    """
    global _last_tick
    cur = now()
    with _last_lock:
        prev = _last_tick
        _last_tick = cur
        if prev is None:
            return None
        diff = (cur - prev).total_seconds()
        if diff < 0:
            return {"anomaly_type": "BACKWARD", "diff_seconds": diff,
                    "last_known_at": fmt(prev), "current_at": fmt(cur)}
        if diff > ANOMALY_THRESHOLD_SECONDS:
            return {"anomaly_type": "FORWARD_JUMP", "diff_seconds": diff,
                    "last_known_at": fmt(prev), "current_at": fmt(cur)}
    return None
