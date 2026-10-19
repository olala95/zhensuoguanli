# -*- coding: utf-8 -*-
"""第四阶段基础数据包导入（F128 完整版）。

覆盖：标准模板、校验阶段不写数据、全量事务回滚、幂等 upsert、分批导入、
      错误报告（行号/字段/原因/值）、导入锁（E3010）、导入审计、数据包版本比对、
      崩溃恢复（残留 RUNNING 任务标记失败）。
用法：python tests/phase4_import_test.py
"""

import csv
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from core import db, runtime  # noqa: E402
from core import bizlock  # noqa: E402
from core.context import Context, set_ctx  # noqa: E402
from core.errors import AppError  # noqa: E402
from modules import m14_system as m14, m06_prescription as m06  # noqa: E402

PASSED, FAILED = [], []
_LOG_PATH = os.path.join(tempfile.gettempdir(), "clinic_import_last.log")
_LOG = open(_LOG_PATH, "w", encoding="utf-8")
_orig_print = print


def print(*args, **kwargs):  # noqa: A001
    msg = " ".join(str(a) for a in args)
    _orig_print(msg, **kwargs)
    _LOG.write(msg + "\n")
    _LOG.flush()


def check(name, cond, extra=""):
    if cond:
        PASSED.append(name)
        print("  PASS  %s %s" % (name, extra))
    else:
        FAILED.append(name)
        print("  FAIL  %s %s" % (name, extra))


def expect_error(name, fn, code_prefix=None):
    try:
        fn()
    except AppError as exc:
        ok = (code_prefix is None) or str(exc.code).startswith(code_prefix)
        check(name, ok, "→ %s %s" % (exc.code, exc.message))
        return exc
    except Exception as exc:
        check(name, False, "→ 抛出非 AppError：%r" % exc)
        return None
    check(name, False, "→ 未拒绝（应当报错）")
    return None


def _one(sql, params=()):
    return db.query_one(sql, params)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


DRUG_HEADER = ["drug_code", "generic_name", "trade_name", "spec", "dosage_form",
               "unit", "manufacturer", "price_yuan", "is_antibiotic", "is_injection"]


def main():
    root = tempfile.mkdtemp(prefix="clinic_import_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    tmp = os.path.join(root, "tmp")
    os.makedirs(tmp, exist_ok=True)
    print("临时数据目录：%s\n" % root)
    _, _, report = runtime.bootstrap(root, start_server=False)
    check("启动自检无 FAIL", all(c["result"] != "FAIL" for c in report["checks"]))

    sys_ctx = Context(source="SYSTEM")
    set_ctx(sys_ctx)
    m14.init_system(sys_ctx, {"admin_username": "admin", "admin_password": "Clinic@2026",
                              "admin_name": "管理员", "clinic_name": "示例中医诊所"}, {})
    m14.user_roles(sys_ctx, {"user_id": 1, "roles": ["ADMIN", "DOCTOR", "PHARMACIST",
                                                     "CASHIER"]}, {})
    sess = m14.login(sys_ctx, {"username": "admin", "password": "Clinic@2026",
                               "role_code": "ADMIN"}, {})
    ctx = Context(user_id=sess["user_id"], role_code="ADMIN", source="UI",
                  username="admin", session_id=sess["session_id"])
    set_ctx(ctx)

    # ============================================================
    print("\n[F128.1] 标准模板与校验阶段")
    tpl = m14.import_template(ctx, {}, {"data_type": "DRUG"})
    check("模板含表头与示例行", "drug_code" in tpl["content"]
          and tpl["content"].count("\n") == 1, tpl["filename"])
    check("模板标注必填列", "generic_name" in tpl["required"])

    bad = write_csv(os.path.join(tmp, "bad.csv"), DRUG_HEADER,
                    [["D%04d" % i, "" if i % 2 else "药品%d" % i, "", "0.1g", "片剂",
                      "盒", "某厂", "10.00", "0", "0"] for i in range(1, 11)])
    pv = m14.import_preview(ctx, {"data_type": "DRUG", "file_path": bad}, {})
    check("校验阶段发现 5 行必填缺失", pv["error_count"] == 5, str(pv["error_count"]))
    check("错误含行号/字段/原因",
          all(k in pv["errors"][0] for k in ("line", "field", "reason", "value")),
          str(pv["errors"][0]))
    check("校验阶段不写任何数据", _one("SELECT COUNT(*) c FROM drug")["c"] == 0)
    check("校验阶段标记不可写", pv["writable"] is False)
    check("给出文件哈希", len(pv["file_sha256"]) == 64)

    res = m14.import_data(ctx, {"data_type": "DRUG", "file_path": bad}, {})
    check("有错直接拒绝（未建任务、未写入）", res["status"] == "REJECTED"
          and _one("SELECT COUNT(*) c FROM drug")["c"] == 0, str(res["status"]))

    # ============================================================
    print("\n[F128.2] 药品导入与幂等 upsert")
    good = write_csv(os.path.join(tmp, "good.csv"), DRUG_HEADER,
                     [["D0001", "布洛芬片", "芬必得", "0.1g×24", "片剂", "盒", "某厂",
                       "12.50", "0", "0"],
                      ["D0002", "阿莫西林胶囊", "", "0.25g×24", "胶囊", "盒", "某厂",
                       "15.00", "1", "0"],
                      ["D0003", "维生素C片", "", "0.1g×100", "片剂", "瓶", "某厂",
                       "8.80", "0", "0"]])
    r1 = m14.import_data(ctx, {"data_type": "DRUG", "file_path": good}, {})
    check("导入成功 3 条", r1["success"] == 3 and r1["status"] == "SUCCEEDED", str(r1))
    check("金额按分存储", _one("SELECT price_fen FROM drug WHERE drug_code='D0001'")["price_fen"]
          == 1250)
    check("导入记录文件哈希", len(r1["file_sha256"]) == 64)
    hr = _one("SELECT * FROM audit_high_risk WHERE risk_type='IMPORT' ORDER BY id DESC LIMIT 1")
    check("导入写高风险审计（含统计）",
          hr is not None and "3" in (hr["reason"] or ""), hr["reason"] if hr else "无")

    # 改价后重复导入（upsert）
    good2 = write_csv(os.path.join(tmp, "good2.csv"), DRUG_HEADER,
                      [["D0001", "布洛芬片", "芬必得", "0.1g×24", "片剂", "盒", "某厂",
                        "13.00", "0", "0"]])
    r2 = m14.import_data(ctx, {"data_type": "DRUG", "file_path": good2}, {})
    check("重复导入不新增记录", _one("SELECT COUNT(*) c FROM drug")["c"] == 3,
          str(_one("SELECT COUNT(*) c FROM drug")["c"]))
    check("已存在则更新（价格 1300 分）",
          _one("SELECT price_fen FROM drug WHERE drug_code='D0001'")["price_fen"] == 1300)
    r3 = m14.import_data(ctx, {"data_type": "DRUG", "file_path": good}, {})
    check("连续导入 3 次总数不增长", _one("SELECT COUNT(*) c FROM drug")["c"] == 3)

    # ============================================================
    print("\n[F128.1] 全量事务回滚（AC2）")
    before = _one("SELECT COUNT(*) c FROM drug")["c"]
    mixed = write_csv(os.path.join(tmp, "mixed.csv"), DRUG_HEADER,
                      [["D100%d" % i, "新药%d" % i, "", "1g", "片剂", "盒", "某厂",
                        "10.00", "0", "0"] for i in range(1, 6)] +
                      [["", "缺编码的药", "", "1g", "片剂", "盒", "某厂", "10.00", "0", "0"]])
    exc = expect_error("强制提交含错文件 → 整体回滚",
                       lambda: m14.import_data(ctx, {"data_type": "DRUG",
                                                     "file_path": mixed,
                                                     "on_error": "force"}, {}),
                       "E2002")
    check("回滚后药品数不变", _one("SELECT COUNT(*) c FROM drug")["c"] == before,
          "%d → %d" % (before, _one("SELECT COUNT(*) c FROM drug")["c"]))
    check("报告指出出错行号", exc and "第 7 行" in exc.message, exc.message if exc else "")
    job = _one("SELECT * FROM sys_import_job ORDER BY id DESC LIMIT 1")
    check("任务标记为 FAILED", job["status"] == "FAILED", str(job["status"]))
    check("错误清单文件已生成", bool(job["error_file"])
          and os.path.isfile(job["error_file"]), str(job["error_file"]))
    if job["error_file"] and os.path.isfile(job["error_file"]):
        with open(job["error_file"], encoding="utf-8") as f:
            head = f.readline().strip()
        check("错误清单含 行号/字段/原因/值 四列",
              head == "line,field,reason,value", head)

    # ============================================================
    print("\n[F128.1] 分批导入（AC4）")
    big = write_csv(os.path.join(tmp, "big.csv"), DRUG_HEADER,
                    [["B%04d" % i, "批药%d" % i, "", "1g", "片剂", "盒", "某厂",
                      "5.00", "0", "0"] for i in range(1, 1201)])
    pv2 = m14.import_preview(ctx, {"data_type": "DRUG", "file_path": big}, {})
    check("1200 行分为 3 批", pv2["batches"] == 3, str(pv2["batches"]))
    rb = m14.import_data(ctx, {"data_type": "DRUG", "file_path": big}, {})
    check("分批导入结果一致", rb["success"] == 1200 and rb["status"] == "SUCCEEDED",
          str(rb["success"]))
    check("库中药品总数 1203", _one("SELECT COUNT(*) c FROM drug")["c"] == 1203,
          str(_one("SELECT COUNT(*) c FROM drug")["c"]))
    st = m14.import_status(ctx, {}, {})
    check("任务进度可查询", st["items"] and st["items"][0]["total_rows"] == 1200)

    # ============================================================
    print("\n[F128] 导入锁（R8 / AC13）")
    bizlock.begin("IM-TEST", "DRUG")
    check("导入锁生效", bizlock.is_locked())
    expect_error("导入中禁止开处方",
                 lambda: m06.create(ctx, {"visit_id": 1, "prescription_type": "WEST"}, {}),
                 "E3010")
    bizlock.end()
    check("导入结束后锁释放", not bizlock.is_locked())
    expect_error("非导入期不受影响（缺 visit 报别的错）",
                 lambda: m06.create(ctx, {"visit_id": 0, "prescription_type": "WEST"}, {}),
                 "E2")

    # ============================================================
    print("\n[F128.3/F128.4] 收费项目与诊断字典")
    ci = write_csv(os.path.join(tmp, "ci.csv"),
                   ["item_code", "name", "category", "unit", "price_yuan"],
                   [["GH01", "挂号费", "REGISTRATION", "次", "10.00"],
                    ["ZJ01", "针灸费", "TREATMENT", "次", "60.00"]])
    rc = m14.import_data(ctx, {"data_type": "CHARGE_ITEM", "file_path": ci}, {})
    check("收费项目导入成功", rc["success"] == 2, str(rc["success"]))
    check("收费项目单价整数分",
          _one("SELECT price_fen FROM charge_item WHERE item_code='ZJ01'")["price_fen"] == 6000)
    icd = write_csv(os.path.join(tmp, "icd.csv"), ["code", "name"],
                    [["J06.9", "急性上呼吸道感染"], ["A09.9", "急性胃肠炎"]])
    ri = m14.import_data(ctx, {"data_type": "ICD10", "file_path": icd}, {})
    check("ICD-10 字典导入成功", ri["success"] == 2, str(ri["success"]))
    check("字典可被查询",
          _one("SELECT COUNT(*) c FROM sys_dict WHERE dict_type='ICD10'")["c"] == 2)

    # ============================================================
    print("\n[F128.5] 数据包版本比对（AC11）")
    man = write_csv(os.path.join(tmp, "manifest.csv"),
                    ["data_type", "drug_code", "name", "is_active"],
                    [["DRUG", "D0001", "布洛芬片（改名）", "1"],
                     ["DRUG", "D9999", "新药A", "1"],
                     ["DRUG", "D0002", "阿莫西林胶囊", "0"]])
    dps = m14.datapackage_status(ctx, {}, {})
    check("数据包状态可查", dps["counts"]["drug"] == 1203, str(dps["counts"]))
    cmp1 = m14.datapackage_check(ctx, {"manifest_path": man, "version": "v1.1"}, {})
    check("比对出新增项", cmp1["added"] >= 1, str(cmp1["added"]))
    check("比对出修改项", cmp1["modified"] >= 1, str(cmp1["modified"]))
    check("比对出停用项", cmp1["disabled"] >= 1, str(cmp1["disabled"]))
    ap = m14.datapackage_apply(ctx, {"data_type": "DRUG", "file_path": good,
                                     "version": "v1.1", "name": "本地数据包"}, {})
    check("应用数据包记录版本", ap.get("datapackage_version") == "v1.1",
          str(ap.get("datapackage_version")))
    check("应用数据包不删除既有数据",
          _one("SELECT COUNT(*) c FROM drug")["c"] == 1203)

    # ============================================================
    print("\n[F128] 崩溃恢复（AC15）")
    with db.tx() as conn:
        db.execute(conn, "INSERT INTO sys_import_job (job_no, data_type, source_file, status, "
                         "started_at, created_at) VALUES ('IM99990001','DRUG','x.csv','RUNNING',"
                         "?,?)", (m14.tu.now_str(), m14.tu.now_str()))
    n = m14.recover_stale_imports()
    check("残留 RUNNING 任务被标记失败", n >= 1, "恢复 %d 个" % n)
    check("任务状态为 FAILED",
          _one("SELECT status FROM sys_import_job WHERE job_no='IM99990001'")["status"]
          == "FAILED")

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print("  - %s" % f)
    print("日志：%s" % _LOG_PATH)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
