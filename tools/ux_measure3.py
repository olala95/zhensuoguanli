# -*- coding: utf-8 -*-
"""复诊场景步数实测（本次提速的目标场景）。

准备：用接口造一个"已经看过一次病"的患者（首诊已开过归脾汤）。
测量：真实浏览器里走完一次复诊（接诊 → 病历 → 开方 → 发药 → 收费）。

用法：python tools/ux_measure3.py
输出：%TEMP%/clinic_ux3.txt
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
LOG = os.path.join(TMP, "clinic_ux3.txt")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"
TOKEN = None

_fh = open(LOG, "w", encoding="utf-8")
N = {"click": 0, "type": 0, "select": 0, "prompt": 0}
PHASES = []
_cur = {"name": "启动", "click": 0, "type": 0, "select": 0, "prompt": 0}


def say(s=""):
    _fh.write(str(s) + "\n")
    _fh.flush()


def phase(name):
    global _cur
    PHASES.append(_cur)
    _cur = {"name": name, "click": 0, "type": 0, "select": 0, "prompt": 0}
    say("\n--- %s ---" % name)


def _add(kind, n=1):
    N[kind] += n
    _cur[kind] += n


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
        say("HTTP %s %s → %s" % (e.code, path, raw[:600]))
        raise
    j = json.loads(raw)
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


class UI:
    def __init__(self, b):
        self.b = b

    def wait(self, sel, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.b.js("!!document.querySelector(%s)" % json.dumps(sel)):
                return True
            self.b.drain(0.25)
        return False

    def click_text(self, text, tag="button", wait=0.9):
        r = self.b.js("(function(){var els=Array.from(document.querySelectorAll(%s));"
                      "var e=els.find(function(x){return (x.innerText||'').trim()===%s});"
                      "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                      "e.scrollIntoView({block:'center'});e.click();return 'ok'})()"
                      % (json.dumps(tag), json.dumps(text)))
        self.b.drain(wait)
        _add("click")
        return r

    def click_match(self, kw, tag="button", wait=0.9):
        """按包含关系点按钮（按钮文案带括号说明时用这个）"""
        r = self.b.js("(function(){var els=Array.from(document.querySelectorAll(%s));"
                      "var e=els.find(function(x){return (x.innerText||'').indexOf(%s)>=0});"
                      "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                      "e.scrollIntoView({block:'center'});e.click();return 'ok'})()"
                      % (json.dumps(tag), json.dumps(kw)))
        self.b.drain(wait)
        _add("click")
        return r

    def click_chip(self, input_id, value, wait=0.5):
        r = self.b.js("(function(){var els=Array.from(document.querySelectorAll('span.btn'));"
                      "var e=els.find(function(x){return (x.innerText||'').trim()===%s"
                      " && /tagPick\\('(?:%s)'/.test(x.getAttribute('onclick')||'')});"
                      "if(!e)return 'no-el';e.click();return 'ok'})()"
                      % (json.dumps(value), input_id))
        self.b.drain(wait)
        _add("click")
        return r

    def type(self, sel, text, wait=0.25):
        r = self.b.js("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                      "e.focus();var d=Object.getOwnPropertyDescriptor("
                      "window.HTMLInputElement.prototype,'value');"
                      "if(!d){d=Object.getOwnPropertyDescriptor("
                      "window.HTMLTextAreaElement.prototype,'value');}"
                      "d.set.call(e,%s);"
                      "e.dispatchEvent(new Event('input',{bubbles:true}));"
                      "e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
                      % (json.dumps(sel), json.dumps(text)))
        self.b.drain(wait)
        _add("type")
        return r

    def select(self, sel, value, wait=0.8):
        r = self.b.js("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                      "e.value=%s;e.dispatchEvent(new Event('change',{bubbles:true}));"
                      "return 'ok'})()" % (json.dumps(sel), json.dumps(value)))
        self.b.drain(wait)
        _add("select")
        return r

    def prompt(self, answer=""):
        self.b.answers.append(answer)
        _add("prompt")
        return answer


def prepare():
    """造一个首诊已开过方的患者，返回新复诊 visit_id"""
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": PWD, "role_code": "ADMIN"})["token"]
    nm = "复测患者%s" % str(int(time.time()))[-5:]
    pat = api("/api/patient/create", "POST",
              {"name": nm, "gender": "F", "birth_date": "1985-03-12", "phone": "13900000003"})
    say("准备：患者 %s（id=%s）" % (nm, pat["id"]))

    v1 = api("/api/visit/create", "POST", {"patient_id": pat["id"], "visit_type": "TCM"})
    api("/api/visit/start", "POST", {"visit_id": v1["id"]})
    rx = api("/api/prescription/create", "POST",
             {"visit_id": v1["id"], "prescription_type": "HERB", "doses": 7})
    tpls = api("/api/prescription/templates", "GET")["items"]
    t = [x for x in tpls if x["name"] == "归脾汤"][0]
    ap = api("/api/prescription/template/apply", "POST",
             {"prescription_id": rx["id"], "template_id": t["id"]})
    api("/api/prescription/submit", "POST", {"prescription_id": rx["id"]})
    api("/api/prescription/confirm", "POST",
        {"prescription_id": rx["id"], "password": PWD})
    say("准备：首诊处方 %s（归脾汤 %s 味）" % (rx["prescription_no"], ap["herbs"]))
    bill = api("/api/bill/create", "POST", {"visit_id": v1["id"]})
    api("/api/bill/pay", "POST", {"bill_id": bill["id"], "payments": [
        {"channel": "WECHAT", "amount_fen": bill["total_amount_fen"],
         "received_amount_fen": bill["total_amount_fen"]}]})
    api("/api/visit/complete", "POST", {"visit_id": v1["id"]})

    v2 = api("/api/visit/create", "POST", {"patient_id": pat["id"], "visit_type": "TCM"})
    say("准备：复诊就诊已挂号 visit_id=%s（挂号动作未计入测量）\n" % v2["id"])
    return v2["id"], pat["id"]


def main():
    vid, pid = prepare()
    b = Browser(9222)
    ui = UI(b)

    # 登录（一天一次，单列）
    phase("登录（每天一次）")
    b.goto(BASE + "/", wait=2.0)
    if ui.wait("#u", 8):
        ui.type("#u", "admin")
        ui.type("#p", PWD)
        ui.click_text("登录")
        b.drain(2.5)

    # ---------- 接诊 ----------
    phase("接诊")
    b.goto(BASE + "/#/workbench", wait=2.5)
    r = ui.click_text("接诊", wait=2.5)
    say("  点「接诊」：%s" % r)

    # ---------- 写病历 ----------
    phase("写病历")
    ui.wait("#cc_content", 8)
    for sel, v in [("#cc_content", "头晕好转，仍乏力"),
                   ("#pi_content", "前方后头晕减轻，仍感乏力，纳可，夜寐改善。"),
                   ("#tf_ins", "神疲好转"),
                   ("#tf_aus", "语声偏低"),
                   ("#tf_int", "畏寒减轻，纳可"),
                   ("#syn_meth", "守前方加减"),
                   ("#dx_west", "眩晕"),
                   ("#dx_tcm", "眩晕·气血两虚")]:
        ui.type(sel, v)
    for iid, v in [("tf_tb", "淡红"), ("tf_tc", "薄白"), ("tf_pd", "细"),
                   ("syn_name", "气血两虚"), ("syn_prin", "补气养血")]:
        ui.click_chip(iid, v)
    ui.click_match("确认签名", "button", 1.5)
    if ui.wait("#sign_pwd", 4):
        ui.type("#sign_pwd", PWD)
        ui.click_text("确认签名", "button", 2.5)
        say("  首次签名需输口令（本次会话之后免输，签名变为 1 步）")
    else:
        say("  签名已免输口令（会话内已验证）")

    # ---------- 开方（照上次方）----------
    phase("开方（复诊：照上次方）")
    b.goto(BASE + "/#/prescribe/%s" % vid, wait=2.5)
    ui.wait("#rx_visit", 8)
    ui.click_match("建方", "button", 2.5)
    ui.wait("#rx_fast", 6)
    btn = b.js("(function(){var els=Array.from(document.querySelectorAll('button'));"
               "var e=els.find(function(x){return (x.innerText||'').indexOf('照上次方')>=0});"
               "return e?e.innerText.trim():'无按钮'})()")
    say("  照上次方按钮：%s" % btn)
    r = ui.click_match("照上次方", "button", 2.5)
    say("  点「照上次方」：%s" % r)
    b.drain(2.0)
    # RX_STATE 是模块作用域 const，读不到；直接看表格里的药名
    names = b.js("(function(){var t=document.querySelector('#rx_editor table tbody');"
                 "if(!t)return 'no-table';"
                 "return Array.from(t.querySelectorAll('tr')).map(function(r){"
                 "return r.cells[1]?r.cells[1].innerText.trim():'';}).join('、')})()")
    say("  照抄后明细：%s" % names)
    ui.click_match("提交", "button", 2.0)
    b.drain(2.0)
    if ui.wait("#sign_pwd", 2):
        ui.type("#sign_pwd", PWD)
        ui.click_text("确认签名", "button", 2.0)
    else:
        ui.click_match("确认", "button", 2.5)
    say("  收尾：提交 + 确认 = 2 步（提交时自动计价与规则预检）")
    # 用接口回查：照上次方到底有没有把明细写进库
    for p in api("/api/prescription/list?visit_id=%s" % vid, "GET")["items"]:
        g = api("/api/prescription/get?id=%s" % p["id"], "GET")
        say("  回查 %s %s：饮片 %d 味 / 成药 %d 种，金额 %s 元"
            % (p["prescription_no"], p["status"], len(g.get("herbs") or []),
               len(g.get("items") or []), p.get("total_amount")))

    # ---------- 发药 ----------
    phase("发药")
    b.goto(BASE + "/#/pharmacy", wait=2.0)
    ui.wait("#ph_visit", 8)
    popts = b.js("Array.from(document.querySelectorAll('#ph_visit option'))"
                 ".map(function(o){return o.value}).filter(Boolean)")
    if popts:
        ui.select("#ph_visit", popts[0], 2.5)
        ui.click_text("发药", "button", 2.5)
        say("  发药：选就诊 + 点发药 = 2 步")

    # ---------- 收费 ----------
    phase("收费")
    b.goto(BASE + "/#/billing", wait=2.0)
    ui.wait("#bl_visit", 8)
    bopts = b.js("Array.from(document.querySelectorAll('#bl_visit option'))"
                 ".map(function(o){return o.value}).filter(Boolean)")
    if bopts:
        ui.select("#bl_visit", bopts[0], 1.8)
        ui.click_text("生成账单", "button", 2.5)
        due = b.js("(document.querySelector('#bl_due')||{}).innerText||''")
        say("  账单状态：%s" % due.replace("\n", " ")[:60])
        ui.click_text("微信收讫", "button", 2.5)
        say("  收费：选就诊 + 生成账单 + 点收讫 = 3 步")

    PHASES.append(_cur)
    say("\n" + "=" * 62)
    say("复诊全流程：点击 %d / 输入 %d / 下拉 %d / 弹窗 %d"
        % (N["click"], N["type"], N["select"], N["prompt"]))
    total = sum(N.values())
    say("合计操作员动作：%d 次" % total)
    say("-" * 62)
    for p in PHASES:
        t = p["click"] + p["type"] + p["select"] + p["prompt"]
        if t == 0:
            continue
        say("  %-22s %3d 步  (点%d 输%d 选%d 弹窗%d)"
            % (p["name"], t, p["click"], p["type"], p["select"], p["prompt"]))
    say("=" * 62)
    say("对照：首诊全流程 50 步（改造前 85 步）")
    say("本次复诊 %d 步（含登录 3 步；不含挂号）" % total)
    b.close()
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        say(traceback.format_exc())
        rc = 2
    _fh.flush()
