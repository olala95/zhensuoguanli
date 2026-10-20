# -*- coding: utf-8 -*-
"""验证：数据库损坏时程序能正常启动并进入安全模式，而不是启动即崩。

这是故障演练 8 暴露的真实缺陷的回归验证：
    修复前 db.init() 里 PRAGMA journal_mode=WAL 裸抛 sqlite3.DatabaseError，
    启动直接崩，F95 的"冻结现场 + 从备份恢复"链路根本执行不到。

用法：python tools/verify_corrupt_startup.py
输出写到 %TEMP%/clinic_corrupt_startup.txt
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from core import db, runtime  # noqa: E402
from core.errors import AppError  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "clinic_corrupt_startup.txt")
lines = []
PASSED, FAILED = [], []


def say(msg):
    lines.append(msg)


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    say("  %s  %s %s" % ("PASS" if cond else "FAIL", name, extra))


def main():
    root = tempfile.mkdtemp(prefix="clinic_corrupt_")
    shutil.copytree(os.path.join(ROOT, "migrations"), os.path.join(root, "migrations"))
    say("临时目录：%s" % root)

    # 第一次正常启动，把库建起来
    _, _, rep = runtime.bootstrap(root, start_server=False)
    check("正常启动未进安全模式", rep["safe_mode"] is False)
    db.close_all()

    # 注入损坏
    db_path = os.path.join(root, "data", "clinic.db")
    size = os.path.getsize(db_path)
    off = (size // 2 // 4096) * 4096
    with open(db_path, "r+b") as fh:
        fh.seek(off)
        fh.write(b"\x00CORRUPT\x01" * 200)
    say("已注入损坏：偏移 %d（1 页）" % off)

    # 第二次启动：关键——不能崩
    rep2 = None
    try:
        _, _, rep2 = runtime.bootstrap(root, start_server=False)
        crashed = False
    except Exception as exc:
        crashed = True
        say("启动抛异常：%r" % exc)

    say("")
    check("损坏后启动未崩溃（不再裸抛 DatabaseError）", not crashed)
    if crashed:
        return 1
    check("自动进入安全模式（只读）", rep2["safe_mode"] is True, str(rep2["safe_mode"]))
    # malformed 只在文件头损坏时置位；本次损坏落在数据页，由 integrity_check 报 FAIL
    check("损坏被识别（malformed 标记或完整性检查 FAIL）",
          db.is_malformed() or any(c["result"] == "FAIL" for c in rep2["checks"]),
          "malformed=%s" % db.is_malformed())
    say("  自检：%s" % [(c["item"], c["result"]) for c in rep2["checks"]])

    # 安全模式下写业务必须被明确拒绝（E1003），而不是 500 堆栈
    rejected, err = False, ""
    try:
        with db.tx() as conn:
            db.execute(conn, "INSERT INTO patient (name, gender, created_at, updated_at)"
                             " VALUES ('不该写入','M','x','x')")
    except AppError as exc:
        rejected, err = True, "%s %s" % (exc.code, exc.message)
    check("安全模式下写业务被明确拒绝", rejected, err)

    # 冻结现场留痕：崩溃日志（force=True 写入）
    try:
        n_crash = db.query("SELECT COUNT(*) c FROM crash_recovery_log")[0]["c"]
    except AppError:
        n_crash = -1
    check("冻结现场已留痕 crash_recovery_log", n_crash >= 1, str(n_crash))

    try:
        n_risk = db.query("SELECT COUNT(*) c FROM audit_high_risk "
                          "WHERE risk_type='DB_CORRUPT'")[0]["c"]
    except AppError:
        n_risk = -1
    check("冻结写了高风险审计 DB_CORRUPT", n_risk >= 1, str(n_risk))

    # 库外旁路证据（新增）：库坏到写不进去时证据仍在磁盘上
    ev_dir = os.path.join(root, "logs", "integrity")
    files = sorted(os.listdir(ev_dir)) if os.path.isdir(ev_dir) else []
    check("完整性旁路证据文件已生成", bool(files), str(files))
    if files:
        with open(os.path.join(ev_dir, files[0]), encoding="utf-8") as fh:
            say("  证据内容：%s" % fh.read().strip()[:400])

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
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    sys.exit(rc)
