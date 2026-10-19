# -*- coding: utf-8 -*-
"""首装体验测试：全新空库 → 初始化 → 立刻走完整门诊。

这条用例专门锁死一个曾经致命的问题：
  初始化只给管理员 ADMIN 一个角色时，新装系统第一次登录
  建档/写病历/开方/收费/发药全部 E6002「账号无权执行」，
  表现为"装好了但什么都干不了"。

用法：python tests/first_run_test.py
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import runtime  # noqa: E402
from modules import (m01_patient, m02_visit, m03_record, m12_audit, m14_system,  # noqa: E402,F401
                     m04_ai, m05_lab, m06_prescription, m07_pharmacy, m08_treatment,
                     m09_billing, m10_print, m11_archive, m13_recovery, m15_report)

PASSED, FAILED = [], []
BASE = ""
PWD = "Clinic@2026"


def call(path, body=None, method=None, token=None):
    m = method or ("POST" if body is not None else "GET")
    data = json.dumps(body).encode() if (body is not None and m != "GET") else None
    req = urllib.request.Request(BASE + path, data=data, method=m)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    global BASE
    root = tempfile.mkdtemp(prefix="clinic_firstrun_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    shutil.copytree(os.path.join(PROJECT, "web"), os.path.join(root, "web"))
    port = free_port()
    BASE = "http://127.0.0.1:%d" % port
    httpd, url, report = runtime.bootstrap(root, port=port, start_server=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("全新库：%s\n服务：%s\n" % (root, url))

    try:
        print("[首装] 初始化")
        code, r = call("/api/system/init", {"clinic_name": "首装测试诊所",
                                            "admin_username": "doctor",
                                            "admin_password": PWD, "admin_name": "张医生"})
        check("初始化成功", code == 200 and r["data"]["initialized"], str(code))

        print("\n[首装] 登录并检查权限")
        code, r = call("/api/auth/login", {"username": "doctor", "password": PWD,
                                           "role_code": "DOCTOR"})
        check("管理员首次登录成功", code == 200 and bool(r["data"]["token"]))
        tok = r["data"]["token"]
        roles = set(r["data"]["roles"])
        check("首装即授予四个角色", {"ADMIN", "DOCTOR", "PHARMACIST", "CASHIER"} <= roles,
              str(sorted(roles)))
        perms = set(r["data"]["permissions"])
        need = {"patient.create", "record.edit", "record.confirm", "prescription.create",
                "prescription.confirm", "bill.create", "bill.pay", "dispense.do",
                "print.create", "system.config", "report.view"}
        check("首装权限覆盖完整门诊流程", need <= perms, "缺少 %s" % sorted(need - perms))

        print("\n[首装] 立刻走一遍门诊（每步都是真接口）")
        code, r = call("/api/patient/create", {"name": "首装患者", "gender": "M",
                                               "birth_date": "1980-01-01"}, token=tok)
        check("建档", code == 200, r.get("error", {}).get("message", "")[:60])
        pid = r["data"]["id"]

        code, r = call("/api/visit/create", {"patient_id": pid, "visit_type": "TCM"}, token=tok)
        check("挂号", code == 200, r.get("error", {}).get("message", "")[:60])
        vid = r["data"]["id"]

        code, r = call("/api/visit/start", {"visit_id": vid}, token=tok)
        check("接诊", code == 200 and bool(r.get("data", {}).get("record_id")),
              r.get("error", {}).get("message", "")[:60])
        rid = r["data"]["record_id"]

        code, r = call("/api/record/save/chief", {"record_id": rid, "content": "咳嗽 2 天"},
                       token=tok)
        check("写病历", code == 200, r.get("error", {}).get("message", "")[:60])
        # 业务规则：没有诊断不能确认病历
        code, r = call("/api/record/save/diagnosis",
                       {"record_id": rid, "items": [{"diag_type": "WEST",
                                                     "icd10_name": "急性支气管炎",
                                                     "is_primary": 1}], "replace": True},
                       token=tok)
        check("写诊断", code == 200, r.get("error", {}).get("message", "")[:60])
        code, r = call("/api/record/confirm", {"record_id": rid, "password": PWD}, token=tok)
        check("病历签名", code == 200, r.get("error", {}).get("message", "")[:60])

        # 饮片目录 + 开方
        code, r = call("/api/herb/create", {"herb_code": "H001", "name": "甘草",
                                            "price_yuan_per_g": 0.03, "max_dose_g": 30},
                       token=tok)
        check("饮片建档", code == 200, r.get("error", {}).get("message", "")[:60])
        herb_id = r["data"]["id"]

        code, r = call("/api/prescription/create", {"visit_id": vid, "prescription_type": "HERB",
                                                    "doses": 7,
                                                    "decoction_method": "水煎",
                                                    "administration": "每日一剂分两次温服"},
                       token=tok)
        check("开饮片方", code == 200, r.get("error", {}).get("message", "")[:60])
        rxid = r["data"]["id"]
        code, r = call("/api/prescription/save/herbs", {"prescription_id": rxid,
                                                        "items": [{"herb_id": herb_id,
                                                                   "dose_g": 10,
                                                                   "foot_note": "NORMAL"}]},
                       token=tok)
        check("保存饮片明细", code == 200, r.get("error", {}).get("message", "")[:60])
        code, r = call("/api/prescription/submit", {"prescription_id": rxid}, token=tok)
        check("提交处方", code == 200, r.get("error", {}).get("message", "")[:60])
        code, r = call("/api/prescription/confirm", {"prescription_id": rxid,
                                                     "password": PWD}, token=tok)
        check("确认处方", code == 200, r.get("error", {}).get("message", "")[:60])

        code, r = call("/api/bill/create", {"visit_id": vid}, token=tok)
        check("生成账单", code == 200, r.get("error", {}).get("message", "")[:60])
        bid = r["data"]["id"]
        total = r["data"]["total_amount_fen"]
        code, r = call("/api/bill/pay", {"bill_id": bid,
                                         "payments": [{"channel": "CASH",
                                                       "amount_fen": total}]}, token=tok)
        check("收费", code == 200, r.get("error", {}).get("message", "")[:60])

        code, r = call("/api/print/prescription", {"prescription_id": rxid}, token=tok)
        check("生成处方笺打印任务", code == 200, r.get("error", {}).get("message", "")[:60])

        code, r = call("/api/settlement/today", method="GET", token=tok)
        check("今日日结状态可查", code == 200 and r["data"]["settled"] is False,
              str(r.get("data", {}).get("settled")))
        code, r = call("/api/settlement/close", {}, token=tok)
        check("日结可执行", code == 200, r.get("error", {}).get("message", "")[:60])
        code, r = call("/api/settlement/today", method="GET", token=tok)
        check("日结后状态为已日结", code == 200 and r["data"]["settled"] is True)
        code, r = call("/api/settlement/reopen", {"reason": "首装测试：撤销当日日结"}, token=tok)
        check("可撤销当日日结", code == 200 and r["data"]["voided"], 
              str(r.get("error", {}).get("message", ""))[:60])
        code, r = call("/api/settlement/today", method="GET", token=tok)
        check("撤销后可继续营业", code == 200 and r["data"]["settled"] is False)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass

    print("\n" + "=" * 60)
    print("首装体验：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  失败：" + f)
    print("=" * 60)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
