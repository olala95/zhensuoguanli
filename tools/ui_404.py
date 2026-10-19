# -*- coding: utf-8 -*-
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

OUT = os.path.join(os.environ.get("TEMP", "."), "ui_404.txt")
req = urllib.request.Request("http://127.0.0.1:8787/api/auth/login",
                             data=json.dumps({"username": "admin", "password": "Clinic@2026",
                                              "role_code": "ADMIN"}).encode(), method="POST")
req.add_header("Content-Type", "application/json")
tok = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())["data"]["token"]

b = Browser(9222)
lines = []
try:
    b.goto("http://127.0.0.1:8787/", 1.0)
    b.js("localStorage.setItem('token', %s)" % json.dumps(tok))
    b.send("Page.reload", {"ignoreCache": True})
    b.drain(2.0)
    for h in ("#/lab", "#/print", "#/system", "#/report"):
        b.js("location.hash='%s'" % h)
        b.drain(1.5)
        bad = b.js("performance.getEntriesByType('resource').filter(e=>e.responseStatus&&e.responseStatus>=400).map(e=>e.responseStatus+' '+e.name)")
        lines.append("%s -> %s" % (h, bad))
finally:
    b.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
