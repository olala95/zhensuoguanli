# -*- coding: utf-8 -*-
"""验证处置项目维护：列表、新增、改价、改归属、停用，并截处置页。"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
BASE = "http://127.0.0.1:8787"
TMP = os.environ.get("TEMP", ".")
PWD = "Clinic@2026"
TOKEN = None
lines, PASSED, FAILED = [], [], []


def say(s=""):
    lines.append(str(s))


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    say("  %s  %s %s" % ("PASS" if cond else "FAIL", name, extra))


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    if TOKEN:
        req.add_header("X-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        say("HTTP %s %s → %s" % (e.code, path, e.read().decode("utf-8")[:300]))
        raise
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


def main():
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": PWD, "role_code": "ADMIN"})["token"]

    act = api("/api/treatment/item/list").get("items") or []
    tcm = [x for x in act if int(x.get("is_tcm") or 0) == 1]
    say("启用中 %d 项（中医 %d / 西医 %d）" % (len(act), len(tcm), len(act) - len(tcm)))
    check("处置项目目录可用", len(act) >= 20, "%d 项" % len(act))
    check("不再有 M08 测试垃圾",
          not [x for x in act if x["name"].startswith(("M08", "M09", "M10"))])

    code = "VT%d" % (int(time.time()) % 100000)
    r = api("/api/treatment/item/create", "POST",
            {"item_code": code, "name": "验证用火针", "category": "TCM",
             "price_yuan": 55, "duration_min": 25, "is_tcm": 1, "need_record": 1})
    new_id = r["id"]
    row = next((x for x in api("/api/treatment/item/list").get("items") or []
                if x["id"] == new_id), None)
    say("新增：%s / %s / %.2f 元 / %s 分钟 / is_tcm=%s"
        % (row["name"], row["category"], (row["price_fen"] or 0) / 100.0,
           row["duration_min"], row["is_tcm"]))
    check("可新增处置项目并正确归类为中医",
          row and row["category"] == "TCM" and int(row["is_tcm"]) == 1)

    api("/api/treatment/item/update", "POST",
        {"id": new_id, "price_fen": 6600, "duration_min": 40, "is_tcm": 0,
         "reason": "验证改价与改归属"})
    row2 = next((x for x in api("/api/treatment/item/list").get("items") or []
                 if x["id"] == new_id), None)
    check("改价生效（66.00 元）", row2["price_fen"] == 6600, str(row2["price_fen"]))
    check("耗时已改", row2["duration_min"] == 40, str(row2["duration_min"]))
    check("归属可改为西医", int(row2["is_tcm"]) == 0, str(row2["is_tcm"]))

    api("/api/treatment/item/update", "POST", {"id": new_id, "is_active": 0})
    act2 = [x for x in api("/api/treatment/item/list").get("items") or []
            if x["id"] == new_id]
    allr = [x for x in api("/api/treatment/item/list?include_inactive=1").get("items") or []
            if x["id"] == new_id]
    check("停用后不出现在启用列表", not act2)
    check("停用后维护列表仍可见", len(allr) == 1)

    srch = api("/api/treatment/item/search?keyword=%s" % code).get("items") or []
    check("停用后不出现在开单搜索", not srch, "%d 项" % len(srch))

    api("/api/treatment/item/update", "POST", {"id": new_id, "is_active": 0,
                                               "reason": "验证完成"})
    say("")

    # 截图
    b = Browser(9222)
    try:
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
        b.goto(BASE + "/#/treatment", wait=3.0)
        b.drain(2.5)
        b.js("(function(){var ds=document.querySelectorAll('details');"
             "for(var i=0;i<ds.length;i++){if((ds[i].innerText||'').indexOf('新增处置项目')>=0)"
             "{ds[i].open=true;break;}}})()")
        b.drain(0.8)
        p = os.path.join(TMP, "treat_items.png")
        b.screenshot(p)
        say("截图 -> " + p)
    finally:
        b.close()

    say("")
    say("结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    if FAILED:
        say("失败项：%s" % FAILED)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(TMP, "clinic_treat.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
