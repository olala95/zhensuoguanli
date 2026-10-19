# -*- coding: utf-8 -*-
"""探测：用指定 Python 导入全部业务模块，验证运行期兼容性。

不启动服务、不碰数据库，只做 import 级别的检查
（能抓到 3.9+ 才有的运行期特性，如 dict | dict、str.removeprefix 等）。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

print("Python %s" % sys.version.split()[0])

mods = [
    "core.db", "core.api", "core.audit", "core.session", "core.backup",
    "core.selftest", "core.migrate", "core.money", "core.ids",
    "modules.m01_patient", "modules.m02_visit", "modules.m03_record",
    "modules.m04_ai", "modules.m05_lab", "modules.m06_prescription",
    "modules.m07_pharmacy", "modules.m08_treatment", "modules.m09_billing",
    "modules.m10_print", "modules.m11_archive", "modules.m12_audit",
    "modules.m13_recovery", "modules.m14_system", "modules.m15_report",
]

fails = []
for m in mods:
    try:
        __import__(m)
    except Exception as exc:
        fails.append((m, "%s: %s" % (type(exc).__name__, exc)))

# 再试一下几个典型的新语法运行期特性
runtime_probes = [
    ("dict | dict", lambda: {"a": 1} | {"b": 2}),
    ("str.removeprefix", lambda: "xabc".removeprefix("x")),
    ("dict |=", None),
]
rt = []
for name, fn in runtime_probes:
    if fn is None:
        continue
    try:
        fn()
    except Exception as exc:
        rt.append("%s -> %s" % (name, type(exc).__name__))

print("\n模块导入：%d/%d 成功" % (len(mods) - len(fails), len(mods)))
for m, e in fails:
    print("  ✗ %-32s %s" % (m, e))
if rt:
    print("\n运行期特性不兼容：")
    for x in rt:
        print("  ✗ " + x)

ok = not fails and not rt
print("\n结论：%s" % ("可用" if ok else "不可用"))
sys.exit(0 if ok else 1)
