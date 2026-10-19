# F98 Hardware Agent 看门狗

[TYPE: SERVICE] [PRIORITY: P1] [SIZE: M]

---

## 1. 职责

打印机掉线、读卡器无响应、扫码枪卡住——这些**不该让诊所停摆**。看门狗周期性探测
硬件健康，失败到阈值就**重启驱动**，仍失败则把设备降级并在 UI 提示，
同时把卡在 PRINTING 的打印任务收敛掉。

---

## 2. 依赖

- 依赖：M10（`hardware_device` / `hardware_health` / `print_job`）、`core/hardware`（驱动注册表）
- 被依赖：定时任务 / 手动触发 / M10 打印流程

---

## 3. 涉及的表

`hardware_health`、`hardware_event`、`hardware_device`、`print_job`
> 表所有权属 M10；M13 通过 `core/hardware` 注册表与 M10 暴露的收敛函数操作。

---

## 4. 规则

**R1 周期心跳。** 按 `hardware.heartbeat_seconds`（默认 60）对每个已启用设备探测一次，
结果写 `hardware_health`（result OK/WARN/FAIL，online，elapsed_ms）。

**R2 连续失败达阈值才动手。** `hardware.watchdog_fail_threshold`（默认 3）：
同一驱动**连续** FAIL 达到阈值 → 触发重启。**单次失败只记录不重启**
（避免拔一下 USB 就疯狂重连）。

**R3 重启走驱动注册表。** 调用 `core/hardware.restart(code)` 重建驱动实例；
真实驱动场景由 Agent 负责重连。重启后**立即复测一次**，写 `hardware_event`
（event_type=RESTART，result=SUCCESS/FAIL）。

**R4 重启失败则降级设备。** 连续 2 轮"重启后仍失败" → `hardware_device.status`
置 `OFFLINE`（曾部分可用则 `DEGRADED`），写 `audit_operation`，UI 健康检查显著提示。

**R5 卡住的打印任务收敛。** `print_job.status='PRINTING'` 且超过
`print.job_timeout_sec`（默认 120）→ 置 FAILED 并写 `print_failure`（retryable=1），
**允许重打**（打印无资金影响，R-M13-7）。

**R6 硬件失败绝不回滚业务。** 处方已开立、费已收，打印失败只是"没出纸"；
绝不能因为打印机掉线把处方或收费回滚（M10 R：硬件失败转 E4xxx，业务不回滚）。

**R7 留痕与可量化。** 返回 `{checked, restarted, degraded, jobs_recovered}`；
每次重启与降级都写 `hardware_event` + `audit_operation`（M13 / watchdog.hardware）。

---

## 5. 验收标准

**AC1（正常探测写入健康表）**
- Given 三个 mock 设备在线
- When 执行一次看门狗
- Then `hardware_health` 新增 3 条，result=OK
- 验证方式：自动

**AC2（单次失败不重启）**
- Given 某驱动首次探测 FAIL
- When 执行看门狗
- Then 记录 FAIL，restarted=0
- 验证方式：自动

**AC3（连续失败触发重启）**
- Given 某驱动连续 FAIL 达到阈值
- When 执行看门狗
- Then 调用驱动重启，写 RESTART 事件，restarted=1
- 验证方式：自动

**AC4（重启成功则恢复）**
- Given 驱动被注入 fail 后由看门狗重启
- When 复测
- Then 该驱动 online=true，设备状态回到 ONLINE
- 验证方式：自动

**AC5（重启无效则降级）**
- Given 驱动重启后仍连续失败两轮
- When 执行看门狗
- Then `hardware_device.status` = OFFLINE，有审计记录
- 验证方式：自动

**AC6（卡住的打印任务被收敛）**
- Given 一个 PRINTING 且超时的 print_job
- When 执行看门狗
- Then 变为 FAILED，写入 print_failure，可重打
- 验证方式：自动

**AC7（硬件失败不回滚业务）**
- Given 打印失败
- When 查处方与账单
- Then 状态与金额完全不受影响
- 验证方式：自动

---

## 6. 异常码

| 码 | 场景 | 可重试 |
|---|---|---|
| E4001 | 打印机离线/缺纸 | 是 |
| E4002 | Agent/驱动不可达 | 是 |

---

## 7. 失败恢复

| 失败点 | 恢复方式 |
|---|---|
| 驱动重启异常 | 捕获并记日志，本轮记为 FAIL，下轮再试 |
| 设备长期离线 | UI 提示检查连线；恢复后把设备 status 置回 ONLINE（手动或下轮探测） |

---

## 8. 契约缺口

- mock 驱动的"重启"是重建实例并清故障标记；**真实驱动的重启语义依赖厂商 SDK**，
  注入真实驱动时需同时提供 `restart()` 语义，否则看门狗只能降级不能自愈。
