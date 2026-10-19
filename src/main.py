# -*- coding: utf-8 -*-
"""诊所管理系统 · 启动入口。

用法：
    python src/main.py                 # 正常启动（自动打开浏览器）
    python src/main.py --port 8790     # 指定端口
    python src/main.py --no-browser    # 不自动打开浏览器
    python src/main.py --selftest      # 只跑启动自检后退出
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import runtime  # noqa: E402
# 导入业务模块即完成路由注册
from modules import (m01_patient, m02_visit, m03_record, m12_audit, m14_system,  # noqa: E402,F401
                     m04_ai, m05_lab, m06_prescription,
                     m07_pharmacy, m08_treatment, m09_billing, m15_report, m10_print,
                     m11_archive, m13_recovery)


def main(argv=None):
    parser = argparse.ArgumentParser(description="基层中医诊所门诊管理系统")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--selftest", action="store_true", help="仅执行启动自检后退出")
    parser.add_argument("--root", default=None, help="数据根目录（默认项目根目录）")
    args = parser.parse_args(argv)

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    httpd, url, report = runtime.bootstrap(
        root, port=args.port, open_browser=not args.no_browser,
        start_server=not args.selftest)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.selftest:
        return 0 if not report["safe_mode"] else 2

    print("服务已启动：%s" % url)
    runtime.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
