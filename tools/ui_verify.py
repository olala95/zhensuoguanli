# -*- coding: utf-8 -*-
"""验证改造后的选择器：真实浏览器里选就诊 → 联动载入。"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "ui_verify.txt")

# 现登一次拿新 token
req = urllib.request.Request("http://127.0.0.1:8787/api/auth/login",
                             data=json.dumps({"username": "admin", "password": "Clinic@2026",
                                              "role_code": "ADMIN"}).encode(),
                             method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=10) as r:
    tok = json.loads(r.read().decode())["data"]["token"]

lines = []


def w(s=""):
    lines.append(str(s))


b = Browser(9222)
try:
    b.goto("http://127.0.0.1:8787/", 1.0)
    b.js("localStorage.setItem('token', %s); localStorage.setItem('user', %s);" %
         (json.dumps(tok), json.dumps(json.dumps({"user_id": 1, "username": "admin",
                                                  "display_name": "王医生",
                                                  "role_code": "ADMIN"}))))
    b.send("Page.reload", {"ignoreCache": True})
    b.drain(2.5)

    # --- 检验页：选就诊应自动载入 ---
    b.js("location.hash='#/lab'"); b.drain(1.8)
    opts = b.js("Array.from(document.querySelectorAll('#lb_visit option')).map(o=>o.value+':'+o.textContent).slice(0,3)")
    w("lab 就诊下拉 = %s" % opts)
    n = b.js("document.querySelectorAll('#lb_visit option').length")
    w("lab 下拉项数 = %s" % n)
    if isinstance(n, int) and n > 1:
        b.js("(function(){var s=document.getElementById('lb_visit');s.selectedIndex=1;s.dispatchEvent(new Event('change'));return s.value})()")
        b.drain(2.0)
        body = b.js("document.getElementById('lb_body').innerText")
        w("lab 载入后文本长度 = %s" % (len(body or "")))
        w("lab 载入后片段 = %s" % (body or "")[:300].replace("\n", " / "))
        chk = b.js("document.querySelectorAll('.lb_item_chk').length")
        w("lab 检验项目勾选框 = %s" % chk)
    w("lab console: %s" % b.console_errors()[:3])

    # --- 收费页：选就诊应联动账单 ---
    b.events = []
    b.js("location.hash='#/billing'"); b.drain(1.8)
    n2 = b.js("document.querySelectorAll('#bl_visit option').length")
    w("billing 下拉项数 = %s" % n2)
    if isinstance(n2, int) and n2 > 1:
        b.js("(function(){var s=document.getElementById('bl_visit');s.selectedIndex=1;s.dispatchEvent(new Event('change'));return s.value})()")
        b.drain(2.0)
        w("billing 账单下拉 = %s" % b.js("Array.from(document.querySelectorAll('#bl_bill option')).map(o=>o.value+':'+o.textContent)"))
        w("billing 提示 = %s" % b.js("document.getElementById('bl_info').innerText")[:200])
    w("billing console: %s" % b.console_errors()[:3])

    # --- 药房：选就诊 → 处方下拉 ---
    b.events = []
    b.js("location.hash='#/pharmacy'"); b.drain(1.8)
    n3 = b.js("document.querySelectorAll('#ph_visit option').length")
    w("pharmacy 下拉项数 = %s" % n3)
    if isinstance(n3, int) and n3 > 1:
        b.js("(function(){var s=document.getElementById('ph_visit');s.selectedIndex=1;s.dispatchEvent(new Event('change'));return s.value})()")
        b.drain(2.0)
        w("pharmacy 处方下拉 = %s" % b.js("Array.from(document.querySelectorAll('#rx_id option')).map(o=>o.value+':'+o.textContent)"))
    w("pharmacy console: %s" % b.console_errors()[:3])

    # --- 打印页 ---
    b.events = []
    b.js("location.hash='#/print'"); b.drain(1.8)
    w("print 下拉项数 = %s" % b.js("document.querySelectorAll('#pr_visit option').length"))
    w("print console: %s" % b.console_errors()[:3])

    # --- 工作台筛选 ---
    b.events = []
    b.js("location.hash='#/workbench'"); b.drain(2.0)
    w("工作台按钮数 = %s" % b.js("document.querySelectorAll('.main button').length"))
    w("工作台筛选按钮 = %s" % b.js("Array.from(document.querySelectorAll('.main .row button')).map(b=>b.textContent).slice(0,6)"))
    w("workbench console: %s" % b.console_errors()[:3])
finally:
    b.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
