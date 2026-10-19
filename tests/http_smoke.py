# -*- coding: utf-8 -*-
"""HTTP 接口冒烟：真正起服务，用 HTTP 打一遍第一阶段主流程。

用法：python tests/http_smoke.py
"""

import datetime
import json
import os
import shutil
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
from modules import (m01_patient, m02_visit, m03_record, m12_audit, m14_system,  # noqa: E402,F401
                     m04_ai, m05_lab, m06_prescription,
                     m07_pharmacy, m08_treatment, m09_billing, m15_report)

PASSED, FAILED = [], []
BASE = ""


def call(path, body=None, method=None, token=None):
    method = method or ("POST" if body is not None else "GET")
    url = BASE + path
    if method == "GET" and body:
        # 同时提供 query string 与 body：业务函数有的读 q、有的读 body
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
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("  %s  %s %s" % ("PASS" if cond else "FAIL", name, extra))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    global BASE
    root = tempfile.mkdtemp(prefix="clinic_http_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    shutil.copytree(os.path.join(PROJECT, "web"), os.path.join(root, "web"))
    port = free_port()
    BASE = "http://127.0.0.1:%d" % port

    httpd, url, report = runtime.bootstrap(root, port=port, start_server=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print("服务已启动：%s\n" % url)

    try:
        code, r = call("/api/system/status")
        check("GET /api/system/status", code == 200 and r["ok"], str(r.get("data", {}).get("initialized")))
        check("静态页面可访问", urllib.request.urlopen(BASE + "/").status == 200)

        code, r = call("/api/system/init", {"clinic_name": "HTTP 测试诊所",
                                            "admin_username": "admin",
                                            "admin_password": "Clinic@2026",
                                            "admin_name": "管理员"})
        check("初始化", code == 200 and r["data"]["initialized"])

        code, r = call("/api/auth/login", {"username": "admin", "password": "Clinic@2026",
                                           "role_code": "ADMIN"})
        check("管理员登录", code == 200 and bool(r["data"]["token"]))
        token = r["data"]["token"]

        # 给自己加医生角色，便于后续以医生身份操作
        call("/api/system/user/roles", {"user_id": 1, "roles": ["ADMIN", "DOCTOR"]}, token=token)
        code, r = call("/api/auth/login", {"username": "admin", "password": "Clinic@2026",
                                           "role_code": "DOCTOR"})
        dtoken = r["data"]["token"]

        code, r = call("/api/patient/create", {"name": "王五", "gender": "M",
                                               "birth_date": "1978-05-20",
                                               "phone": "13612345678"}, token=dtoken)
        check("建档（医生角色）", code == 200 and r["data"]["patient_no"].startswith("P"))
        pid = r["data"]["id"]

        code, r = call("/api/patient/search", {"keyword": "王五"}, method="GET", token=dtoken)
        check("患者搜索", code == 200 and len(r["data"]["items"]) == 1)

        code, r = call("/api/visit/create", {"patient_id": pid, "visit_type": "TCM"}, token=dtoken)
        check("创建就诊", code == 200 and r["data"]["status"] == "WAITING")
        vid = r["data"]["id"]

        code, r = call("/api/visit/queue", {"date": ""}, method="GET", token=dtoken)
        check("候诊队列", code == 200 and any(i["id"] == vid for i in r["data"]["items"]))

        code, r = call("/api/visit/start", {"visit_id": vid}, token=dtoken)
        check("接诊并自动建病历", code == 200 and r["data"]["record_id"] > 0)
        rid = r["data"]["record_id"]

        code, r = call("/api/record/save/chief", {"record_id": rid, "content": "咳嗽 5 天"},
                       token=dtoken)
        check("保存主诉", code == 200)

        call("/api/record/save/diagnosis", {"record_id": rid, "items": [
            {"diag_type": "WEST", "icd10_code": "R05", "icd10_name": "咳嗽", "is_primary": 1},
            {"diag_type": "TCM", "tcm_name": "风寒袭肺"}]}, token=dtoken)
        code, r = call("/api/record/confirm", {"record_id": rid, "password": "Clinic@2026"},
                       token=dtoken)
        check("病历确认签名", code == 200 and r["data"]["to"] == "CONFIRMED")

        # ---------- M06 处方（HTTP）----------
        code, r = call("/api/drug/create", {"drug_code": "IBU001", "generic_name": "布洛芬胶囊",
                                            "spec": "0.3g*10粒", "unit": "盒", "price_fen": 1200},
                       token=dtoken)
        check("药品建档", code == 200 and bool(r.get("data", {}).get("id")),
              "%s %s" % (code, str(r)[:180]))
        drug_id = r.get("data", {}).get("id")

        code, r = call("/api/herb/create", {"herb_code": "HQ", "name": "黄芪",
                                            "price_yuan_per_g": 0.05, "max_dose_g": 30},
                       token=dtoken)
        check("饮片建档", code == 200 and bool(r["data"]["id"]))
        herb_id = r["data"]["id"]

        code, r = call("/api/prescription/create", {"visit_id": vid,
                                                    "prescription_type": "WEST"}, token=dtoken)
        check("开处方", code == 200 and r["data"]["prescription_no"].startswith("R"))
        rxid = r["data"]["id"]

        code, r = call("/api/prescription/save/items", {"prescription_id": rxid,
                                                        "items": [{"drug_id": drug_id,
                                                                   "quantity": 2}]}, token=dtoken)
        check("保存处方明细", code == 200)
        code, r = call("/api/prescription/submit", {"prescription_id": rxid}, token=dtoken)
        check("提交处方 PENDING", code == 200 and r["data"]["status"] == "PENDING")
        code, r = call("/api/prescription/confirm", {"prescription_id": rxid,
                                                     "password": "Clinic@2026"}, token=dtoken)
        check("处方确认 CONFIRMED", code == 200 and r["data"]["status"] == "CONFIRMED")
        code, r = call("/api/prescription/get", {"id": rxid}, method="GET", token=dtoken)
        check("处方查询含金额", code == 200 and r.get("data", {}).get("total_amount") == 2400,
              "%s %s" % (code, str(r)[:200]))

        # 饮片处方：必填项拦截 → 补齐 → 计价
        code, r = call("/api/prescription/create", {"visit_id": vid,
                                                    "prescription_type": "HERB"}, token=dtoken)
        hxid = r["data"]["id"]
        call("/api/prescription/save/herbs", {"prescription_id": hxid,
                                              "items": [{"herb_id": herb_id, "dose_g": 15}]},
             token=dtoken)
        code, r = call("/api/prescription/submit", {"prescription_id": hxid}, token=dtoken)
        check("饮片必填项缺失被拦截", code == 409,
              r.get("error", {}).get("code", ""))
        call("/api/prescription/save/herbs", {"prescription_id": hxid, "doses": 7,
                                              "decoction_method": "水煎",
                                              "administration": "每日一剂",
                                              "items": [{"herb_id": herb_id, "dose_g": 15}]},
             token=dtoken)
        code, r = call("/api/prescription/price", {"prescription_id": hxid}, token=dtoken)
        check("饮片计价 15g×0.05元×7付 = 525 分",
              code == 200 and r["data"]["total_amount_fen"] == 525,
              "%s 元" % r["data"].get("total_yuan"))

        # ---------- M05 检验（HTTP）----------
        code, r = call("/api/lab/agency/create", {"agency_code": "LAB01", "name": "示例检验所"},
                       token=dtoken)
        agid = r["data"]["id"]
        code, r = call("/api/lab/item/create", {"item_code": "CBC", "name": "血常规",
                                                "agency_id": agid, "price_yuan": 30,
                                                "settle_price_yuan": 12}, token=dtoken)
        check("检验项目建档", code == 200)
        item_id = r["data"]["id"]
        code, r = call("/api/lab/order/create", {"visit_id": vid, "agency_id": agid},
                       token=dtoken)
        check("开检验申请", code == 200 and r["data"]["order_no"].startswith("L"))
        loid = r["data"]["id"]
        call("/api/lab/order/save/items", {"lab_order_id": loid,
                                           "items": [{"lab_item_id": item_id}]}, token=dtoken)
        code, r = call("/api/lab/order/submit", {"lab_order_id": loid}, token=dtoken)
        check("检验提交并生成条码", code == 200 and len(r["data"]["barcodes"]) == 1,
              str(r["data"]["barcodes"]))
        code, r = call("/api/lab/specimen/collect", {"lab_order_id": loid}, token=dtoken)
        check("标本采集 → SAMPLED", code == 200 and r["data"]["status"] == "SAMPLED")

        # ---------- M04 AI（HTTP）----------
        code, r = call("/api/ai/task/create", {"task_type": "ASR", "visit_id": vid},
                       token=dtoken)
        check("AI 任务创建", code == 200 and r["data"]["task_no"].startswith("AI"))
        tid = r["data"]["id"]
        code, r = call("/api/ai/transcript/save", {"ai_task_id": tid,
                                                   "text": "患者咳嗽三天，体温 37.5 度"},
                       token=dtoken)
        check("AI 转写", code == 200 and r["data"]["status"] == "SUCCEEDED")
        code, r = call("/api/ai/task/create", {"task_type": "EXTRACT", "visit_id": vid},
                       token=dtoken)
        texid = r["data"]["id"]
        code, r = call("/api/ai/extract", {"ai_task_id": texid}, token=dtoken)
        check("AI 事实抽取", code == 200 and len(r.get("data", {}).get("facts", [])) > 0,
              "%s %s" % (code, str(r)[:150]))
        code, r = call("/api/ai/risk/check", {"patient_id": pid}, token=dtoken)
        check("AI 风险提醒接口", code == 200 and isinstance(r["data"]["alerts"], list))

        # ---------- 第三阶段：M07 药房（HTTP）----------
        expiry = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        call("/api/system/user/roles", {"user_id": 1,
                                        "roles": ["ADMIN", "DOCTOR", "PHARMACIST", "CASHIER"]},
             token=token)
        code, r = call("/api/auth/login", {"username": "admin", "password": "Clinic@2026",
                                           "role_code": "PHARMACIST"})
        phtoken = r["data"]["token"]

        code, r = call("/api/stock/supplier/create", {"name": "康美药业", "supplier_code": "KM001"},
                       token=phtoken)
        check("供应商建档", code == 200 and bool(r["data"]["id"]))
        sup_id = r["data"]["id"]
        code, r = call("/api/stock/in/create", {
            "supplier_id": sup_id,
            "items": [{"item_kind": "DRUG", "drug_id": drug_id, "qty": 50, "unit": "盒",
                       "cost_yuan_per_unit": 8.5, "expiry_date": expiry}]}, token=phtoken)
        check("入库单号 IN{yyyyMMdd}{4位}",
              code == 200 and r["data"]["stock_in_no"].startswith("IN"),
              r.get("error", {}).get("message", ""))
        si_id = r["data"]["id"]
        code, r = call("/api/stock/in/confirm", {"id": si_id}, token=phtoken)
        check("入库确认生效", code == 200 and r["data"]["status"] == "CONFIRMED")

        code, r = call("/api/stock/query", {"item_kind": "DRUG", "drug_id": drug_id},
                       method="GET", token=phtoken)
        check("入库后可用量 50", code == 200 and r["data"]["available"] == 50,
              str(r.get("data", {}).get("available")))

        code, r = call("/api/pharmacy/dispense", {"prescription_id": hxid}, token=dtoken)
        check("医生无发药权限（perm=dispense.do）", code == 403,
              r.get("error", {}).get("code", ""))

        code, r = call("/api/pharmacy/dispense", {"prescription_id": rxid}, token=phtoken)
        check("成药发药 → DISPENSED", code == 200 and r["data"]["status"] == "DISPENSED",
              r.get("error", {}).get("message", ""))
        code, r = call("/api/stock/query", {"item_kind": "DRUG", "drug_id": drug_id},
                       method="GET", token=phtoken)
        check("发药后库存 48", code == 200 and r["data"]["available"] == 48,
              str(r.get("data", {}).get("available")))
        code, r = call("/api/stock/transactions", {"ref_type": "DISPENSE", "limit": 10},
                       method="GET", token=phtoken)
        check("发药产生出库流水", code == 200 and len(r["data"]["items"]) == 1,
              str(len(r.get("data", {}).get("items", []))))

        # ---------- 第三阶段：M08 处置（HTTP）----------
        code, r = call("/api/treatment/item/create", {
            "item_code": "TZ01", "name": "针灸治疗", "category": "TCM",
            "is_tcm": 1, "need_record": 1, "price_fen": 3000}, token=dtoken)
        check("处置项目建档", code == 200 and bool(r["data"]["id"]))
        ti_id = r["data"]["id"]
        code, r = call("/api/treatment/order", {"visit_id": vid,
                                                "items": [{"item_id": ti_id, "quantity": 1}]},
                       token=dtoken)
        check("开立处置 TE{yyyyMMdd}{4位}",
              code == 200 and r["data"]["items"][0]["exec_no"].startswith("TE"),
              r.get("error", {}).get("message", ""))
        exec_id = r["data"]["items"][0]["id"]
        call("/api/treatment/start", {"id": exec_id}, token=dtoken)
        code, r = call("/api/treatment/complete", {"id": exec_id}, token=dtoken)
        check("中医项目未填穴位不能完成", code == 400, r.get("error", {}).get("code", ""))
        code, r = call("/api/treatment/tcm/save", {
            "exec_id": exec_id, "therapy_type": "ACUP", "points": "足三里,三阴交",
            "manipulation": "平补平泻", "retain_minutes": 30}, token=dtoken)
        check("中医治疗记录保存", code == 200, r.get("error", {}).get("message", ""))
        call("/api/treatment/process/add", {"exec_id": exec_id, "phase": "STEP",
                                            "content": "常规消毒后进针"}, token=dtoken)
        code, r = call("/api/treatment/complete", {"id": exec_id}, token=dtoken)
        check("处置完成 COMPLETED", code == 200 and r["data"]["to"] == "COMPLETED",
              r.get("error", {}).get("message", ""))

        # ---------- 第三阶段：M09 收费（HTTP）----------
        code, r = call("/api/auth/login", {"username": "admin", "password": "Clinic@2026",
                                           "role_code": "CASHIER"})
        ctoken2 = r["data"]["token"]
        code, r = call("/api/charge_item/create", {
            "item_code": "GH01", "name": "普通挂号费", "category": "REGISTRATION",
            "price_fen": 1000, "auto_add_on_visit": 1}, token=ctoken2)
        check("收费项目建档", code == 200)
        code, r = call("/api/bill/create", {"visit_id": vid}, token=ctoken2)
        # 处方2400 + 处置3000 + 检验3000 + 挂号1000 = 9400 分
        check("账单汇总 = 处方2400 + 处置3000 + 检验3000 + 挂号1000 = 9400 分",
              code == 200 and r["data"]["total_amount_fen"] == 9400,
              str(r.get("data", {}).get("total_amount_fen")))
        bill_id = r["data"]["id"]
        code, r = call("/api/bill/pay", {"bill_id": bill_id,
                                         "payments": [{"channel": "CASH", "amount_fen": 9400}]},
                       token=ctoken2)
        check("收费 → PAID", code == 200 and r["data"]["status"] == "PAID",
              r.get("error", {}).get("message", ""))
        code, r = call("/api/bill/pay", {"bill_id": bill_id,
                                         "payments": [{"channel": "CASH", "amount_fen": 100}]},
                       token=ctoken2)
        check("金额与应收不符被拒（无部分支付）", code == 409 or code == 400,
              r.get("error", {}).get("code", ""))
        code, r = call("/api/settlement/close", {}, token=ctoken2)
        check("日结净收 9400 分",
              code == 200 and r["data"]["net_amount_fen"] == 9400,
              str(r.get("data", {}).get("net_amount_fen")))
        code, r = call("/api/finance/bills", {"limit": 10}, method="GET", token=ctoken2)
        check("财务查询", code == 200 and len(r["data"]["items"]) >= 1)
        code, r = call("/api/bill/void", {"bill_id": bill_id, "reason": "日结后试图作废"},
                       token=ctoken2)
        check("日结后账单锁定（E3006）", code == 409 or code == 400,
              r.get("error", {}).get("code", ""))

        # 权限：药师不能开方
        call("/api/system/user/roles", {"user_id": 1,
                                        "roles": ["ADMIN", "DOCTOR", "PHARMACIST"]}, token=token)
        code, r = call("/api/auth/login", {"username": "admin", "password": "Clinic@2026",
                                           "role_code": "PHARMACIST"})
        ptoken = r["data"]["token"]
        code, r = call("/api/prescription/create", {"visit_id": vid}, token=ptoken)
        check("药师不能开处方", code == 403, r.get("error", {}).get("code", ""))
        call("/api/system/user/roles", {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "CASHIER"]},
             token=token)

        code, r = call("/api/visit/complete", {"visit_id": vid}, token=dtoken)
        check("完成就诊", code == 200 and r["data"]["to"] == "COMPLETED")

        # ---------- 第四阶段：M15 经营统计（HTTP，就诊已完成才有门诊量）----------
        today = datetime.date.today().isoformat()
        code, r = call("/api/report/revenue_daily", {"from": today, "to": today},
                       method="GET", token=dtoken)
        check("日营收报表（医生可看）",
              code == 200 and r["data"]["summary"]["net_amount_fen"] == 9400,
              str(r.get("data", {}).get("summary")))
        code, r = call("/api/report/visit_stats", {"from": today, "to": today},
                       method="GET", token=dtoken)
        check("门诊量报表（仅 COMPLETED）",
              code == 200 and r["data"]["summary"]["visit_count"] >= 1,
              str(r.get("data", {}).get("summary")))
        code, r = call("/api/report/doctor_workload", {"from": today, "to": today},
                       method="GET", token=dtoken)
        check("医生工作量报表", code == 200 and len(r["data"]["items"]) >= 1,
              str(r.get("data", {}).get("items"))[:120])
        code, r = call("/api/report/stock_ledger", {"from": today, "to": today},
                       method="GET", token=dtoken)
        check("药品进销存报表", code == 200 and len(r["data"]["batches"]) >= 1,
              str(len(r.get("data", {}).get("batches", []))))
        code, r = call("/api/report/prescription_stats", {"from": today, "to": today},
                       method="GET", token=dtoken)
        check("处方统计报表", code == 200 and r["data"]["summary"]["valid_count"] >= 1)
        code, r = call("/api/report/patient_stats", {"from": today, "to": today},
                       method="GET", token=dtoken)
        check("患者与疾病谱报表", code == 200 and "disease_top_west" in r["data"])
        code, r = call("/api/report/export", {"kind": "revenue", "from": today, "to": today},
                       token=dtoken)
        check("医生无导出权限（report.export）", code == 403,
              r.get("error", {}).get("code", ""))
        code, r = call("/api/report/export", {"kind": "revenue", "from": today, "to": today},
                       token=token)
        check("管理员导出 CSV 成功", code == 200 and r["data"]["rows"] >= 1,
              str(r.get("data", {}).get("file_name")))
        code, r = call("/api/report/print_data", {"kind": "stock", "from": today, "to": today},
                       method="GET", token=dtoken)
        check("报表打印数据", code == 200 and bool(r["data"]["footer"]))
        code, r = call("/api/report/revenue_daily", {"from": "2000-01-01", "to": "2010-01-01"},
                       method="GET", token=dtoken)
        check("报表区间过大被拒", code == 400, r.get("error", {}).get("code", ""))

        # 权限：收费员角色不能写病历
        call("/api/system/user/roles", {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "CASHIER"]},
             token=token)
        code, r = call("/api/auth/login", {"username": "admin", "password": "Clinic@2026",
                                           "role_code": "CASHIER"})
        ctoken = r["data"]["token"]
        code, r = call("/api/record/save/chief", {"record_id": rid, "content": "改内容"},
                       token=ctoken)
        check("越权写病历被拒绝", code == 403, r.get("error", {}).get("code", ""))
        code, r = call("/api/audit/permission", {"limit": 10}, method="GET", token=token)
        check("越权写入审计", code == 200 and len(r["data"]["items"]) >= 1)

        # 未登录访问
        code, r = call("/api/patient/search", {"keyword": "王"}, method="GET")
        check("未登录被拒绝", code == 401, r.get("error", {}).get("code", ""))

        # 审计只读
        code, r = call("/api/audit/summary", method="GET", token=token)
        check("审计概览可读", code == 200 and r["data"]["counts"]["operations"] > 0)

        # 备份
        code, r = call("/api/system/backup", {"reason": "HTTP_TEST"}, token=token)
        check("手动备份", code == 200 and r["data"].get("valid") is True)

        # 诊断
        code, r = call("/api/system/diagnose", token=token)
        check("系统诊断", code == 200 and len(r["data"]["items"]) >= 4)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)

    print("\nPASS %d / FAIL %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：%s" % FAILED)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
