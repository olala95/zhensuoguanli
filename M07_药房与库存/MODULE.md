# M07 药房与库存

> 阶段：第三阶段
> 依赖：M06（`drug` / `herb` / `prescription` 及其明细，只读 + 经 `mark_dispensed` 回写状态）、M01、M02、M14、M12
> 被依赖：M09（退药退款 F63）、M10（药袋标签 F69）、M15（进销存报表 F121）

---

## 1. 模块职责

**两条库存链路**：

| 链路 | 对象 | 库存单位 | 出库量算法 |
|---|---|---|---|
| 成药 | `drug` + 批次 | 最小发药单位（盒/瓶/片），`REAL` 支持拆零 | 处方明细 `quantity` |
| 饮片 | `herb` + 批次 | **克（g）**（契约 §8） | `剂量(g) × 付数`，含损耗率 |

覆盖：入库（F40）、批次（F41）、库存（F42）、流水（F43）、拆零（F44/F45）、退药（F46）、报损（F47）、盘点（F48）、效期（F49）、预警（F50）、成药发药（F51）、饮片调剂（F114）、中药发药（F115）。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `supplier` | 供应商 |
| `stock_in` | 入库单，`IN{yyyyMMdd}{4位}` |
| `stock_batch` | 批次（**一个入库明细行 = 一个批次**），`BT{yyyyMMdd}{6位}` |
| `stock` | 成药库存（药品 + 批次） |
| `herb_stock` | 饮片库存（饮片 + 批次，单位 g） |
| `stock_transaction` | 库存流水（**只允许 INSERT**，F43） |
| `stock_split` | 拆零记录与尾差（F44/F45） |
| `drug_return` / `drug_return_item` | 退药单与明细（F46） |
| `stock_loss` / `stock_loss_item` | 报损单与明细（F47） |
| `stock_check` / `stock_check_item` | 盘点单与明细（F48） |
| `stock_expiry_alert` | 效期与库存预警（F49/F50） |
| `dispense` | 成药发药记录（F51） |
| `herb_dispense` | 中药调配与发药（F115） |
| `stock_batch_status_log` | 批次状态流转日志（F41 新增） |
| `stock_check_status_log` | 盘点单状态流转日志（F48 新增） |

---

## 3. 模块级规则

**R-M07-1 一切库存变动走流水。** 入库、出库（发药）、退药、报损、盘点调整、拆零——**每一种数量变动都必须写 `stock_transaction`**，且流水可追溯到单据（`ref_type` + `ref_id`）。不存在"只改 `stock.qty` 不写流水"的路径。

**R-M07-2 流水与库存同事务。** `stock_transaction` 与 `stock` / `herb_stock` 的增减在**同一个 `db.tx()`** 内完成（故障演练 #3：发药中途断电不得少扣或多扣）。

**R-M07-3 批次状态门禁。** `EXPIRED` 与 `LOCKED` 状态的批次**禁止出库**（契约 §2.8）；发药选批时自动跳过。

**R-M07-4 出库不允许负库存。** 可用量不足 → `E3002` 拒绝，**不静默放行**（R6）。盘点差异导致的负数单独走调整流水，不绕过。

**R-M07-5 饮片单位恒为克。** 入库可用 kg 录入，系统换算为 g 存储（契约 §8）；`herb_stock.qty_g` 为克。

**R-M07-6 饮片出库量 = 剂量 × 付数。** 允许配置损耗率 `herb.loss_rate`（千分比，默认 0），扣减量 = `round(剂量 × 付数 × (1 + 损耗‰))`。损耗不计入患者计费，只影响库存。

**R-M07-7 跨模块回写只走服务接口。** 发药成功后调 `m06_prescription.mark_dispensed(conn, rx_id)`，**禁止裸 UPDATE `prescription.status`**（R2/R5）。

**R-M07-8 退药必须有来源处方。** 退药数量 ≤ 该处方明细**已发数量**；退药回库生成正向流水，不动原出库流水。

**R-M07-9 盘点冻结。** 盘点进入 `COUNTING` 时相关批次置 `LOCKED`，审批生成调整流水后解锁（契约 §2.12）。

**R-M07-10 金额一律整数分。** 成本单价用毫元 `unit_cost_milli`，流水金额 `amount_fen = round(qty × unit_cost_milli / 10)`（契约 §3）。

**R-M07-11 拆零尾差不隐式吸收。** 拆零单位价 = 整包装价 ÷ 包装数量，尾差显式记入 `stock_split.tail_diff`（契约 §3.3）。

**R-M07-12 报表埋点就位。** 每次出库流水记录 `unit_cost_milli` / `amount_fen` / `occurred_at`，供 M15 F121 进销存月报（此时 M15 未实现，字段必须先写）。

---

## 4. 状态机引用

```
stock_batch: NORMAL → DEPLETED | EXPIRED | LOCKED      （契约 §2.8）
             LOCKED → NORMAL
stock_check: DRAFT → COUNTING → ADJUSTING → APPROVED   （契约 §2.12）
             DRAFT → CANCELLED
drug_return: DRAFT → CONFIRMED | VOID
stock_loss : DRAFT → CONFIRMED | VOID
stock_in   : DRAFT → CONFIRMED | VOID
```

> `drug_return` / `stock_loss` / `stock_in` 未列入契约 §2 的 13 个状态机，按「确认即生效、确认后只能作废」的最小三态实现，**确认动作写状态日志与高风险审计**。

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `dispense.by_prescription(rx_id)` | 成药发药（内部 F51） | M09 / M10 |
| `herb_dispense.by_prescription(rx_id)` | 中药发药（内部 F115） | M09 / M10 |
| `drug_return.create(prescription_id, items)` | 退药（**生成退款依据**） | M09 F63 |
| `stock.available(drug_id)` / `herb_stock.available(herb_id)` | 可用量查询 | UI / M10 |
| `import.upsert(kind, rows)` | 基础数据导入（供应商） | M14 F128 |
| `patient.reassign(from, to)` | 患者合并改挂（不改库存历史） | M01 F04 |

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F40 | 入库 | FEATURE | P0 | M |
| F41 | 批次 | FEATURE | P0 | M |
| F42 | 库存 | FEATURE | P0 | S |
| F43 | 库存流水 | FEATURE | P0 | M |
| F44 | 拆零 | FEATURE | P0 | M |
| F45 | 拆零计价/尾差 | FEATURE | P0 | S |
| F46 | 退药 | FEATURE | P0 | M |
| F47 | 报损 | FEATURE | P0 | S |
| F48 | 盘点 | FEATURE | P0 | **L（拆 F48.1–F48.4）** |
| F49 | 有效期 | FEATURE | P0 | S |
| F50 | 库存预警 | FEATURE | P1 | S |
| F51 | 发药 | FEATURE | P0 | M |
| **F114** | 饮片库存与调剂出库 | FEATURE | P0 | M |
| **F115** | 中药发药（代煎/自煎） | FEATURE | P0 | M |
