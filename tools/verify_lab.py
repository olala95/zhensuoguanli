# -*- coding: utf-8 -*-
"""验证检验项目维护：列表、新增、改价、停用、再启用。"""
import json
import os
import time
import urllib.error
import urllib.request

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
BASE = "http://127.0.0.1:8787"
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


def main():
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": "Clinic@2026",
                 "role_code": "ADMIN"})["token"]

    cur = api("/api/lab/item/list").get("items") or []
    say("现有检验项目 %d 项：%s" % (len(cur), [x["name"] for x in cur[:8]]))

    code = "UXT%d" % (int(time.time()) % 100000)
    r = api("/api/lab/item/create", "POST",
            {"item_code": code, "name": "验证用C反应蛋白", "short_name": "CRP",
             "category": "检验", "specimen_type": "血",
             "price_fen": 3500, "settle_price_fen": 1800, "report_days": 1})
    new_id = r["id"]
    say("已新增 id=%s code=%s" % (new_id, code))
    check("可新增检验项目（带标本类型/类别）", bool(new_id))

    row = next((x for x in api("/api/lab/item/list").get("items") or []
                if x["id"] == new_id), None)
    say("  新项目：%s / 收费 %s 分 / 结算 %s 分 / %s 天 / 标本 %s"
        % (row["name"], row["price_fen"], row["settle_price_fen"],
           row["report_days"], row["specimen_type"]))
    check("新增后能查到且字段正确",
          row and row["price_fen"] == 3500 and row["settle_price_fen"] == 1800
          and row["specimen_type"] == "血")

    api("/api/lab/item/update", "POST",
        {"id": new_id, "price_yuan": 42.5, "settle_price_yuan": 20,
         "report_days": 2, "reason": "验证改价"})
    row2 = next((x for x in api("/api/lab/item/list").get("items") or []
                 if x["id"] == new_id), None)
    say("  改价后：收费 %s 分 / 结算 %s 分 / %s 天"
        % (row2["price_fen"], row2["settle_price_fen"], row2["report_days"]))
    check("改价生效（42.50 元 = 4250 分）", row2["price_fen"] == 4250, str(row2["price_fen"]))
    check("结算价可单独改", row2["settle_price_fen"] == 2000, str(row2["settle_price_fen"]))
    check("报告天数可改", row2["report_days"] == 2, str(row2["report_days"]))

    api("/api/lab/item/update", "POST", {"id": new_id, "is_active": 0})
    act = [x for x in api("/api/lab/item/list").get("items") or []
           if x["is_active"] == 1 and x["id"] == new_id]
    allrows = [x for x in api("/api/lab/item/list?include_inactive=1").get("items") or []
               if x["id"] == new_id]
    check("停用后不出现在启用列表", not act)
    check("停用后仍能在维护列表找到（可再启用）", len(allrows) == 1)

    srch = [x for x in api("/api/lab/item/search?keyword=%s" % code).get("items") or []]
    check("停用后不出现在开单搜索里", not srch, "%d 项" % len(srch))

    api("/api/lab/item/update", "POST", {"id": new_id, "is_active": 1})
    back = [x for x in api("/api/lab/item/search?keyword=%s" % code).get("items") or []]
    check("重新启用后回到开单搜索", len(back) == 1)

    # 收尾：停用留在库里（软停用，不删）
    api("/api/lab/item/update", "POST", {"id": new_id, "is_active": 0,
                                         "reason": "验证完成，停用"})
    say("")
    say("结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    if FAILED:
        say("失败项：%s" % FAILED)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_lab.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
