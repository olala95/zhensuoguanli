# -*- coding: utf-8 -*-
"""重启诊所服务（杀掉旧进程 -> 清 __pycache__ -> 后台起新的 -> 等就绪）。

本机 home 目录含单引号，shell 传参会被截断，所以一律用 Python 驱动。
用法：python tools/restart_server.py [--no-start]
"""
import os
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
PORT = int(os.environ.get("CLINIC_PORT", "8787"))
LOG = os.path.join(os.environ.get("TEMP", "."), "clinic_server.log")


def kill_old():
    killed = []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-NetTCPConnection -LocalPort %d -State Listen "
                              "-ErrorAction SilentlyContinue).OwningProcess" % PORT],
                             capture_output=True, text=True, timeout=30).stdout
        for tok in out.split():
            if tok.strip().isdigit():
                pid = int(tok.strip())
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Stop-Process -Id %d -Force" % pid],
                               capture_output=True, timeout=30)
                killed.append(pid)
    except Exception as e:
        print("kill warn:", e)
    time.sleep(1.5)
    return killed


def clear_pycache():
    n = 0
    for dirpath, dirnames, _ in os.walk(os.path.join(ROOT, "src")):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                n += 1
    return n


def wait_ready(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.6)
    return False


_OUT_PATH = os.path.join(os.environ.get("TEMP", "."), "clinic_restart_out.txt")
with open(_OUT_PATH, "w", encoding="utf-8") as _m:
    _m.write("module imported\n")


def main():
    # PowerShell 工具的 stdout 不回传（而且中文走 GBK 会炸），一律写文件
    _out = open(_OUT_PATH, "w", encoding="utf-8")
    sys.stdout = _out
    sys.stderr = _out
    try:
        _run()
    except Exception:
        import traceback
        traceback.print_exc(file=_out)
    finally:
        _out.flush()
        _out.close()


def _run():
    print("killed:", kill_old())
    print("pycache cleared:", clear_pycache())
    if "--no-start" in sys.argv:
        return
    # 必须用 Start-Process 脱离：直接 Popen 的子进程会随调用方会话一起被回收，
    # 表现为"启动成功、脚本一退出服务就没了"。
    #
    # 坑：本机 home 目录含单引号（zh'j'k），把路径拼进 PowerShell 命令串会被解析坏
    # （'C:\Users\zh'j'k\...' 会被切成三段再拼接，变成不存在的 C:\Users\zhjk\...）。
    # 所以路径一律走环境变量，命令串里不出现任何路径字面量。
    main_py = os.path.join(ROOT, "src", "main.py")
    env = os.environ.copy()
    env["CLINIC_PY"] = PY
    env["CLINIC_MAIN"] = main_py
    env["CLINIC_ROOT"] = ROOT
    env["CLINIC_LOG"] = LOG
    env["CLINIC_ERR"] = LOG + ".err"
    env["CLINIC_PORT_ENV"] = str(PORT)
    for p in (LOG, LOG + ".err"):
        try:
            os.remove(p)
        except OSError:
            pass
    ps = ("Start-Process -FilePath $env:CLINIC_PY "
          "-ArgumentList $env:CLINIC_MAIN,'--port',$env:CLINIC_PORT_ENV,'--no-browser' "
          "-WorkingDirectory $env:CLINIC_ROOT -WindowStyle Hidden "
          "-RedirectStandardOutput $env:CLINIC_LOG -RedirectStandardError $env:CLINIC_ERR")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60, env=env)
    if r.returncode != 0 or (r.stderr or "").strip():
        print("Start-Process rc=%s stderr=%s" % (r.returncode, (r.stderr or "")[:500]))
    print("started via Start-Process, log", LOG)
    ok = wait_ready()
    print("ready:", ok)
    if not ok:
        for p in (LOG, LOG + ".err"):
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    print("---- %s ----" % p)
                    print(f.read()[-3000:])


if __name__ == "__main__":
    main()
