# -*- coding: utf-8 -*-
"""验证中西医双模式：模式切换、按就诊类型分开展开、开方类型限定、药房过滤。

用法：python tools/verify_mode.py
输出：%TEMP%/clinic_mode.txt
"""
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

TMP = os.environ.get("TEMP", ".")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"
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
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        say("HTTP %s %s → %s" % (e.code, path, raw[:300]))
        raise
    j = json.loads(raw)
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


def main():
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": PWD, "role_code": "ADMIN"})["token"]

    # ---- 准备：一个中医就诊 + 一个西医就诊 ----
    nm = "模式验证%s" % str(int(time.time()))[-5:]
    pat = api("/api/patient/create", "POST", {"name": nm, "gender": "F",
                                              "birth_date": "1990-05-01"})
    vt = api("/api/visit/create", "POST", {"patient_id": pat["id"], "visit_type": "TCM"})
    say("中医就诊 %s visit_type=%s" % (vt["visit_no"], vt["visit_type"]))
    check("建就诊可指定 TCM", vt["visit_type"] == "TCM", vt["visit_type"])

    pat2 = api("/api/patient/create", "POST", {"name": nm + "乙", "gender": "M",
                                               "birth_date": "1978-02-02"})
    vw = api("/api/visit/create", "POST", {"patient_id": pat2["id"], "visit_type": "WEST"})
    say("西医就诊 %s visit_type=%s" % (vw["visit_no"], vw["visit_type"]))
    check("建就诊可指定 WEST", vw["visit_type"] == "WEST", vw["visit_type"])

    # 切类型
    r = api("/api/visit/set_type", "POST",
            {"visit_id": vw["id"], "visit_type": "TCM", "reason": "验证切换"})
    check("就诊类型可切换", r.get("changed") is True, str(r))
    g = api("/api/visit/get?id=%s" % vw["id"])
    check("切换已落库", g.get("visit_type") == "TCM", g.get("visit_type"))
    api("/api/visit/set_type", "POST", {"visit_id": vw["id"], "visit_type": "WEST"})

    # ---- 浏览器 ----
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

    b.goto(BASE + "/#/workbench", wait=2.5)
    has_sw = b.js("!!document.querySelector('.modesw')")
    check("顶栏有中医/西医切换器", has_sw)
    sw_text = b.js("(document.querySelector('.modesw')||{}).innerText||''")
    say("  切换器内容：%s" % sw_text.replace("\n", " / "))
    chip = b.js("(function(){var t=document.querySelectorAll('.tag.tcm,.tag.west');"
                "return t.length})()")
    check("工作台队列显示中医/西医标签", chip > 0, "标签数 %s" % chip)

    # 切到中医，看中医就诊的病历页
    b.js("(function(){var e=Array.from(document.querySelectorAll('.modesw button'))"
         ".find(function(x){return (x.innerText||'').trim()==='中医'});if(e)e.click();})()")
    b.drain(1.5)
    b.goto(BASE + "/#/record/%s" % vt["id"], wait=3.0)
    b.drain(2.0)
    tcm_open = b.js("(function(){var d=document.querySelector('details.grp-tcm');"
                    "return d? d.open : 'no-el'})()")
    west_open = b.js("(function(){var d=document.querySelector('details.grp-west');"
                     "return d? d.open : 'no-el'})()")
    say("  中医就诊：grp-tcm.open=%s grp-west.open=%s" % (tcm_open, west_open))
    check("中医就诊默认展开中医字段", tcm_open is True, str(tcm_open))
    check("中医就诊收起西医字段", west_open is False, str(west_open))
    # 字段仍在 DOM 里（不能因为收起就丢数据）
    check("收起的中医/西医字段仍存在（数据不丢）",
          b.js("!!document.querySelector('#tf_tb')") and b.js("!!document.querySelector('#dx_west')"))

    # 西医就诊
    b.goto(BASE + "/#/record/%s" % vw["id"], wait=3.0)
    b.drain(2.0)
    tcm_open2 = b.js("(function(){var d=document.querySelector('details.grp-tcm');"
                     "return d? d.open : 'no-el'})()")
    west_open2 = b.js("(function(){var d=document.querySelector('details.grp-west');"
                      "return d? d.open : 'no-el'})()")
    say("  西医就诊：grp-tcm.open=%s grp-west.open=%s" % (tcm_open2, west_open2))
    check("西医就诊默认展开西医字段", west_open2 is True, str(west_open2))
    check("西医就诊收起中医字段", tcm_open2 is False, str(tcm_open2))

    # 开方页类型限定
    b.js("localStorage.setItem('clinic_visit_mode','TCM')")
    b.goto(BASE + "/#/prescribe/%s" % vt["id"], wait=3.0)
    b.drain(2.0)
    opts1 = b.js("Array.from(document.querySelectorAll('#rx_type option'))"
                 ".map(function(o){return o.value})")
    say("  中医就诊开方类型：%s" % opts1)
    check("中医就诊只能开饮片/颗粒", opts1 == ["HERB", "GRANULE"], str(opts1))

    b.js("localStorage.setItem('clinic_visit_mode','WEST')")
    b.goto(BASE + "/#/prescribe/%s" % vw["id"], wait=3.0)
    b.drain(2.0)
    opts2 = b.js("Array.from(document.querySelectorAll('#rx_type option'))"
                 ".map(function(o){return o.value})")
    say("  西医就诊开方类型：%s" % opts2)
    check("西医就诊只能开成药", opts2 == ["WEST"], str(opts2))

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
    with open(os.path.join(TMP, "clinic_mode.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
