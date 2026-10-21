# -*- coding: utf-8 -*-
"""截检验项目维护页。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"
out = []

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
    b.goto(BASE + "/#/lab", wait=3.0)
    b.drain(2.5)
    # 展开"新增检验项目"面板，让表单也入镜
    b.js("(function(){var ds=document.querySelectorAll('details');"
         "for(var i=0;i<ds.length;i++){if((ds[i].innerText||'').indexOf('新增检验项目')>=0)"
         "{ds[i].open=true;break;}}})()")
    b.drain(0.8)
    out.append("rows=%s" % b.js("document.querySelectorAll('.card table tbody tr').length"))
    p = os.path.join(TMP, "lab_items.png")
    b.screenshot(p)
    out.append("shot -> " + p)
finally:
    b.close()

with open(os.path.join(TMP, "shots_lab.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
