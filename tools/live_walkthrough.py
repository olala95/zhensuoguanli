# -*- coding: utf-8 -*-
"""实操演练：真起 HTTP 服务，按诊所一天的真实操作顺序走一遍全流程。

和 tests/ 里的用例不同——**这里打的是真实 HTTP 接口、用真实数据目录**，
每一步都打印操作员能看到的单据号与关键结果，用来回答"这系统到底能不能用"。

用法：
    python tools/live_walkthrough.py                     # 用项目根目录 data/clinic.db
    python tools/live_walkthrough.py --password 你的口令  # 库已初始化时指定登录口令
    python tools/live_walkthrough.py --port 8790

输出：控制台 + %TEMP%/clinic_walkthrough.txt
"""

import argparse
import datetime
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import runtime  # noqa: E402
from modules import (m01_patient, m02_visit, m03_record, m04_ai, m05_lab,  # noqa: E402,F401
                     m06_prescription, m07_pharmacy, m08_treatment, m09_billing,
                     m10_print, m11_archive, m12_audit, m13_recovery, m14_system,
                     m15_report)

# 默认口令仅供本地测试库使用；正式环境用环境变量 CLINIC_PWD 覆盖
DEFAULT_PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
PASSED, FAILED, SKIPPED = [], [], []
BASE = ""
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_walkthrough.txt")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")


_stdout_write = sys.stdout.write


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _stdout_write(msg + "\n")
    _LOG.write(msg + "\n")
    _LOG.flush()


def step(n, title):
    print("\n%s  %s" % ("─" * 4, title))


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("      %s %s %s" % ("[OK]" if cond else "[!!]", name, extra))


def call(path, body=None, method=None, token=None):
    method = method or ("POST" if body is not None else "GET")
    url = BASE + path
    if method == "GET" and body:
        qs = urllib.parse.urlencode({k: v for k, v in body.items()
                                     if isinstance(v, (str, int, float))})
        if qs:
            url += "?" + qs
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": e.read().decode("utf-8", "ignore")}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _future(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def main():
    import shutil
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=PROJECT)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--password", default=DEFAULT_PWD)
    ap.add_argument("--fresh", action="store_true",
                    help="用全新临时数据目录演练（模拟「新诊所第一天」，保证流程能跑到底）")
    args = ap.parse_args()

    global BASE
    root = args.root
    if args.fresh:
        root = tempfile.mkdtemp(prefix="clinic_fresh_")
        shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
        shutil.copytree(os.path.join(PROJECT, "web"), os.path.join(root, "web"))
    port = args.port or free_port()
    BASE = "http://127.0.0.1:%d" % port

    print("=" * 68)
    print("实操演练 · 基层中医诊所门诊管理系统")
    if args.fresh:
        print("模式：全新诊所第一天（临时目录，演练完即弃）")
    print("数据目录：%s" % root)
    print("库文件  ：%s" % os.path.join(root, "data", "clinic.db"))
    print("=" * 68)

    httpd, url, report = runtime.bootstrap(root, port=port, start_server=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print("服务已启动：%s\n" % url)

    for c in report["checks"]:
        print("  自检 %-24s %-6s %s" % (c.get("item"), c.get("result"),
                                        (c.get("detail") or "")[:60]))
    if report.get("safe_mode"):
        print("  !! 系统处于安全模式（只读），业务将被拒绝")
    from core import db, migrate
    applied = sorted(migrate.applied(db.write_conn()))
    print("  已应用迁移：%d 个，最新 %s" % (len(applied), applied[-1] if applied else "-"))

    try:
        return _run(args)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass


def _run(args):
    PWD = args.password

    # ============ 1. 开机 ============
    step(1, "开机：系统状态 / 静态页面")
    code, r = call("/api/system/status")
    check("GET /api/system/status", code == 200 and r.get("ok"), str(r.get("data")))
    initialized = (r.get("data") or {}).get("initialized")
    try:
        check("Web 页面可访问", urllib.request.urlopen(BASE + "/", timeout=10).status == 200)
    except Exception as e:
        check("Web 页面可访问", False, repr(e))

    if not initialized:
        code, r = call("/api/system/init", {"clinic_name": "示例中医诊所",
                                            "admin_username": "admin",
                                            "admin_password": PWD,
                                            "admin_name": "王医生"})
        check("首次初始化（建管理员）", code == 200 and r["data"]["initialized"])
        print("      >> 已创建管理员 admin / %s（请登录后立即改口令）" % PWD)
    else:
        print("      库已初始化，跳过")

    # ============ 2. 登录 ============
    step(2, "登录并分配角色")
    code, r = call("/api/auth/login", {"username": "admin", "password": PWD,
                                       "role_code": "ADMIN"})
    check("管理员登录", code == 200 and bool(r["data"]["token"]),
          "" if code == 200 else str(r))
    token = r["data"]["token"]
    call("/api/system/user/roles", {"user_id": 1, "roles": [
        "ADMIN", "DOCTOR", "PHARMACIST", "CASHIER"]}, token=token)

    # 按真实岗位分别登录：医生看病、药师管药、收费员收费、管理员归档与运维
    def login_as(role):
        c, rr = call("/api/auth/login", {"username": "admin", "password": PWD,
                                         "role_code": role})
        return (rr.get("data") or {}).get("token")

    dt = login_as("DOCTOR")        # 医生
    pt = login_as("PHARMACIST")    # 药师
    ct = login_as("CASHIER")       # 收费员
    check("医生登录", bool(dt))
    check("药师登录", bool(pt))
    check("收费员登录", bool(ct))

    # ============ 3. 基础数据 ============
    step(3, "基础数据：收费项目（收费员）/ 饮片与入库（药师）")
    def ensure_charge(code, name, price, auto, tok):
        """幂等：重复编码会被 E2004 拒绝，已有就复用。"""
        c, rr = call("/api/charge_item/search", {"keyword": name}, method="GET", token=tok)
        for x in ((rr.get("data") or {}).get("items") or []):
            if x.get("item_code") == code:
                return x["id"], True
        c, rr = call("/api/charge_item/create", {"item_code": code, "name": name,
                                                 "price_fen": price,
                                                 "auto_add_on_visit": auto}, token=tok)
        return (rr.get("data") or {}).get("id"), False

    for code, name, price, auto in (("GH01", "挂号费", 1000, 1),
                                    ("ZL01", "中医诊疗费", 3000, 0)):
        cid, existed = ensure_charge(code, name, price, auto, ct)
        check("收费项目 %s" % name, bool(cid),
              "id=%s%s" % (cid, "（已存在，复用）" if existed else "（新建）"))

    def ensure_herb(code, name, price, tok):
        """幂等：已有就用，没有才建（重复编码会被 E2004 拒绝）。返回 (id, 是否已存在)。"""
        c, rr = call("/api/herb/search", {"keyword": name}, method="GET", token=tok)
        for x in ((rr.get("data") or {}).get("items") or []):
            if x.get("herb_code") == code:
                return x["id"], True
        c, rr = call("/api/herb/create", {"herb_code": code, "name": name,
                                          "price_milli_per_g": price}, token=tok)
        return (rr.get("data") or {}).get("id"), False

    herbs = {}
    for code, name, price in (("HQ001", "黄芪", 50), ("DZ001", "党参", 80),
                              ("BZ001", "白术", 60)):
        hid, existed = ensure_herb(code, name, price, pt)
        if hid:
            herbs[code] = hid
        check("饮片 %s" % name, bool(hid),
              "id=%s%s" % (hid, "（已存在，复用）" if existed else "（新建）"))

    code, r = call("/api/stock/in/create", {"items": [
        {"item_kind": "HERB", "herb_id": v, "qty": 5, "input_unit": "KG",
         "cost_yuan_per_unit": 0.03, "expiry_date": _future(600)}
        for v in herbs.values()]}, token=pt)
    check("饮片入库单创建", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))
    if code == 200:
        code2, r2 = call("/api/stock/in/confirm", {"id": r["data"]["id"]}, token=pt)
        check("入库确认（库存到账）", code2 == 200, str(r2.get("data"))[:60])

    # ============ 4. 建档 ============
    step(4, "建档：患者登记")
    code, r = call("/api/patient/create", {"name": "李桂芳", "gender": "F",
                                           "birth_date": "1962-08-14",
                                           "phone": "13900001111",
                                           "address": "示例街道 12 号"}, token=dt)
    check("建档", code == 200 and r["data"]["patient_no"].startswith("P"),
          r["data"].get("patient_no") if code == 200 else str(r))
    pid = r["data"]["id"]

    # ============ 5. 就诊 ============
    step(5, "就诊登记与接诊")
    code, r = call("/api/visit/create", {"patient_id": pid, "visit_type": "TCM"}, token=dt)
    check("挂号建就诊", code == 200, r["data"].get("visit_no") if code == 200 else str(r))
    vid = r["data"]["id"]
    code, r = call("/api/visit/start", {"visit_id": vid}, token=dt)
    check("开始接诊（自动建病历）", code == 200 and r["data"]["record_id"] > 0)
    rid = r["data"]["record_id"]

    # ============ 6. 写病历 ============
    step(6, "写病历：主诉 / 现病史 / 中医四诊 / 辨证论治 / 诊断 / 辅助检查")
    call("/api/record/save/chief", {"record_id": rid, "content": "反复咳嗽气短两月，加重一周",
                                    "duration": "2月，加重1周"}, token=dt)
    call("/api/record/save/present", {"record_id": rid,
                                      "content": "两月前感冒后咳嗽迁延不愈，痰白清稀，动则气短，"
                                                 "纳差便溏，畏寒自汗。",
                                      "onset_time": "两月前"}, token=dt)
    call("/api/record/save/tcm_four", {"record_id": rid,
                                       "inspection": "神疲乏力，面色㿠白",
                                       "auscultation": "语声低怯",
                                       "interrogation": "畏寒自汗，纳差便溏",
                                       "palpation": "腹软无压痛",
                                       "tongue_body": "舌淡胖边有齿痕",
                                       "tongue_coating": "苔白腻",
                                       "pulse_desc": "脉细弱",
                                       "constitution": "气虚质"}, token=dt)
    code, r = call("/api/record/save/syndrome", {"record_id": rid, "replace": True, "items": [
        {"syndrome_name": "肺脾气虚证",
         "basis": "咳嗽气短、纳差便溏、舌淡胖苔白腻、脉细弱",
         "treatment_principle": "补脾益肺，化痰止咳",
         "treatment_method": "中药汤剂内服，每日一剂",
         "formula_ref": "六君子汤加减"}]}, token=dt)
    check("辨证论治", code == 200 and r["data"]["saved"] == 1, str(r.get("data")))

    call("/api/record/save/diagnosis", {"record_id": rid, "replace": True, "items": [
        {"diag_type": "TCM", "tcm_name": "咳嗽·肺脾气虚证", "is_primary": 1},
        {"diag_type": "WEST", "icd10_code": "R05", "icd10_name": "咳嗽"}]}, token=dt)
    call("/api/record/save/vital", {"record_id": rid, "temperature_c": 36.6, "pulse": 78,
                                    "respiration": 18, "systolic": 118, "diastolic": 74},
         token=dt)
    call("/api/record/save/exam", {"record_id": rid, "replace": True, "items": [
        {"section": "GENERAL", "content": "咽部略红，双肺呼吸音清，未闻及干湿啰音"}]},
         token=dt)

    # F16：即时检查 + 引用外送检验
    code, r = call("/api/record/save/exam_ref", {"record_id": rid, "items": [
        {"ref_type": "POINT", "exam_name": "指尖血糖", "exam_value": "5.4",
         "unit": "mmol/L"}], "reason": "院内即时检查"}, token=dt)
    check("F16 即时检查登记", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))

    # 检验机构与项目：同样幂等（AG01 已存在就复用）
    c, rr = call("/api/lab/agency/list", method="GET", token=dt)
    ag_id = None
    for x in ((rr.get("data") or {}).get("items") or []):
        if x.get("agency_code") == "AG01":
            ag_id = x["id"]
    if ag_id is None:
        c, rr = call("/api/lab/agency/create", {"agency_code": "AG01", "name": "示例检验所"},
                     token=dt)
        ag_id = (rr.get("data") or {}).get("id")
    print("      检验机构 AG01：id=%s" % ag_id)

    c, rr = call("/api/lab/item/search", {"keyword": "血常规"}, method="GET", token=dt)
    items = ((rr.get("data") or {}).get("items") or [])
    item_id = items[0]["id"] if items else None
    if item_id is None:
        c, rr = call("/api/lab/item/create", {"agency_id": ag_id, "item_code": "CBC",
                                              "name": "血常规", "price_fen": 3000,
                                              "settle_price_fen": 1800, "report_days": 1,
                                              "specimen_type": "BLOOD"}, token=dt)
        item_id = (rr.get("data") or {}).get("id")
    print("      检验项目 血常规：id=%s" % item_id)

    c, rr = call("/api/lab/order/create", {"visit_id": vid, "clinical_note": "咳嗽待查"},
                 token=dt)
    order = rr.get("data") or {}
    check("开立检验申请", bool(order.get("id")), order.get("order_no") or str(rr))
    call("/api/lab/order/save/items", {"lab_order_id": order["id"],
                                       "items": [{"lab_item_id": item_id}]}, token=dt)
    call("/api/lab/order/submit", {"lab_order_id": order["id"]}, token=dt)
    call("/api/lab/specimen/collect", {"lab_order_id": order["id"]}, token=dt)
    call("/api/lab/delivery/create", {"lab_order_id": order["id"],
                                      "agency_id": ag_id}, token=dt)
    call("/api/lab/result/save", {"lab_order_id": order["id"], "items": [
        {"item_name": "白细胞", "result_text": "6.8", "unit": "10^9/L",
         "ref_range": "3.5-9.5", "abnormal_flag": "NORMAL",
         "result_summary": "血常规未见明显异常"}]}, token=dt)
    lab = call("/api/lab/order/get", {"lab_order_id": order["id"]}, method="GET", token=dt)[1]
    results = ((lab.get("data") or {}).get("results") or [])
    res_id = results[0]["id"] if results else None
    check("外送结果已回报", res_id is not None, "result_id=%s" % res_id)
    if res_id:
        code, r = call("/api/record/save/exam_ref", {"record_id": rid, "items": [
            {"ref_type": "LAB", "lab_result_id": res_id, "summary": "血常规未见明显异常"}],
            "reason": "引用检验结果"}, token=dt)
        check("F16 引用外送检验", code == 200,
              str(r.get("data"))[:60] if code == 200 else str(r))
    else:
        check("F16 引用外送检验", False, "无结果可引用")

    code, r = call("/api/record/exam_ref/list", {"record_id": rid}, method="GET", token=dt)
    if code == 200:
        for x in r["data"]["items"]:
            print("      · %s" % x.get("display_status"))
        check("辅助检查展示（含即时/已回报）", len(r["data"]["items"]) == 2)

    # 语音录入（AI 降级也照常）
    code, r = call("/api/ai/task/create", {"task_type": "ASR", "visit_id": vid}, token=dt)
    if code == 200:
        tr = call("/api/ai/transcript/save", {
            "ai_task_id": r["data"]["id"],
            "text": "患者咳嗽两月，动则气短，纳差便溏"}, token=dt)[1]
        check("AI 语音转写", tr.get("ok") is True or "data" in tr, str(tr.get("data"))[:60])

    # ============ 7. 确认签名 ============
    step(7, "病历确认签名（DRAFT → CONFIRMED）")
    code, r = call("/api/record/confirm", {"record_id": rid, "password": PWD}, token=dt)
    check("确认签名", code == 200 and r["data"]["to"] == "CONFIRMED",
          "v%s" % r["data"].get("version_no") if code == 200 else str(r))

    # ============ 8. 开方 ============
    step(8, "开中药饮片方（7 付）")
    code, r = call("/api/prescription/create", {"visit_id": vid, "prescription_type": "HERB",
                                                "doses": 7, "decoction_method": "水煎",
                                                "administration": "每日一剂，分两次温服"},
                   token=dt)
    check("建方", code == 200, r["data"].get("prescription_no") if code == 200 else str(r))
    rx = r["data"]["id"]
    code, r = call("/api/prescription/save/herbs", {"prescription_id": rx, "items": [
        {"herb_id": herbs["HQ001"], "dose_g": 30, "foot_note": "NORMAL"},
        {"herb_id": herbs["DZ001"], "dose_g": 15, "foot_note": "NORMAL"},
        {"herb_id": herbs["BZ001"], "dose_g": 12, "foot_note": "NORMAL"}]}, token=dt)
    check("写饮片明细（3 味）", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))
    code, r = call("/api/prescription/submit", {"prescription_id": rx}, token=dt)
    check("提交（规则校验）", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))
    code, r = call("/api/prescription/confirm", {"prescription_id": rx, "password": PWD},
                   token=dt)
    check("确认并计价", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))
    rx_amt = (r.get("data") or {}).get("total_amount") or \
        call("/api/prescription/get", {"id": rx}, method="GET", token=dt)[1]["data"] \
        .get("total_amount")
    print("      处方金额：%s 分" % rx_amt)

    # ============ 9. 收费（收费员）============
    step(9, "收费（换收费员操作）")
    code, r = call("/api/bill/create", {"visit_id": vid}, token=ct)
    if code != 200:
        err = (r.get("error") or {}).get("code")
        if err == "E3006":
            # 这是**正确的业务行为**：当天已日结就不能再造新账单（F58 R8）。
            # 真实诊所里意味着"今天的生意已经收工了"，要再看完整流程请加 --fresh。
            print("      [跳过] 当天已日结，不能生成新账单（E3006 / F58 R8）——这是正确行为")
            print("      >> 想看完整流程请用：python tools/live_walkthrough.py --fresh")
            SKIPPED.append("日结后的新业务（当天已收工）")
            return _summary(args)
        check("生成账单", False, str(r))
        return 1
    check("生成账单", code == 200, "%s 分" % (r["data"].get("total_amount_fen")))
    bill = r["data"]
    code, r = call("/api/bill/pay", {"bill_id": bill["id"], "payments": [
        {"channel": "WECHAT", "amount_fen": bill["total_amount_fen"],
         "received_amount_fen": bill["total_amount_fen"], "trade_no": "WX-DEMO-001"}]},
        token=ct)
    check("微信支付完成", code == 200 and r["data"]["status"] == "PAID",
          "应收/实收 %s 分" % bill["total_amount_fen"])

    # ============ 10. 发药（药师）============
    step(10, "中药调剂发药（换药师操作）")
    code, r = call("/api/pharmacy/herb_dispense", {"prescription_id": rx,
                                                   "decoction_type": "DAIJIAN"}, token=pt)
    check("代煎发药", code == 200, str(r.get("data"))[:80] if code == 200 else str(r))
    code, r = call("/api/stock/query", {"herb_id": herbs["HQ001"]}, method="GET", token=pt)
    d = r.get("data") or {}
    check("饮片库存查询按 herb_id 正确识别为 HERB", d.get("item_kind") == "HERB",
          "item_kind=%s id=%s%s" % (d.get("item_kind"), d.get("item_id"),
                                    "" if code == 200 else " HTTP=%s %s" % (code, r)))
    if code == 200:
        print("      黄芪库存：可用 %s g，批次 %d 个"
              % (d.get("available"), len(d.get("batches") or [])))
        check("库存已扣减（< 5000 g）", float(d.get("available") or 0) < 5000.0,
              "%s g" % d.get("available"))

    # ============ 11. 打印 ============
    step(11, "打印：处方笺 / 收费凭条 / 药袋标签")
    for name, path, body in (("处方笺", "/api/print/prescription", {"prescription_id": rx}),
                             ("收费凭条", "/api/print/receipt", {"bill_id": bill["id"]}),
                             ("药袋标签", "/api/print/label", {"prescription_id": rx})):
        code, r = call(path, body, token=dt)
        if code == 200:
            ex = call("/api/print/job/execute", {"job_id": r["data"]["id"]}, token=dt)
            check("打印%s" % name, ex[0] == 200 and ex[1]["data"]["status"] == "SUCCESS",
                  r["data"].get("print_job_no"))
        else:
            check("打印%s" % name, False, str(r))

    # ============ 12. 归档（归档医生可，封存只管理员）============
    step(12, "病历归档与封存")
    code, r = call("/api/record/archive", {"record_id": rid}, token=dt)
    check("归档（医生）", code == 200, r["data"].get("archive_no") if code == 200 else str(r))
    code, r = call("/api/record/seal", {"record_id": rid, "reason": "疗程结束，规范封存"},
                   token=token)
    check("封存", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))
    code, r = call("/api/record/hash/verify", {"record_id": rid}, method="GET", token=dt)
    check("Hash 链校验", code == 200 and r["data"].get("valid") is True, str(r.get("data"))[:60])

    # ============ 13. 完成就诊 ============
    step(13, "完成就诊")
    code, r = call("/api/visit/complete", {"visit_id": vid}, token=dt)
    check("就诊完成", code == 200, str(r.get("data"))[:60] if code == 200 else str(r))

    # ============ 14. 日结（收费员）============
    step(14, "日结")
    today = datetime.date.today().isoformat()
    code, r = call("/api/settlement/close", {"biz_date": today, "remark": "实操演练日结"},
                   token=ct)
    check("日结", code == 200, "%s 净额 %s 分" % (r["data"].get("settlement_no"),
                                                r["data"].get("net_amount_fen"))
          if code == 200 else str(r))

    # ============ 15. 报表（管理员）============
    step(15, "经营报表")
    code, r = call("/api/report/revenue_daily", {"from": today, "to": today}, method="GET",
                   token=token)
    check("收入日报", code == 200, str((r.get("data") or {}).get("summary"))[:70])
    for nm, p in (("就诊统计", "/api/report/visit_stats"),
                  ("处方统计", "/api/report/prescription_stats"),
                  ("患者统计", "/api/report/patient_stats"),
                  ("库存流水", "/api/report/stock_ledger")):
        code, r = call(p, {"from": today, "to": today}, method="GET", token=token)
        check(nm, code == 200, str(r.get("ok")))
    code, r = call("/api/report/export", {"kind": "revenue", "from": today, "to": today},
                   token=token)
    check("报表导出 CSV", code == 200, str((r.get("data") or {}).get("file"))[:70])

    # ============ 16. 运维 ============
    step(16, "运维：完整性检查 / 备份 / 看门狗 / 审计")
    code, r = call("/api/system/integrity/full", {}, token=token)
    check("完整性检查（三层）", code == 200 and r["data"].get("ok") is True,
          str(r.get("data"))[:70])
    code, r = call("/api/system/backup", {"reason": "实操演练：日终手工备份"}, token=token)
    check("手工全量备份", code == 200 and r["data"].get("valid") is True,
          "%s（%s 字节）" % (r["data"].get("file"), r["data"].get("size"))
          if code == 200 else str(r))
    code, r = call("/api/system/backup/incremental", {"force": True}, token=token)
    check("增量备份", code == 200 and r["data"].get("skipped") is not True,
          str(r.get("data"))[:70])
    code, r = call("/api/system/watchdog", {}, token=token)
    check("跑一次看门狗", code == 200, str(r.get("data"))[:70])
    code, r = call("/api/system/recovery/crash", {}, token=token)
    check("崩溃恢复扫描", code == 200, str(r.get("data"))[:70])
    code, r = call("/api/audit/summary", method="GET", token=token)
    if code == 200:
        print("      审计条数：%s" % (r["data"].get("counts")))
        check("审计概览可查", True)
    else:
        check("审计概览可查", False, str(r))

    return _summary(args)


def _summary(args):
    print("\n" + "=" * 68)
    print("实操演练结果：%d 项通过 / %d 项失败 / %d 项跳过"
          % (len(PASSED), len(FAILED), len(SKIPPED)))
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("  - %s" % n)
    if SKIPPED:
        print("跳过项：")
        for n in SKIPPED:
            print("  - %s" % n)
    print("备份目录：%s" % os.path.join(args.root, "backups"))
    print("日志目录：%s" % os.path.join(args.root, "logs"))
    print("打印输出：%s" % os.path.join(args.root, "logs", "prints"))
    print("详细输出：%s" % _LOG_PATH)
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc(file=_LOG)
        _LOG.flush()
        sys.exit(2)
