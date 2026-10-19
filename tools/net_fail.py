# -*- coding: utf-8 -*-
"""抓取页面里所有非 2xx 请求的具体 URL（定位控制台里的 404/409）。"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "net_fail.txt")
BASE = "http://127.0.0.1:8787"
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")

req = urllib.request.Request(BASE + "/api/auth/login",
                             data=json.dumps({"username": "admin", "password": PWD,
                                              "role_code": "ADMIN"}).encode(),
                             method="POST")
req.add_header("Content-Type", "application/json")
tok = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())["data"]["token"]

b = Browser(9222)
lines = []
try:
    b.events = []
    b.goto(BASE + "/", 1.2)
    b.js("localStorage.setItem('token', %s)" % json.dumps(tok))
    b.send("Page.reload", {"ignoreCache": True})
    b.drain(2.6)
    for h in ("#/workbench", "#/prescribe", "#/lab", "#/pharmacy", "#/treatment",
              "#/billing", "#/print", "#/ai", "#/report", "#/system"):
        b.js("location.hash='%s'" % h)
        b.drain(1.7)
    seen = set()
    for e in b.events:
        if e.get("method") == "Network.responseReceived":
            r = e["params"]["response"]
            st = r.get("status", 0)
            if st >= 400:
                key = "%s %s" % (st, r["url"])
                if key not in seen:
                    seen.add(key)
                    lines.append(key)
    if not seen:
        lines.append("no 4xx/5xx")
finally:
    b.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
