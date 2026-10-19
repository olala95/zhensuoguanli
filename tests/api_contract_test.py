# -*- coding: utf-8 -*-
"""前后端接口契约测试：前端点得出来的每个接口，后端必须真的接得住。

这类问题（E2005 接口不存在 / 方法不匹配）单元测试调服务函数时**永远测不到**——
服务函数是好的，坏的是"前端用什么 HTTP 方法去调"。只能静态比对。

门禁：
  A. 前端调用了但后端没有该路径        → 必须为 0
  B. 路径存在但 HTTP 方法不匹配        → 必须为 0
  C. 每个业务模块的接口都要有方法合法   → 信息统计，不算错

用法：python tests/api_contract_test.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))
sys.path.insert(0, os.path.join(PROJECT, "tools"))

import api_audit  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def main():
    missing, method_bad, unused, routes, calls = api_audit.audit()

    print("后端路由 %d 条 / 前端调用 %d 处（去重 %d）"
          % (len(routes), len(calls), len({(c[2], c[3]) for c in calls})))

    print("\n[A] 前端调用了但后端没有的接口")
    for fn, line, path, method, raw in missing:
        print("    %s:%d  %s %s" % (fn, line, method, path))
    check("A 项为 0（不存在点了报 E2005 的接口）", not missing,
          "%d 项" % len(missing))

    print("\n[B] HTTP 方法不匹配的接口")
    for fn, line, path, method, allowed, raw in method_bad:
        print("    %s:%d  %s（前端 %s / 后端 %s）" % (fn, line, path, method, allowed))
    check("B 项为 0（不存在方法不匹配的接口）", not method_bad,
          "%d 项" % len(method_bad))

    print("\n[C] 基本规模校验")
    check("后端路由数量达到 200+", len(routes) >= 200, "%d 条" % len(routes))
    check("前端调用数量达到 50+", len(calls) >= 50, "%d 处" % len(calls))
    get_only = sorted(p for p, ms in routes.items() if ms == {"GET"})
    print("    纯 GET（查询类）接口 %d 个——前端调它们时必须显式写 'GET'" % len(get_only))
    check("所有纯 GET 接口都被前端以 GET 调用或尚未接入",
          not any(c[2] in get_only and c[3] != "GET" for c in calls))

    print("\n" + "=" * 60)
    print("接口契约测试：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    for n in FAILED:
        print("  - %s" % n)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
