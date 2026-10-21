# -*- coding: utf-8 -*-
"""整理处置项目目录：停用测试垃圾 → 预置中医/西医常用处置项目（幂等）。

用法：python tools/init_treatment.py
输出：%TEMP%/clinic_init_treat.txt
"""
import json
import os
import urllib.error
import urllib.request

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
BASE = "http://127.0.0.1:8787"
TOKEN = None
lines = []

# (编码, 名称, 类别, 单价元, 分钟, is_tcm, need_record)
ITEMS = [
    ("TCM001", "普通针刺", "TCM", 35, 30, 1, 1),
    ("TCM002", "电针治疗", "TCM", 45, 30, 1, 1),
    ("TCM003", "温针灸", "TCM", 50, 30, 1, 1),
    ("TCM004", "艾灸", "TCM", 30, 20, 1, 1),
    ("TCM005", "拔罐", "TCM", 25, 15, 1, 1),
    ("TCM006", "走罐", "TCM", 35, 20, 1, 1),
    ("TCM007", "刮痧", "TCM", 30, 20, 1, 1),
    ("TCM008", "推拿（颈肩）", "TCM", 60, 30, 1, 1),
    ("TCM009", "推拿（腰背）", "TCM", 60, 30, 1, 1),
    ("TCM010", "小儿推拿", "TCM", 50, 20, 1, 1),
    ("TCM011", "耳穴压豆", "TCM", 20, 10, 1, 1),
    ("TCM012", "穴位贴敷（三伏贴）", "TCM", 40, 15, 1, 1),
    ("TCM013", "中药熏蒸", "TCM", 60, 30, 1, 1),
    ("TCM014", "刺络放血", "TCM", 40, 15, 1, 1),
    ("WM001", "换药（小）", "DRESSING", 15, 10, 0, 0),
    ("WM002", "换药（大）", "DRESSING", 30, 15, 0, 0),
    ("WM003", "清创缝合", "DRESSING", 80, 30, 0, 1),
    ("WM004", "肌肉注射", "INJECTION", 8, 5, 0, 0),
    ("WM005", "静脉输液", "INJECTION", 25, 30, 0, 1),
    ("WM006", "雾化吸入", "NEBULIZE", 20, 20, 0, 0),
    ("WM007", "导尿", "OTHER", 40, 20, 0, 1),
    ("WM008", "吸氧（每小时）", "OTHER", 10, 60, 0, 0),
    ("WM009", "红外线理疗", "PHYSIO", 25, 20, 0, 0),
]


def say(s=""):
    lines.append(str(s))


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
        say("  HTTP %s %s → %s" % (e.code, path, e.read().decode("utf-8")[:200]))
        raise
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


def main():
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": "Clinic@2026",
                 "role_code": "ADMIN"})["token"]

    allrows = api("/api/treatment/item/list?include_inactive=1").get("items") or []
    say("=== 1) 停用测试项目 ===")
    n = 0
    for x in allrows:
        if x.get("is_active") and (x.get("item_code") or "")[:2] in ("M0", "M1") \
                or (x.get("is_active") and x["name"][:3] in ("M08", "M09", "M10")):
            api("/api/treatment/item/update", "POST",
                {"id": x["id"], "is_active": 0})
            n += 1
    say("  已停用 %d 个测试项目" % n)

    say("")
    say("=== 2) 预置常用处置项目 ===")
    exist = {(x.get("item_code") or ""): x
             for x in api("/api/treatment/item/list?include_inactive=1").get("items") or []}
    added = skipped = 0
    for code, name, cat, price, mins, is_tcm, need in ITEMS:
        if code in exist:
            skipped += 1
            continue
        api("/api/treatment/item/create", "POST",
            {"item_code": code, "name": name, "category": cat,
             "price_yuan": price, "duration_min": mins,
             "is_tcm": is_tcm, "need_record": need})
        added += 1
        say("  + %-8s %-18s %-10s %5.1f 元 %3d 分钟 %s"
            % (code, name, cat, price, mins, "中医" if is_tcm else "西医"))
    say("  新增 %d 项，已存在跳过 %d 项" % (added, skipped))

    say("")
    say("=== 3) 最终状态 ===")
    act = api("/api/treatment/item/list").get("items") or []
    tcm = [x for x in act if int(x.get("is_tcm") or 0) == 1]
    wm = [x for x in act if int(x.get("is_tcm") or 0) != 1]
    say("  启用中 %d 项：中医操作 %d 项 / 西医操作 %d 项" % (len(act), len(tcm), len(wm)))
    for x in tcm:
        say("    [中医] %-20s %6.2f 元" % (x["name"], (x["price_fen"] or 0) / 100.0))
    for x in wm:
        say("    [西医] %-20s %6.2f 元" % (x["name"], (x["price_fen"] or 0) / 100.0))
    total = len(api("/api/treatment/item/list?include_inactive=1").get("items") or [])
    say("  停用（仍在库）%d 项" % (total - len(act)))
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_init_treat.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
