# -*- coding: utf-8 -*-
"""调用 /api/catalog/seed 一键载入内置目录，并校验检索接口。
用法：python tools/seed_catalog.py [kind]
"""
import json
import os
import sys
import urllib.request

# 沙箱给本机也配了代理，不打洞会被 502 挡掉
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

BASE = "http://127.0.0.1:8787"
PWD = os.environ.get("CLINIC_PWD", "Clinic@2026")


def call(path, body=None, method="POST", token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "body": e.read().decode()[:500]}


def main():
    # PowerShell 不回传 stdout，结果写文件再看
    _f = open(os.path.join(os.environ.get("TEMP", "."), "clinic_seed_out.txt"),
              "w", encoding="utf-8")
    _real = sys.stdout
    sys.stdout = _f
    try:
        _go(kind=sys.argv[1] if len(sys.argv) > 1 else "all")
    finally:
        sys.stdout = _real
        _f.close()


def _go(kind):
    d = call("/api/auth/login", {"username": "admin", "password": PWD,
                                 "role_code": "ADMIN"})
    if "__http_error__" in d:
        print("login failed:", d)
        return
    token = d["data"]["token"]
    print("登录 ok，角色:", d["data"].get("roles"))

    r = call("/api/catalog/seed", {"kind": kind}, token=token)
    print("seed:", json.dumps(r.get("data", r), ensure_ascii=False))

    hs = call("/api/catalog/herbs", None, "GET", token)
    items = hs.get("data", {}).get("items", [])
    print("herbs count:", len(items))

    ds = call("/api/catalog/drugs", None, "GET", token)
    print("drugs count:", len(ds.get("data", {}).get("items", [])))

    # 抽查：首字母检索能否命中当归
    hit = [h for h in items if h["name"] == "当归"]
    print("当归:", json.dumps(hit, ensure_ascii=False))
    hit2 = [h for h in items if h["name"] == "黄芪"]
    print("黄芪:", json.dumps(hit2, ensure_ascii=False))
    toxic = [h["name"] for h in items if h.get("is_toxic")]
    print("毒性饮片:", toxic)


if __name__ == "__main__":
    main()
