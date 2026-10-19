# F95 SQLite 异常恢复

[TYPE: SERVICE] [PRIORITY: P0] [SIZE: M]

---

## 1. 职责

把所有 SQLite 抛出的底层异常**翻译成明确的动作**，而不是让诊所看到一句
"database disk image is malformed" 就干瞪眼。核心是**分类处置**：
哪些能自愈、哪些必须进只读、哪些必须恢复。

---

## 2. 依赖

- 依赖：`core/db._translate`（异常翻译）、F94（校验）、F93（恢复）、M12（高风险审计）
- 被依赖：`core/api`（全局异常兜底）、所有模块

---

## 3. 涉及的表

`crash_recovery_log`、`integrity_check`（只读登记）

---

## 4. 规则

**R1 四类异常，四种处置。**

| SQLite 异常 | 异常码 | 处置 | 是否进只读 |
|---|---|---|---|
| `SQLITE_BUSY` / `LOCKED` | E1001 | 等待重试（busy_timeout 已设 5s），超限返回可重试错误 | 否 |
| `SQLITE_FULL` | E1002 | 告警 + 清理旧备份腾空间 + 拒绝写入 | 否（但禁止新写） |
| `SQLITE_IOERR` | E1004 | 记录 + 重试一次；仍失败进只读并告警 | 是（连续失败） |
| `SQLITE_CORRUPT` / `NOTADB` | E1003 | **立即进只读** + 高风险审计 + 提示恢复 | **是** |

**R2 损坏即冻结现场。** 捕获 CORRUPT 的那一刻：① `db.set_readonly(True)`；
② 写 `crash_recovery_log`（result=NEED_MANUAL_REVIEW）；③ 写 `audit_high_risk`
（RISK_DB_CORRUPT）；④ **绝不删除或改写原库文件**，为后续 salvage 保留证据。

**R3 提供 salvage 通道（手动、默认关闭）。** 管理员确认后可执行
`sqlite3 .recover` 风格的抢救：把还能读出的行导出到新库 `clinic_salvage_{ts}.db`，
**不覆盖原库**；抢救结果必须跑完整性检查，且提示"抢救库可能缺行，需人工核对"。

**R4 翻译统一走 `db._translate`。** 任何模块不得自己 `except sqlite3.Error` 后
`pass` 或抛裸异常（00_AI_RULES R6：禁止静默吞异常）。

**R5 触发器抛出的 `E2006` 等自定义码必须透传。** `_translate` 已用正则优先匹配
`^E\d{4}:`，不得被统一包成 E1004。

**R6 只读解除只能由人来做。** 系统**绝不自动**解除只读（自动解除可能在坏库上继续写）；
解除入口：成功恢复（F93）后自动解除，或管理员在确认后手动解除并留痕。

**R7 全程留痕。** 每类异常的处置动作都写 `crash_recovery_log`（issue / action_taken /
result / detail_json）。

---

## 5. 验收标准

**AC1（BUSY 可重试）**
- Given 另一连接持有写锁
- When 本进程写入
- Then 返回 E1001 且 retryable=true，系统未进只读
- 验证方式：自动

**AC2（CORRUPT 进只读）**
- Given 注入一个 CORRUPT 异常
- When 业务写入并触发兜底处置
- Then `db.is_readonly()` 为 true，业务再写入返回 E1005
- 验证方式：自动

**AC3（损坏不删原库）**
- Given CORRUPT 已触发
- When 查看数据目录
- Then 原 `.db` 文件仍存在且大小未变
- 验证方式：自动

**AC4（高风险审计）**
- Given CORRUPT 已触发
- When 查 `audit_high_risk`
- Then 有 RISK_DB_CORRUPT 记录，含原始错误信息
- 验证方式：自动

**AC5（崩溃日志）**
- Given CORRUPT 已触发
- When 查 `crash_recovery_log`
- Then 新增一条，result=NEED_MANUAL_REVIEW
- 验证方式：自动

**AC6（salvage 不覆盖原库）**
- Given 管理员确认抢救
- When 执行 salvage
- Then 生成独立的 salvage 库文件，原库完好
- 验证方式：自动

**AC7（E2006 透传）**
- Given 对 INSERT-ONLY 表执行 UPDATE
- When 触发触发器
- Then 异常码为 E2006，不是 E1004
- 验证方式：自动

---

## 6. 异常码

| 码 | 场景 | 可重试 |
|---|---|---|
| E1001 | 库忙 | 是 |
| E1002 | 磁盘满 | 否 |
| E1003 | 库损坏 | 否 |
| E1004 | I/O 错误 | 是 |
| E1005 | 只读中 | 是 |
| E2006 | INSERT-ONLY 表被改/删 | 否 |

---

## 7. 失败恢复

| 失败点 | 恢复方式 |
|---|---|
| salvage 失败 | 保留原库，改用最近备份恢复（F93） |
| 只读解除后再次损坏 | 立刻回到只读，建议更换磁盘 |

---

## 8. 契约缺口

- 纯 Python 标准库没有 `sqlite3 .recover` 的直接等价物；salvage 实现为
  **逐表 `SELECT *` 导出 + 重建**，损坏页上的行会丢失。文档与 UI 必须明确提示
  "抢救结果不完整"。
