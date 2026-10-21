# -*- coding: utf-8 -*-
"""清理磁盘：按正式保留策略收缩备份目录 + 删除测试遗留的抢救库。

只删两类东西，都留清单到 %TEMP%/clinic_cleanup_log.txt：
  1. backups/ 下按保留策略判定为过期的备份（走 core.backup.cleanup()，含 DB 标记）
  2. data/clinic_salvage_*.db —— F95 R3「数据库抢救」功能测试产生的副本，主库正常时无价值
主库 clinic.db 与其 -wal/-shm 绝不触碰。
"""
import glob
import os
import sys
import time

ROOT = "F:\\诊所"
sys.path.insert(0, os.path.join(ROOT, "src"))

lines = []


def say(s=""):
    lines.append(str(s))


def human(n):
    return "%.2f GB" % (n / 1024.0 ** 3) if n >= 1024 ** 3 else "%.1f MB" % (n / 1024.0 / 1024.0)


def total_size(paths):
    t = 0
    for p in paths:
        try:
            t += os.path.getsize(p)
        except OSError:
            pass
    return t


def main():
    from core import db, backup, logging_setup
    logging_setup.init(os.path.join(ROOT, "logs"))
    db.init(os.path.join(ROOT, "data", "clinic.db"))
    backup.init(os.path.join(ROOT, "backups"))

    bdir = os.path.join(ROOT, "backups")
    before = glob.glob(os.path.join(bdir, "*.dbbak"))
    size_before = total_size(before)
    say("=== 清理前 ===")
    say("  backups: %d 个文件 / %s" % (len(before), human(size_before)))

    say("")
    say("=== 按保留策略收缩备份 ===")
    n = backup.cleanup()
    after = glob.glob(os.path.join(bdir, "*.dbbak"))
    size_after = total_size(after)
    say("  cleanup() 删除 %d 个，现 %d 个 / %s" % (n, len(after), human(size_after)))
    say("  回收 %s" % human(size_before - size_after))
    say("")
    say("  保留清单：")
    for p in sorted(after):
        st = os.stat(p)
        say("    %s  %10s  %s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                                  human(st.st_size), os.path.basename(p)))

    say("")
    say("=== 删除测试抢救库 ===")
    salv = sorted(glob.glob(os.path.join(ROOT, "data", "clinic_salvage_*.db")))
    freed = 0
    for p in salv:
        try:
            sz = os.path.getsize(p)
            os.remove(p)
            freed += sz
            say("  已删除 %s（%s）" % (os.path.basename(p), human(sz)))
        except OSError as e:
            say("  删除失败 %s：%r" % (os.path.basename(p), e))
    if not salv:
        say("  没有需要删除的抢救库")

    say("")
    d = os.path.join(ROOT, "data")
    rest = [f for f in os.listdir(d) if f.startswith("clinic.db")]
    say("=== 主库确认（必须还在）===")
    for f in sorted(rest):
        say("  %s  %s" % (f, human(os.path.getsize(os.path.join(d, f)))))

    say("")
    say("=== 清理后总计 ===")
    for sub in ("backups", "data", "logs"):
        p = os.path.join(ROOT, sub)
        t, c = 0, 0
        for dp, _dn, fs in os.walk(p):
            for f in fs:
                try:
                    t += os.path.getsize(os.path.join(dp, f))
                    c += 1
                except OSError:
                    pass
        say("  %-8s %s  %d 个文件" % (sub, human(t), c))
    say("")
    say("本次共回收约 %s" % human((size_before - size_after) + freed))
    return 0


if __name__ == "__main__":
    rc = 0
    try:
        rc = main()
    except Exception:
        import traceback
        lines.append(traceback.format_exc())
        rc = 2
    with open(os.path.join(os.environ.get("TEMP", "."), "clinic_cleanup_log.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
