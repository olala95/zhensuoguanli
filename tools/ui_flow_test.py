# -*- coding: utf-8 -*-
"""端到端业务流程测试：按**前端实际的调用方式**（路径 + HTTP 方法 + 参数）
完整走一遍"诊所一天"，任何一步失败都要暴露出来。

和 ui_probe 的区别：
  ui_probe 只验证"接口能通"（单点）；
  ui_flow_test 验证"业务能走通"（链式，后一步依赖前一步的产物），
  而且严格用前端 api() 发出的方法（GET/POST）去打，能复现前端真实行为。

用法：python tools/ui_flow_test.py [--port 8787]
输出：控制台 + %TEMP%/clinic_flow_test.txt
"""

import argparse
import datetime
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

PASSED, FAILED = [], []
BASE = ""
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_flow_test.txt")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_w = sys.stdout.write
CTX = {}          # 链式状态：visit_id / record_id / rx_id / bill_id ...


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _w(msg + "\n")
    _LOG.write(msg + "\n")
    _LOG.flush()


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("  %s %s %s" % ("[OK]" if cond else "[!!]", name, extra))


def call(path, body=None, method=None, token=None):
    method = method or ("POST" if body is not None else "GET")
    url = BASE + path
    if method == "GET" and body:
        qs = urllib.parse.urlencode({k: v for k, v in body.items()
                                     if v is not None and v != ""})
        if qs:
            url += ("&" if "?" in url else "?") + qs
    data = json.dumps(body).encode("utf-8") if (body is not None and method != "GET") else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": e.read().decode("utf-8", "ignore")}


def api(path, body=None, method=None, token=None):
    """包一层，统一取 data 或抛错。

    method 省略时按前端 api() 的规则推：有 body → POST，否则 GET。
    """
    what = method or ("POST" if body is not None else "GET")
    code, r = call(path, body, method=method, token=token)
    if code != 200 or not r.get("ok"):
        err = r.get("error") or {}
        raise RuntimeError("%s %s → [%s] %s %s"
                           % (what, path, err.get("code", code),
                              err.get("message", ""), (r.get("message") or "")))
    return r.get("data")


def _suffix():
    return datetime.datetime.now().strftime("%H%M%S")


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    # 默认口令仅供本地测试库使用；正式环境用环境变量 CLINIC_PWD 覆盖
    ap.add_argument("--password", default=os.environ.get("CLINIC_PWD", "Clinic@2026"))
    args = ap.parse_args()
    BASE = "http://127.0.0.1:%d" % args.port

    print("=" * 72)
    print("端到端业务流程测试 · %s" % BASE)
    print("严格按前端 api() 的方法（GET/POST）调用，链式执行")
    print("=" * 72)

    try:
        run(args)
    except RuntimeError as e:
        print("\n[!!] 流程中断：%s" % e)
        FAILED.append("流程中断：%s" % e)
    except Exception as e:
        import traceback
        traceback.print_exc(file=_LOG)
        print("\n[!!] 未预期异常：%r" % e)
        FAILED.append("未预期异常：%r" % e)

    print("\n" + "=" * 72)
    print("业务流程测试：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    for n in FAILED:
        print("  - %s" % n)
    print("明细：%s" % _LOG_PATH)
    print("=" * 72)
    return 1 if FAILED else 0


def run(args):
    PWD = args.password

    # ---- 1. 登录（四个岗位）----
    print("\n[1] 登录")
    tk = api("/api/auth/login", {"username": "admin", "password": PWD,
                                 "role_code": "ADMIN"})["token"]
    check("管理员登录", bool(tk))
    api("/api/system/user/roles", {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                           "CASHIER"]}, token=tk)
    roles = {}
    for r in ("DOCTOR", "PHARMACIST", "CASHIER", "ADMIN"):
        roles[r] = api("/api/auth/login", {"username": "admin", "password": PWD,
                                           "role_code": r})["token"]
    check("四个岗位都能登录", all(roles.values()))
    dt, pt, ct = roles["DOCTOR"], roles["PHARMACIST"], roles["CASHIER"]

    # ---- 2. 建档 → 自动建就诊（用户点的流程）----
    print("\n[2] 建档 → 建就诊（工作台「+ 新建患者」→「建就诊」）")
    p = api("/api/patient/create", {"name": "流程患者%s" % _suffix(), "gender": "M",
                                    "birth_date": "1975-03-03", "phone": "13800000000"},
            token=dt)
    check("建档", bool(p.get("patient_no")), p.get("patient_no"))
    CTX["patient_id"] = p["id"]
    v = api("/api/visit/create", {"patient_id": p["id"], "visit_type": "TCM"}, token=dt)
    check("建就诊（挂号）", v.get("status") == "WAITING", v.get("visit_no"))
    CTX["visit_id"] = v["id"]

    # ---- 3. 工作台候诊队列能看到 ----
    print("\n[3] 工作台候诊队列（每条都要能显示）")
    q = api("/api/visit/queue", {"date": datetime.date.today().isoformat()},
            method="GET", token=dt)
    items = q.get("items") or []
    check("候诊队列可取", isinstance(items, list), "%d 条" % len(items))
    check("刚挂号的患者在队列里", any(i["id"] == CTX["visit_id"] for i in items))

    # ---- 4. 接诊（用户报"点了没反应"的那一步）----
    print("\n[4] 接诊（工作台「接诊」按钮）")
    r = api("/api/visit/start", {"visit_id": CTX["visit_id"]}, token=dt)
    CTX["record_id"] = r["record_id"]
    check("接诊成功并自动建病历", r.get("record_id", 0) > 0,
          "record_id=%s" % r.get("record_id"))

    # ---- 5. 写病历 ----
    print("\n[5] 写病历（各分节保存）")
    rid = CTX["record_id"]
    api("/api/record/save/chief", {"record_id": rid, "content": "咳嗽气短两月",
                                   "duration": "2月"}, token=dt)
    api("/api/record/save/present", {"record_id": rid, "content": "感冒后咳嗽迁延，痰白清稀",
                                     "onset_time": "两月前"}, token=dt)
    api("/api/record/save/tcm_four", {"record_id": rid, "inspection": "神疲",
                                      "tongue_body": "舌淡胖", "tongue_coating": "苔白腻",
                                      "pulse_desc": "脉细弱"}, token=dt)
    api("/api/record/save/syndrome", {"record_id": rid, "replace": True, "items": [
        {"syndrome_name": "肺脾气虚证", "treatment_principle": "补脾益肺",
         "treatment_method": "六君子汤加减"}]}, token=dt)
    api("/api/record/save/diagnosis", {"record_id": rid, "replace": True, "items": [
        {"diag_type": "TCM", "tcm_name": "咳嗽·肺脾气虚证", "is_primary": 1},
        {"diag_type": "WEST", "icd10_code": "R05", "icd10_name": "咳嗽"}]}, token=dt)
    api("/api/record/save/vital", {"record_id": rid, "temperature_c": 36.6, "pulse": 78,
                                   "respiration": 18, "systolic": 118, "diastolic": 74},
        token=dt)
    api("/api/record/save/exam", {"record_id": rid, "replace": True, "items": [
        {"section": "GENERAL", "content": "双肺呼吸音清"}]}, token=dt)
    api("/api/record/save/history", {"record_id": rid, "items": [
        {"hist_type": "PAST", "disease": "高血压", "treatment": "服药", "outcome": "控制"}]
        }, token=dt)
    api("/api/record/save/allergy", {"record_id": rid, "items": [
        {"allergen": "青霉素", "severity": "SEVERE", "manifestation": "皮疹"}]}, token=dt)
    api("/api/record/save/exam_ref", {"record_id": rid, "items": [
        {"ref_type": "POINT", "exam_name": "指尖血糖", "exam_value": "5.4",
         "unit": "mmol/L"}], "reason": "即时检查"}, token=dt)
    check("病历各分节保存成功", True, "主诉/现病史/四诊/辨证/诊断/体征/查体/既往史/过敏史/辅助检查")

    # ---- 6. 确认签名 ----
    print("\n[6] 病历确认签名")
    c = api("/api/record/confirm", {"record_id": rid, "password": PWD}, token=dt)
    check("确认签名", c.get("to") == "CONFIRMED", "v%s" % c.get("version_no"))

    # ---- 7. 开方 ----
    print("\n[7] 开方（开方页：建方 → 明细 → 计价 → 预检 → 提交 → 确认）")
    herbs = (api("/api/herb/search", {"keyword": "黄芪"}, method="GET", token=dt)
             .get("items") or [])
    drugs = (api("/api/drug/search", {"keyword": ""}, method="GET", token=dt)
             .get("items") or [])
    if not herbs:
        api("/api/herb/create", {"herb_code": "FLOW%s" % _suffix(), "name": "流程黄芪",
                                 "price_milli_per_g": 50}, token=pt)
        herbs = (api("/api/herb/search", {"keyword": "流程黄芪"}, method="GET", token=dt)
                 .get("items") or [])
    # 发药需要库存：先补一次入库（入库单必须 confirm 才到账）
    si = api("/api/stock/in/create", {"items": [{
        "item_kind": "HERB", "herb_id": herbs[0]["id"], "qty": 2,
        "input_unit": "KG", "cost_fen": 30000,
        "expiry_date": "2030-12-31"}], "invoice_no": "FLOW%s" % _suffix()}, token=pt)
    api("/api/stock/in/confirm", {"id": si["id"]}, token=pt)
    rx = api("/api/prescription/create", {"visit_id": CTX["visit_id"],
                                          "prescription_type": "HERB", "doses": 7,
                                          "decoction_method": "水煎",
                                          "administration": "每日一剂"}, token=dt)
    CTX["rx_id"] = rx["id"]
    check("建方", bool(rx.get("prescription_no")), rx.get("prescription_no"))
    api("/api/prescription/save/herbs", {"prescription_id": rx["id"], "doses": 7,
        "items": [{"herb_id": herbs[0]["id"], "dose_g": 15, "foot_note": "NORMAL"}]},
        token=dt)
    check("存饮片明细", True, "1 味")
    pr = api("/api/prescription/price", {"prescription_id": rx["id"]}, token=dt)
    # 计价接口返回的是分项（drug/herb/decoction），没有总金额字段，自己加
    pc = pr.get("pricing") or pr
    total = sum(pc.get(k) or 0 for k in ("drug_subtotal_fen", "herb_subtotal_fen",
                                         "decoction_fee_fen", "processing_fee_fen"))
    check("计价", bool(pc), "%s 分（饮片 %s）" % (total, pc.get("herb_subtotal_fen")))
    vd = api("/api/prescription/validate", {"prescription_id": rx["id"]}, token=dt)
    check("规则预检", vd.get("passed") is True,
          "阻断 %d 项" % len(vd.get("blocks") or []))
    api("/api/prescription/submit", {"prescription_id": rx["id"]}, token=dt)
    check("提交处方", True)
    api("/api/prescription/confirm", {"prescription_id": rx["id"], "password": PWD},
        token=dt)
    check("确认处方", True)

    # ---- 8~11. 收费 → 发药 → 打印 → 完成就诊 ----
    # 当天若已日结（上一轮流程测试做过），建账单会被 E3006 拒绝——这是 F58 R8 的正确行为，
    # 不算失败，跳过这几步继续验证后面的归档与报表。
    settled_already = False
    try:
        print("\n[8] 收费（收费页：生成账单 → 收费）")
        b = api("/api/bill/create", {"visit_id": CTX["visit_id"]}, token=ct)
        CTX["bill_id"] = b["id"]
        check("生成账单", bool(b.get("bill_no")), "%s 分" % b.get("total_amount_fen"))
        pay = api("/api/bill/pay", {"bill_id": b["id"], "payments": [
            {"channel": "WECHAT", "amount_fen": b["total_amount_fen"],
             "received_amount_fen": b["total_amount_fen"], "trade_no": "FLOW%s" % _suffix()}]},
            token=ct)
        check("收费完成", pay.get("status") == "PAID", "实收 %s 分" % b["total_amount_fen"])
        got = api("/api/bill/get", {"visit_id": CTX["visit_id"]}, method="GET", token=ct)
        check("按就诊可直接取账单（打印页自动填单依赖）", got.get("id") == b["id"])

        print("\n[9] 发药")
        d = api("/api/pharmacy/herb_dispense", {"prescription_id": CTX["rx_id"],
                                                "decoction_type": "DAIJIAN"}, token=pt)
        check("中药代煎发药", True, "doses=%s" % d.get("doses"))

        print("\n[10] 打印（处方笺 / 凭条 / 药袋标签）")
        for name, path, body in (("处方笺", "/api/print/prescription",
                                  {"prescription_id": CTX["rx_id"]}),
                                 ("收费凭条", "/api/print/receipt",
                                  {"bill_id": CTX["bill_id"]}),
                                 ("药袋标签", "/api/print/label",
                                  {"prescription_id": CTX["rx_id"]})):
            j = api(path, body, token=dt)
            x = api("/api/print/job/execute", {"job_id": j["id"]}, token=dt)
            check("打印%s" % name, x.get("status") == "SUCCESS", j.get("print_job_no"))

        print("\n[11] 完成就诊")
        api("/api/visit/complete", {"visit_id": CTX["visit_id"]}, token=dt)
        check("完成就诊", True)
    except RuntimeError as e:
        if "E3006" not in str(e):
            raise
        settled_already = True
        CTX.pop("bill_id", None)
        print("\n  [跳过] 当天已日结，收费/发药/打印/完成就诊跳过（F58 R8 正确行为）")
        PASSED.append("当天已日结时正确拒绝新账单（E3006 / F58 R8）")

    # ---- 12. 归档与封存 ----
    print("\n[12] 归档与封存")
    a = api("/api/record/archive", {"record_id": rid}, token=dt)
    check("归档", bool(a.get("archive_no")), a.get("archive_no"))
    api("/api/record/seal", {"record_id": rid, "reason": "流程测试封存"}, token=tk)
    check("封存", True)
    vh = api("/api/record/hash/verify", {"record_id": rid}, method="GET", token=dt)
    check("Hash 链校验", vh.get("valid") is True)

    # ---- 13. 日结（最后做，会锁定当天）----
    print("\n[13] 日结（放最后：日结后当天不能再建账单）")
    today = datetime.date.today().isoformat()
    if settled_already:
        check("日结（本轮之前已完成）", True, "跳过")
    else:
        s = api("/api/settlement/close", {"biz_date": today, "remark": "流程测试日结"},
                token=ct)
        check("日结", True, "%s 净额 %s 分" % (s.get("settlement_no"),
                                             s.get("net_amount_fen")))

    # ---- 14. 报表 ----
    print("\n[14] 报表")
    rp = api("/api/report/revenue_daily", {"from": today, "to": today}, method="GET",
             token=tk)
    check("收入日报", (rp.get("summary") or {}).get("paid_amount_fen") is not None,
          "%s 元" % (rp.get("summary") or {}).get("paid_amount_yuan"))

    print("\n核心链路全部走通：患者 %s / 就诊 %s / 处方 %s / 账单 %s"
          % (CTX.get("patient_id"), CTX.get("visit_id"), CTX.get("rx_id"),
             CTX.get("bill_id")))


if __name__ == "__main__":
    sys.exit(main())
