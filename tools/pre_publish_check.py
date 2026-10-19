# -*- coding: utf-8 -*-
"""发布前自检：确认 git 只跟踪了该跟踪的文件，且没有泄露本机信息。"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 绝对不能入库的路径前缀
FORBIDDEN_PREFIX = ("data/", "backups/", "logs/", ".workbuddy/")
FORBIDDEN_EXT = (".db", ".db-wal", ".db-shm", ".dbbak", ".pyc", ".log")
# 不应出现的内容特征（用拼接构造，避免自检脚本把自己当成泄露源）
SUSPECT = (("C:" + "\\Users" + "\\", "本机绝对路径"),
           ("/c/" + "Users/", "本机绝对路径"),
           ("zh" + "'" + "j" + "'" + "k", "本机用户名"))


def run(args):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stdout


def main():
    files = [f for f in run(["git", "ls-files"]).splitlines() if f.strip()]
    print("=" * 66)
    print("发布前自检 · 已跟踪文件 %d 个" % len(files))
    print("=" * 66)

    bad = []
    for f in files:
        low = f.replace("\\", "/")
        if low.startswith(FORBIDDEN_PREFIX):
            bad.append((f, "敏感目录"))
        elif low.endswith(FORBIDDEN_EXT):
            bad.append((f, "敏感扩展名"))
        elif "__pycache__" in low:
            bad.append((f, "缓存"))

    print("\n[1] 敏感文件是否被跟踪")
    if bad:
        for f, why in bad:
            print("  ✗ %-50s %s" % (f, why))
    else:
        print("  ✓ 无（data / backups / logs / .workbuddy / *.db 均未入库）")

    print("\n[2] 内容里是否有本机路径或用户名")
    hits = []
    for f in files:
        if not f.lower().endswith((".py", ".js", ".md", ".sql", ".html", ".css",
                                   ".json", ".txt", ".yml", ".yaml")):
            continue
        p = os.path.join(ROOT, f)
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        for needle, why in SUSPECT:
            if needle in text:
                hits.append((f, why, needle))
    if hits:
        for f, why, n in hits[:20]:
            print("  ✗ %-46s %s（%s）" % (f, why, n))
    else:
        print("  ✓ 无")

    print("\n[3] 体积与大文件")
    total = 0
    big = []
    for f in files:
        p = os.path.join(ROOT, f)
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        total += sz
        if sz > 500 * 1024:
            big.append((f, sz))
    print("  总计 %.2f MB" % (total / 1024.0 / 1024.0))
    if big:
        for f, sz in big:
            print("  ! %-50s %.0f KB" % (f, sz / 1024.0))
    else:
        print("  ✓ 无单文件超过 500KB（GitHub 单文件限制 100MB）")

    print("\n[4] 关键文件是否齐全")
    need = ["README.md", ".gitignore", "src/main.py", "web/index.html",
            "web/app.js", "migrations/0001_init.sql", "01_SYSTEM_OVERVIEW.md"]
    tracked = set(files)
    miss = [n for n in need if n not in tracked]
    if miss:
        for n in miss:
            print("  ✗ 缺少 %s" % n)
    else:
        print("  ✓ README / .gitignore / 入口 / 前端 / 迁移 / 总览 都在")

    print("\n" + "=" * 66)
    problems = len(bad) + len(hits) + len(miss)
    if problems:
        print("结论：发现 %d 处问题，**不要推送**，先修。" % problems)
    else:
        print("结论：可以推送到 GitHub。")
    print("=" * 66)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
