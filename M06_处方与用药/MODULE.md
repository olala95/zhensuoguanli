# M06 处方与用药

> 阶段：第二阶段
> 依赖：M01（患者与快照）、M02（Visit）、M03（诊断、过敏、辨证）、M04（AI 建议）、M14、M12
> 被依赖：M07 发药与库存、M09 收费、M10 打印、M15 报表

---

## 1. 模块职责

药品与饮片资料、处方开立（成药 / 饮片 / 颗粒）、处方规则校验、皮试、处方确认签名、作废、用药指导。

**本模块是安全风险最高的业务模块**，规则校验必须硬保障，AI 建议不豁免。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `drug` | 药品资料（成药/西药） |
| `herb` | 中药饮片资料（F110） |
| `prescription` | 处方主表（类型 WEST/HERB/GRANULE） |
| `prescription_item` | 成药明细 |
| `herb_prescription_item` | 饮片明细（F111） |
| `prescription_pricing` | 计价结果与尾差（F112） |
| `prescription_rule_hit` | 规则命中记录 |
| `herb_compatibility` | 配伍禁忌表（十八反十九畏） |
| `skin_test` | 皮试记录 |
| `prescription_template` | 处方模板与经验方（F127） |
| `medication_guide` | 用药指导 |

---

## 3. 模块级规则

**R-M06-1 三类处方共用处方号序列。** 成药 / 饮片 / 颗粒共用 `R{yyyyMMdd}{4位}`，类型在字段中（契约 §1.2）。

**R-M06-2 药品只走处方。** 药品（含饮片、颗粒）只能通过处方开立，**医嘱（M03 F18）不承载药品**。

**R-M06-3 过敏是强制拦截，不是提醒。** 命中过敏原的处方**强制拦截**，需医生显式说明理由才能继续，且写 `audit_high_risk`。这是本模块唯一的强制阻断规则。

**R-M06-4 AI 建议不豁免规则。** 来自 M04 F24 的建议，必须完整走本模块规则校验（F35/F113），不因来源而放宽。

**R-M06-5 确认后不可直接改。** `CONFIRMED` 后处方不可编辑；已发药不可作废（只能退药 M07 F46）。

**R-M06-6 皮试前置。** 需要皮试的药品，未记录皮试结果或结果阳性 → **不得进入 CONFIRMED**。

**R-M06-7 剂量与配伍：提醒为主，毒性饮片例外。** 剂量超限、配伍禁忌 → **提醒不阻断**（医生处方权优先）；**毒性饮片超限量 → 强制二次确认 + 高风险审计**。

**R-M06-8 诊断驱动。** 开方时若无诊断，提示但不阻断；急诊场景允许，写审计。

**R-M06-9 患者快照固化。** 处方保存患者姓名/性别/年龄快照，不随患者主数据变更。

**R-M06-10 计价规则集中。** 成药与饮片计价规则见契约 §3.1 / §3.2，**不在本模块自定义**。

**R-M06-11 中药处方必填项。** 饮片处方必须含：每味脚注、煎法、服法、付数（契约 §8）。

**R-M06-12 抗生素与注射剂标记。** 药品资料标记是否为抗生素；处方记录给药途径。用于 M15 F122 统计（内部管理用，不做监管上报）。

---

## 4. 状态机引用

```
DRAFT → PENDING（提交待确认）
PENDING → CONFIRMED（F37 确认签名）| VOID（F38 作废）
CONFIRMED → DISPENSED（M07 F51/F115 发药）| VOID（仅未发药）
DISPENSED → PARTIAL_RETURNED（退药）→ FULLY_RETURNED
```

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `prescription.getByVisit(visitId)` | 取处方 | M02 / M09 |
| `prescription.validate(items, patientId)` | 规则校验（供 AI 建议预检） | M04 F24 |
| `drug.search(keyword)` / `herb.search(keyword)` | 药品搜索 | M07 / UI |
| `import.upsert(rows)` | 基础数据导入（药品/饮片） | M14 F128 |
| `voidByVisit(visitId)` | Visit 作废时作废未确认处方 | M02 F125 |
| `patient.reassign(from, to)` | 患者合并改挂 | M01 F04 |
| `allergy.check(drugId, patientId)` | 过敏校验（消费 M03 数据） | 内部 F35 |

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F32 | 药品资料 | FEATURE | P0 | M |
| F33 | 药品搜索 | FEATURE | P0 | S |
| F34 | 处方（成药） | FEATURE | P0 | **L（拆 F34.1–F34.4）** |
| F35 | 处方规则 | FEATURE | P0 | **L（拆 F35.1–F35.4）** |
| F36 | 皮试 | FEATURE | P0 | M |
| F37 | 处方确认 | FEATURE | P0 | M |
| F38 | 处方作废 | FEATURE | P0 | S |
| F39 | 用药指导 | FEATURE | P1 | S |
| **F110** | 中药饮片资料 | FEATURE | P0 | M |
| **F111** | 中药饮片处方 | FEATURE | P0 | **L（拆 F111.1–F111.4）** |
| **F112** | 中药处方计价 | FEATURE | P0 | M |
| **F113** | 中药处方规则 | FEATURE | P0 | M |
| **F127** | 处方模板与经验方 | FEATURE | P1 | M |
