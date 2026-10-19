# -*- coding: utf-8 -*-
"""在**全新临时库**上跑一遍功能点全量验收。

用法：python tools/run_fresh_full.py

为什么需要它：
  fn_full_test 在长期使用的演练库上跑时，会被早期版本留下的历史脏数据
  （例如还没有冲正保护时造成的"已收 > 应收"账单）干扰，
  导致 F94 完整性检查报违规——那是历史数据的问题，不是当前代码的问题。
  在全新库上跑一遍，才能证明"系统从零开始，每个流程都能跑通"。

它会自动：建临时库 → 起服务（随机端口）→ 初始化 → 跑全量验收 → 停服务。
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
PY = sys.executable


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_up(port, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/system/status" % port,
                                        timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def main():
    root = tempfile.mkdtemp(prefix="clinic_freshfull_")
    shutil.copytree(os.path.join(PROJECT, "migrations"), os.path.join(root, "migrations"))
    shutil.copytree(os.path.join(PROJECT, "web"), os.path.join(root, "web"))
    port = free_port()
    print("临时库：%s" % root)
    print("服务端口：%d\n" % port)

    # 必须把子进程输出写到文件：用 PIPE 却不消费的话，缓冲区写满后服务会被阻塞，
    # 表现为"服务起来了但所有请求超时"。
    logf = open(os.path.join(root, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [PY, os.path.join(PROJECT, "src", "main.py"),
         "--port", str(port), "--root", root, "--no-browser"],
        stdout=logf, stderr=subprocess.STDOUT)
    try:
        if not wait_up(port):
            print("[!!] 服务未能在 60 秒内启动")
            return 1
        rc = subprocess.call(
            [PY, os.path.join(PROJECT, "tools", "fn_full_test.py"),
             "--port", str(port), "--init"])
        return rc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        logf.close()
        print("\n临时库保留在：%s（可自行删除）" % root)
        print("服务日志：%s" % os.path.join(root, "server.log"))


if __name__ == "__main__":
    sys.exit(main())
