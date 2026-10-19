# -*- coding: utf-8 -*-
"""界面探针：像浏览器那样真实请求，验证"菜单里有、点了也能用"。

不是测服务函数（那早就是绿的），而是验证**前端页面真正依赖的那串 HTTP 调用**
能不能跑通——包括静态资源是否已更新、菜单是否含新页签、开方页依赖的接口链是否可用。

用法：python tools/ui_probe.py [--port 8787]
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
sys.path.insert(0, os.path.join(PROJECT, "src"))

PASSED, FAILED = [], []
BASE = ""
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_ui_probe.txt")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_w = sys.stdout.write


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


def raw_get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.status, r.read().decode("utf-8", "ignore"), dict(r.headers)


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    # 默认口令仅供本地测试库使用；正式环境用环境变量 CLINIC_PWD 覆盖
    ap.add_argument("--password", default=os.environ.get("CLINIC_PWD", "Clinic@2026"))
    args = ap.parse_args()
    BASE = "http://127.0.0.1:%d" % args.port

    print("=" * 68)
    print("界面探针 · %s" % BASE)
    print("=" * 68)

    # 1) 静态资源是否已更新
    code, js, hdr = raw_get("/app.js")
    check("GET /app.js 可取", code == 200, "%d 字节" % len(js))
    check("静态资源不缓存（no-store）",
          "no-store" in (hdr.get("Cache-Control") or ""),
          str(hdr.get("Cache-Control")))
    for token, label in (("#/prescribe", "开方菜单"), ("viewPrescribe", "开方页渲染函数"),
                         ("#/lab", "检验菜单"), ("viewLab", "检验页"),
                         ("#/print", "打印菜单"), ("viewPrint", "打印页"),
                         ("#/ai", "AI 菜单"), ("viewAi", "AI 页")):
        check("前端含 %s" % label, token in js)

    # 2) 登录
    code, r = call("/api/auth/login", {"username": "admin", "password": args.password,
                                       "role_code": "ADMIN"})
    check("管理员登录", code == 200 and bool((r.get("data") or {}).get("token")),
          "" if code == 200 else str(r))
    tk = (r.get("data") or {}).get("token")
    if not tk:
        print("登录失败，后续无法继续")
        return _summary()
    call("/api/system/user/roles", {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                            "CASHIER"]}, token=tk)
    dt = (call("/api/auth/login", {"username": "admin", "password": args.password,
                                   "role_code": "DOCTOR"})[1].get("data") or {}).get("token")

    # 3) 开方页真正依赖的接口链
    print("\n--- 开方页依赖的接口链 ---")
    code, r = call("/api/visit/list", {"status": "IN_PROGRESS", "limit": 50}, method="GET",
                   token=dt)
    visits = (r.get("data") or {}).get("items") or []
    check("开方页「进行中的就诊」下拉可取", code == 200, "%d 条" % len(visits))

    if visits:
        vid = visits[0]["id"]
        code, r = call("/api/visit/get", {"id": vid}, method="GET", token=dt)
        check("开方页患者信息卡可取", code == 200, str((r.get("data") or {}).get("patient_name")))
        code, r = call("/api/prescription/list", {"visit_id": vid}, method="GET", token=dt)
        check("开方页处方列表可取", code == 200, "%d 张" % len((r.get("data") or {}).get("items") or []))
    else:
        print("  （暂无进行中的就诊，跳过开方链路的下游检查）")
        vid = None

    code, r = call("/api/drug/search", {"keyword": "布洛"}, method="GET", token=dt)
    check("开方页搜药品可取", code == 200, "%d 条" % len((r.get("data") or {}).get("items") or []))
    code, r = call("/api/herb/search", {"keyword": "黄芪"}, method="GET", token=dt)
    check("开方页搜饮片可取", code == 200, "%d 条" % len((r.get("data") or {}).get("items") or []))

    # 真的开一张方，验证整条链路
    if vid:
        code, r = call("/api/prescription/create", {"visit_id": vid,
                                                    "prescription_type": "HERB", "doses": 7,
                                                    "decoction_method": "水煎",
                                                    "administration": "每日一剂"}, token=dt)
        rx = r.get("data") or {}
        check("建方（开方页「建方」按钮）", code == 200 and bool(rx.get("id")),
              rx.get("prescription_no") if code == 200 else str(r))
        if rx.get("id"):
            herbs = (call("/api/herb/search", {"keyword": "黄芪"}, method="GET",
                          token=dt)[1].get("data") or {}).get("items") or []
            if herbs:
                code, r = call("/api/prescription/save/herbs", {"prescription_id": rx["id"],
                               "items": [{"herb_id": herbs[0]["id"], "dose_g": 15,
                                          "foot_note": "NORMAL"}], "doses": 7,
                               "decoction_method": "水煎", "administration": "每日一剂"}, token=dt)
                check("保存饮片明细（开方页「保存明细」）", code == 200, str(r.get("data"))[:60])
            code, r = call("/api/prescription/price", {"prescription_id": rx["id"]}, token=dt)
            check("计价（开方页「计价」）", code == 200, str(r.get("data"))[:60])
            code, r = call("/api/prescription/validate", {"prescription_id": rx["id"]},
                           token=dt)
            check("规则预检（开方页「规则预检」）", code == 200,
                  "通过" if (r.get("data") or {}).get("passed") else str(r.get("data"))[:70])
            code, r = call("/api/prescription/get", {"id": rx["id"]}, method="GET", token=dt)
            check("处方详情可取", code == 200, "%s 味" % len((r.get("data") or {}).get("herbs") or []))

    # 4) 检验页 / 打印页 / AI 页依赖的接口链
    print("\n--- 检验 / 打印 / AI 页依赖的接口链 ---")
    for label, path, body, m in (
            ("检验页机构列表", "/api/lab/agency/list", {}, "GET"),
            ("检验页项目搜索", "/api/lab/item/search", {"keyword": ""}, "GET"),
            ("检验页标本列表", "/api/lab/specimen/list", {"lab_order_id": 1}, "GET"),
            ("检验页对账列表", "/api/lab/reconcile/list", {}, "GET"),
            ("打印页任务列表", "/api/print/job/list", {"limit": 10}, "GET"),
            ("打印页设备列表", "/api/hardware/devices", {}, "GET"),
            ("打印页模板列表", "/api/print/templates", {}, "GET"),
            ("打印页失败记录", "/api/print/failures", {}, "GET"),
            ("打印页硬件事件", "/api/hardware/events", {"limit": 5}, "GET"),
            ("AI 页任务列表", "/api/ai/task/list", {"limit": 10}, "GET"),
            ("AI 页草稿列表", "/api/ai/draft/list", {"limit": 10}, "GET"),
            ("AI 页硬件自检", "/api/hardware/health", {}, "GET")):
        code, r = call(path, body, method=m, token=dt)
        check(label, code == 200, "" if code == 200 else str(r)[:90])

    # 5) 新增页面依赖的接口链（收费项目 / 药房 / 处置 / 系统 / 归档 / 审计 / 报表）
    print("\n--- 新增页面依赖的接口链 ---")
    for label, path, body, m, tk in (
            ("收费项目搜索", "/api/charge_item/search", {"keyword": ""}, "GET", dt),
            ("支付流水", "/api/finance/payments", {"limit": 5}, "GET", dt),
            ("退款记录", "/api/refund/list", {"limit": 5}, "GET", dt),
            ("患者查重", "/api/patient/duplicates", {}, "GET", dt),
            ("库存批次", "/api/stock/batches", {"item_kind": "HERB", "item_id": 1}, "GET", dt),
            ("入库单列表", "/api/stock/in/list", {"limit": 5}, "GET", dt),
            ("退药记录", "/api/pharmacy/return/list", {"limit": 5}, "GET", dt),
            ("处置套餐列表", "/api/treatment/package/list", {}, "GET", dt),
            ("皮试记录（按患者）", "/api/skin_test/list", {"patient_id": 1}, "GET", dt),
            ("病历模板列表", "/api/record/templates", {}, "GET", dt),
            ("处方模板列表", "/api/prescription/templates", {}, "GET", dt),
            ("归档导出记录", "/api/record/export/list", {"limit": 5}, "GET", tk),
            ("辅助检查列表", "/api/record/exam_ref/list", {"record_id": 1}, "GET", dt),
            ("报表口径定义", "/api/report/definitions", {}, "GET", tk),
            ("系统角色列表", "/api/system/roles", {}, "GET", tk),
            ("系统健康检查", "/api/system/health", {}, "GET", tk),
            ("导入模板", "/api/system/import/template", {"data_type": "DRUG"}, "GET", tk),
            ("版本信息", "/api/system/update", {}, "GET", tk),
            ("审计·时间异常", "/api/audit/time_anomaly", {"limit": 5}, "GET", tk),
            ("审计·高风险", "/api/audit/high_risk", {"limit": 5}, "GET", tk),
            ("审计·登录", "/api/audit/logins", {"limit": 5}, "GET", tk),
            ("审计·操作", "/api/audit/operations", {"limit": 5}, "GET", tk)):
        code, r = call(path, body, method=m, token=tk)
        check(label, code == 200, "" if code == 200 else str(r)[:90])

    return _summary()


def _summary():
    print("\n" + "=" * 68)
    print("界面探针：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    for n in FAILED:
        print("  - %s" % n)
    print("明细：%s" % _LOG_PATH)
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
