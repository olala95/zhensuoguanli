# -*- coding: utf-8 -*-
"""截收费页（含中药费/西药费小计）与报表页（诊疗类型筛选）。"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
TMP = os.environ.get("TEMP", ".")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"
out = []


def api(path, body=None, method="GET"):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method, headers={"Content-Type": "application/json"})
    if TOKEN:
        req.add_header("X-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode("utf-8"))
    return j["data"] if isinstance(j, dict) and "data" in j else j


TOKEN = None
req = urllib.request.Request(BASE + "/api/auth/login",
                             data=json.dumps({"username": "admin", "password": PWD,
                                              "role_code": "ADMIN"}).encode(),
                             method="POST", headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=20) as r:
    TOKEN = json.loads(r.read().decode("utf-8"))["data"]["token"]

# 找最近一张含 HERB 明细的账单，拿到它的就诊
target = None
for v in (api("/api/visit/list?limit=30").get("items") or []):
    try:
        for b in (api("/api/finance/bills?visit_id=%s&limit=3" % v["id"]).get("items") or []):
            det = api("/api/bill/get?id=%s" % b["id"])
            if any(i.get("source_type") == "HERB" for i in det.get("items") or []):
                target = (v["id"], b["id"], b["bill_no"])
                break
    except Exception:
        continue
    if target:
        break
out.append("target visit=%s bill=%s" % (target[0], target[2]) if target else "no target")
if not target:
    with open(os.path.join(TMP, "shots_billing.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    sys.exit(0)

b = Browser(9222)
b.goto(BASE + "/", wait=2.0)
if b.js("!!document.querySelector('#u')"):
    b.js("(function(){document.querySelector('#u').value='admin';"
         "document.querySelector('#p').value=%s;"
         "document.querySelector('#r').value='ADMIN';"
         "document.querySelector('#r').dispatchEvent(new Event('change',{bubbles:true}));})()"
         % json.dumps(PWD))
    b.js("(function(){var e=Array.from(document.querySelectorAll('button'))"
         ".find(function(x){return (x.innerText||'').trim()==='登录'});if(e)e.click();})()")
    b.drain(2.5)

b.goto(BASE + "/#/billing/%s" % target[0], wait=3.0)
b.drain(3.0)
p1 = os.path.join(TMP, "split_billing.png")
b.screenshot(p1)
out.append("billing -> " + p1)

# 报表：门诊量 + 只看中医
b.goto(BASE + "/#/report", wait=2.5)
b.js("(function(){var s=document.querySelector('#rp_kind');if(s){s.value='visit';"
     "s.dispatchEvent(new Event('change',{bubbles:true}));}})()")
b.drain(0.6)
b.js("(function(){var s=document.querySelector('#rp_type');if(s){s.value='TCM';"
     "s.dispatchEvent(new Event('change',{bubbles:true}));}})()")
b.drain(2.5)
p2 = os.path.join(TMP, "split_report.png")
b.screenshot(p2)
out.append("report -> " + p2)
b.close()

with open(os.path.join(TMP, "shots_billing.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
