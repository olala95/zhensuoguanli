# -*- coding: utf-8 -*-
"""验证中西医拆分三处：报表按类型筛选、收费明细归类、处置项目 is_tcm 过滤。"""
import json
import os
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
        say("HTTP %s %s → %s" % (e.code, path, e.read().decode("utf-8")[:300]))
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

    say("=== 1) 报表按诊疗类型筛选 ===")
    allv = api("/api/report/visit_stats")
    tcm = api("/api/report/visit_stats?visit_type=TCM")
    west = api("/api/report/visit_stats?visit_type=WEST")
    n_all = allv["summary"]["visit_count"]
    n_tcm = tcm["summary"]["visit_count"]
    n_west = west["summary"]["visit_count"]
    say("  全部 %d / 中医 %d / 西医 %d" % (n_all, n_tcm, n_west))
    check("中医筛选生效（少于全部）", n_tcm < n_all, "%d < %d" % (n_tcm, n_all))
    check("西医筛选生效（少于全部）", n_west < n_all, "%d < %d" % (n_west, n_all))
    check("中医+西医 = 全部（无遗漏无重复）", n_tcm + n_west == n_all,
          "%d + %d vs %d" % (n_tcm, n_west, n_all))
    byt = {t["visit_type"]: t["count"] for t in allv.get("by_type") or []}
    say("  按类型分布：%s" % byt)
    check("中医数与分布一致", n_tcm == byt.get("TCM", 0),
          "%d vs %d" % (n_tcm, byt.get("TCM", 0)))
    check("criteria 记录了筛选条件",
          tcm["criteria"].get("visit_type") == "TCM", str(tcm["criteria"].get("visit_type")))

    say("")
    say("=== 2) 处置项目的 is_tcm 分布 ===")
    items = api("/api/treatment/item/search?keyword=").get("items") or []
    tcm_items = [x for x in items if int(x.get("is_tcm") or 0) == 1]
    west_items = [x for x in items if int(x.get("is_tcm") or 0) != 1]
    say("  总 %d 项：中医操作 %d / 其他 %d" % (len(items), len(tcm_items), len(west_items)))
    say("  中医操作示例：%s" % [x["name"] for x in tcm_items[:6]])
    check("处置项目带 is_tcm 字段可区分", all("is_tcm" in x for x in items) if items else False,
          "%d 项" % len(items))

    say("")
    say("=== 3) 收费明细归类（找一张含药费的账单）===")
    found = False
    for v in (api("/api/visit/list?limit=40").get("items") or []):
        try:
            bills = api("/api/finance/bills?visit_id=%s&limit=5" % v["id"]).get("items") or []
        except Exception:
            continue
        for b in bills:
            det = api("/api/bill/get?id=%s" % b["id"])
            its = det.get("items") or []
            if any(i.get("source_type") == "PRESCRIPTION" for i in its):
                say("  账单 %s（就诊 %s）明细：" % (b.get("bill_no"), v["id"]))
                for i in its[:6]:
                    say("    %-14s %-14s %s 元" % (i.get("name_snap"),
                                                   i.get("source_type"),
                                                   round((i.get("amount_fen") or 0) / 100.0, 2)))
                # 处方类型映射（前端用同样方式）
                rl = api("/api/prescription/list?visit_id=%s" % v["id"]).get("items") or []
                m = {r["id"]: r["prescription_type"] for r in rl}
                kinds = [(i.get("source_id"), m.get(i.get("source_id")))
                         for i in its if i.get("source_type") == "PRESCRIPTION"]
                say("  处方→类型映射：%s" % kinds)
                check("能通过 source_id 查到处方类型",
                      all(k[1] for k in kinds), str(kinds))
                found = True
                break
        if found:
            break
    if not found:
        check("找到含药费的账单", False, "库中没有可用样本")

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
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_split.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
