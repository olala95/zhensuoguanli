# -*- coding: utf-8 -*-
"""截改造后的界面图（开方 / 病历 / 收费）。
用法：python tools/ux_shots.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"
LOG = os.path.join(TMP, "ux_shots.txt")
_fh = open(LOG, "w", encoding="utf-8")


def say(s):
    _fh.write(str(s) + "\n")
    _fh.flush()


def js(b, e):
    return b.js(e)


def main():
    b = Browser(9222)
    b.goto(BASE + "/", wait=2.0)
    if b.js("!!document.querySelector('#u')"):
        b.js("(function(){document.querySelector('#u').value=%s;"
             "document.querySelector('#p').value=%s;"
             "document.querySelector('#r').value='DOCTOR';"
             "document.querySelector('#r').dispatchEvent(new Event('change',{bubbles:true}));})()"
             % (json.dumps("admin"), json.dumps(PWD)))
        b.js("(function(){var els=Array.from(document.querySelectorAll('button'));"
             "var e=els.find(function(x){return (x.innerText||'').trim()==='登录'});"
             "if(e)e.click();})()")
        b.drain(2.5)

    # 找最近一个还在接诊中的就诊
    v = b.js("(async function(){return 1})()")
    vid = b.js("""
      (function(){var s=document.querySelector('#kw');return s?1:0})()
    """)
    b.goto(BASE + "/#/workbench", wait=2.5)
    opts = b.js("Array.from(document.querySelectorAll('#bl_visit option')).map(function(o){return o.value}).filter(Boolean)")
    # 工作台里没有 visit 下拉，用接口取
    b.js("window.__v=null")
    vid = b.js("""
      (async function(){try{var r=await fetch('/api/visit/list?status=IN_PROGRESS&limit=5',
        {headers:{'X-Token':localStorage.getItem('token')}});
        var j=await r.json();var it=(j.data&&j.data.items)||j.items||[];
        return it.length?it[0].id:0;}catch(e){return 0;}})()
    """, await_promise=True)
    say("visit id = %s" % vid)
    if not vid:
        vid = b.js("""
          (async function(){var r=await fetch('/api/visit/list?limit=5',
            {headers:{'X-Token':localStorage.getItem('token')}});
            var j=await r.json();var it=(j.data&&j.data.items)||j.items||[];
            return it.length?it[0].id:0;})()
        """, await_promise=True)
        say("fallback visit id = %s" % vid)

    shots = []
    if vid:
        b.goto(BASE + "/#/prescribe/%s" % vid, wait=3.0)
        b.js("(function(){var els=Array.from(document.querySelectorAll('button'));"
             "var e=els.find(function(x){return (x.innerText||'').trim()==='建方'});if(e)e.click();})()")
        b.drain(3.0)
        # 复诊提速：先展开经典方面板
        b.js("(function(){var els=Array.from(document.querySelectorAll('button'));"
             "var e=els.find(function(x){return (x.innerText||'').indexOf('经典方')>=0});"
             "if(e)e.click();})()")
        b.drain(1.0)
        # 再点「照上次方」，让明细落到表格里
        b.js("(function(){var els=Array.from(document.querySelectorAll('button'));"
             "var e=els.find(function(x){return (x.innerText||'').indexOf('照上次方')>=0});"
             "if(e)e.click();})()")
        b.drain(2.5)
        names = b.js("(function(){var t=document.querySelector('#rx_editor table tbody');"
                     "if(!t)return 'no-table';"
                     "return Array.from(t.querySelectorAll('tr')).map(function(r){"
                     "return r.cells[1]?r.cells[1].innerText.trim():'';}).join('、')})()")
        say("照上次方后明细：" + str(names)[:200])
        p = os.path.join(TMP, "ux_prescribe.png")
        b.screenshot(p); shots.append(p)
        say("prescribe -> " + p)

        b.goto(BASE + "/#/record/%s" % vid, wait=3.0)
        p = os.path.join(TMP, "ux_record.png")
        b.screenshot(p); shots.append(p)
        say("record -> " + p)

        b.goto(BASE + "/#/billing/%s" % vid, wait=3.0)
        p = os.path.join(TMP, "ux_billing.png")
        b.screenshot(p); shots.append(p)
        say("billing -> " + p)

    say("OK")
    b.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say("FATAL %r" % (e,))
