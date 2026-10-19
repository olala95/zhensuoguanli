# M02 就诊中心

> 阶段：第一阶段
> 依赖：M01（患者）、M14（用户上下文、编号）、M12（审计）
> 被依赖：**M03 病历**（病历挂在 Visit 下）、M06 处方、M05 检验、M08 处置、M09 收费、M11 归档、M15 报表

---

## 1. 模块职责

管理一次就诊（Visit）的生命周期：创建、排队、接诊、挂单留观、恢复、作废。Visit 是所有临床与收费单据的**聚合根**。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `visit` | 就诊主表（就诊号、患者、医生、就诊类型、状态） |
| `visit_status_log` | 状态流转日志 |
| `visit_queue` | 候诊队列 |
| `visit_suspension` | 留观/挂单记录 |
| `visit_void` | 作废记录与原因 |

---

## 3. 模块级规则

**R-M02-1 Visit 是聚合根。** 病历、处方、检验申请、处置、账单全部挂在 Visit 下。**不存在无 Visit 的临床单据**（补录场景也必须创建一个 Visit）。

**R-M02-2 就诊号按日流水。** `V{yyyyMMdd}{3位}`，每日重置，事务内取号。

**R-M02-3 状态机严格。** 见 `03_GLOBAL_CONTRACTS` §2.2。尤其：
- `COMPLETED` 是终态，退费不改 Visit 状态
- `VOID` 必须填原因，写 `audit_high_risk`
- `SUSPENDED`（留观/挂单）可恢复到 `IN_PROGRESS`

**R-M02-4 一位患者同时只能有一个进行中的 Visit。** 创建 Visit 时若该患者已有 `WAITING`/`IN_PROGRESS`/`SUSPENDED` 的 Visit，提示并引导复用。**允许强制新建**（如确为不同病症），但记录原因。

**R-M02-5 接诊医生必填。** Visit 必须记录接诊医生（审计与工作量统计需要）。创建时可指定，接诊时可改（改动作写审计）。

**R-M02-6 挂单不阻塞他人。** 挂单（留观）后患者仍在队列中标记为挂单，医生可继续接诊下一位；不是全局阻塞。

**R-M02-7 完成 Visit 的前置检查。** 尝试标记 `COMPLETED` 时检查：
- 是否存在未确认的病历草稿（提示但不强制）
- 是否存在未确认或未发药的处方（**强制提示**）
- 是否存在未收费的账单（**强制提示**）
用户可选择强制完成，但需填原因并写审计。

**R-M02-8 作废限制。** 已收费/已发药的 Visit 不可直接作废，必须先走退款（M09）与退药（M07），再作废。

**R-M02-9 患者快照。** Visit 记录患者姓名/性别/出生日期的当时快照（配合 M01 快照机制）。

**R-M02-10 初诊/复诊判定。** Visit 创建时根据该患者是否有历史 `COMPLETED` Visit 标记 `is_first_visit`，用于 M15 统计（契约 §7）。

---

## 4. 状态机引用

```
创建 → WAITING（候诊）
WAITING → IN_PROGRESS（接诊）| VOID
IN_PROGRESS → SUSPENDED（挂单/留观）| COMPLETED | VOID
SUSPENDED → IN_PROGRESS（F09 恢复）| VOID
COMPLETED / VOID → 终态
```

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `visit.get(id)` / `visit.getByNo(no)` | 取就诊 | 全部 |
| `visit.current(patientId)` | 取该患者进行中的 Visit | M01 / M06 |
| `visit.assertActive(id)` | 校验 Visit 处于可开单状态 | M03 / M05 / M06 / M08 |
| `visit.patientSnapshot(id)` | 取快照 | M06 / M09 / M11 |
| `patient.reassign(from, to)` | **患者合并改挂**（F04 用） | M01 |
| `visit.listByDate(date)` | 当日就诊列表 | M15 / M09 |

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F05 | 创建 Visit | FEATURE | P0 | M |
| F06 | 就诊队列 | FEATURE | P0 | M |
| F07 | 接诊工作台 | FEATURE | P0 | **L（拆 F07.1–F07.4）** |
| F08 | 留观/挂单 | FEATURE | P0 | S |
| F09 | Visit 恢复 | FEATURE | P0 | S |
| F125 | 就诊作废与退号 | FEATURE | P0 | S |
