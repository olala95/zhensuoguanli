# 04_DATA_MODEL · 数据模型与表所有权

> **本文定义"谁拥有哪张表"。** 跨模块只能读视图或走对方服务接口，**禁止直接写非本模块的表**（`00_AI_RULES` R2）。
> 字段级设计在各模块开发时细化，本文只冻结表清单、所有权与硬约束。

---

## 1. 命名规范

- 表名：`snake_case`，单数，模块前缀见下表（无强制前缀，但命名不得跨模块混淆）。
- 主键：统一 `id INTEGER PRIMARY KEY`。
- 外键：`{表名}_id`，如 `patient_id`、`visit_id`。
- 布尔：`is_* INTEGER`（0/1）。
- 金额：`*_amount INTEGER`（分）；单价可用 `*_price_milli`（毫元）。
- 时间：`*_at TEXT`（`YYYY-MM-DD HH:MM:SS`）。
- 软删除：`deleted_at TEXT NULL`。

---

## 2. 表所有权总表

### M14 系统管理

| 表 | 职责 |
|---|---|
| `sys_config` | 系统配置（诊所信息、默认付数、代煎费、重试次数等） |
| `sys_user` | 用户 |
| `sys_role` | 角色 |
| `sys_user_role` | 用户角色关联 |
| `sys_session` | 登录会话与锁屏状态 |
| `sys_sequence` | 编号计数器（按 `biz_date + type` 唯一） |
| `sys_migration` | 迁移脚本执行记录 |
| `sys_dict` | 数据字典（给药途径、煎法、脚注、单位等枚举） |
| `sys_health_check` | 健康检查记录（F105） |
| `sys_diagnostic` | 系统诊断记录（F104） |
| `sys_import_job` | 基础数据包导入任务（F128） |

### M12 安全审计（**只允许 INSERT**）

| 表 | 职责 |
|---|---|
| `audit_operation` | 操作审计（F84） |
| `audit_data_change` | 数据修改审计，含 before/after（F85） |
| `audit_high_risk` | 高风险操作（F86） |
| `audit_login` | 登录/锁屏/登出（F87） |
| `audit_time_anomaly` | 系统时间异常（F88） |
| `audit_permission` | 权限拒绝（F89） |

> 登录日志归 M12（审计性质），**不归 M14**。

### M01 患者中心

| 表 | 职责 |
|---|---|
| `patient` | 患者主表（含患者号、姓名、性别、出生日期、证件） |
| `patient_identifier` | 证件/卡号（身份证、医保卡号不用于结算仅作标识） |
| `patient_contact` | 联系人与联系方式 |
| `patient_merge_log` | 重复患者合并记录（F04） |

### M02 就诊中心

| 表 | 职责 |
|---|---|
| `visit` | 就诊主表（就诊号、患者、医生、类型、状态） |
| `visit_status_log` | 状态流转日志 |
| `visit_queue` | 候诊队列（F06） |
| `visit_suspension` | 留观/挂单记录（F08） |
| `visit_void` | 就诊作废记录与原因（F125） |

### M03 临床病历

| 表 | 职责 |
|---|---|
| `record` | 病历主表（关联 visit，含状态与版本） |
| `record_chief_complaint` | 主诉（F10） |
| `record_present_illness` | 现病史（F11） |
| `record_history` | 既往史/个人史/家族史（F12） |
| `record_allergy` | 过敏史（F13） |
| `vital_sign` | 生命体征（F14） |
| `physical_exam` | 查体（F15） |
| `record_exam_ref` | 辅助检查（F16）：引用 M05 的 `lab_result` / `lab_order`，或院内即时检查 |
| `diagnosis` | 诊断，含 ICD-10 与中医证型（F17） |
| `medical_order` | 非药品医嘱（F18） |
| `tcm_four_diagnosis` | 中医四诊：望闻问切、舌象、脉象（F108） |
| `tcm_syndrome` | 辨证论治：证型、治则、治法（F109） |
| `record_template` | 病历模板与常用语（F126） |
| `record_signature` | 病历确认签名（F19） |

> 迁移 0019 补充 `record_exam_ref`（F16 落地）：`exam_name` / `exam_value` / `unit` /
> `abnormal_flag` / `attachment_path`（院内即时检查，不走 M05）、
> `source`（`M05` / `POINT`）、`source_updated_at`（引用时刻源数据 `updated_at` 快照，
> 用于提示"引用的结果已更新"）、`updated_at` / `updated_by`。
> **只存引用不复制结果全文**（F16 R1）：本表不含 `result_text` 列，结果文本始终留在 M05。

### M04 AI 与语音

| 表 | 职责 |
|---|---|
| `ai_task` | AI 任务主表（类型、状态、重试次数、耗时） |
| `ai_audio` | 录音文件元信息（F20） |
| `ai_transcript` | ASR 转写文本（F21） |
| `ai_fact` | 医疗事实提取结果（F22） |
| `ai_draft` | AI 病历草稿（F23），**未确认前不得写入 `record`** |
| `ai_suggestion` | AI 处方建议（F24） |
| `ai_risk_alert` | AI 风险提醒（F25） |
| `ai_failure_log` | 失败与恢复日志（F26，**只允许 INSERT**） |

### M05 检验检查（全外送）

| 表 | 职责 |
|---|---|
| `lab_agency` | 外送机构（名称、联系方式、结算周期） |
| `lab_item` | 外送项目目录（项目、机构、收费价、结算价、报告周期） |
| `lab_order` | 检查申请单（F28） |
| `lab_order_item` | 申请明细（F28 新增，一个申请单多个项目） |
| `lab_order_status_log` | 状态流转日志（03 §2.1 要求，F28 新增） |
| `lab_specimen` | 标本与条码（F29） |
| `lab_result` | 结果记录（F30，手工回录） |
| `lab_result_attachment` | 报告图片附件（F30） |
| `lab_delivery` | 送检登记与回报登记（F117） |
| `lab_reconciliation` | 机构月度对账（F118） |

### M06 处方与用药

| 表 | 职责 |
|---|---|
| `drug` | 药品资料（成药/西药，含规格、剂型、抗生素标记、皮试标记） |
| `herb` | 中药饮片资料（F110：性味归经、常用量、max_dose_g、is_toxic） |
| `prescription` | 处方主表（处方号、类型 WEST/HERB/GRANULE、状态、付数、代煎标记） |
| `prescription_item` | 成药明细（F34） |
| `herb_prescription_item` | 饮片明细：药味、剂量、脚注、单价（F111） |
| `prescription_pricing` | 计价结果与尾差（F112） |
| `prescription_rule_hit` | 规则命中记录（F35 / F113） |
| `herb_compatibility` | 配伍禁忌表（十八反十九畏） |
| `skin_test` | 皮试记录（F36） |
| `prescription_template` | 处方模板与经验方（F127） |
| `prescription_template_item` | 模板明细：药品/饮片混合，用 `item_kind` 区分（F127 新增） |
| `prescription_status_log` | 处方状态流转日志（03 §2.1 要求） |
| `medication_guide` | 用药指导（F39） |

### M07 药房与库存

| 表 | 职责 |
|---|---|
| `supplier` | 供应商 |
| `stock_in` | 入库单（F40） |
| `stock_batch` | 批次（F41：系统批次内码、供应商批号、效期、成本） |
| `stock` | 库存（按药品 + 批次；F42） |
| `herb_stock` | 饮片库存（按饮片 + 批次，单位 g；F114） |
| `stock_transaction` | 库存流水（F43，**禁止删除**） |
| `stock_split` | 拆零记录与尾差（F44/F45） |
| `drug_return` | 退药单（F46） |
| `stock_loss` | 报损单（F47） |
| `stock_check` | 盘点单（F48） |
| `stock_check_item` | 盘点明细与差异 |
| `stock_expiry_alert` | 效期与库存预警（F49/F50） |
| `dispense` | 成药发药记录（F51） |
| `herb_dispense` | 中药调配与发药（F115：代煎/自煎、付数、包装） |
| `drug_return_item` | 退药明细（F46 **新增**：契约 §3.4 要求退款指定到明细行） |
| `stock_loss_item` | 报损明细（F47 **新增**） |
| `stock_batch_status_log` | 批次状态流转日志（F41 **新增**，03 §2.1） |
| `stock_check_status_log` | 盘点单状态流转日志（F48 **新增**，03 §2.1） |

### M08 处置执行

| 表 | 职责 |
|---|---|
| `treatment_item` | 处置项目（F52） |
| `treatment_package` | 处置组套（F53） |
| `treatment_package_item` | 组套明细 |
| `treatment_execution` | 执行记录（F54/F55） |
| `treatment_process` | 治疗过程记录（F56） |
| `tcm_treatment_record` | 中医治疗执行：穴位、手法、留针时长、反应（F116） |
| `treatment_status_log` | 处置执行状态流转日志（F55 **新增**，03 §2.1） |

### M09 收费财务

| 表 | 职责 |
|---|---|
| `charge_item` | 收费项目（F57） |
| `bill` | 账单主表（F58） |
| `bill_item` | 账单明细（F60，**禁止删除**，冲正走负数行） |
| `payment` | 支付记录（F59：渠道、金额、时间） |
| `refund` | 退款单（F61） |
| `refund_item` | 退款明细 |
| `reversal` | 红字冲正（F62） |
| `day_settlement` | 日结（F64） |
| `bill_status_log` | 账单状态流转日志（F58 **新增**，03 §2.1） |
| `payment_status_log` | 支付状态流转日志（F59 **新增**，03 §2.1） |

> 无账户余额表、无应收账款表（不做欠费挂账与储值卡）。

### M10 打印与硬件

| 表 | 职责 |
|---|---|
| `print_job` | 打印任务（F66，含重打次数） |
| `print_template` | 打印模板（含中药饮片方模板） |
| `print_failure` | 打印失败与重试记录（F71） |
| `hardware_device` | 硬件设备登记（F72） |
| `hardware_event` | 硬件事件（F73/F74） |
| `hardware_health` | 硬件监控心跳（F75） |

### M11 病历归档

| 表 | 职责 |
|---|---|
| `record_version` | 病历版本（F76） |
| `record_amendment` | 病历修改留痕（F77） |
| `record_closure` | 病历关闭（F78） |
| `record_archive` | 归档（F79） |
| `record_hash` | Hash 链（F80，每条含 prev_hash + 当前 hash） |
| `record_seal` | 封存（F81） |
| `record_unseal` | 解封（F82） |
| `record_export` | 导出记录（F83） |

### M13 数据安全与恢复

| 表 | 职责 |
|---|---|
| `backup_job` | 备份任务（F90-F92） |
| `backup_file` | 备份文件登记 |
| `restore_log` | 恢复记录（F93） |
| `integrity_check` | 完整性检查结果（F94） |
| `crash_recovery_log` | 崩溃恢复记录（F95/F96），**只允许 INSERT** |
| `watchdog_log` | 看门狗动作记录（F97/F98），监控性质可轮转 |

> 迁移 0018 补充：`backup_job.parent_job_no`（增量挂所属全量）、
> `backup_job.write_seq`（备份时点的写事务序号）、`restore_log.restore_no`。

### M15 经营统计（**只读聚合层**）

| 表 | 职责 |
|---|---|
| `report_definition` | 报表定义（可选，P2） |
| `report_snapshot` | 报表快照/缓存（可选，P2） |

> **M15 不拥有任何业务表。** 所有数据通过 §4 的只读视图获取。

---

## 3. 硬约束

| 约束 | 说明 |
|---|---|
| 外键 | `PRAGMA foreign_keys = ON`（强制开启，见 `05_RUNTIME_TOPOLOGY`） |
| 审计表只读 | 6 张 `audit_*` 表 + `stock_transaction` + `bill_item` + `treatment_process`：**只允许 INSERT**。用触发器拦截 UPDATE/DELETE，应用层二次校验 |
| 归档表只读 | M11 全部 8 张表（`record_version` / `record_amendment` / `record_closure` / `record_archive` / `record_hash` / `record_seal` / `record_unseal` / `record_export`）：**只允许 INSERT**（迁移 0017 建触发器）。历史 Hash 与封存痕迹**不可修补、不可删除** |
| 软删除 | 业务主表禁止物理删除；审计与流水表禁止任何删除 |
| 唯一索引 | 编号字段（患者号、就诊号、处方号…）建唯一索引，防重号 |
| 状态字段 | 不建 CHECK 约束（状态机在应用层），但必须有索引用于查询 |
| 金额 | `INTEGER` 分；不允许 REAL 类型金额字段 |
| 患者删除保护 | `patient` 存在有效 `visit` 或 `prescription` 时不得软删 |

---

## 4. 只读视图层（报表专用）

M15 只查以下视图，**不查原始表**。业务表结构变更时只改视图，不改报表代码。

| 视图 | 用途 | 主要来源 |
|---|---|---|
| `v_revenue_daily` | 日营收/班结（F119） | `payment` + `refund` + `bill` |
| `v_visit_stats` | 门诊量与工作量（F120） | `visit` + `patient` + `sys_user` |
| `v_stock_ledger` | 药品进销存（F121） | `stock_in` + `stock_transaction` + `stock_batch` + `stock_check` |
| `v_prescription_stats` | 处方统计（F122） | `prescription` + `prescription_item` + `herb_prescription_item` |
| `v_patient_stats` | 患者统计（F123） | `patient` + `visit` |
| `v_diagnosis_stats` | 疾病谱（F123 **新增**） | `diagnosis` + `record` + `visit` |

视图命名字首 `v_`；视图内**只做 SELECT**，不做业务规则判断；口径定义集中在 `03_GLOBAL_CONTRACTS` §7。

---

## 5. 迁移规则

- 迁移脚本放 `migrations/`，命名 `{序号}_{简述}.sql`，序号 **4 位零填充递增**（`0001_init.sql`）。
- 每次迁移在 `sys_migration` 记录：序号、文件名、执行时间、校验和。
- **迁移脚本只增不改**：已执行的脚本禁止修改，需要修正就新增下一个序号。
- 迁移必须在事务内执行；失败整体回滚。
- 应用启动时自动检查并执行未执行的迁移，执行前**自动触发一次全量备份**。
