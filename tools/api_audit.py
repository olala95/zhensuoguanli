# -*- coding: utf-8 -*-
"""前后端接口对账：把 web/*.js 里的 api() 调用与后端注册的路由表逐一比对。

用来回答"前端点了会 404/E2005 吗"——这类问题单元测试根本测不到，
必须静态比对才能一次查全。

判定规则（严格对齐 web/app.js 的 api() 实现）：
    api(path, body, method)  →  method || (body ? 'POST' : 'GET')

输出：控制台 + %TEMP%/clinic_api_audit.txt
用法：python tools/api_audit.py
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import api  # noqa: E402
# 导入业务模块即完成路由注册
from modules import (m01_patient, m02_visit, m03_record, m04_ai, m05_lab,  # noqa: E402,F401
                     m06_prescription, m07_pharmacy, m08_treatment, m09_billing,
                     m10_print, m11_archive, m12_audit, m13_recovery, m14_system,
                     m15_report)

_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_api_audit.txt")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_stdout_write = sys.stdout.write


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _stdout_write(msg + "\n")
    _LOG.write(msg + "\n")
    _LOG.flush()


# ---------------- 解析前端 api() 调用 ----------------

def _find_call_end(text, start):
    """start 指向 '(' 之后。返回匹配的 ')' 下标。"""
    depth = 1
    i = start
    instr = None
    while i < len(text):
        ch = text[i]
        if instr:
            if ch == "\\":
                i += 2
                continue
            if ch == instr:
                instr = None
        elif ch in "\"'`":
            instr = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _split_args(inner):
    """按顶层逗号切分实参。"""
    args, depth, cur = [], 0, ""
    instr = None
    i = 0
    while i < len(inner):
        ch = inner[i]
        if instr:
            cur += ch
            if ch == "\\":
                cur += inner[i + 1]
                i += 2
                continue
            if ch == instr:
                instr = None
            i += 1
            continue
        if ch in "\"'`":
            instr = ch
            cur += ch
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip():
        args.append(cur.strip())
    return args


def parse_front(web_dir):
    """返回 [(file, line, path, method, raw)]"""
    out = []
    for fn in sorted(os.listdir(web_dir)):
        if not fn.endswith(".js"):
            continue
        p = os.path.join(web_dir, fn)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        for m in re.finditer(r"(?<![A-Za-z0-9_$.])api\s*\(", text):
            lp = m.end() - 1                     # '(' 的位置
            end = _find_call_end(text, m.end())
            inner = text[m.end():end]
            args = _split_args(inner)
            if not args:
                continue
            first = args[0].strip()
            if len(first) < 2 or first[0] not in "\"'`":
                continue                          # 动态路径（变量），跳过
            path = first[1:-1]
            if "${" in path:
                continue                          # 模板字符串拼出来的，跳过
            if not path.startswith("/api/"):
                continue
            method = None
            if len(args) >= 3:
                cand = args[2].strip().strip("\"'")
                if cand in ("GET", "POST", "PUT", "DELETE"):
                    method = cand
            if method is None:
                body = args[1].strip() if len(args) >= 2 else ""
                # 对齐 api()：body 空/null/undefined → GET，否则 POST
                method = "GET" if body in ("", "null", "undefined") else "POST"
            line = text[:m.start()].count("\n") + 1
            out.append((fn, line, path, method, text[m.start():end + 1].replace("\n", " ")[:90]))
    return out


def audit(web_dir=None):
    """返回 (missing, method_bad, unused, routes, calls)。供测试与命令行共用。"""
    web_dir = web_dir or os.path.join(PROJECT, "web")
    routes = {}
    for (method, path), meta in api.ROUTES.items():
        routes.setdefault(path, set()).add(method)
    calls = parse_front(web_dir)

    missing, method_bad, seen_bad = [], [], set()
    for fn, line, path, method, raw in calls:
        if path not in routes:
            missing.append((fn, line, path, method, raw))
        elif method not in routes[path]:
            if (path, method) not in seen_bad:
                seen_bad.add((path, method))
                method_bad.append((fn, line, path, method, sorted(routes[path]), raw))
    # 已接入判定：除了 api() 的字面量调用，还要算上"变量映射"的写法，
    # 例如报表页把 6 个路径放在 {kind: '/api/report/...'} 映射表里再用 api(paths[kind])
    literals = set(c[2] for c in calls)
    for fn in sorted(os.listdir(web_dir)):
        if not fn.endswith(".js"):
            continue
        with open(os.path.join(web_dir, fn), encoding="utf-8") as fh:
            literals |= set(re.findall(r"['\"](/api/[A-Za-z0-9_/\-]+)['\"]", fh.read()))
    used = {p for p in literals if p in routes}
    unused = sorted(p for p in routes if p not in used)
    return missing, method_bad, unused, routes, calls


def main():
    web_dir = os.path.join(PROJECT, "web")
    missing, method_bad, unused, routes, calls = audit(web_dir)

    print("=" * 74)
    print("前后端接口对账")
    print("后端已注册路由：%d 条（去重路径 %d 个）" % (len(api.ROUTES), len(routes)))
    print("前端 api() 调用：%d 处（去重 %d 个）"
          % (len(calls), len({(c[2], c[3]) for c in calls})))
    print("=" * 74)

    print("\n【A】前端调用了，但后端根本没有这个接口（点了必 E2005）")
    if not missing:
        print("  无")
    for fn, line, path, method, raw in missing:
        print("  %s:%d  %s %s" % (fn, line, method, path))
        print("        %s" % raw)

    print("\n【B】接口存在，但前端用的 HTTP 方法与后端注册的不一致（点了也 E2005）")
    if not method_bad:
        print("  无")
    for fn, line, path, method, allowed, raw in method_bad:
        print("  %s:%d  前端 %s，后端只注册了 %s  →  %s" % (fn, line, method, allowed, path))
        print("        %s" % raw)

    # 3) 后端有但前端没用（信息性，不算错）
    # 按模块统计接入度——这才是"哪些功能前端还没做"的真实清单
    used = {p for p in routes if p not in set(unused)}
    mod_all, mod_used = {}, {}
    for (method, path), meta in api.ROUTES.items():
        m = meta.get("module") or "M00"
        mod_all[m] = mod_all.get(m, 0) + 1
        if path in used:
            mod_used[m] = mod_used.get(m, 0) + 1
    print("\n【C】按模块看前端接入度（未接入 = 该功能在界面上根本点不到）")
    print("  %-6s %-16s %-8s %-8s %-8s" % ("模块", "名称", "接口数", "已接入", "未接入"))
    print("  " + "-" * 54)
    names = {"M01": "患者中心", "M02": "就诊中心", "M03": "临床病历", "M04": "AI与语音",
             "M05": "检验检查", "M06": "处方用药", "M07": "药房库存", "M08": "处置执行",
             "M09": "收费财务", "M10": "打印硬件", "M11": "病历归档", "M12": "安全审计",
             "M13": "数据安全", "M14": "系统管理", "M15": "经营统计", "M00": "未标注"}
    for m in sorted(mod_all):
        a, u = mod_all[m], mod_used.get(m, 0)
        flag = "  <<< 界面完全缺失" if u == 0 and a > 0 else ""
        print("  %-6s %-16s %-8d %-8d %-8d%s" % (m, names.get(m, m), a, u, a - u, flag))

    print("\n【D】后端有、前端尚未接入的接口（%d 个）" % len(unused))
    for p in unused:
        print("  %-38s %s" % (p, ",".join(sorted(routes[p]))))

    # 动态路径的调用点（api(someVar, ...)）：静态分析判不了，列出来人工核对
    dyn = set()
    for fn in sorted(os.listdir(web_dir)):
        if not fn.endswith(".js"):
            continue
        with open(os.path.join(web_dir, fn), encoding="utf-8") as fh:
            text = fh.read()
        for m in re.finditer(r"(?<![A-Za-z0-9_$.])api\s*\(\s*([A-Za-z_$][A-Za-z0-9_$.]*)", text):
            v = m.group(1)
            if v in ("path", "paths"):
                continue
            if v.startswith("'") or v.startswith('"'):
                continue
            line = text[:m.start()].count("\n") + 1
            dyn.add("%s:%d  api(%s, ...)" % (fn, line, v))
    if dyn:
        print("\n【E】动态路径调用点（静态判不了，需人工核对）%d 处" % len(dyn))
        for x in sorted(dyn):
            print("  %s" % x)

    print("\n" + "=" * 74)
    print("结论：致命问题 A=%d 项，B=%d 项" % (len(missing), len(method_bad)))
    print("明细：%s" % _LOG_PATH)
    print("=" * 74)
    return 1 if (missing or method_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
