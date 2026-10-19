# 03_GLOBAL_CONTRACTS · 全局契约

> **本文是所有模块唯一引用的规则来源。** 任何模块不得重新定义本文已定义的东西。
> 变更走 `00_AI_RULES` §7 的契约变更流程。

---

## 1. 编号规则

### 1.1 通用约定

- 编号一律为**字符串**，含前缀 + 日期 + 流水，全局唯一。
- 生成器：单进程内用数据库计数表 `sys_sequence`（按 `biz_date + type` 唯一），**在事务内取号**。
- **允许跳号（事务回滚时），绝不允许重号。**
- 编号一经生成不可修改，即使对应单据作废，编号也保留（作废是状态，不是删除）。

### 1.2 编号总表

| 编号 | 格式 | 示例 | 归属模块 | 说明 |
|---|---|---|---|---|
| 患者号 | `P{yyyyMMdd}{4位}` | P202610090001 | M01 | 建档当日流水 |
| 就诊号 | `V{yyyyMMdd}{3位}` | V20261009007 | M02 | 当日流水，每日重置 |
| 处方号 | `R{yyyyMMdd}{4位}` | R202610090012 | M06 | 成药/饮片/颗粒**共用同一序列**，类型在字段中 |
| 检验申请号 | `L{yyyyMMdd}{4位}` | L202610090003 | M05 | |
| 标本条码 | `BC{yyyyMMdd}{6位}` | BC20261009000001 | M05 | Code128，外送时用 |
| 账单号 | `B{yyyyMMdd}{4位}` | B202610090015 | M09 | |
| 退款单号 | `RF{yyyyMMdd}{4位}` | RF202610090002 | M09 | |
| 冲正单号 | `RV{yyyyMMdd}{4位}` | RV202610090001 | M09 | |
| 日结号 | `DS{yyyyMMdd}{2位}` | DS2026100901 | M09 | 同一天可多次日结 |
| 入库单号 | `IN{yyyyMMdd}{4位}` | IN202610090004 | M07 | |
| 批次内码 | `BT{yyyyMMdd}{6位}` | BT20261009000012 | M07 | 系统生成，与供应商批号（原样保存）区分 |
| 盘点单号 | `CK{yyyyMMdd}{4位}` | CK202610090001 | M07 | |
| 报损单号 | `LS{yyyyMMdd}{4位}` | LS202610090001 | M07 | |
| 退药单号 | `DR{yyyyMMdd}{4位}` | DR202610090001 | M07 | |
| 处置执行号 | `TE{yyyyMMdd}{4位}` | TE202610090021 | M08 | |
| 打印任务号 | `PJ{yyyyMMddHHmmss}{3位}` | PJ20261009143022001 | M10 | 含时间戳，便于重打印定位 |
| 归档号 | `AR{yyyyMM}{4位}` | AR2026100007 | M11 | 按月归档 |
| AI 任务号 | `AI{yyyyMMddHHmmss}{6位随机}` | AI20261009143022ab12cd | M04 | |
| 备份文件名 | `clinic_{yyyyMMdd_HHmmss}_{full|incr}.dbbak` | clinic_20261009_180000_full.dbbak | M13 | |

### 1.3 单据与明细

主单据有编号，明细行**不单独编号**，用 `主表ID + 行号(line_no)` 定位。

---

## 2. 状态机总表

### 2.1 通用约定

- 状态字段禁止裸 `UPDATE`，必须走状态机。
- 每次转换写入 `*_status_log`（记录：单据ID、from、to、操作人、时间、原因）。
- 非法转换 → 拒绝 + 异常码 `E3xxx` + 写高风险审计（若涉及已确认单据）。

### 2.2 Visit（就诊）

```
创建 → WAITING（候诊）
WAITING → IN_PROGRESS（接诊）| VOID（作废）
IN_PROGRESS → SUSPENDED（留观/挂单）| COMPLETED（完成）| VOID
SUSPENDED → IN_PROGRESS（F09 恢复）| VOID
COMPLETED → （终态，不可变）
VOID → （终态，不可变）
```
- `COMPLETED` 之后如需退费，走 M09 退款流程，**不改 Visit 状态**。
- `VOID` 必须填写作废原因，写 audit_high_risk。

### 2.3 病历 record

```
DRAFT → CONFIRMED（F19 确认签名）
CONFIRMED → ARCHIVED（F79 归档）
ARCHIVED → SEALED（F81 封存）
SEALED → UNSEALED（F82 解封）
UNSEALED → ARCHIVED（F79 重新归档，开**新**归档号；更正后可再次封存）
```
> **修订（2026-10-14）**：原「UNSEALED = 终态」使解封后无法闭环——解封的目的就是更正，
> 更正后必须能重新归档再封存。现放开 `UNSEALED → ARCHIVED`，但**不可原地重复封存**：
> 同一归档周期内 `record_seal` 与 `record_archive` 次数对应，再次封存必须先开新归档号，
> 使「封存—解封—再封存」留下成对的、可审计的痕迹，而不是原地反复冻结。
```
```
- **`CONFIRMED` 之后不得直接编辑内容**。修改走 F77：新建版本 + 记录修改原因与时间，原版本完整保留，Hash 链延续。
- 草稿可删除（仅限 DRAFT 且创建者本人）。

### 2.4 处方 prescription

```
DRAFT → PENDING（提交待确认）
PENDING → CONFIRMED（F37 确认签名）| VOID（F38 作废）
CONFIRMED → DISPENSED（F51/F115 发药）| VOID
DISPENSED → PARTIAL_RETURNED（F46 部分退药）| FULLY_RETURNED
CONFIRMED → VOID（仅未发药）
PARTIAL_RETURNED → FULLY_RETURNED
```
- **已发药的处方不得作废**，只能走退药（F46）+ 退药退款（F63）。
- 作废必须填原因，写 audit_high_risk。
- 皮试结果未记录/为阳性时，不得进入 CONFIRMED（F36 前置）。

### 2.5 账单 bill

```
PENDING（待收费）→ PAID（已收费）| VOID（作废）
PAID → PARTIAL_REFUNDED（部分退款）| FULLY_REFUNDED（全额退款）
PARTIAL_REFUNDED → FULLY_REFUNDED
FULLY_REFUNDED / VOID → （终态）
```
- 无欠费挂账：不存在"部分支付"中间态，要么全额付清要么退回 PENDING。
- **日结（F64）之后，当日账单不可修改**，只能走红字冲正 F62。

### 2.6 支付 payment

```
INIT → SUCCESS | FAILED
SUCCESS → REFUNDED（已退款）| REVERSED（已冲正）
FAILED → （终态，可重新发起新支付）
```

### 2.7 打印任务 print_job

```
PENDING → PRINTING → SUCCESS | FAILED
FAILED → RETRYING（F71）→ PRINTING
FAILED → ABANDONED（超过重试上限或人工放弃）
```
- **重打印（F70）不产生新任务号**，在原任务上增加 `reprint_count` 并写重打标记。

### 2.8 库存批次 stock_batch

```
NORMAL → DEPLETED（用完）| EXPIRED（过期）| LOCKED（盘点冻结）
LOCKED → NORMAL（盘点结束解锁）
EXPIRED / DEPLETED → （终态）
```
- `EXPIRED` 与 `LOCKED` 状态下禁止出库。

### 2.9 检验申请 lab_order（外送）

```
DRAFT → ORDERED（已开单收费）→ SAMPLED（已采样）→ DELIVERED（已送检）
      → REPORTED（已回报）→ REVIEWED（医生已阅）
ORDERED / SAMPLED → CANCELLED（作废退费）
```

### 2.10 AI 任务 ai_task

```
CREATED → RUNNING → SUCCEEDED | FAILED
FAILED → RETRYING → RUNNING
FAILED → ABANDONED（超重试上限）
```
- AI 结果**不得直接写入病历**，只能作为草稿，经医生确认后落库并标记来源。

### 2.11 处置执行 treatment_execution

```
SCHEDULED（已开立）→ IN_PROGRESS（执行中）→ COMPLETED（完成）
SCHEDULED → CANCELLED（取消）
IN_PROGRESS → INTERRUPTED（中断，需记录原因）
INTERRUPTED → IN_PROGRESS | CANCELLED
```

### 2.12 盘点单 stock_check

```
DRAFT → COUNTING（盘点中，批次 LOCKED）→ ADJUSTING（差异待处理）
      → APPROVED（已审批，生成调整流水）→ （终态）
DRAFT → CANCELLED
```

### 2.13 备份任务 backup_job

```
PENDING → RUNNING → SUCCEEDED | FAILED
FAILED → RETRYING → RUNNING
FAILED → ABANDONED（告警，写高风险审计）
```

---

## 3. 金额与精度

- **所有金额内部存储为整数分**（`INTEGER`）。禁止用浮点。
- 显示时 `/100`，保留 2 位。
- **舍入：ROUND_HALF_UP，只在行项目级舍入**，不在总额级二次舍入。
- 单价可支持到厘（如饮片 0.035 元/g），此时单价用 `price_milli`（毫元，1/1000 元）存储，行小计舍入到分。

### 3.1 成药计价

`行小计(分) = round(单价(分) × 数量)`，`处方金额 = Σ 行小计`。

### 3.2 中药饮片计价（F112）

```
每味小计(分) = round(单价(毫元/g) × 剂量(g) × 付数 / 10)
饮片合计 = Σ 每味小计
处方金额 = 饮片合计 + 代煎费(分) + 包装费(分)
```
- 代煎费按付数计或按处方计，由系统配置决定。
- **付数变更必须重算全部行小计**，不得按比例缩放总额。

### 3.3 拆零与尾差（F45）

- 拆零按"整包装价 / 包装数量"计算单位价，尾差计入**最后一行**。
- 尾差必须显式记录在 `stock_split.tail_diff`，不得隐式吸收。

### 3.4 退款与冲正

- 退款金额 ≤ 已付金额，且必须指定到明细行。
- 红字冲正（F62）生成负数明细行，不修改原行。

---

## 4. 时间与软删除

### 4.1 时间

- 存储格式：本地时间字符串 `YYYY-MM-DD HH:MM:SS`（单机单时区，不引入 UTC 转换复杂度）。
- 业务表区分两个时间：
  - `occurred_at`：**业务发生时间**（如收费时间、发药时间），报表归集以它为准。
  - `created_at`：记录写入时间。
- **审计时间一律取系统时间，不接受任何外部传入。**
- F88 时间异常：检测到系统时间回拨或跳变 > 5 分钟 → 记录 `audit_time_anomaly` + 告警；业务时间不得早于该单据上一条状态日志的时间。

### 4.2 软删除

- 业务主表一律软删除：设 `deleted_at`，不物理删行。
- 查询默认过滤 `deleted_at IS NULL`。
- **审计表、流水表（库存流水、账单明细、审计记录）禁止任何删除**，包括软删除。
- 患者资料在存在有效 Visit / 处方时不得删除（只能合并，见 F04）。

---

## 5. 审计字段标准

### 5.1 每张业务表必备字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `created_at` | TEXT | 记录写入时间，系统生成 |
| `created_by` | INTEGER | 操作用户 ID |
| `updated_at` | TEXT | 最后修改时间 |
| `updated_by` | INTEGER | 最后修改人 |
| `deleted_at` | TEXT NULL | 软删除标记 |

### 5.2 高风险业务表额外字段

处方、账单、库存、病历、检验申请需额外：
- `version` INTEGER —— 乐观锁，更新时校验，防止覆盖。
- `confirmed_by` / `confirmed_at` —— 确认人与确认时间（签名环节）。

### 5.3 审计写入要求（R4）

| 场景 | 写什么 |
|---|---|
| 任何业务写操作 | `audit_operation`：模块、动作、单据类型、单据ID、操作人、角色、时间、结果、来源（UI/AI/Hardware） |
| 字段级变更 | `audit_data_change`：表名、记录ID、字段名、before、after、操作人、时间、原因 |
| 高风险操作 | `audit_high_risk`：作废、删除、退款、冲正、解封、恢复、权限变更、配置变更 |
| 登录/锁屏/登出 | `audit_login` |
| 权限拒绝 | `audit_permission` |
| 时间异常 | `audit_time_anomaly` |

**审计表只允许 INSERT。** 应用层与数据库层双重约束（见 `04_DATA_MODEL` §约束）。

---

## 6. 异常码表

格式 `E{大类}{3位}`，必须携带：异常码 + 人话消息 + `retryable` 标记 + 上下文。

| 大类 | 范围 | 含义 |
|---|---|---|
| E1xxx | 系统 | 数据库不可用、磁盘满、文件损坏、进程异常 |
| E2xxx | 数据校验 | 必填缺失、格式错误、外键不存在、重复 |
| E3xxx | 业务规则 | 状态非法转换、库存不足、超量、皮试未做、配伍禁忌、日结后不可改 |
| E4xxx | 硬件 | 打印机离线/缺纸、读卡器失败、扫码失败、Hardware Agent 不可达 |
| E5xxx | AI | ASR 失败、超时、返回不可解析、内容不合规、配额不足 |
| E6xxx | 权限 | 未登录、无角色权限、越权访问、签名无效 |
| E7xxx | 打印 | 模板缺失、渲染失败、任务丢失、重打超限 |

**约定**：E4xxx / E5xxx 一律 `retryable=true` 且必须有降级路径；E2xxx / E6xxx 一律 `retryable=false`；E3xxx 视具体规则而定。

**已分配的异常码（模块分区，避免撞码）**

| 模块 | 区间 | 已用 |
|---|---|---|
| **通用业务** | **E3001 / E3002 / E3007** | **E3001 状态不允许该操作 / E3002 库存不足 / E3007 已确认（已签名）不可直接编辑，修改须走留痕流程** |
| M03 病历 | 复用通用 + E3021 | **E3021 引用的检验结果/检查申请不存在或已作废（F16）** |
| M06 处方 | E3020–E3046 | E3003 超量 / E3004 皮试 / E3005 配伍禁忌（**注意：E3005 属配伍禁忌，不是"已确认"**） |
| M07 药房 | E3050–E3059 | E3002 库存不足 / E3057 报损超量 |
| M08 处置 | E3060–E3063 | — |
| M09 收费 | E3070–E3079 | E3006 日结锁定 |
| M10 硬件/打印 | E4xxx / E7xxx | E4001 打印机离线 / E4002 Agent 不可达 / E7001 模板缺失 / E7002 渲染失败 |
| **M11 归档** | **E3090–E3092** | **E3090 Hash 链校验失败（拒绝归档/封存/导出）/ E3091 重复解封 / E3092 导出路径非法** |
| M15 报表 | E3080–E3081 | E3080 查询区间过大 |
| M14 导入 | E3010 | E3010 基础数据导入中，业务暂时锁定（原草案 E3004 与「皮试未做」撞码） |
| **M13 恢复** | **E3093–E3095** | **E3093 恢复后完整性检查未通过 / E3094 看门狗连续重启超限 / E3095 崩溃恢复需人工复核** |

---

## 7. 统计口径（M15 专用，第一阶段冻结）

> 这些口径决定了第一至三阶段要埋哪些字段。**字段不在第一阶段就位，第四阶段做报表时必然返工。**

| 指标 | 口径 |
|---|---|
| **营收** | `Σ payment.amount WHERE status='SUCCESS'` **减去** `Σ refund.amount WHERE status='SUCCESS'`。归集时间 = `payment.paid_at`（收费时间），**不是** Visit 创建时间 |
| **门诊量** | `COUNT(DISTINCT visit.id) WHERE visit.status='COMPLETED'`，**不含 VOID**，不含 SUSPENDED |
| **初诊 / 复诊** | 该患者在此次 Visit 之前是否存在 `status='COMPLETED'` 的 Visit；无 = 初诊 |
| **客单价** | 营收 ÷ 门诊量 |
| **医生工作量** | 按 Visit 的接诊医生归集：接诊人次、开方数、处置执行数、产生的营收 |
| **进销存成本** | 批次**移动加权平均**；期初/期末由库存流水推算，盘点差异单独列示 |
| **药品进销存月报** | 期初 → 入库 → 出库（发药）→ 退药 → 报损 → 盘点调整 → 期末，按批次维度 |
| **处方统计** | 仅统计 `status IN (CONFIRMED, DISPENSED, PARTIAL_RETURNED, FULLY_RETURNED)`；作废处方单独统计 |
| **中西药占比** | 按处方 `type` 字段（WEST / HERB / GRANULE）分组 |
| **抗生素/注射剂使用率** | 含抗生素标记或给药途径为注射的处方数 ÷ 总处方数 |
| **疾病谱 TOP** | 按 `diagnosis` 的 ICD-10 编码或中医证型分组计数 |

**报表只查只读视图，不查原始表**（见 `04_DATA_MODEL` §4）。

---

## 8. 中药特殊规则

| 项 | 规则 |
|---|---|
| 饮片库存单位 | **克（g）**；入库可用 kg 录入，系统换算为 g 存储 |
| 调剂出库量 | `剂量(g) × 付数`，允许配置损耗率 |
| 饮片处方必填 | 每味的**脚注**（先煎/后下/包煎/烊化/冲服/另煎/烊化）、**煎法**、**服法**、**付数** |
| 剂量上限 | 单味超过资料库 `max_dose_g` → F113 提醒（**不阻断**，需医生确认并记录） |
| 毒性饮片 | 资料库标记 `is_toxic`；超限量 → **强制二次确认 + audit_high_risk** |
| 配伍禁忌 | 十八反十九畏表；命中 → **仅提醒**，不阻断 |
| 默认付数 | 7 付（可配置），代煎另收代煎费 |
| 颗粒处方 | 按袋/盒计价，走成药计价规则，但仍记录中医诊断与证型 |

---

## 9. 权限与签名约定

- 权限按**角色**授予（医生 / 药师 / 收费员 / 管理员），登录时选择当前角色。
- 签名环节（病历确认 F19、处方确认 F37）记录 `signer_id + signed_at + 签名方式`，签名后计入 Hash 链。
- 管理员**无权修改已确认的病历内容**，仅可封存/解封；解封写 audit_high_risk。
- 越权访问一律拒绝并写 `audit_permission`，不静默放行。
