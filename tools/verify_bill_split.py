# -*- coding: utf-8 -*-
"""造一张含药费的账单，验证收费明细能否区分中药费/西药费。"""
import json
import os
import time
import urllib.error
import urllib.request

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
BASE = "http://127.0.0.1:8787"
PWD = "Clinic@2026"
TOKEN = None
lines, PASSED, FAILED = [], [], []


def say(s=""):
    lines.append(str(s))


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    say("  %s  %s %s" % ("PASS" if cond else "FAIL", name, extra))


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    if TOKEN:
        req.add_header("X-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        say("HTTP %s %s → %s" % (e.code, path, e.read().decode("utf-8")[:400]))
        raise
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


def rx_kind(t):
    return "TCM" if t in ("HERB", "GRANULE") else "WEST"


def main():
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": PWD, "role_code": "ADMIN"})["token"]
    nm = "拆分验证%s" % str(int(time.time()))[-5:]
    pat = api("/api/patient/create", "POST",
              {"name": nm, "gender": "F", "birth_date": "1988-06-06"})
    vis = api("/api/visit/create", "POST", {"patient_id": pat["id"], "visit_type": "TCM"})
    api("/api/visit/start", "POST", {"visit_id": vis["id"]})

    # 中药方（归脾汤）
    rx1 = api("/api/prescription/create", "POST",
              {"visit_id": vis["id"], "prescription_type": "HERB", "doses": 7})
    tpls = api("/api/prescription/templates").get("items") or []
    t = next((x for x in tpls if x["name"] == "归脾汤"), None)
    api("/api/prescription/template/apply", "POST",
        {"prescription_id": rx1["id"], "template_id": t["id"]})
    api("/api/prescription/submit", "POST", {"prescription_id": rx1["id"]})
    api("/api/prescription/confirm", "POST",
        {"prescription_id": rx1["id"], "password": PWD})

    # 西药方（成药）
    drugs = api("/api/catalog/drugs").get("items") or []
    d0 = drugs[0] if drugs else None
    rx2 = None
    if d0:
        rx2 = api("/api/prescription/create", "POST",
                  {"visit_id": vis["id"], "prescription_type": "WEST"})
        api("/api/prescription/save/items", "POST",
            {"prescription_id": rx2["id"],
             "items": [{"drug_id": d0["id"], "quantity": 1, "dosage": "1片",
                        "frequency": "tid", "route": "PO", "days": 3}]})
        api("/api/prescription/submit", "POST", {"prescription_id": rx2["id"]})
        api("/api/prescription/confirm", "POST",
            {"prescription_id": rx2["id"], "password": PWD})

    bill = api("/api/bill/create", "POST", {"visit_id": vis["id"]})
    det = api("/api/bill/get?id=%s" % bill["id"])
    its = det.get("items") or []
    rl = api("/api/prescription/list?visit_id=%s" % vis["id"]).get("items") or []
    typemap = {r["id"]: r["prescription_type"] for r in rl}

    say("就诊 %s（中医）" % vis["visit_no"])
    say("处方：%s" % [(r["prescription_no"], r["prescription_type"], r["status"]) for r in rl])
    say("账单 %s 明细 %d 行：" % (bill["bill_no"], len(its)))
    herb = west = other = 0
    for i in its:
        st = i.get("source_type")
        amt = int(i.get("amount_fen") or 0)
        tag = {"HERB": "中药", "DRUG": "西药/成药", "TREATMENT": "处置",
               "LAB": "检验", "CHARGE": "收费项目"}.get(st, st)
        if st == "HERB":
            herb += amt
        elif st == "DRUG":
            west += amt
        else:
            other += amt
        say("  %-18s %-10s %-10s %8.2f 元"
            % (i.get("name_snap"), st, tag, amt / 100.0))

    say("")
    say("小计：中药费 %.2f / 西药费 %.2f / 其它 %.2f / 合计 %.2f"
        % (herb / 100.0, west / 100.0, other / 100.0,
           (herb + west + other) / 100.0))
    check("账单里能区分出中药费", herb > 0, "%.2f 元" % (herb / 100.0))
    check("账单里能区分出西药费", west > 0 if rx2 else True,
          "%.2f 元" % (west / 100.0))
    check("三项小计之和 = 账单应收",
          herb + west + other == int(det.get("total_amount_fen") or 0),
          "%d vs %s" % (herb + west + other, det.get("total_amount_fen")))
    check("处方来源行都能查到类型",
          all(typemap.get(i.get("source_id")) for i in its
              if i.get("source_type") == "PRESCRIPTION"))

    say("")
    say("结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_bill_split.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
