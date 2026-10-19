# -*- coding: utf-8 -*-
"""真实浏览器巡检：逐页打开前端，记录渲染内容、控制台错误、截图。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "clinic_ui_walk.txt")
TOKEN = open(os.path.join(TMP, "clinic_token.txt")).read().strip()
USER = json.dumps({"user_id": 1, "username": "admin", "display_name": "王医生",
                   "role_code": "ADMIN"}, ensure_ascii=False)

pages = [
    ("#/workbench", "工作台"),
    ("#/prescribe", "开方"),
    ("#/lab", "检验检查"),
    ("#/pharmacy", "药房库存"),
    ("#/treatment", "处置执行"),
    ("#/billing", "收费财务"),
    ("#/print", "打印硬件"),
    ("#/ai", "AI 语音"),
    ("#/report", "经营统计"),
    ("#/system", "系统管理"),
]

lines = []


def w(s=""):
    lines.append(str(s))


b = Browser(9222)
try:
    b.goto("http://127.0.0.1:8787/", 1.0)
    b.js("localStorage.setItem('token', %s); localStorage.setItem('user', %s);" %
         (json.dumps(TOKEN), json.dumps(USER, ensure_ascii=False)))
    # 关键：S.token 在脚本加载时从 localStorage 读取，改完必须整页刷新才生效
    b.send("Page.reload", {"ignoreCache": True})
    b.drain(2.2)
    w("刷新后 landed: %s" % str(b.js("location.hash")))
    for hash_, name in pages:
        b.events = []
        b.js("location.hash = %s" % json.dumps(hash_))
        b.drain(1.8)
        txt = b.js("document.body.innerText")
        html = b.js("document.querySelector('.main') ? document.querySelector('.main').innerHTML.length : -1")
        btns = b.js("document.querySelectorAll('.main button').length")
        inputs = b.js("document.querySelectorAll('.main input, .main select, .main textarea').length")
        errs = b.console_errors()
        shot = os.path.join(TMP, "ui_%s.png" % hash_.replace("#/", "").replace("/", "_"))
        try:
            b.screenshot(shot)
        except Exception:
            shot = "shot-fail"
        w("=" * 78)
        w("页面 %-12s %s" % (name, hash_))
        w("  main.html 长度=%s  按钮数=%s  输入框数=%s" % (html, btns, inputs))
        w("  文本长度=%s" % (len(txt or "")))
        w("  文本头部: %s" % (txt or "")[:700].replace("\n", " / "))
        if errs:
            w("  !! 控制台问题 %d:" % len(errs))
            for e in errs[:8]:
                w("     " + e.replace("\n", " ")[:300])
        else:
            w("  控制台：无 error/warning")
        w("  截图: %s" % shot)
finally:
    b.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
print(OUT)
