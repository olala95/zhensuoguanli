# -*- coding: utf-8 -*-
"""跑全部回归测试，结果写文件（PowerShell 不回传 stdout）。

用法：python tools/run_all_tests.py [关键字过滤]
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OUT = os.path.join(os.environ.get("TEMP", "."), "clinic_tests.txt")

# 本机给 127.0.0.1 也配了代理，不打洞 urllib 会被 502
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

SUITES = [
    ("tests/smoke_test.py", "直调服务函数"),
    ("tests/http_smoke.py", "HTTP 冒烟"),
    ("tests/first_run_test.py", "首次运行全链路"),
    ("tests/phase3_test.py", "阶段三 M07/M08/M09"),
    ("tests/phase4_archive_test.py", "阶段四 病历归档"),
    ("tests/phase4_import_test.py", "阶段四 基础数据导入"),
    ("tests/phase4_print_test.py", "阶段四 打印"),
    ("tests/phase4_report_test.py", "阶段四 报表"),
    ("tests/phase5_fault_drills_test.py", "阶段五 故障演练"),
    ("tests/phase5_recovery_test.py", "阶段五 恢复"),
    ("tests/api_contract_test.py", "接口契约"),
    ("tests/final_acceptance_test.py", "最终验收"),
]

filt = sys.argv[1] if len(sys.argv) > 1 else ""
lines = []
summary = []

for path, desc in SUITES:
    if filt and filt not in path:
        continue
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        continue
    try:
        r = subprocess.run([PY, full], cwd=ROOT, capture_output=True,
                           text=True, timeout=900, encoding="utf-8",
                           errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        rc = r.returncode
    except subprocess.TimeoutExpired:
        out, rc = "TIMEOUT", -1
    tail = out.strip().splitlines()
    head = [l for l in tail if ("通过" in l or "失败" in l or "FAIL" in l.upper()
                                or "OK" in l or "通过率" in l or "PASS" in l.upper())]
    lines.append("=" * 66)
    lines.append("%s  (%s)  rc=%s" % (path, desc, rc))
    lines.append("-" * 66)
    lines.append("\n".join(head[-14:]) if head else "\n".join(tail[-14:]))
    summary.append((path, rc))

lines.append("")
lines.append("=" * 66)
lines.append("汇总")
for p, rc in summary:
    lines.append("  %-40s %s" % (p, "PASS" if rc == 0 else "rc=%s" % rc))
lines.append("  通过 %d / %d" % (sum(1 for _, rc in summary if rc == 0), len(summary)))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
