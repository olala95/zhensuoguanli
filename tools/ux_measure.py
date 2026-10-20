# -*- coding: utf-8 -*-
"""易用性实测：在真实浏览器里走完一个完整门诊，统计"操作员动作数"。

与 ui_e2e.py 的区别：
  ui_e2e 只关心"能不能跑通"（断言）；
  本脚本关心"要费多少力气"（计数）——每次点击 / 每次输入 / 每次弹窗都算一步。

用法：python tools/ux_measure.py
输出：控制台 + %TEMP%/clinic_ux_measure.txt
前置：服务在 8787；Edge 带 --remote-debugging-port=9222
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
LOG = os.path.join(TMP, "clinic_ux_measure.txt")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"

_fh = open(LOG, "w", encoding="utf-8")
N = {"click": 0, "type": 0, "select": 0, "prompt": 0, "page": 0}
PHASES = []
_cur = {"name": "启动", "click": 0, "type": 0, "select": 0, "prompt": 0}


def say(s=""):
    print(s)
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

    def js(self, expr):
        return self.b.js(expr)

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

    def type(self, sel, text, wait=0.25):
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "e.focus();var s=Object.getOwnPropertyDescriptor("
                "window.HTMLInputElement.prototype,'value')"
                "||Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');"
                "s.set.call(e,%s);"
                "e.dispatchEvent(new Event('input',{bubbles:true}));"
                "e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
                % (json.dumps(sel), json.dumps(text)))
        r = self.b.js(expr)
        self.b.drain(wait)
        _add("type")
        return r

    def select(self, sel, value, wait=0.8):
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "e.value=%s;e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
                % (json.dumps(sel), json.dumps(value)))
        r = self.b.js(expr)
        self.b.drain(wait)
        _add("select")
        return r

    def prompt(self, answer=""):
        """页面里的 prompt() 会被 cdp 自动应答；这里只计数并预置答案。"""
        self.b.answers.append(answer)
        _add("prompt")
        return answer

    def text(self, limit=1200):
        t = self.b.js("document.body.innerText.slice(0,%d)" % limit)
        return t or ""


def main():
    b = Browser(9222)
    ui = UI(b)

    # ---------- 登录 ----------
    phase("登录")
    ui.b.goto(BASE + "/", wait=2.0)
    ui.wait("#u", 8)
    ui.type("#u", "admin")
    ui.type("#p", PWD)
    ui.select("#r", "DOCTOR")
    ui.click_text("登录")
    ui.b.drain(2.0)
    say("  登录后: " + ui.text(160).replace("\n", " | ")[:160])

    # ---------- 建档 + 挂号 ----------
    phase("新建患者 + 建就诊")
    if not ui.wait("#kw", 6):
        ui.b.goto(BASE + "/#/workbench", wait=2.0)
        ui.wait("#kw", 6)
    ui.click_text("+ 新建患者")
    ui.wait("#name", 5)
    nm = "测试易用%s" % str(int(time.time()))[-5:]
    for sel, v in [("#name", nm), ("#gender", "F"), ("#birth", "1985-03-12"),
                   ("#phone", "13900000001")]:
        if sel == "#gender":
            ui.select(sel, v)
        else:
            ui.type(sel, v)
    say("  新建患者表单必填/常填字段数: 4（姓名/性别/出生/手机）")
    ui.click_text("建档")
    ui.b.drain(2.0)

    # ---------- 接诊 ----------
    phase("接诊")
    r = ui.b.js("(function(){var els=Array.from(document.querySelectorAll('button'));"
                "var e=els.find(function(x){return (x.innerText||'').trim()==='接诊'});"
                "if(!e)return 'no-el';e.click();return 'ok'})()")
    _add("click")
    ui.b.drain(2.5)
    say("  接诊点击结果: %s" % r)

    # ---------- 写病历（填到能确认签名） ----------
    phase("写病历")
    ui.wait("#cc_content", 8)
    fields = [("#cc_content", "反复头晕 3 天"),
              ("#pi_content", "3 天前劳累后出现头晕，伴乏力，纳差，夜寐欠安。"),
              ("#tf_ins", "神疲，面色少华"),
              ("#tf_aus", "语声低微"),
              ("#tf_int", "畏寒，自汗，纳差，便溏"),
              ("#tf_tb", "淡胖"),
              ("#tf_tc", "白腻"),
              ("#tf_pd", "细弱"),
              ("#syn_name", "气血两虚"),
              ("#syn_prin", "补气养血"),
              ("#syn_meth", "归脾汤加减"),
              ("#dx_west", "眩晕"),
              ("#dx_tcm", "眩晕·气血两虚")]
    for sel, v in fields:
        ok = ui.type(sel, v)
        if ok != "ok":
            say("    !! 字段不存在: %s (%s)" % (sel, ok))
    say("  病历页手填字段数（中医门诊最小集）: %d" % len(fields))
    ui.click_text("保存全部")
    ui.b.drain(1.8)
    say("  保存全部 -> %s" % ui.b.js("(document.querySelector('#toast')||{}).innerText||'(无)'"))

    # 确认签名（弹窗内输入口令）
    ui.click_text("确认签名")
    ui.b.drain(1.2)
    if ui.wait("#sign_pwd", 5):
        ui.type("#sign_pwd", PWD)
        ui.click_text("确认签名", "button", 2.5)
        say("  确认签名：点按钮 -> 弹窗输口令 -> 再点确认 = 3 步")
    else:
        say("  !! 未弹出签名框")
    ui.b.drain(1.5)
    say("  病历状态: " + ui.text(300).replace("\n", " | ")[:200])

    # ---------- 开方 ----------
    phase("开方（饮片方，12 味）")
    ui.b.goto(BASE + "/#/prescribe", wait=2.0)
    ui.wait("#rx_visit", 8)
    opts = ui.b.js("Array.from(document.querySelectorAll('#rx_visit option')).map(function(o){return o.value}).filter(Boolean)")
    if not opts:
        say("  !! 没有可选就诊"); return
    ui.select("#rx_visit", opts[0], 1.8)
    say("  选择就诊后自动载入，无需点「载入」")

    # 建方：类型 + 付数 + 煎法 + 服法
    ui.select("#rx_type", "HERB", 0.3)
    ui.type("#rx_doses", "7")
    ui.type("#rx_decoction", "水煎")
    ui.type("#rx_admin", "每日一剂，分两次温服")
    ui.click_text("建方")
    ui.b.drain(2.5)
    say("  建方阶段动作数（类型/付数/煎法/服法/建方）: 5")

    herbs = ["黄芪", "当归", "党参", "白术", "茯苓", "炙甘草",
             "远志", "酸枣仁", "木香", "龙眼肉", "生姜", "大枣"]
    added = 0
    for h in herbs:
        ui.type("#rx_kw", h)
        ui.click_text("搜索")
        ui.b.drain(0.5)
        r = ui.b.js("(function(){var els=Array.from(document.querySelectorAll('#rx_results span'));"
                    "if(!els.length)return 'no-result';els[0].click();return 'ok'})()")
        _add("click")
        ui.b.drain(0.35)
        if r != "ok":
            say("    !! 搜索无结果: %s" % h)
            continue
        ui.prompt("15")   # 剂量 prompt
        added += 1
    say("  加 %d 味饮片，每味 = 输关键词+点搜索+点药名+prompt 输剂量 = 4 步 -> %d 步"
        % (added, added * 4))
    ui.click_text("保存明细")
    ui.b.drain(2.0)
    say("  另需单独点「保存明细」才落库（反直觉）")

    ui.click_text("计价")
    ui.b.drain(1.2)
    ui.click_text("规则预检")
    ui.b.drain(1.2)
    ui.click_text("提交")
    ui.b.drain(1.8)
    ui.prompt(PWD)
    ui.click_text("确认（需口令）")
    ui.b.drain(2.5)
    say("  收尾动作：计价/预检/提交/确认 = 4 步（确认还要输一次口令）")

    # ---------- 发药 ----------
    phase("发药")
    ui.b.goto(BASE + "/#/pharmacy", wait=2.0)
    ui.wait("#ph_visit", 8)
    popts = ui.b.js("Array.from(document.querySelectorAll('#ph_visit option')).map(function(o){return o.value}).filter(Boolean)")
    if popts:
        ui.select("#ph_visit", popts[0], 1.8)
        xopts = ui.b.js("Array.from(document.querySelectorAll('#ph_rx option')).map(function(o){return o.value}).filter(Boolean)")
        if xopts:
            ui.select("#ph_rx", xopts[0], 1.5)
            ui.click_text("发药")
            ui.b.drain(2.0)
            say("  发药动作：选就诊+选处方+点发药 = 3 步")
        else:
            say("  !! 处方下拉为空")
    else:
        say("  !! 药房无可选就诊")

    # ---------- 收费 ----------
    phase("收费")
    ui.b.goto(BASE + "/#/billing", wait=2.0)
    ui.wait("#bl_visit", 8)
    bopts = ui.b.js("Array.from(document.querySelectorAll('#bl_visit option')).map(function(o){return o.value}).filter(Boolean)")
    if bopts:
        ui.select("#bl_visit", bopts[0], 1.8)
        ui.click_text("生成账单")
        ui.b.drain(2.0)
        xo = ui.b.js("Array.from(document.querySelectorAll('#bl_bill option')).map(function(o){return o.value}).filter(Boolean)")
        if xo:
            ui.select("#bl_bill", xo[0], 1.5)
        ui.click_text("查询账单")
        ui.b.drain(1.5)
        say("  生成账单+选账单+查询 = 3 步")
        amt = ui.b.js("(function(){var t=document.body.innerText.match(/应收\\\\s*([0-9.]+)\\\\s*元/);return t?t[1]:''})()")
        say("  页面显示应收 = %s 元，仍需人工手打进金额框" % (amt or "?"))
        if amt:
            ui.type("#pay_amount", amt)
        ui.click_text("收费")
        ui.b.drain(2.0)
        say("  收费动作：选就诊+生成账单+选账单+查询+填金额+点收费 = 6 步")

    PHASES.append(_cur)
    say("\n" + "=" * 62)
    say("总计：点击 %d 次 / 输入 %d 次 / 下拉选择 %d 次 / 弹窗应答 %d 次"
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
    b.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say("FATAL: %r" % (e,))
        raise
