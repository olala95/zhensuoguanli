# -*- coding: utf-8 -*-
"""备份目录清理：按保留策略列出/删除旧备份。

默认只体检。策略：
    · 保留最近 --keep 份（默认 5）
    · 保留每个月最早 1 份（跨月归档）
    · 保留 before_slim_*.db（数据库瘦身前的安全备份）与 audit_archive_*

用法：
    python tools/backup_cleanup.py                 # 只列清单
    python tools/backup_cleanup.py --apply         # 删除
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep", type=int, default=5)
    args = ap.parse_args()

    if not os.path.isdir(BACKUPS):
        print("备份目录不存在：%s" % BACKUPS)
        return 1

    files = []
    for fn in os.listdir(BACKUPS):
        p = os.path.join(BACKUPS, fn)
        if not os.path.isfile(p):
            continue
        if fn.startswith("before_slim_") or fn.startswith("audit_archive_"):
            continue          # 安全备份与审计归档不动
        if fn.endswith(".dbbak") or fn.endswith(".db"):
            files.append((os.path.getmtime(p), os.path.getsize(p), fn))

    files.sort(reverse=True)
    total = sum(f[1] for f in files)
    print("备份目录：%s" % BACKUPS)
    print("可清理备份 %d 个，共 %.1f GB（保留最近 %d 份 + 每月 1 份）"
          % (len(files), total / 1024 ** 3, args.keep))

    keep = set()
    for i, (_, _, fn) in enumerate(files):
        if i < args.keep:
            keep.add(fn)
    months = {}
    for _, _, fn in files:
        # clinic_YYYYMMDD_HHMMSS...
        parts = fn.split("_")
        ym = parts[1][:6] if len(parts) > 1 and parts[1][:6].isdigit() else fn[:6]
        months.setdefault(ym, fn)
    for fn in months.values():
        keep.add(fn)

    drop = [f for f in files if f[2] not in keep]
    print("将保留 %d 个，删除 %d 个（释放 %.1f GB）"
          % (len(keep), len(drop), sum(f[1] for f in drop) / 1024 ** 3))
    for _, sz, fn in drop[:200]:
        print("  [删除] %-52s %8.1f MB" % (fn, sz / 1024 ** 2))
    if len(drop) > 200:
        print("  …… 另有 %d 个" % (len(drop) - 200))

    if not args.apply:
        print("\n以上为体检结果，未删除任何文件。加 --apply 执行。")
        return 0

    removed = 0
    for _, _, fn in drop:
        try:
            os.remove(os.path.join(BACKUPS, fn))
            removed += 1
        except OSError as e:
            print("删除失败 %s：%s" % (fn, e))
    print("\n已删除 %d 个备份" % removed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
