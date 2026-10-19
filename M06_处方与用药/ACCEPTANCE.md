# M06 处方与用药 · 模块验收

> 本文**只写跨 F 的端到端场景与故障演练**，各 F 内部的 AC 见对应 `Fxx.md`，此处不重复。
> 通过标准：全部【关键】场景必须通过，其余允许在明确记录的前提下遗留。

---

## 1. 端到端主流程

### A1 成药处方全流程【关键】

患者建档 → 就诊 → 接诊 → 填主诉与诊断 → 开成药处方（2 个药品）→ 规则校验 → 提交（PENDING）
→ 确认签名（CONFIRMED）→ 查金额

**检查点**
- 处方号格式 `R{yyyyMMdd}{4位}`，与饮片处方共用序列
- 患者快照（姓名/性别/年龄）固化在处方上，之后改患者资料不影响此处方
- 行小计 = `round(单价分 × 数量)`，处方金额 = Σ 行小计（契约 §3.1）
- 状态流转 DRAFT → PENDING → CONFIRMED 均有 `prescription_status_log`
- 确认签名记录 `confirmed_by` / `confirmed_at` / 签名方式
- CONFIRMED 后尝试编辑明细 → 拒绝（E3007）

### A2 饮片处方全流程【关键】

接诊 → 中医四诊 → 辨证 → 开饮片处方（6 味，每味带剂量与脚注）→ 付数 7、煎法、服法
→ 计价 → 提交 → 确认

**检查点**
- 每味小计 = `round(单价毫元/g × 剂量g × 付数 / 10)`（契约 §3.2）
- 处方金额 = 饮片合计 + 代煎费 + 包装费；代煎费按配置（按付数或按处方）
- 付数改为 14 → **全量重算**每味小计，不是总额 ×2（F112 R3）
- 缺付数/煎法/服法 → 拦截（F113 AC8）
- 每味脚注缺省按 `NORMAL` 处理并提醒，不拦截

### A3 过敏强制拦截与强制继续【关键】

患者青霉素过敏 → 开含阿莫西林的处方 → 提交

**检查点**
- **强制拦截**，提交失败（E3020），红色高危提示
- 医生填理由强制继续 → 通过，写 `audit_high_risk`，处方标记 `override_flag`
- 同类药物（青霉素类其他药）同样拦截（F35 AC3）
- 过敏接口不可用 → 按"过敏史未知"要求人工确认，**不视为无过敏**（F35 AC4）
- AI 建议的过敏药同样拦截（F35 AC5）

### A4 皮试前置【关键】

开需皮试药品（青霉素类）→ 未做皮试直接确认 → 拒绝（E3035）；
补录皮试（阴性）→ 可确认；皮试阳性 → 拒绝确认。

### A5 毒性饮片超量二次确认【关键】

开制川乌 12g（上限 10g）→ 提交被拦（E3040，需二次确认）
→ 带 `toxic_confirm=1` 与理由重新提交 → 通过 + `audit_high_risk`（OVERRIDE）
→ 空理由 → 拒绝；改了剂量后确认标记失效需重确认。

### A6 配伍禁忌提醒不阻断

处方同时含甘草与甘遂 → 提交时提醒（E3041）但**允许继续**，命中记录 `doctor_action`。

### A7 模板开方

创建饮片模板 → 套用 → 过规则校验 → 计价 → 确认。
套用为复制语义，改模板不影响已开处方；模板含停用药品时跳过并明确提示。

### A8 作废与联动

- 未发药处方（PENDING / CONFIRMED）→ 可作废，必须填原因，写 `audit_high_risk`
- **已发药（DISPENSED）→ 作废必须被拒绝**（E3008），只能走 M07 F46 退药
- Visit 作废（M02 F125）→ 该 Visit 下**所有未确认处方自动作废**，已确认的保留并告警

### A9 患者合并改挂

M01 F04 合并患者 → 调用 `prescription.reassign()` → 处方 `patient_id` 改挂，
**处方上的患者快照字段不变**（快照是历史事实，不随合并改写）。

---

## 2. 故障演练

| # | 场景 | 期望 |
|---|---|---|
| D1 | 提交处方过程中断电 | 重启后无半写处方；无孤儿明细；WAL 自动回滚 |
| D2 | 规则校验服务异常 | **拒绝提交**（E3037），不静默放行 |
| D3 | 计价服务异常 | **拒绝提交**（E3043），无金额的处方不进入收费 |
| D4 | 过敏接口超时 | 降级为"过敏史未知"要求人工确认，不得视为无过敏 |
| D5 | 并发提交同一 Visit 两张处方（乐观锁） | 第二张因 `version` 冲突被拒（E2007） |
| D6 | 处方号生成器在事务回滚后重取 | 允许跳号，绝不重号 |
| D7 | 只读安全模式下开方 | 拒绝（E1005），提示系统处于只读 |
| D8 | 饮片单价缺失 | 拒绝提交（E3044），**不允许按 0 计价** |
| D9 | 套用模板中途失败 | 整体回滚，不留半张处方 |

---

## 3. 边界与权限

- **药品只走处方**：M03 F18 医嘱写入 `order_type='DRUG'` → 拒绝
- **跨模块禁写**：M07 不得直接 UPDATE `prescription.status`，只能调 `prescription.transition()`
- 药师角色不得开方（无 `prescription.create`），尝试时写 `audit_permission`
- 处方金额字段一律整数分，DB 中不得出现 REAL 金额

---

## 4. 数据一致性检查（验收时跑）

```sql
-- 处方金额 == 明细小计之和（饮片另加代煎费与包装费）
SELECT p.id FROM prescription p
LEFT JOIN prescription_pricing pr ON pr.prescription_id=p.id
WHERE p.deleted_at IS NULL AND p.status IN ('PENDING','CONFIRMED','DISPENSED')
  AND pr.total_amount IS NULL;

-- 无孤儿的处方明细
SELECT COUNT(*) FROM prescription_item i
LEFT JOIN prescription p ON p.id=i.prescription_id WHERE p.id IS NULL;

-- 已发药却仍被作废（应为 0）
SELECT COUNT(*) FROM prescription WHERE status='VOID' AND dispensed_at IS NOT NULL;
```
