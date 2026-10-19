# M03 临床病历

> 阶段：第一阶段
> 依赖：M02（Visit 上下文）、M01（患者）、M14、M12
> 被依赖：M06 处方（诊断驱动）、M11 归档、M04 AI（草稿落库）、M15 报表

---

## 1. 模块职责

一次就诊的完整临床记录：主诉、现病史、既往史/个人史、过敏史、生命体征、查体、辅助检查记录、诊断、医嘱、**中医四诊与辨证**，以及病历确认签名。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `record` | 病历主表（关联 visit，含状态与版本） |
| `record_chief_complaint` | 主诉 |
| `record_present_illness` | 现病史 |
| `record_history` | 既往史/个人史/家族史 |
| `record_allergy` | 过敏史 |
| `vital_sign` | 生命体征 |
| `physical_exam` | 查体 |
| `record_exam_ref` | 辅助检查引用（指向 M05 `lab_result`） |
| `diagnosis` | 诊断（含 ICD-10 与中医证型） |
| `medical_order` | 非药品医嘱 |
| `tcm_four_diagnosis` | 中医四诊（F108） |
| `tcm_syndrome` | 辨证论治（F109） |
| `record_template` | 病历模板与常用语（F126） |
| `record_signature` | 确认签名 |

---

## 3. 模块级规则

**R-M03-1 一份 Visit 一份病历。** 一个 Visit 对应一份主病历（可有多个版本）。不允许同一 Visit 有多份并列主病历。

**R-M03-2 确认后不可直接编辑。** `CONFIRMED` 之后内容锁定，修改走 M11 F77 留痕修改（新建版本 + 记录原因 + 原版本保留）。

**R-M03-3 过敏史是安全红线。** 过敏记录必须在工作台顶部常驻展示，并在开处方时参与规则校验（M06 F35/F113 消费）。**没有过敏史也要有明确标记（"已确认无过敏"）**，不能是空值导致误判。

**R-M03-4 诊断驱动处方。** 处方开立时校验：至少有一条诊断或明确的"待查"标记。无诊断开方 → 提示但不强制（急诊场景允许）。

**R-M03-5 中医四诊必填项（中医诊所场景）。** 若开启了中医模式（配置），病历需包含：望、闻、问、切至少记录舌象与脉象；证型必填。

**R-M03-6 生命体征可多次记录。** 一次 Visit 可记录多组生命体征（如输液前后），每组带时间。

**R-M03-7 AI 草稿必须有来源标记。** AI 生成的内容落库时标记 `source=AI` 与确认人；医生在此基础上编辑后标记 `AI_MODIFIED`。**归档时该标记不可消除**（合规需要区分 AI 生成与医生确认）。

**R-M03-8 辅助检查只存引用。** 病历中不复制检验结果原文，只存对 M05 `lab_result` 的引用，避免数据双写不一致。

**R-M03-9 医嘱只承载非药品。** 药品一律走 M06 处方；医嘱只承载处置、检验、用药说明、复诊建议、生活医嘱等。

**R-M03-10 模板不替代个体化。** 模板（F126）用于提速，套用后**必须可编辑**，且不允许"整份病历完全套用未修改"直接确认（提示检查）。

---

## 4. 状态机引用

```
DRAFT → CONFIRMED（F19 确认签名）
CONFIRMED → ARCHIVED（M11 F79）
ARCHIVED → SEALED / UNSEALED（M11 F81/F82）
```

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `record.getByVisit(visitId)` | 取病历 | M02 / M11 |
| `record.saveDraft(visitId, content)` | 保存草稿（AI 与手工共用） | M04 / F07 |
| `record.confirm(recordId, signer)` | 确认签名 | F19 |
| `allergy.list(patientId)` | 过敏史查询（含"已确认无过敏"标记） | M02 工作台、M06 处方规则 |
| `diagnosis.list(visitId)` | 诊断列表 | M06 / M15 |
| `tcm.syndrome(visitId)` | 证型 | M06 中药处方 |
| `ai.draft.apply(visitId, draft)` | **AI 草稿应用接口（第一阶段冻结）** | M04 |

> **`ai.draft.apply` 必须在第一阶段冻结**，M04 只是第二阶段的实现方，不应回头改 M03。

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F10 | 主诉 | FEATURE | P0 | S |
| F11 | 现病史 | FEATURE | P0 | S |
| F12 | 既往史/个人史 | FEATURE | P0 | S |
| F13 | 过敏史 | FEATURE | P0 | M |
| F14 | 生命体征 | FEATURE | P0 | S |
| F15 | 查体 | FEATURE | P0 | S |
| F16 | 辅助检查记录 | FEATURE | P0 | S |
| F17 | 诊断 | FEATURE | P0 | M |
| F18 | 医嘱 | FEATURE | P0 | M |
| F19 | 病历确认 | FEATURE | P0 | M |
| **F108** | 中医四诊记录 | FEATURE | P0 | M |
| **F109** | 辨证论治 | FEATURE | P0 | M |
| **F126** | 病历模板与常用语 | FEATURE | P1 | M |
