# -*- coding: utf-8 -*-
"""清理历史脏账单：把"净收 > 应收"的多收部分按真实业务退掉。

背景：
  早期版本的红字冲正（F62）没有"应收不得低于已收"的限制，
  冲正后 bill.total 减少但 payment 不变，留下了一批"已收 > 应收"的账单，
  导致 F94 完整性检查一直报警。

  代码侧已修好（冲正会拒绝把应收降到已收以下），但**历史数据要单独修**。
  本脚本对每条问题账单走**真实退款流程**（不是直接改数），
  把多收的差额退掉，使 已收 − 已退 ≤ 应收。

用法：python tools/fix_dirty_bills.py [--port 8787] [--apply]
      不带 --apply 只输出将要做什么（dry run）。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime  # noqa: E402

# 默认口令仅供本地测试库使用；正式环境用环境变量 CLINIC_PWD 覆盖
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")


def api(path, body=None, method=None, token=None):
    method = method or ("POST" if body is not None else "GET")
    url = BASE + path
    if method == "GET" and body:
        qs = urllib.parse.urlencode({k: v for k, v in body.items()
                                     if v is not None and v != ""})
        if qs:
            url += ("&" if "?" in url else "?") + qs
    data = json.dumps(body).encode("utf-8") if (body is not None and method != "GET") else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": e.read().decode("utf-8", "ignore")}


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--password", default=PWD)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    BASE = "http://127.0.0.1:%d" % args.port

    code, r = api("/api/auth/login", {"username": "admin", "password": args.password,
                                      "role_code": "CASHIER"})
    if code != 200:
        print("登录失败：%s" % r)
        return 1
    tk = r["data"]["token"]

    code, r = api("/api/audit/time_anomaly", {}, method="GET", token=tk)  # 连通性
    # 找出问题账单
    _, bills = api("/api/finance/bills", {"limit": 200}, method="GET", token=tk)
    bad = []
    for b in (bills.get("data") or {}).get("items") or []:
        bid = b["id"]
        _, g = api("/api/bill/get", {"id": bid}, method="GET", token=tk)
        d = g.get("data") or {}
        total = d.get("total_amount_fen") or 0
        paid = sum(p.get("amount_fen") or 0 for p in (d.get("payments") or [])
                   if p.get("status") == "SUCCESS")
        refunded = sum(f.get("total_amount_fen") or 0 for f in (d.get("refunds") or [])
                       if f.get("status") == "SUCCEEDED")
        over = (paid - refunded) - total
        if over > 0:
            bad.append((bid, d.get("bill_no"), total, paid, refunded, over,
                        d.get("items") or []))

    print("=" * 66)
    print("脏账单清理（净收 > 应收）")
    print("=" * 66)
    if not bad:
        print("  没有需要清理的账单。")
        return 0

    for bid, no, total, paid, rf, over, items in bad:
        print("  bill.id=%s %s 应收=%s 已收=%s 已退=%s **多收 %s**"
              % (bid, no, total, paid, rf, over))

    if not args.apply:
        print("\n（dry run：加 --apply 才真正退款）")
        return 0

    ok = 0
    for bid, no, total, paid, rf, over, items in bad:
        # 退掉多收的差额：从明细行里挑一行可退的
        done = False
        for it in items:
            line_id = it.get("id")
            if not line_id:
                continue
            _, g = api("/api/bill/get", {"id": bid}, method="GET", token=tk)
            d = g.get("data") or {}
            # 该行可退净额
            net = (it.get("amount_fen") or 0)
            amt = min(over, max(net, 0))
            if amt <= 0:
                continue
            code, rr = api("/api/refund/create", {
                "bill_id": bid,
                "items": [{"bill_item_id": line_id, "amount_fen": amt}],
                "reason": "清理历史脏数据：冲正后多收部分退款"}, token=tk)
            if code == 200:
                print("    ✓ bill.id=%s 退款 %s 分（%s）" % (bid, amt, rr["data"].get("refund_no")))
                ok += 1
                done = True
                break
            else:
                err = rr.get("error") or {}
                print("    · bill.id=%s 行%s 退款失败：%s %s"
                      % (bid, line_id, err.get("code"), err.get("message")))
        if not done:
            print("    ✗ bill.id=%s 无法自动退款，需人工处理" % bid)

    print("\n完成：成功退款 %d 条" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
