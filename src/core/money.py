# -*- coding: utf-8 -*-
"""金额与精度。契约：03 §3。

· 所有金额内部存储为整数分（INTEGER），禁止浮点
· 舍入 ROUND_HALF_UP，只在行项目级舍入，不在总额级二次舍入
· 饮片单价可用毫元（1/1000 元）存储，行小计舍入到分
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from .errors import AppError, DATA_FORMAT


def to_fen(value):
    """元(float/str/Decimal) → 分(int)。拒绝非法输入。"""
    if value is None or value == "":
        return 0
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AppError(DATA_FORMAT, "金额格式非法：%r" % value)
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_milli(value):
    """元 → 毫元(int)。用于饮片单价等需要厘级精度的场景。"""
    if value is None or value == "":
        return 0
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AppError(DATA_FORMAT, "金额格式非法：%r" % value)
    return int((d * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fen_to_yuan(fen):
    """分 → 元字符串（2 位小数）。"""
    if fen is None:
        return "0.00"
    return str((Decimal(fen) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def milli_to_yuan(milli):
    if milli is None:
        return "0.000"
    return str((Decimal(milli) / 1000).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def round_half_up(value):
    """对 Decimal/数值做 ROUND_HALF_UP 到整数。"""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def line_amount_fen(unit_price_fen, quantity):
    """成药行小计（分）= round(单价(分) × 数量)。契约 §3.1"""
    return round_half_up(Decimal(unit_price_fen) * Decimal(str(quantity)))


def herb_line_amount_fen(price_milli_per_g, dose_g, doses):
    """饮片每味小计（分）= round(单价(毫元/g) × 剂量(g) × 付数 / 10)。契约 §3.2"""
    return round_half_up(Decimal(price_milli_per_g) * Decimal(str(dose_g))
                         * Decimal(str(doses)) / Decimal(10))


def fen(value):
    """展示用：分 → '12.34'（不带货币符号）。"""
    return fen_to_yuan(value)
