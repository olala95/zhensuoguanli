# -*- coding: utf-8 -*-
"""易用性复测（改造后）：在真实浏览器里走完一个完整门诊，统计操作员动作数。

与 tools/ux_measure.py（改造前基线）口径一致，可直接对比。
用法：python tools/ux_measure2.py
输出：%TEMP%/clinic_ux2.txt
前置：服务在 8787；Edge 带 --remote-debugging-port=9222
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
LOG = os.path.join(TMP, "clinic_ux2.txt")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"

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

    def click(self, sel, wait=0.7):
        r = self.b.js("(function(){var e=document.querySelector(%s);"
                      "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                      "e.scrollIntoView({block:'center'});e.click();return 'ok'})()"
                      % json.dumps(sel))
        self.b.drain(wait)
        _add("click")
        return r

    def click_text(self, text, tag="button", wait=0.9):
        r = self.b.js("(function(){var els=Array.from(document.querySelectorAll(%s));"
                      "var e=els.find(function(x){return (x.innerText||'').trim()===%s});"
                      "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                      "e.scrollIntoView({block:'center'});e.click();return 'ok'})()"
                      % (json.dumps(tag), json.dumps(text)))
        self.b.drain(wait)
        _add("click")
        return r

    def click_chip(self, input_id, value, wait=0.5):
        """点常用标签（舌象/脉象/证型快捷标签）"""
        r = self.b.js("(function(){var els=Array.from(document.querySelectorAll('span.btn'));"
                      "var e=els.find(function(x){return (x.innerText||'').trim()===%s"
                      " && /tagPick\\('(?:%s)'/.test(x.getAttribute('onclick')||'')});"
                      "if(!e)return 'no-el';e.click();return 'ok'})()"
                      % (json.dumps(value), input_id))
        self.b.drain(wait)
        _add("click")
        return r

    def type(self, sel, text, wait=0.25):
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "e.focus();var d=Object.getOwnPropertyDescriptor("
                "window.HTMLInputElement.prototype,'value');"
                "if(!d){d=Object.getOwnPropertyDescriptor("
                "window.HTMLTextAreaElement.prototype,'value');}"
                "d.set.call(e,%s);"
                "e.dispatchEvent(new Event('input',{bubbles:true}));"
                "e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
                % (json.dumps(sel), json.dumps(text)))
        r = self.b.js(expr)
        self.b.drain(wait)
        _add("type")
        return r

    def type_enter(self, sel, text, wait=0.9):
        """连续录入：输入 + 回车 = 一次动作"""
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "e.focus();var d=Object.getOwnPropertyDescriptor("
                "window.HTMLInputElement.prototype,'value');"
                "if(!d){d=Object.getOwnPropertyDescriptor("
                "window.HTMLTextAreaElement.prototype,'value');}"
                "d.set.call(e,%s);"
                "e.dispatchEvent(new Event('input',{bubbles:true}));"
                "e.dispatchEvent(new KeyboardEvent('keydown',"
                "{key:'Enter',bubbles:true,cancelable:true}));return 'ok'})()"
                % (json.dumps(sel), json.dumps(text)))
        r = self.b.js(expr)
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


def main():
    b = Browser(9222)
    ui = UI(b)

    # ---------- 登录 ----------
    phase("登录")
    ui.b.goto(BASE + "/", wait=2.0)
    ui.wait("#u", 8)
    ui.type("#u", "admin")
    ui.type("#p", PWD)
    ui.click_text("登录")
    ui.b.drain(2.0)

    # ---------- 建档 + 挂号 ----------
    phase("新建患者 + 建就诊")
    ui.wait("#kw", 6)
    ui.click_text("+ 新建患者")
    ui.wait("#name", 5)
    nm = "易用复测%s" % str(int(time.time()))[-5:]
    ui.type("#name", nm)
    ui.select("#gender", "F")
    ui.type("#birth", "1985-03-12")
    ui.type("#phone", "13900000002")
    ui.click_text("建档")
    ui.b.drain(2.5)

    # ---------- 接诊 ----------
    phase("接诊")
    ui.click_text("接诊")
    ui.b.drain(2.5)

    # ---------- 写病历 ----------
    phase("写病历")
    ui.wait("#cc_content", 8)
    for sel, v in [("#cc_content", "反复头晕 3 天"),
                   ("#pi_content", "3 天前劳累后出现头晕，伴乏力，纳差，夜寐欠安。"),
                   ("#tf_ins", "神疲，面色少华"),
                   ("#tf_aus", "语声低微"),
                   ("#tf_int", "畏寒，自汗，纳差，便溏"),
                   ("#syn_meth", "归脾汤加减"),
                   ("#dx_west", "眩晕"),
                   ("#dx_tcm", "眩晕·气血两虚")]:
        ui.type(sel, v)
    # 舌质/舌苔/脉象/证型/治则 走快捷标签（点一下）
    for iid, v in [("tf_tb", "淡胖"), ("tf_tc", "白腻"), ("tf_pd", "细弱"),
                   ("syn_name", "气血两虚"), ("syn_prin", "补气养血")]:
        ui.click_chip(iid, v)
    ui.click_text("确认签名（先自动保存）")
    ui.b.drain(1.2)
    if ui.wait("#sign_pwd", 5):
        ui.type("#sign_pwd", PWD)
        ui.click_text("确认签名", "button", 2.5)

    # ---------- 开方 ----------
    phase("开方（饮片方，12 味）")
    ui.b.goto(BASE + "/#/prescribe", wait=2.0)
    ui.wait("#rx_visit", 8)
    opts = ui.b.js("Array.from(document.querySelectorAll('#rx_visit option'))"
                   ".map(function(o){return o.value}).filter(Boolean)")
    if not opts:
        say("  !! 没有可选就诊"); return
    ui.select("#rx_visit", opts[0], 2.2)
    # 默认已是饮片方，直接建方
    ui.click_text("建方")
    ui.b.drain(2.5)
    ui.wait("#rx_fast", 6)
    herbs = ["黄芪 30", "当归 15", "党参 15", "白术 12", "茯苓 15", "炙甘草 6",
             "远志 10", "酸枣仁 15", "木香 10", "龙眼肉 12", "生姜 10", "大枣 15"]
    ok_n = 0
    for h in herbs:
        r = ui.type_enter("#rx_fast", h)
        if r != "ok":
            say("    !! 录入失败: %s (%s)" % (h, r))
        else:
            ok_n += 1
    say("  逐味录入 %d 味：每味「打字+回车」= 1 步 -> %d 步" % (ok_n, ok_n))
    ui.click_text("计价")
    ui.b.drain(1.2)
    ui.click_text("提交")
    ui.b.drain(1.8)
    ui.prompt(PWD)
    ui.click_text("确认（需口令）")
    ui.b.drain(2.5)
    say("  收尾：计价/提交/确认 = 3 步（确认仍需签一次口令）")

    # ---------- 发药 ----------
    phase("发药")
    ui.b.goto(BASE + "/#/pharmacy", wait=2.0)
    ui.wait("#ph_visit", 8)
    popts = ui.b.js("Array.from(document.querySelectorAll('#ph_visit option'))"
                    ".map(function(o){return o.value}).filter(Boolean)")
    if popts:
        ui.select("#ph_visit", popts[0], 2.5)
        xopts = ui.b.js("Array.from(document.querySelectorAll('#rx_id option'))"
                        ".map(function(o){return o.value}).filter(Boolean)")
        say("  处方下拉自动带出 %d 条：%s" % (len(xopts or []), xopts))
        if xopts:
            ui.click_text("发药")
            ui.b.drain(2.5)
            say("  发药动作：选就诊 + 点「发药」 = 2 步（处方自动带出、类型自动判断）")
        else:
            say("  !! 处方下拉为空")
    else:
        say("  !! 药房无可选就诊")

    # ---------- 收费 ----------
    phase("收费")
    ui.b.goto(BASE + "/#/billing", wait=2.0)
    ui.wait("#bl_visit", 8)
    bopts = ui.b.js("Array.from(document.querySelectorAll('#bl_visit option'))"
                    ".map(function(o){return o.value}).filter(Boolean)")
    if bopts:
        ui.select("#bl_visit", bopts[0], 1.8)
        ui.click_text("生成账单")
        ui.b.drain(2.5)
        state = ui.b.js("(document.querySelector('#bl_due')||{}).innerText||''")
        say("  账单区状态: %s" % state.replace("\n", " ")[:80])
        ui.prompt("200")   # 现金实收弹窗（金额自动带出，只需确认）
        ui.click_text("微信收讫")
        ui.b.drain(2.5)
        say("  收费动作：选就诊 + 生成账单 + 点「微信收讫」 = 3 步（不再手打金额）")

    PHASES.append(_cur)
    say("\n" + "=" * 62)
    say("改造后总计：点击 %d / 输入 %d / 下拉 %d / 弹窗 %d"
        % (N["click"], N["type"], N["select"], N["prompt"]))
    say("合计操作员动作：%d 次" % sum(N.values()))
    say("-" * 62)
    for p in PHASES:
        t = p["click"] + p["type"] + p["select"] + p["prompt"]
        if t == 0 and p["name"] == "启动":
            continue
        say("  %-22s %3d 步  (点%d 输%d 选%d 弹窗%d)"
            % (p["name"], t, p["click"], p["type"], p["select"], p["prompt"]))
    say("=" * 62)
    say("基线（改造前）：85 次")
    say("现在：%d 次    减少 %d 次（%.0f%%）"
        % (sum(N.values()), 85 - sum(N.values()),
           (85 - sum(N.values())) / 85.0 * 100))
    b.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say("FATAL: %r" % (e,))
        raise
