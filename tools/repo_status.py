# -*- coding: utf-8 -*-
"""检查提交信息编码与仓库状态（发布前用）。"""

import subprocess
import sys
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, check=True):
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    if check and p.returncode != 0:
        print("git %s 失败：%s" % (" ".join(args), err))
    return out, err, p.returncode


def main():
    print("=" * 66)
    print("仓库状态检查")
    print("=" * 66)

    out, _, _ = git("log", "--oneline", "-3")
    print("\n[最近提交]")
    for line in out.splitlines():
        print("  " + line)

    out, _, _ = git("branch", "--show-current")
    print("\n[当前分支] %s" % out.strip())

    out, _, _ = git("remote", "-v")
    print("\n[远程] %s" % (out.strip() or "（未配置，等你给仓库地址）"))

    out, _, _ = git("status", "--short")
    dirty = [l for l in out.splitlines() if l.strip()]
    print("\n[未提交改动] %d 项" % len(dirty))
    for l in dirty[:10]:
        print("  " + l)

    out, _, _ = git("ls-files")
    files = [f for f in out.splitlines() if f.strip()]
    print("\n[已跟踪] %d 个文件" % len(files))

    # 检查提交信息是否正确解码（中文）
    out, _, _ = git("log", "-1", "--pretty=%B")
    msg = out.strip()
    has_cn = any("\u4e00" <= ch <= "\u9fff" for ch in msg)
    print("\n[提交信息] %s" % ("中文正常" if has_cn else "⚠ 未检测到中文，可能编码异常"))
    print("  首行：%s" % (msg.splitlines()[0] if msg else "（空）"))

    print("\n" + "=" * 66)
    print("下一步：")
    print("  git remote add origin <你的仓库地址>")
    print("  git push -u origin main")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
