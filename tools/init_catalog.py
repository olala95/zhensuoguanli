# -*- coding: utf-8 -*-
"""一键把目录和经典方整理到可用状态：补种子药 → 停用测试垃圾 → 预置经典方。

幂等，可反复执行。用法：python tools/init_catalog.py
输出写到 %TEMP%/clinic_init_catalog.txt
"""
import json
import os
import urllib.request

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
BASE = "http://127.0.0.1:8787"
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")
lines = []


def say(s):
    lines.append(str(s))


def api(path, method="GET", body=None, token=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Token", token)
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.loads(r.read().decode("utf-8"))
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


def main():
    tok = api("/api/auth/login", "POST",
              {"username": "admin", "password": PWD, "role_code": "ADMIN"})["token"]
    say("登录 OK")

    say("\n[1] 补种饮片/成药目录")
    say("  seed  → %s" % api("/api/catalog/seed", "POST", {}, tok))

    say("\n[2] 停用测试垃圾（M0x/M1x 开头的品目与模板）")
    say("  cleanup → %s" % api("/api/catalog/cleanup_test", "POST", {}, tok))

    say("\n[3] 预置经典协定方")
    r = api("/api/prescription/template/seed_classic", "POST", {}, tok)
    say("  classic → 新建 %s" % r.get("total"))
    if r.get("missing"):
        say("  目录缺药、未能建方：%s" % r.get("missing"))
    if r.get("skipped_exist"):
        say("  已存在跳过：%s" % r.get("skipped_exist"))

    say("\n[4] 整理后状态")
    tpls = api("/api/prescription/templates", "GET", token=tok)["items"]
    say("  生效模板 %d 张：%s" % (len(tpls), [t["name"] for t in tpls]))
    herbs = api("/api/catalog/herbs", "GET", token=tok)["items"]
    drugs = api("/api/catalog/drugs", "GET", token=tok)["items"]
    say("  饮片 %d 味 / 成药 %d 种" % (len(herbs), len(drugs)))
    for nm in ("淡竹叶", "芦根", "白扁豆", "法半夏"):
        say("    %s：%s" % (nm, "在目录" if any(h["name"] == nm for h in herbs) else "缺失"))
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_init_catalog.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
