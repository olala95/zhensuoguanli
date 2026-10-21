# -*- coding: utf-8 -*-
"""整理检验项目目录：停用测试垃圾 → 预置常用外送检验项目（幂等）。

用法：python tools/init_lab.py
输出：%TEMP%/clinic_init_lab.txt
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

# (编码, 名称, 简称, 类别, 标本, 收费元, 结算元, 报告天数)
ITEMS = [
    ("LB001", "血常规", "血常规", "检验", "血", 25, 12, 1),
    ("LB002", "尿常规", "尿常规", "检验", "尿", 20, 10, 1),
    ("LB003", "粪便常规+隐血", "便常规", "检验", "便", 25, 12, 1),
    ("LB004", "肝功能（ALT/AST/总胆红素）", "肝功", "检验", "血", 45, 22, 1),
    ("LB005", "肾功能（肌酐/尿素氮/尿酸）", "肾功", "检验", "血", 45, 22, 1),
    ("LB006", "空腹血糖", "血糖", "检验", "血", 10, 5, 1),
    ("LB007", "血脂四项", "血脂", "检验", "血", 50, 25, 1),
    ("LB008", "C反应蛋白", "CRP", "检验", "血", 35, 18, 1),
    ("LB009", "糖化血红蛋白", "HbA1c", "检验", "血", 60, 30, 1),
    ("LB010", "甲状腺功能三项", "甲功三项", "检验", "血", 120, 65, 2),
    ("LB011", "乙肝五项", "乙肝五项", "检验", "血", 60, 30, 1),
    ("LB012", "电解质六项", "电解质", "检验", "血", 55, 28, 1),
    ("LB013", "凝血四项", "凝血", "检验", "血", 60, 30, 1),
    ("LB014", "心肌酶谱", "心肌酶", "检验", "血", 70, 35, 1),
    ("LB015", "类风湿因子+抗链O", "RF+ASO", "检验", "血", 60, 30, 2),
    ("LB016", "幽门螺杆菌抗体", "HP抗体", "检验", "血", 45, 22, 1),
    ("LB017", "尿妊娠试验", "HCG", "检验", "尿", 15, 8, 1),
    ("LB018", "白带常规", "白带", "检验", "分泌物", 25, 12, 1),
    ("LB019", "胸部正位片", "胸片", "影像", "其他", 60, 35, 1),
    ("LB020", "腹部彩超（肝胆胰脾肾）", "腹部彩超", "影像", "其他", 120, 70, 1),
    ("LB021", "心电图", "ECG", "功能", "其他", 25, 15, 1),
    ("LB022", "红外热成像（中医体质）", "红外热成像", "功能", "其他", 80, 50, 1),
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
        detail = e.read().decode("utf-8")[:200]
        say("  HTTP %s %s → %s" % (e.code, path, detail))
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

    allrows = api("/api/lab/item/list?include_inactive=1").get("items") or []
    say("=== 1) 停用测试项目 ===")
    n = 0
    for x in allrows:
        if x.get("is_active") and x["name"][:2] in ("M0", "M1"):
            api("/api/lab/item/update", "POST",
                {"id": x["id"], "is_active": 0, "reason": "停用自动化测试遗留项目"})
            n += 1
    say("  已停用 %d 个测试项目" % n)

    say("")
    say("=== 2) 预置常用检验项目 ===")
    exist = {x["item_code"]: x for x in api("/api/lab/item/list?include_inactive=1")
             .get("items") or []}
    added = skipped = 0
    for code, name, short, cat, spec, price, settle, days in ITEMS:
        if code in exist:
            skipped += 1
            continue
        api("/api/lab/item/create", "POST",
            {"item_code": code, "name": name, "short_name": short, "category": cat,
             "specimen_type": spec, "price_fen": int(round(price * 100)),
             "settle_price_fen": int(round(settle * 100)), "report_days": days})
        added += 1
        say("  + %-12s %-24s 收费 %5.1f / 结算 %5.1f / %d 天"
            % (code, name, price, settle, days))
    say("  新增 %d 项，已存在跳过 %d 项" % (added, skipped))

    say("")
    say("=== 3) 最终状态 ===")
    act = api("/api/lab/item/list").get("items") or []
    say("  启用中的检验项目 %d 项：" % len(act))
    for x in act:
        say("    %-12s %-24s %-6s %-4s %6.2f 元"
            % (x["item_code"], x["name"], x["category"] or "", x["specimen_type"] or "",
               (x["price_fen"] or 0) / 100.0))
    total_inactive = len(api("/api/lab/item/list?include_inactive=1").get("items") or []) - len(act)
    say("  停用（仍在库，可随时启用）%d 项" % total_inactive)
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_init_lab.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
