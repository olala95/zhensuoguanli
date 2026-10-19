# M08 处置执行

> 阶段：第三阶段
> 依赖：M02（Visit）、M01（患者快照）、M03（诊断/医嘱）、M06（皮试可作为处置）、M14、M12
> 被依赖：M09（处置收费 F58/F60）、M15 F120（医生工作量：处置执行数）

---

## 1. 模块职责

非药品类诊疗操作的**开立 → 执行 → 记录**全链路：换药、注射、输液、雾化、理疗、清创等，以及中医特有操作（针灸、推拿、拔罐、艾灸、刮痧、穴位贴敷等）。

**与 M06 的边界**：药品只能走处方（F34/F111）；处置只承载**非药品操作**。皮试（M06 F36）可作为处置执行，但皮试结果仍归 M06 记录——M08 只记录"做了这个操作"。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `treatment_item` | 处置项目目录（编码、名称、类别、计价单位、单价、是否需执行记录） |
| `treatment_package` | 处置组套（如"换药包"） |
| `treatment_package_item` | 组套明细 |
| `treatment_execution` | 执行记录主表，`TE{yyyyMMdd}{4位}` |
| `treatment_process` | 治疗过程记录（分次/分步骤留痕） |
| `tcm_treatment_record` | 中医治疗执行：穴位、手法、留针时长、反应（F116） |
| `treatment_status_log` | 执行状态流转日志（03 §2.1 新增） |

---

## 3. 模块级规则

**R-M08-1 处置必须挂 Visit。** 每条执行记录绑定 `visit_id`（+ 患者快照），不存在无就诊的处置。

**R-M08-2 状态机（契约 §2.11）。**
```
SCHEDULED → IN_PROGRESS → COMPLETED
SCHEDULED → CANCELLED
IN_PROGRESS → INTERRUPTED（**必须填原因**）
INTERRUPTED → IN_PROGRESS | CANCELLED
```
终态：`COMPLETED` / `CANCELLED`。状态变更走状态机，写 `treatment_status_log`，禁止裸 UPDATE。

**R-M08-3 取消与中断必须写原因。** 取消/中断属高风险动作，写 `audit_high_risk`（涉及已收费处置时尤其重要，是退款依据）。

**R-M08-4 价格快照。** 执行记录保存开单时的项目名与单价快照，**后续调价不追溯**（与 M06 F112 R7 同族规则）。

**R-M08-5 金额整数分。** 单价 `price_fen`，数量 `quantity`，行小计 `round(price_fen × quantity)`（契约 §3.1 同规则）。

**R-M08-6 中医操作必填结构化字段。** 针灸类必须记录穴位与留针时长；手法类记录手法与部位；出现异常反应必须记录（F116 R3）。

**R-M08-7 执行人必记。** 每次执行记录执行人与执行时间（`occurred_at`），用于工作量统计与责任追溯。

**R-M08-8 组套展开为明细。** 组套执行时展开为多条执行记录（或一个执行单多条明细），**每条都可独立追踪状态**。

**R-M08-9 只读消费上游。** M09 收费只读本模块数据（执行记录 + 单价快照），**不直写本模块表**；处置作废/取消由本模块状态机完成。

**R-M08-10 埋点就位。** 执行记录带 `occurred_at`（执行完成时间）、`operator_id`、`charge_flag`（是否已计费），供 M15 F120 工作量统计。

---

## 4. 状态机引用

见 R-M08-2（契约 §2.11）。

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `treatment.listByVisit(visit_id)` | 取该就诊的处置执行记录（含金额快照） | M09 F58 账单生成 |
| `treatment.packages()` | 组套查询与展开 | UI / M09 |
| `import.upsert(kind, rows)` | 基础数据导入（处置项目） | M14 F128 |
| `patient.reassign(from, to)` | 患者合并改挂 | M01 F04 |
| `treatment.cancelByVisit(visit_id)` | Visit 作废时取消未执行处置 | M02 F125 |

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F52 | 处置项目 | FEATURE | P0 | S |
| F53 | 处置组套 | FEATURE | P1 | S |
| F54 | 处置执行（开立） | FEATURE | P0 | M |
| F55 | 执行记录（执行/完成/中断） | FEATURE | P0 | M |
| F56 | 治疗过程 | FEATURE | P1 | M |
| **F116** | 中医治疗执行记录 | FEATURE | P0 | M |
