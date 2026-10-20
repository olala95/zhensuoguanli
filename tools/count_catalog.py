# -*- coding: utf-8 -*-
"""统计基础数据目录的规模：没有目录，开方就只能一味味手建。"""
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "clinic.db")
if not os.path.exists(DB):
    for cand in ["clinic.db", "clinic.sqlite3", "data.db"]:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), cand)
        if os.path.exists(p):
            DB = p
            break

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

WANT = ["drug", "herb", "charge_item", "lab_item", "lab_agency",
        "treatment_item", "prescription_template", "record_template"]
print("DB:", DB)
print("=" * 60)
for t in tables:
    low = t.lower()
    if not any(w in low for w in WANT):
        continue
    try:
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    except Exception as e:
        print("  %-28s ERR %s" % (t, e))
        continue
    print("  %-28s %d" % (t, n))

print("=" * 60)
for t in tables:
    if t.lower() in ("herb", "drug"):
        try:
            rows = conn.execute("SELECT * FROM %s LIMIT 30" % t).fetchall()
        except Exception:
            continue
        print("-- %s 前 30 条 --" % t)
        for r in rows:
            d = dict(r)
            keep = {k: v for k, v in d.items()
                    if k in ("id", "herb_code", "drug_code", "name",
                             "generic_name", "price_milli_per_g", "price_fen",
                             "spec", "unit", "deleted_at")}
            print("   ", keep)
conn.close()
