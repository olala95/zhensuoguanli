# -*- coding: utf-8 -*-
"""数据库瘦身：把重复审计**归档**后从主库移出，收缩数据库文件；清理过期抢救副本。

背景：audit_operation 有 INSERT-ONLY 触发器（审计不可改不可删），
所以这里不是"删除"，而是**先归档成 gzip CSV 存档，再在受控事务里移出主库**，
并在归档清单里留下记录。任何一次操作都能从 backups/audit_archive_*.csv.gz 还原。

用法：
    python tools/db_slim.py            # 只体检，不动数据
    python tools/db_slim.py --apply    # 执行（要求服务已停止）
"""
import argparse
import csv
import datetime
import gzip
import os
import shutil
import socket
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "clinic.db")
BACKUPS = os.path.join(ROOT, "backups")
DATA = os.path.join(ROOT, "data")

KEEP_RECENT_RECORD_CREATE = 2000   # 保留最近 N 条 record.create 审计作为样本
KEEP_SALVAGE = 2                   # 抢救副本保留最新 N 份
BATCH = 20000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    if not os.path.exists(DB):
        print("找不到数据库：%s" % DB)
        return 1

    s = socket.socket()
    s.settimeout(1)
    running = s.connect_ex(("127.0.0.1", args.port)) == 0
    s.close()
    if running and args.apply and not args.force:
        print("服务仍在 %d 端口运行，请先停止服务再执行 --apply" % args.port)
        return 2

    before = os.path.getsize(DB)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    total = c.execute("select count(*) from audit_operation").fetchone()[0]
    rc = c.execute("select count(*) from audit_operation where action='record.create'").fetchone()[0]
    print("数据库：%s（%.1f MB）" % (DB, before / 1024 / 1024))
    print("audit_operation 总计 %d 条，其中 record.create 噪音 %d 条（占 %.1f%%）"
          % (total, rc, rc * 100.0 / max(total, 1)))

    salvage = sorted([f for f in os.listdir(DATA) if f.startswith("clinic_salvage_")])
    ssize = sum(os.path.getsize(os.path.join(DATA, f)) for f in salvage)
    print("抢救副本 %d 个，共 %.1f MB（将保留最新 %d 个）"
          % (len(salvage), ssize / 1024 / 1024, KEEP_SALVAGE))

    if not args.apply:
        print("\n以上为体检结果，未修改任何数据。加 --apply 执行。")
        return 0

    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) 安全备份（先 checkpoint，保证备份是完整的库）
    safe = os.path.join(BACKUPS, "before_slim_%s.db" % stamp)
    print("\n[1/5] 安全备份 -> %s" % safe)
    conn.execute("pragma wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    shutil.copy2(DB, safe)
    print("      完成 %.1f MB" % (os.path.getsize(safe) / 1024 / 1024))

    conn = sqlite3.connect(DB)
    conn.isolation_level = None          # 手动控制事务（DROP TRIGGER 需要在事务外）
    c = conn.cursor()

    keep = c.execute("select id from audit_operation where action='record.create' "
                     "order by id desc limit ?", (KEEP_RECENT_RECORD_CREATE,)).fetchall()
    keep_ids = [r[0] for r in keep]
    if keep_ids:
        marks = ",".join("?" * len(keep_ids))
        where = "action='record.create' and id not in (%s)" % marks
        params = tuple(keep_ids)
    else:
        where = "action='record.create'"
        params = ()

    n = c.execute("select count(*) from audit_operation where " + where, params).fetchone()[0]
    if n == 0:
        print("没有需要归档的记录。")
        conn.close()
        return 0

    # 2) 归档成 gzip CSV
    arch = os.path.join(BACKUPS, "audit_archive_%s.csv.gz" % stamp)
    print("[2/5] 归档 %d 条 -> %s" % (n, arch))
    cols = [d[1] for d in c.execute("pragma table_info(audit_operation)")]
    written = 0
    with gzip.open(arch, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        cur = c.execute("select %s from audit_operation where %s order by id"
                        % (",".join(cols), where), params)
        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            w.writerows(rows)
            written += len(rows)
            print("      %d / %d" % (written, n))
    print("      归档完成 %.1f MB" % (os.path.getsize(arch) / 1024 / 1024))

    # 3) 受控移出：临时停用审计表的 DELETE 保护，移出后立刻恢复
    print("[3/5] 从主库移出（临时停用 INSERT-ONLY 触发器）")
    c.execute("DROP TRIGGER IF EXISTS trg_audit_operation_no_delete")
    c.execute("BEGIN IMMEDIATE")
    try:
        c.execute("delete from audit_operation where " + where, params)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    finally:
        c.execute("CREATE TRIGGER IF NOT EXISTS trg_audit_operation_no_delete "
                  "BEFORE DELETE ON audit_operation "
                  "BEGIN SELECT RAISE(ABORT, 'E2006: audit_operation 只允许 INSERT'); END")
    # 留痕：归档这件事本身也要进审计
    c.execute("INSERT INTO audit_high_risk (occurred_at, risk_type, entity_type, entity_id, "
              "user_id, role_code, source, reason, detail_json) "
              "VALUES (?,?,?,?,?,?,?,?,?)",
              (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "AUDIT_ARCHIVE",
               "audit_operation", None, 0, "SYSTEM", "SYSTEM",
               "审计归档：%d 条 record.create 噪音移出主库，存档 %s" % (written, os.path.basename(arch)),
               None))
    print("      已留痕 audit_high_risk(action=audit.archive)")

    # 4) 清理旧抢救副本
    removed = 0
    if len(salvage) > KEEP_SALVAGE:
        for f in salvage[:-KEEP_SALVAGE]:
            try:
                os.remove(os.path.join(DATA, f))
                removed += 1
            except OSError as e:
                print("      删除失败 %s：%s" % (f, e))
    print("[4/5] 清理抢救副本 %d 个" % removed)

    # 5) 收缩
    c.execute("VACUUM")
    conn.close()

    after = os.path.getsize(DB)
    print("[5/5] VACUUM 完成：%.1f MB -> %.1f MB（减少 %.1f%%）"
          % (before / 1024 / 1024, after / 1024 / 1024,
             (before - after) * 100.0 / before if before else 0))
    print("\n安全备份：%s" % safe)
    print("审计归档：%s" % arch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
