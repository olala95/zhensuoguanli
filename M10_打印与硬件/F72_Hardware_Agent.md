# F72 Hardware Agent

[TYPE: SERVICE] [PRIORITY: P0] [SIZE: M]

---

## 1. 职责

硬件能力的**适配层**：把打印机、身份证读卡器、扫码枪等设备抽象成可注入的驱动，隔离业务代码与具体硬件；提供设备登记、状态查询与心跳。

> 部署形态见 `05_RUNTIME_TOPOLOGY`：Agent 若独立进程运行，**不得直接打开业务库**，一律通过本地 IPC 调主程序接口。本实现在主程序内提供驱动注册与 mock 实现。

---

## 2. 依赖

- 依赖：M14（配置与诊断）、M12（审计）
- 被依赖：F66/F71（打印）、F73（身份证）、F74（扫码）、F75（监控）、F98（看门狗）

---

## 3. 涉及的表

`hardware_device`、`hardware_event`、`hardware_health`

---

## 4. 规则

**R1 驱动注册表。** `core/hardware.py` 维护 `driver_code → driver 实例`；内置 `mock` 系列驱动（打印落盘、身份证/扫码返回模拟数据）。真实驱动可运行时注入替换（R8：必须有 mock）。

**R2 设备登记。** `hardware_device` 记录 `device_code`、类型（PRINTER/ID_READER/SCANNER）、厂商型号、连接参数（不含密钥明文）、绑定的驱动、是否启用。

**R3 状态三态。** `ONLINE / OFFLINE / DEGRADED`；由心跳（F75）与调用结果更新。

**R4 调用失败降级。** 驱动抛异常 → 转成带异常码的 `AppError`（E4xxx，`retryable=true`），**业务层不得因硬件失败而回滚**（R-M10-6）。

**R5 事件留痕。** 每次驱动调用（成功/失败）写 `hardware_event`，含耗时与返回摘要（**不记录隐私原文**）。

**R6 幂等与超时。** 驱动调用带超时（默认 5 秒）；超时按失败处理且可重试。

**R7 不直连业务库。** 驱动只做"设备动作"，任何数据读写都回到主程序侧接口（05 §2 单写者原则）。

---

## 5. 验收标准

**AC1（mock 驱动可打印）**
- Given 默认 mock 打印机
- When 执行打印任务
- Then 输出落盘成功，返回 SUCCESS
- 验证方式：自动（mock）

**AC2（驱动可注入替换）**
- Given 注入一个失败驱动
- When 执行
- Then 走失败路径，任务 FAILED + E4001
- 验证方式：自动

**AC3（设备登记）**
- Given 登记一台 PRINTER
- When 查询设备列表
- Then 返回该设备，状态 ONLINE
- 验证方式：自动

**AC4（离线设备调用被拒）**
- Given 设备置为 OFFLINE
- When 打印
- Then 拒绝 E4001，可重试
- 验证方式：自动

**AC5（事件留痕）**
- Given 连续 3 次驱动调用
- When 查 `hardware_event`
- Then 3 条记录，含耗时
- 验证方式：自动

**AC6（不记录隐私原文）**
- Given 一次身份证读取
- When 查 `hardware_event`
- Then 只含摘要（如"读取成功/耗时"），不含姓名身份证号
- 验证方式：自动

**AC7（超时按失败）**
- Given 驱动配置为超时
- When 调用
- Then 抛 E4002，`retryable=true`
- 验证方式：自动（mock）

---

## 6. 异常码

| 码 | 场景 | 可重试 |
|---|---|---|
| E4001 | 设备离线/缺纸 | 是 |
| E4002 | 驱动/Agent 不可达或超时 | 是 |

---

## 7. 失败恢复

| 失败点 | 恢复方式 |
|---|---|
| Agent/驱动崩溃 | F98 看门狗自动重新初始化驱动，未完成任务不丢 |
| 设备离线 | 转为 OFFLINE，恢复后由心跳置回 ONLINE |
| 超时 | 可重试，重试上限后放弃并告警 |

---

## 8. 契约缺口

（无）
