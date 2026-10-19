# -*- coding: utf-8 -*-
"""导出后端路由清单（路径 / 方法 / 权限 / 模块），供功能点验收测试构造用例。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import api  # noqa: E402
from modules import (m01_patient, m02_visit, m03_record, m04_ai, m05_lab,  # noqa: E402,F401
                     m06_prescription, m07_pharmacy, m08_treatment, m09_billing,
                     m10_print, m11_archive, m12_audit, m13_recovery, m14_system,
                     m15_report)

rows = []
for (method, path), meta in api.ROUTES.items():
    rows.append((meta.get("module") or "M00", method, path,
                 meta.get("perm") or "-", meta.get("action") or ""))
rows.sort()
for m, method, path, perm, action in rows:
    print("%s\t%s\t%s\t%s\t%s" % (m, method, path, perm, action))
print("---TOTAL=%d" % len(rows))
