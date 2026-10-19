# -*- coding: utf-8 -*-
"""交互审计：扫描 web/app.js 里所有 onclick 处理函数，找出"点了可能没反应"的按钮。

为什么需要这个：
  前端 `async function onXxx(){ await api(...) }` 如果**没有 try/catch**，
  接口一报错就是未捕获的 Promise rejection——用户看到的是"点了完全没反应"，
  既没有提示也没有日志。这是"功能用不了"最常见的真因，
  而 Python 探针（直连 HTTP）和接口对账都测不到。

检查项：
  A. onclick 里调用的函数根本没定义        → 点击直接 ReferenceError（致命）
  B. 处理函数会调 api() 但没有 try/catch   → 失败时静默（点了没反应，致命）
  C. 处理函数既不调 api() 也无 try/catch   → 仅提示（可能只是纯 UI 动作）

用法：python tools/ui_click_audit.py
输出：控制台 + %TEMP%/clinic_click_audit.txt
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

WEB = os.path.join(PROJECT, "web")
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_click_audit.txt")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_w = sys.stdout.write


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _w(msg + "\n")
    _LOG.write(msg + "\n")
    _LOG.flush()


def _read():
    out = {}
    for fn in sorted(os.listdir(WEB)):
        if fn.endswith(".js"):
            with open(os.path.join(WEB, fn), encoding="utf-8") as fh:
                out[fn] = fh.read()
    return out


def _find_defs(src):
    """返回 {func_name: (start, end_body)}，用括号配对定位函数体。"""
    defs = {}
    for m in re.finditer(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", src):
        name = m.group(1)
        i = src.find("{", m.end() - 1)
        if i < 0:
            continue
        depth, end = 0, -1
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end > 0:
            defs[name] = src[m.start():end]
    return defs


def _has_try(body):
    """函数体里 try 的位置是否早于第一个 await api( 的位置。"""
    t = body.find("try {")
    if t < 0:
        t = body.find("try{")
    calls = [m.start() for m in re.finditer(r"\bawait\s+api\s*\(", body)]
    if not calls:
        return True, None          # 不调 api，无所谓
    if t < 0:
        return False, calls[0]
    # try 必须在所有 await api 之前（若第一个 api 调用在 try 之外，仍可能静默）
    return t < calls[0], calls[0]


# 内联表达式里出现的关键字/内置方法，不是处理函数名，不能算"未定义"
_IGNORE = {
    "if", "for", "while", "switch", "catch", "return", "typeof", "Number", "String",
    "Boolean", "parseInt", "parseFloat", "encodeURIComponent", "decodeURIComponent",
    "alert", "confirm", "prompt", "print", "open", "close", "fetch", "setTimeout",
    "setInterval", "clearTimeout", "Array", "Object", "JSON", "Math",
    # 常见数组/字符串方法会被内联表达式用到
    "map", "filter", "join", "split", "slice", "splice", "push", "pop", "indexOf",
    "toLowerCase", "toUpperCase", "trim", "replace", "includes", "startsWith",
    "preventDefault", "stopPropagation", "esc", "val", "fmt", "render",
}


def main():
    files = _read()
    all_src = "\n".join(files.values())
    defs = _find_defs(all_src)

    # 收集所有 onclick / onchange / onkeydown 里调用的函数名
    handlers = set()
    for m in re.finditer(r"\bon(?:click|change|keydown|input)\s*=\s*\"([^\"]+)\"", all_src):
        for fn in re.findall(r"([A-Za-z_$][\w$]*)\s*\(", m.group(1)):
            if fn not in _IGNORE:
                handlers.add(fn)
    # 模板字符串里的 onclick（onclick="fn(${x})"）也已经在上面被扫到
    # 额外：JS 里 addEventListener 绑定的
    for m in re.finditer(r"addEventListener\s*\(\s*['\"][^'\"]+['\"]\s*,\s*([A-Za-z_$][\w$]*)",
                         all_src):
        handlers.add(m.group(1))
    # 页面视图函数：不挂在 onclick 上，而是由 render() 调用。
    # 它们同样会 await api() —— 一抛错整个页面就是白屏（比"点了没反应"更隐蔽）。
    for name in defs:
        if name.startswith("view") and name[4:5].isupper():
            handlers.add(name)
    # render 自身与各加载器也一起纳入（页面首屏依赖）
    for name in ("render", "loadSystemExtras", "arLoad"):
        if name in defs:
            handlers.add(name)

    print("=" * 70)
    print("交互审计 · 前端点击处理函数")
    print("函数定义 %d 个 / 被绑定的处理函数 %d 个" % (len(defs), len(handlers)))
    print("=" * 70)

    missing, silent, ok = [], [], []
    for fn in sorted(handlers):
        body = defs.get(fn)
        if body is None:
            missing.append(fn)
            continue
        calls_api = bool(re.search(r"\bapi\s*\(", body))
        guarded, _ = _has_try(body)
        if calls_api and not guarded:
            silent.append(fn)
        elif not calls_api and not guarded:
            ok.append((fn, "纯 UI 动作"))
        else:
            ok.append((fn, ""))

    print("\n【A】onclick 调用了但未定义的函数（点击必 ReferenceError，完全没反应）")
    if not missing:
        print("  无")
    for fn in missing:
        print("  - %s" % fn)

    print("\n【B】会调 api() 但没有 try/catch 包裹（接口一失败就静默，点了没反应）")
    if not silent:
        print("  无")
    for fn in silent:
        b = defs[fn]
        n = len(re.findall(r"\bawait\s+api\s*\(", b))
        line = all_src[:all_src.find(b)].count("\n") + 1
        print("  - %-24s 第 %d 行，%d 处 await api()" % (fn, line, n))

    print("\n【C】已保护或纯 UI 动作（%d 个）" % len(ok))
    names = [f for f, tag in ok]
    for i in range(0, len(names), 6):
        print("  " + "  ".join("%s" % x for x in names[i:i + 6]))

    print("\n" + "=" * 70)
    print("结论：致命 A=%d 项，B=%d 项" % (len(missing), len(silent)))
    print("明细：%s" % _LOG_PATH)
    print("=" * 70)
    return 1 if (missing or silent) else 0


if __name__ == "__main__":
    sys.exit(main())
