# M15 经营统计

> 阶段：第四阶段（埋点字段在第三阶段已全部就位）  
> 依赖：M02（Visit）、M06（处方）、M07（库存流水与批次）、M08（处置执行）、M09（账单与支付）、M14、M12  
> 被依赖：无（本模块是链路末端）

---

## 1. 模块职责

**只读聚合层**。不拥有任何业务表，只通过 `04_DATA_MODEL` §4 定义的只读视图取数，产出诊所日常经营需要的六类报表：日营收班结、门诊量与医生工作量、药品进销存、处方统计、患者与疾病谱、报表导出。

> 定位重申（`01_SYSTEM_OVERVIEW` §5）：M15 **不是业务模块**。所有数据来自视图，业务表结构变更时只改视图，不改报表代码。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表                   | 职责             |
| ------------------- | -------------- |
| `report_definition` | 报表定义（可选，P2）    |
| `report_snapshot`   | 报表快照/缓存（可选，P2） |

**其余表一律只读，且只能通过视图读**：

| 视图                     | 用途               | 主要来源                                                            |
| ---------------------- | ---------------- | --------------------------------------------------------------- |
| `v_revenue_daily`      | 日营收/班结（F119）     | `payment` + `refund` + `bill`                                   |
| `v_visit_stats`        | 门诊量与工作量（F120）    | `visit` + `patient` + `sys_user`                                |
| `v_stock_ledger`       | 药品进销存（F121）      | `stock_transaction` + `stock_batch` + `drug`/`herb`             |
| `v_prescription_stats` | 处方统计（F122）       | `prescription` + `prescription_item` + `herb_prescription_item` |
| `v_patient_stats`      | 患者统计（F123）       | `patient` + `visit`                                             |
| `v_diagnosis_stats`    | 疾病谱（F123 **新增**） | `diagnosis` + `record` + `visit`                                |

---

## 3. 模块级规则

**R-M15-1 只查视图，不查原始表。** 任何报表 SQL 都以 `v_` 开头；发现视图缺字段 → 走契约变更流程改视图，**禁止在报表代码里 JOIN 业务表**（04 §4）。

**R-M15-2 口径唯一来源。** 营收、门诊量、客单价、初复诊、进销存成本、中西药占比、抗生素使用率、疾病谱的定义**全部引用 `03_GLOBAL_CONTRACTS` §7**，本模块不得重新发明口径。

**R-M15-3 只读，零业务写入。** 报表接口不产生 `audit_operation`（审计只记写操作，避免噪声）；除 `report_snapshot` 缓存外不写任何表。

**R-M15-4 金额一律整数分。** 接口同时返回 `*_fen`（整数）与格式化字符串，展示层不自己做除法精度处理（R3）。

**R-M15-5 时间归集口径固定。** 营收按 `payment.paid_at` 归集，门诊量按 `visit.completed_at`（无则 `registered_at`）归集，**不用单据创建时间**。区间查询一律左闭右开（`>= from AND < to+1日`）。

**R-M15-6 分页与上限。** 所有列表强制分页（默认 50、上限 500），单机数据量小但禁止一次性全表扫（04 未约束，本模块自定）。

**R-M15-7 权限隔离。** 报表属经营数据，需 `report.view` 权限；导出需 `report.export` 且写 `audit_high_risk`（涉及数据外流）。越权拒绝并写 `audit_permission`。

**R-M15-8 快照可选。** `report_snapshot` 用于日报缓存（P2）；实时查询是默认路径，快照只做加速，不替代口径。

**R-M15-9 口径可复核。** 每张报表返回 `criteria` 字段（口径说明 + 数据区间 + 生成时间），便于与手工核对对齐（第四阶段门禁要求）。

---

## 4. 状态机引用

本模块无业务状态机。可选快照有 `report_snapshot.status`（`VALID / EXPIRED`），非契约状态机，纯缓存标记。

---

## 5. 对外接口

| 接口                                    | 说明           | 消费方    |
| ------------------------------------- | ------------ | ------ |
| `report.revenue_daily(from, to)`      | 日营收与班结       | UI 报表页 |
| `report.visit_stats(from, to)`        | 门诊量、初复诊、客单价  | UI     |
| `report.doctor_workload(from, to)`    | 医生工作量        | UI     |
| `report.stock_ledger(from, to)`       | 药品进销存月报      | UI     |
| `report.prescription_stats(from, to)` | 处方统计与中西药占比   | UI     |
| `report.patient_stats(from, to)`      | 患者与疾病谱 TOP   | UI     |
| `report.export(kind, from, to)`       | 导出 CSV（F124） | UI     |

---

## 6. 功能点清单

| 编号   | 功能点         | 类型      | 优先级 | 复杂度                               |
| ---- | ----------- | ------- | --- | --------------------------------- |
| F119 | 日营收与班结报表    | FEATURE | P0  | M                                 |
| F120 | 门诊量与医生工作量统计 | FEATURE | P0  | M                                 |
| F121 | 药品进销存报表     | FEATURE | P0  | **L（拆 F121.1 期初/期末 / F121.2 明细）** |
| F122 | 处方统计        | FEATURE | P1  | M                                 |
| F123 | 患者与疾病谱统计    | FEATURE | P2  | M                                 |
| F124 | 报表导出与打印     | FEATURE | P1  | S                                 |
