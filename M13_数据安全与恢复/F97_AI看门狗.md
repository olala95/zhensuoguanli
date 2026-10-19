# F97 AI Worker 看门狗

[TYPE: SERVICE] [PRIORITY: P1] [SIZE: M]

---

## 1. 职责

AI 任务跑在**独立进程**（Worker）里，进程可能卡死、超时、崩溃。看门狗负责把
"永远等不到结果"的任务拉回确定状态，**保证医生不会被一个转圈圈的转写卡住**。

---

## 2. 依赖

- 依赖：M04（`ai_task` 状态机、重试上限 `ai.max_retry`）、M12（审计）
- 被依赖：定时任务 / 手动触发 / 启动自检

---

## 3. 涉及的表

`ai_task`（通过 M04 服务接口）、`ai_failure_log`（INSERT-ONLY）
> 表所有权属 M04；M13 只能调用 `m04_ai` 暴露的收敛函数。

---

## 4. 规则

**R1 超时判定。** `ai_task.status='RUNNING'` 且
`now - started_at > ai.timeout_sec`（默认 30）× 安全系数 3 → 判定卡死。
（×3 是因为本地模型冷启动慢，避免误杀。）

**R2 卡死处置：先失败，再决定是否重试。**
- `retry_count < max_retry` → `RETRYING`（写 `ai_failure_log`，action=RETRY），
  重新入队；
- 已达上限 → `ABANDONED`（action=ABANDON），并写 `audit_operation`。

**R3 只收敛，不补偿。** 看门狗**绝不**自动把 AI 草稿写进病历
（M04 R：AI 产出必须医生确认）。任务失败就是失败，草稿留在 `ai_draft` 待医生处置。

**R4 进程级拉起（可选）。** 若配置了 `ai.worker_heartbeat_key`，且心跳超过
`ai.watchdog_seconds`（默认 120）未更新 → 标记 Worker 不可达，
后续 AI 请求直接快速失败（E5002，retryable=true），**不挂住医生**。

**R5 周期与手动。** 定时任务按 `ai.watchdog_seconds` 跑；管理员/医生可手动触发一次
扫描（权限 `system.health`），返回被收敛的任务清单。

**R6 幂等。** 同一任务被收敛两次不会产生两条 `ai_failure_log`（按 task_id + attempt
去重）。

**R7 留痕。** 每次收敛写 `ai_failure_log`（stage=WATCHDOG）+ `audit_operation`
（M13 / watchdog.ai），返回 `{scanned, retried, abandoned}`。

---

## 5. 验收标准

**AC1（超时任务被收敛）**
- Given 一个 RUNNING 且 started_at 已超时 3 倍的 ai_task（retry_count=0）
- When 执行看门狗
- Then 状态变为 RETRYING，`ai_failure_log` 新增 1 条
- 验证方式：自动

**AC2（超重试上限转 ABANDONED）**
- Given RUNNING 且 retry_count 已达 max_retry
- When 执行看门狗
- Then 状态变为 ABANDONED
- 验证方式：自动

**AC3（未超时不误杀）**
- Given 一个刚启动 2 秒的 RUNNING 任务
- When 执行看门狗
- Then 状态不变，scanned 计数包含它但不处置
- 验证方式：自动

**AC4（不自动写病历）**
- Given 一个被丢弃的 AI 任务
- When 执行看门狗后查 record
- Then 无任何记录被 AI 内容修改
- 验证方式：自动

**AC5（幂等）**
- Given 刚被收敛过的任务
- When 再执行一次看门狗（状态已非 RUNNING）
- Then 不产生新的 failure_log
- 验证方式：自动

**AC6（心跳超时快速失败）**
- Given Worker 心跳超时
- When 医生发起新的 AI 请求
- Then 立即返回 E5002（不等待），retryable=true
- 验证方式：自动

**AC7（返回可量化结果）**
- Given 一批混合状态的任务
- When 执行看门狗
- Then 返回 scanned / retried / abandoned 三个计数
- 验证方式：自动

---

## 6. 异常码

| 码 | 场景 | 可重试 |
|---|---|---|
| E5002 | AI 超时 / Worker 不可达 | 是 |
| E1006 | Worker 进程异常 | 是 |

---

## 7. 失败恢复

| 失败点 | 恢复方式 |
|---|---|
| 看门狗自身扫库失败 | 记日志，下周期重试（收敛幂等） |
| Worker 反复崩溃 | 连续 N 次后关闭 `ai.enabled`，转为纯手工录入（AI 是增强不是依赖） |

---

## 8. 契约缺口

- 当前架构 AI 能力是**进程内本地规则引擎**（`ai.engine=LOCAL_RULE`），不存在真正
  的独立 Worker 进程。看门狗按"任务超时"这一层实现即可；心跳拉起部分留接口不实现。
