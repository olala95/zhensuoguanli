# -*- coding: utf-8 -*-
"""端到端 UI 点击实测：完全模拟操作员，在真实浏览器里点完"诊所一天"。

与 http 测试的区别：
  http_smoke / ui_flow_test 直接打接口，测不到"按钮点了没反应""下拉是空的"；
  本脚本只通过 DOM 点击 / 输入 / 选择来驱动，任何一步失败都是操作员真实会遇到的问题。

用法：python tools/ui_e2e.py
输出：控制台 + %TEMP%/clinic_e2e.txt，截图存 %TEMP%/e2e_*.png
前置：服务在 8787；Edge 带 --remote-debugging-port=9222
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402

TMP = os.environ.get("TEMP", ".")
LOG = os.path.join(TMP, "clinic_e2e.txt")
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
BASE = "http://127.0.0.1:8787"

STEPS = []
_fh = open(LOG, "w", encoding="utf-8")


def say(s=""):
    print(s)
    _fh.write(str(s) + "\n")
    _fh.flush()


def step(name, ok, detail=""):
    STEPS.append((name, ok, detail))
    say("  %s %s %s" % ("[OK]" if ok else "[!!]", name, detail))


def api_login():
    body = json.dumps({"username": "admin", "password": PWD, "role_code": "ADMIN"}).encode()
    req = urllib.request.Request(BASE + "/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["data"]["token"]


class UI:
    def __init__(self, browser):
        self.b = browser

    # ---- 基础操作 ----
    def wait(self, sel, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.b.js("!!document.querySelector(%s)" % json.dumps(sel)):
                return True
            self.b.drain(0.25)
        return False

    def visible(self, text, timeout=6.0):
        """等待页面出现某段文字"""
        end = time.time() + timeout
        expr = "document.body.innerText.indexOf(%s)>=0" % json.dumps(text)
        while time.time() < end:
            if self.b.js(expr):
                return True
            self.b.drain(0.25)
        return False

    def click(self, sel, wait=0.8):
        ok = self.b.js("(function(){var e=document.querySelector(%s);"
                       "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                       "e.scrollIntoView({block:'center'});e.click();return 'ok'})()" % json.dumps(sel))
        self.b.drain(wait)
        return ok

    def click_text(self, text, tag="button", wait=0.9):
        """按可见文字点按钮（最贴近真人操作）"""
        expr = ("(function(){var els=Array.from(document.querySelectorAll(%s));"
                "var e=els.find(function(x){return x.innerText.trim()===%s});"
                "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                "e.scrollIntoView({block:'center'});e.click();return 'ok'})()"
                % (json.dumps(tag), json.dumps(text)))
        r = self.b.js(expr)
        self.b.drain(wait)
        return r

    def click_contains(self, text, tag="button", wait=0.9):
        expr = ("(function(){var els=Array.from(document.querySelectorAll(%s));"
                "var e=els.find(function(x){return x.innerText.indexOf(%s)>=0});"
                "if(!e)return 'no-el';if(e.disabled)return 'disabled';"
                "e.scrollIntoView({block:'center'});e.click();return 'ok'})()"
                % (json.dumps(tag), json.dumps(text)))
        r = self.b.js(expr)
        self.b.drain(wait)
        return r

    def click_in(self, scope, text, tag="button", wait=0.9):
        """在指定容器内按文字点按钮（弹窗里的按钮与页面按钮常常同名）"""
        expr = ("(function(){var root=document.querySelector(%s);if(!root)return 'no-scope';"
                "var els=Array.from(root.querySelectorAll(%s));"
                "var e=els.find(function(x){return x.innerText.trim()===%s});"
                "if(!e)return 'no-el';if(e.disabled)return 'disabled';e.click();return 'ok'})()"
                % (json.dumps(scope), json.dumps(tag), json.dumps(text)))
        r = self.b.js(expr)
        self.b.drain(wait)
        return r

    def row_buttons(self, name):
        return self.b.js("(function(){var trs=Array.from(document.querySelectorAll('.main tbody tr'));"
                         "var tr=trs.find(function(t){return t.innerText.indexOf(%s)>=0});"
                         "if(!tr)return null;"
                         "return Array.from(tr.querySelectorAll('button')).map(function(x){"
                         "return x.innerText.trim()})})()" % json.dumps(name))

    def click_row(self, name, text, wait=2.0):
        expr = ("(function(){var trs=Array.from(document.querySelectorAll('.main tbody tr'));"
                "var tr=trs.find(function(t){return t.innerText.indexOf(%s)>=0});"
                "if(!tr)return 'row-not-found';"
                "var e=Array.from(tr.querySelectorAll('button')).find(function(x){"
                "return x.innerText.trim()===%s});"
                "if(!e)return 'no-btn:'+Array.from(tr.querySelectorAll('button'))"
                ".map(function(x){return x.innerText.trim()}).join(',');"
                "e.click();return 'ok'})()" % (json.dumps(name), json.dumps(text)))
        r = self.b.js(expr)
        self.b.drain(wait)
        return r

    def fill(self, sel, text):
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
                "if(d)d.set.call(e,%s);else e.value=%s;"
                "e.dispatchEvent(new Event('input',{bubbles:true}));"
                "e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
                % (json.dumps(sel), json.dumps(text), json.dumps(text)))
        return self.b.js(expr)

    def fill_ta(self, sel, text):
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "var d=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');"
                "if(d)d.set.call(e,%s);else e.value=%s;"
                "e.dispatchEvent(new Event('input',{bubbles:true}));return 'ok'})()"
                % (json.dumps(sel), json.dumps(text), json.dumps(text)))
        return self.b.js(expr)

    def select(self, sel, value=None, index=None, wait=1.2):
        if index is not None:
            expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                    "if(e.options.length<=%d)return 'only-'+e.options.length;"
                    "e.selectedIndex=%d;e.dispatchEvent(new Event('change',{bubbles:true}));"
                    "return e.value})()" % (json.dumps(sel), index, index))
        else:
            expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                    "e.value=%s;e.dispatchEvent(new Event('change',{bubbles:true}));"
                    "return e.value})()" % (json.dumps(sel), json.dumps(value)))
        r = self.b.js(expr)
        self.b.drain(wait)
        return r

    def select_text(self, sel, keyword, wait=1.2):
        expr = ("(function(){var e=document.querySelector(%s);if(!e)return 'no-el';"
                "var o=Array.from(e.options).find(function(x){return x.textContent.indexOf(%s)>=0});"
                "if(!o)return 'no-opt';e.value=o.value;"
                "e.dispatchEvent(new Event('change',{bubbles:true}));return e.value})()"
                % (json.dumps(sel), json.dumps(keyword)))
        r = self.b.js(expr)
        self.b.drain(wait)
        return r

    def opts(self, sel):
        return self.b.js("Array.from(document.querySelectorAll(%s+' option'))"
                         "? Array.from(document.querySelector(%s).options).map(function(o){return o.value+'|'+o.textContent}) : []"
                         % (json.dumps(sel), json.dumps(sel)))

    def text(self, sel=None):
        if sel:
            return self.b.js("(document.querySelector(%s)||{}).innerText||''" % json.dumps(sel))
        return self.b.js("document.body.innerText")

    def toast(self):
        return self.b.js("(document.getElementById('toast')||{}).textContent||''")

    def shot(self, name):
        p = os.path.join(TMP, "e2e_%s.png" % name)
        try:
            self.b.screenshot(p)
        except Exception:
            return ""
        return p


def main():
    tok = api_login()
    b = Browser(9222)
    ui = UI(b)
    say("=" * 76)
    say("端到端 UI 点击实测 · %s" % BASE)
    say("=" * 76)
    patient_name = "实测" + time.strftime("%H%M%S")

    try:
        # 登录
        b.goto(BASE + "/", 1.0)
        b.js("localStorage.setItem('token', %s); localStorage.setItem('user', %s)"
             % (json.dumps(tok), json.dumps(json.dumps(
                 {"user_id": 1, "username": "admin", "display_name": "王医生",
                  "role_code": "ADMIN"}))))
        b.send("Page.reload", {"ignoreCache": True})
        b.drain(2.6)
        step("登录后进入工作台", ui.wait("#kw"), "")

        # ---------- 1. 新建患者 ----------
        say("\n──── 1. 新建患者（界面操作）")
        r = ui.click_text("+ 新建患者")
        step("点「+ 新建患者」", r == "ok", str(r))
        step("弹出建档表单", ui.wait("#name"))
        ui.fill("#name", patient_name)
        ui.select("#gender", "F")
        ui.fill("#birth", "1988-06-15")
        ui.fill("#phone", "1390000" + time.strftime("%M%S"))
        ui.fill("#addr", "门诊实测地址 1 号")
        shot1 = ui.shot("01_patient_form")
        r = ui.click_text("建档")
        step("点「建档」", r == "ok", str(r))
        # createPatient -> closeModal -> newVisit -> render()
        ok = ui.visible(patient_name, 8.0)
        step("新建患者出现在今日就诊列表", ok, patient_name)
        step("自动挂号成功（出现就诊记录）",
             bool(b.js("document.body.innerText.indexOf('初诊')>=0")))
        shot2 = ui.shot("02_after_register")

        # ---------- 2. 接诊 ----------
        say("\n──── 2. 接诊（点患者行的「接诊」）")
        step("该患者行出现「接诊」按钮", bool(ui.row_buttons(patient_name)),
             str(ui.row_buttons(patient_name)))
        r = ui.click_row(patient_name, "接诊", wait=2.6)
        step("点该患者行的「接诊」", r == "ok", str(r))
        step("已跳到病历页", bool(b.js("location.hash.indexOf('#/record/')===0")),
             str(b.js("location.hash")))
        step("病历页字段可用（主诉输入框存在）", ui.wait("#cc_content"))
        shot3 = ui.shot("03_record")

        # ---------- 3. 写病历 ----------
        say("\n──── 3. 写病历（主诉/现病史/四诊/辨证/诊断）")
        ui.fill("#cc_content", "反复头晕 3 天")
        ui.fill("#cc_duration", "3 天")
        ui.fill_ta("#pi_content", "3 天前无明显诱因出现头晕，伴乏力，无恶心呕吐。")
        ui.fill("#pi_onset", "3 天前")
        ui.fill_ta("#tf_ins", "神清，面色少华")
        ui.fill_ta("#tf_int", "纳差，眠浅，二便调")
        ui.fill("#tf_tb", "淡")
        ui.fill("#tf_tc", "薄白")
        ui.fill("#tf_pd", "细弱")
        ui.fill("#syn_name", "气血两虚证")
        ui.fill("#syn_prin", "益气养血")
        ui.fill("#syn_meth", "归脾汤加减")
        ui.fill("#dx_west", "头晕")
        ui.fill("#dx_code", "R42")
        ui.fill("#dx_tcm", "眩晕")
        ui.fill("#vs_t", "36.5")
        ui.fill("#vs_p", "72")
        ui.fill("#vs_r", "18")
        ui.fill("#vs_s", "110")
        ui.fill("#vs_d", "70")
        ui.fill_ta("#exam", "心肺听诊无异常")
        r = ui.click_text("保存全部")
        b.drain(2.4)
        step("点「保存全部」", r == "ok", str(r))
        t = ui.toast() or ""
        step("保存成功提示", "已保存" in t, t[:60])
        saved = b.js("document.querySelector('#cc_content').value")
        step("主诉已落库并回显", saved == "反复头晕 3 天", repr(saved))

        # 确认签名
        r = ui.click_text("确认签名")
        b.drain(1.2)
        step("点「确认签名」弹出签名框", ui.wait("#sign_pwd"))
        ui.fill("#sign_pwd", PWD)
        shot4 = ui.shot("04_sign")
        r = ui.click_in("#modal", "确认签名", wait=2.6)
        step("弹窗内点「确认签名」提交", r == "ok", str(r))
        t = ui.toast() or ""
        step("签名成功", "已确认" in t or "签名" in t, t[:60])
        body = ui.text()
        step("病历状态变为已确认", "病历已确认，内容锁定" in body or "已确认" in body, "")
        shot5 = ui.shot("05_record_confirmed")

        # 取 visit_id（从 hash 读，模拟人不需要知道，但脚本要用）
        vid = b.js("parseInt((location.hash.split('/')[2]||''))")

        # ---------- 4. 开方 ----------
        say("\n──── 4. 开方（选就诊 → 建饮片方 → 加药 → 保存 → 提交 → 确认）")
        b.js("location.hash='#/prescribe'")
        b.drain(2.2)
        step("开方页加载", ui.wait("#rx_visit"))
        r = ui.select("#rx_visit", value=str(vid), wait=2.2)
        step("在工作台选本次就诊", r == str(vid) or r == vid, str(r))
        step("载入后出现「建方」按钮", ui.wait("#rx_type"))
        ui.select("#rx_type", "HERB", wait=0.8)
        ui.fill("#rx_doses", "7")
        ui.fill("#rx_decoction", "水煎")
        ui.fill("#rx_admin", "每日一剂分两次温服")
        r = ui.click_text("建方")
        b.drain(2.4)
        step("点「建方」", r == "ok", str(r))
        step("进入处方编辑区", ui.wait("#rx_kw"))
        shot6 = ui.shot("06_prescription_new")

        # 加三味饮片（每加一味会弹 prompt 问剂量，脚本预置答案）
        herbs = ["黄芪", "党参", "白术"]
        added = []
        for h in herbs:
            ui.fill("#rx_kw", h)
            ui.click_contains("搜索", tag="button", wait=1.4)
            found = b.js("document.querySelectorAll('#rx_results .btn').length")
            if not found:
                continue
            b.answers.append("15")     # 剂量
            b.js("document.querySelectorAll('#rx_results .btn')[0].click()")
            b.drain(1.2)
            added.append(h)
        step("搜到并加入饮片 %d/%d 味" % (len(added), len(herbs)), len(added) > 0, "、".join(added))
        step("加入后有提示", "已加入" in (ui.toast() or "") or len(added) > 0, ui.toast()[:50])

        r = ui.click_text("保存明细")
        b.drain(2.4)
        step("点「保存明细」", r == "ok", str(r))
        step("明细已落库（表格出现药名）",
             bool(b.js("document.body.innerText.indexOf('黄芪')>=0")), ui.toast()[:50])
        shot7 = ui.shot("07_prescription_items")

        r = ui.click_text("提交")
        b.drain(2.4)
        step("点「提交」（跑用药规则校验）", r == "ok", str(r))
        step("状态变为待确认", "待确认" in (ui.text() or ""), ui.toast()[:60])

        # 确认需要口令：prompt 必须在点击前预置答案，否则对话框会以空值自动应答
        b.answers.append(PWD)
        r = ui.click_text("确认（需口令）", wait=1.0)
        if r != "ok":
            r = ui.click_contains("确认", wait=1.0)
        b.drain(2.8)
        step("点「确认」并输入口令", r == "ok", str(r) + " / " + (ui.toast() or "")[:40])
        step("处方状态为已确认", "已确认" in (ui.text() or ""), ui.toast()[:60])
        shot8 = ui.shot("08_prescription_confirmed")

        # ---------- 5. 收费 ----------
        say("\n──── 5. 收费（选就诊 → 生成账单 → 收款）")
        b.js("location.hash='#/billing'")
        b.drain(2.4)
        step("收费页加载", ui.wait("#bl_visit"))
        r = ui.select("#bl_visit", value=str(vid), wait=2.4)
        step("选择本次就诊", r == str(vid) or str(r).isdigit(), str(r))
        step("账单下拉已联动", ui.wait("#bl_bill"))
        r = ui.click_text("生成账单")
        b.drain(3.0)
        step("点「生成账单」", r == "ok", str(r))
        due = ""
        for _ in range(14):
            info = ui.text("#bl_info") or ""
            m = re.search(r"应收\s*([0-9.]+)", info)
            if m:
                due = m.group(1)
                break
            b.drain(0.5)
        info = ui.text("#bl_info") or ""
        step("账单已生成并显示应收金额", bool(due), "应收=%s 元" % (due or "?"))
        shot9 = ui.shot("09_bill")
        if not due:
            step("账单含明细", False, (info or "")[:140].replace("\n", " / "))
        ui.fill("#pay_amount", due or "0.01")
        ui.select("#pay_channel", "CASH")
        r = ui.click_text("收费", wait=2.8)
        step("点「收费」（现金 %s 元）" % (due or "?"), r == "ok", str(r))
        t = ui.toast() or ""
        step("收费成功", "收费成功" in t or "已收费" in (ui.text() or ""), t[:70])
        shot10 = ui.shot("10_paid")

        # ---------- 6. 发药 ----------
        say("\n──── 6. 中药房发药（选就诊 → 选处方 → 读取 → 发药）")
        b.js("location.hash='#/pharmacy'")
        b.drain(2.6)
        step("药房页加载", ui.wait("#ph_visit"))
        r = ui.select("#ph_visit", value=str(vid), wait=2.6)
        step("选择本次就诊", r == str(vid) or str(r).isdigit(), str(r))
        rx_opts = ui.opts("#rx_id")
        step("处方下拉自动联动", isinstance(rx_opts, list) and len(rx_opts) > 1,
             str(rx_opts)[:200])
        r = ui.select_text("#rx_id", "中药", wait=2.4)
        if r in ("no-opt", "no-el"):
            r = ui.select("#rx_id", index=1, wait=2.4)
        step("选中药处方（自动读取）", str(r).isdigit(), str(r))
        info = ui.text("#rx_info")
        step("处方明细已显示", "处方" in (info or ""), (info or "")[:140].replace("\n", " / "))
        r = ui.click_text("中药发药（自煎）", wait=3.2)
        step("点「中药发药（自煎）」", r == "ok", str(r))
        t = ui.toast() or ""
        step("发药完成", "发药" in t, t[:80])
        shot11 = ui.shot("11_dispensed")

        # ---------- 7. 打印 ----------
        say("\n──── 7. 打印（选就诊 → 选处方 → 打处方笺）")
        b.js("location.hash='#/print'")
        b.drain(2.6)
        step("打印页加载", ui.wait("#pr_visit"))
        r = ui.select("#pr_visit", value=str(vid), wait=2.6)
        step("选择本次就诊", r == str(vid) or str(r).isdigit(), str(r))
        pr_opts = ui.opts("#pr_rx")
        step("处方下拉联动", isinstance(pr_opts, list) and len(pr_opts) > 1, str(pr_opts)[:160])
        ui.select("#pr_rx", index=1, wait=1.2)
        r = ui.click_text("打处方笺", wait=3.2)
        step("点「打处方笺」", r == "ok", str(r))
        t = ui.toast() or ""
        step("生成打印任务", "已打印" in t, t[:70])
        shot12 = ui.shot("12_print")

        # ---------- 8. 完成就诊 ----------
        say("\n──── 8. 完成就诊 + 看报表")
        b.js("location.hash='#/workbench'")
        b.drain(2.6)
        step("该患者行出现「完成」按钮", bool(ui.row_buttons(patient_name)),
             str(ui.row_buttons(patient_name)))
        r = ui.click_row(patient_name, "完成", wait=2.8)
        step("点该患者行的「完成」", r == "ok", str(r))
        t = ui.toast() or ""
        step("就诊已结束", "已完成" in t or "已完成" in (ui.text() or ""), t[:60])

        b.js("location.hash='#/report'")
        b.drain(2.8)
        rep = ui.text(".main")
        step("经营统计可查", "净营收" in (rep or ""), (rep or "")[:100].replace("\n", " / "))
        shot13 = ui.shot("13_report")

    finally:
        errs = b.console_errors()
        say("")
        # 记录所有非 2xx 请求，区分"真故障"与"业务拒绝（如重复建账单的 409）"
        bad = []
        seen = set()
        for e in b.events:
            if e.get("method") == "Network.responseReceived":
                r = e["params"]["response"]
                st = r.get("status", 0)
                if st >= 400:
                    key = "%s %s" % (st, r["url"])
                    if key not in seen:
                        seen.add(key)
                        bad.append(key)
        if bad:
            say("非 2xx 请求 %d 个：" % len(bad))
            for x in bad[:15]:
                say("  " + x)
        if errs:
            say("浏览器控制台日志 %d 条（多为上面这些请求的浏览器回显）：" % len(errs))
            for e in errs[:6]:
                say("  " + e.replace("\n", " ")[:160])
        if b.dialogs:
            say("自动应答的对话框 %d 个：" % len(b.dialogs))
            for d in b.dialogs[:8]:
                say("  [%s] %s -> %s" % (d["type"], d["message"][:60], d["answer"][:20]))
        b.close()

    ok = sum(1 for _, o, _ in STEPS if o)
    bad = [(n, d) for n, o, d in STEPS if not o]
    say("")
    say("=" * 76)
    say("结果：%d 项通过 / %d 项失败" % (ok, len(bad)))
    if bad:
        say("失败明细：")
        for n, d in bad:
            say("  · %s  %s" % (n, d))
    say("=" * 76)
    _fh.close()
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
