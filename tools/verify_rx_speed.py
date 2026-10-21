# -*- coding: utf-8 -*-
"""验证开方提速三件事：经典方预置、照上次方、模板套用。

用法：python tools/verify_rx_speed.py
输出写到 %TEMP%/clinic_rx_speed.txt
"""
import json
import os
import urllib.request

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
BASE = "http://127.0.0.1:8787"
TOKEN = None
PASSED, FAILED = [], []
lines = []


def say(s):
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
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode("utf-8"))
    # 响应统一包装成 {"ok":..., "data":...}；少数接口直接返回业务体
    if isinstance(j, dict) and "ok" in j and "data" in j:
        d = j["data"]
        return d if isinstance(d, dict) else j
    return j


def api_q(path, params):
    qs = "&".join("%s=%s" % (k, v) for k, v in params.items())
    return api(path + "?" + qs)


def main():
    global TOKEN
    TOKEN = api("/api/auth/login", "POST",
                {"username": "admin", "password": "Clinic@2026",
                 "role_code": "DOCTOR"})["token"]
    say("登录 OK")

    # ---- 1) 预置经典方 ----
    r = api("/api/prescription/template/seed_classic", "POST", {})
    say("seed_classic: created=%d skipped=%s missing=%s"
        % (r.get("total"), r.get("skipped_exist"), r.get("missing")))
    check("无缺药导致建不出来的方", not (r.get("missing") or []), str(r.get("missing")))

    tpls = api("/api/prescription/templates", "GET")["items"]
    names = [t["name"] for t in tpls]
    say("生效模板 %d 张：%s" % (len(tpls), names))
    check("经典方齐备（14 张）", len(tpls) == 14, str(len(tpls)))
    check("模板列表无测试垃圾（M0x/M1x）",
          not [n for n in names if n[:2] in ("M0", "M1")], str(names))

    # 再来一次应幂等
    r2 = api("/api/prescription/template/seed_classic", "POST", {})
    say("重复预置：total=%s skipped=%d" % (r2.get("total"), len(r2.get("skipped_exist") or [])))
    check("重复预置幂等（不重复建）", r2.get("total") == 0 and
          len(r2.get("skipped_exist") or []) >= 14, str(r2.get("total")))

    # ---- 2) 照上次方 ----
    # 取一个已有处方（visit 288 / 患者）
    lst = api_q("/api/prescription/list", {"visit_id": 288})["items"]
    src = [x for x in lst if x["status"] in ("CONFIRMED", "DISPENSED", "PENDING")]
    if not src:
        say("没有可用源处方，跳过照上次方验证")
        return 1
    src_id = src[0]["id"]
    say("源处方：%s (%s)" % (src[0]["prescription_no"], src[0]["status"]))

    last = api_q("/api/prescription/last", {"visit_id": 288})
    say("last = %s" % last)
    check("能查到上次方概要", last.get("has_last") is True, str(last))

    # 新建一张草稿
    new = api("/api/prescription/create", "POST",
              {"visit_id": 288, "prescription_type": "HERB", "doses": 7})
    say("新草稿：%s" % new["prescription_no"])
    cp = api("/api/prescription/copy_last", "POST", {"prescription_id": new["id"]})
    say("copy_last = %s" % cp)
    check("照上次方复制到了明细", (cp.get("herbs") or 0) + (cp.get("drugs") or 0) > 0, str(cp))
    check("记录了来源处方号", bool(cp.get("source_prescription_no")),
          str(cp.get("source_prescription_no")))

    got = api_q("/api/prescription/get", {"id": new["id"]})
    n_herb = len(got.get("herbs") or [])
    n_src = last.get("count")
    check("明细味数与上次方一致", n_herb == n_src, "%d vs %d" % (n_herb, n_src))

    # ---- 3) 套用经典方 ----
    tpl = [t for t in tpls if t["name"] == "归脾汤"]
    if not tpl:
        check("找到归脾汤模板", False)
    else:
        ap = api("/api/prescription/template/apply", "POST",
                 {"prescription_id": new["id"], "template_id": tpl[0]["id"]})
        say("apply 归脾汤 = %s" % ap)
        check("经典方一键套用成功", (ap.get("herbs") or 0) == 11,
              str(ap.get("herbs")))
        got2 = api_q("/api/prescription/get", {"id": new["id"]})
        names = [h["herb_name_snap"] for h in (got2.get("herbs") or [])]
        say("套用后明细：%s" % names)
        check("套用后为归脾汤组成", "黄芪" in names and "龙眼肉" in names, str(names[:6]))

    # 清理：作废测试草稿
    try:
        api("/api/prescription/void", "POST",
            {"prescription_id": new["id"], "reason": "提速功能验证用草稿"})
        say("已作废测试草稿 %s" % new["prescription_no"])
    except Exception as e:
        say("作废失败（草稿可能已非 DRAFT）：%r" % e)

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
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_rx_speed.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
