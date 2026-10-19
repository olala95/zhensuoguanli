# 提交前必读

## 绝对不能提交的东西

- `data/` 下的业务库（含患者姓名、诊断、处方、收费记录）
- `backups/` 下的备份文件
- `logs/` 下的运行日志（含操作痕迹）
- 任何 `*.db` / `*.dbbak` / `*.log`

这些已在 `.gitignore` 里，**不要用 `git add -f` 强制添加**。

## 推送前自检

```bash
python tools/pre_publish_check.py
```

会检查：敏感文件是否被跟踪、内容里有没有本机绝对路径、总体积、关键文件是否齐全。
**返回非 0 就不要推送。**

## 提交约定

- 一个提交只做一件事，消息写清"为什么改"而不是"改了什么"
- 改了 `src/core/*` 或迁移脚本后，至少跑 `tests/smoke_test.py` 与
  `tests/http_smoke.py`；改了业务模块还要跑对应 phase 测试
- 改前端后跑 `node tools/js_api_check.js` 与 `python tools/api_audit.py`
- 迁移脚本**只增不改**：已执行的迁移文件禁止修改，修正就新增下一个序号
