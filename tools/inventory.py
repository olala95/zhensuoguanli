# -*- coding: utf-8 -*-
"""功能点清点：文档 Fxx.md ↔ 代码实现 ↔ 测试用例 三方对账。

用法：python tools/inventory.py
输出：控制台 + %TEMP%/clinic_inventory.txt
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

MODULES = {
    "M01": "患者中心", "M02": "就诊中心", "M03": "临床病历", "M04": "AI与语音",
    "M05": "检验检查", "M06": "处方用药", "M07": "药房库存", "M08": "处置执行",
    "M09": "收费财务", "M10": "打印硬件", "M11": "病历归档", "M12": "安全审计",
    "M13": "数据安全与恢复", "M14": "系统管理", "M15": "经营统计",
}


def _dirs():
    return [d for d in sorted(os.listdir(PROJECT))
            if re.match(r"^M\d\d_", d) and os.path.isdir(os.path.join(PROJECT, d))]


def _fdocs(mod_dir):
    p = os.path.join(PROJECT, mod_dir)
    out = {}
    for f in sorted(os.listdir(p)):
        m = re.match(r"^F(\d{2,3})_.*\.md$", f)
        if m:
            out[m.group(1)] = f
    return out


def _src_text():
    buf = []
    src = os.path.join(PROJECT, "src")
    for root, _, files in os.walk(src):
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    buf.append(fh.read())
    return "\n".join(buf)


def _test_text():
    buf = []
    t = os.path.join(PROJECT, "tests")
    if os.path.isdir(t):
        for f in sorted(os.listdir(t)):
            if f.endswith(".py"):
                with open(os.path.join(t, f), encoding="utf-8") as fh:
                    buf.append(fh.read())
    return "\n".join(buf)


def main():
    src = _src_text()
    tests = _test_text()
    total = 0
    missing_doc, missing_impl = [], []
    no_test_module = []
    lines = []
    lines.append("%-5s %-16s %-6s %-6s %-10s %-8s"
                 % ("模块", "名称", "文档", "实现", "测试调用", "测试文件"))
    lines.append("-" * 64)
    for code, name in MODULES.items():
        hits = [d for d in _dirs() if d.startswith(code + "_")]
        if not hits:
            lines.append("%-5s %-16s  缺少模块目录" % (code, name))
            continue
        docs = _fdocs(hits[0])
        n_doc = len(docs)
        n_impl = 0
        for num in docs:
            if re.compile(r"\bF%s\b" % num).search(src):
                n_impl += 1
            else:
                missing_impl.append("%s/F%s" % (code, num))
        total += n_doc
        # 测试覆盖按"模块函数在测试里被调用"判定（比找 F 编号文字靠谱：
        # 大量测试直接调服务函数，不会写编号注释）
        alias = code.lower()          # m13_recovery.as_m13 / m01_patient as m01 ...
        calls = sorted(set(re.findall(r"\b%s\.[a-z_][a-z0-9_]*" % alias, tests)))
        files = sorted(set(re.findall(r"\b%s\.([a-z_][a-z0-9_]*)" % alias, tests)))
        tfiles = sorted(set(re.findall(r"from modules import[^\n]*", tests)))
        n_files = sum(1 for line in tfiles if alias in line)
        lines.append("%-5s %-16s %-6d %-6d %-10d %-8d"
                     % (code, name, n_doc, n_impl, len(calls), n_files))
        if not calls:
            no_test_module.append("%s %s（%d 个功能点）" % (code, name, n_doc))
    lines.append("-" * 64)
    lines.append("功能点文档总数：%d（目标 128）" % total)
    lines.append("")
    lines.append("【代码中找不到 F 编号标记的功能点】%d 个" % len(missing_impl))
    for x in missing_impl:
        lines.append("  - %s" % x)
    lines.append("")
    lines.append("【测试中完全没有调用的模块】%d 个" % len(no_test_module))
    for x in no_test_module:
        lines.append("  - %s" % x)

    out = "\n".join(lines)
    print(out)
    p = os.path.join(tempfile.gettempdir(), "clinic_inventory.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(out)
    print("\n明细：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
