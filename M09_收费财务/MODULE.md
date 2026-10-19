# M09 收费财务

> 阶段：第三阶段
> 依赖：M02（Visit）、M01（患者快照）、M06（处方计价，只读）、M07（退药单，只读）、M08（处置执行，只读）、M05（检验申请，只读）、M14、M12
> 被依赖：M10（收费凭条 F68）、M15 F119（日营收）/ F120（客单价）

---

## 1. 模块职责

把一次就诊产生的**所有应付项**汇总成账单并收款：处方（成药 / 饮片）、处置、检验、挂号诊查费等；支持退款、退药退款、红字冲正与日结。

**明确不做**（01 §3）：医保结算、欠费挂账、应收账款、会员储值卡、折扣卡。因此**不存在"部分支付"中间态**——要么全额付清，要么退回 `PENDING`。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `charge_item` | 收费项目目录（挂号费、诊查费、耗材等**非药品非处置**的自有项目） |
| `bill` | 账单主表，`B{yyyyMMdd}{4位}` |
| `bill_item` | 账单明细（**只允许 INSERT**，冲正走负数行） |
| `payment` | 支付记录（渠道、金额、时间） |
| `refund` / `refund_item` | 退款单与明细，`RF{yyyyMMdd}{4位}` |
| `reversal` | 红字冲正，`RV{yyyyMMdd}{4位}` |
| `day_settlement` | 日结，`DS{yyyyMMdd}{2位}` |
| `bill_status_log` / `payment_status_log` | 状态流转日志（03 §2.1 新增） |

> 药品金额不重复定义：成药取 M06 `prescription_pricing`，中药取 M06 计价结果，处置取 M08 执行记录金额快照，检验取 M05 申请明细价。**M09 只汇总，不重算。**

---

## 3. 模块级规则

**R-M09-1 账单是汇总，不是重算。** 各来源金额以上游计价结果为准（M06/M08/M05），M09 不重新计算药品金额（避免口径不一致）。

**R-M09-2 无部分支付。** 账单要么全额付清转 `PAID`，要么保持/退回 `PENDING`（契约 §2.5）。

**R-M09-3 明细行不可删除。** `bill_item` 只允许 INSERT；退款与冲正通过**新增负数行**或退款明细表达，原行永不改动（04 §3）。

**R-M09-4 退款必须指定明细行。** 退款金额 ≤ 该行已付金额，且逐行指定（契约 §3.4）。

**R-M09-5 日结后当日账单锁定。** 日结后当日账单不可修改，只能走红字冲正 F62（E3006）。

**R-M09-6 冲正生成负数行，不改原行。** 红冲针对**已日结**或**已收费**的账单，新增负数明细行并记 `reversal`，原账单与支付记录保留（契约 §3.4）。

**R-M09-7 跨模块只读。** M09 读 M06/M07/M08/M05 数据，**禁止直写**这些模块的表；退药退款只消费 M07 已确认的退药单（R2）。

**R-M09-8 支付方式多笔可混合。** 一次账单可有多笔支付（现金 + 微信），但**合计必须等于应收**（全额付清才转 PAID）。

**R-M09-9 金额整数分。** 全部 `INTEGER` 分，舍入只在行项目级（契约 §3）。

**R-M09-10 高风险审计。** 退款、冲正、作废、日结均写 `audit_high_risk`，且必须填原因。

**R-M09-11 埋点就位。** `payment.paid_at`（收费时间，营收归集口径）、退款标记、作废标记、批次成本（M07 已埋）——M15 未实现但字段先写（03 §7）。

**R-M09-12 幂等。** 支付、退款、冲正均支持 `idempotent_key`，防重复扣款/退款（故障演练 #2）。

---

## 4. 状态机引用

```
bill   : PENDING → PAID | VOID                       （契约 §2.5）
         PAID → PARTIAL_REFUNDED → FULLY_REFUNDED
payment: INIT → SUCCESS | FAILED                     （契约 §2.6）
         SUCCESS → REFUNDED | REVERSED
refund : DRAFT → SUCCEEDED | FAILED | VOID
```

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `bill.createByVisit(visit_id)` | 汇总生成账单 | UI / M02 |
| `bill.pay(bill_id, payments)` | 收费 | UI |
| `refund.create(bill_id, items)` | 退款（逐行） | UI |
| `refund.byDrugReturn(return_id)` | 退药退款（消费 M07 退药单） | M07 F46 → 本模块 |
| `reversal.create(bill_id, items)` | 红字冲正（日结后） | UI |
| `settlement.close(biz_date)` | 日结 | UI |
| `import.upsert(kind, rows)` | 基础数据导入（收费项目） | M14 F128 |
| `patient.reassign(from, to)` | 患者合并改挂 | M01 F04 |

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F57 | 收费项目 | FEATURE | P0 | S |
| F58 | 账单 | FEATURE | P0 | **L（拆 F58.1 汇总 / F58.2 生成 / F58.3 作废）** |
| F59 | 收费 | FEATURE | P0 | M |
| F60 | 收费明细 | FEATURE | P0 | S |
| F61 | 退款 | FEATURE | P0 | M |
| F62 | 红字冲正 | FEATURE | P0 | M |
| F63 | 退药退款 | FEATURE | P0 | M |
| F64 | 日结 | FEATURE | P0 | M |
| F65 | 财务查询 | FEATURE | P1 | M |
