# -*- coding: utf-8 -*-
"""截中医/西医两种病历页对照图。"""
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


def api(path, token=None):
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("X-Token", token)
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode("utf-8"))
    return j["data"] if isinstance(j, dict) and "data" in j else j


def main():
    req = urllib.request.Request(BASE + "/api/auth/login",
                                 data=json.dumps({"username": "admin", "password": PWD,
                                                  "role_code": "ADMIN"}).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        tok = json.loads(r.read().decode("utf-8"))["data"]["token"]

    vs = api("/api/visit/list?limit=60", tok).get("items") or []
    tcm = next((v for v in vs if v.get("visit_type") == "TCM"), None)
    west = next((v for v in vs if v.get("visit_type") == "WEST"), None)
    out.append("TCM visit=%s WEST visit=%s" % (tcm and tcm["id"], west and west["id"]))
    if not (tcm and west):
        return 1

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

    # 中医就诊：先把模式切到中医
    b.js("localStorage.setItem('clinic_visit_mode','TCM')")
    b.goto(BASE + "/#/record/%s" % tcm["id"], wait=3.0)
    b.drain(2.5)
    p1 = os.path.join(TMP, "mode_tcm.png")
    b.screenshot(p1)
    out.append("tcm -> " + p1)

    b.js("localStorage.setItem('clinic_visit_mode','WEST')")
    b.goto(BASE + "/#/record/%s" % west["id"], wait=3.0)
    b.drain(2.5)
    p2 = os.path.join(TMP, "mode_west.png")
    b.screenshot(p2)
    out.append("west -> " + p2)
    b.close()
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        out.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(TMP, "shots_mode.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
