# M04 AI 与语音

> 阶段：第二阶段（第一个）
> 依赖：M03（**AI 草稿接口 `ai.draft.apply()` 已在第一阶段冻结**）、M02、M14、M12
> 被依赖：M03 病历（草稿）、M06 处方（辅助建议）、M15

---

## 1. 模块职责

语音录入、ASR 转写、医疗事实提取、AI 病历草稿、AI 处方辅助、AI 风险提醒，以及 AI 任务的失败恢复。

**本模块是"辅助"，不是"替代"。** 所有 AI 产出必须经医生确认才能成为正式医疗记录。

---

## 2. 表所有权（详见 `04_DATA_MODEL`）

| 表 | 职责 |
|---|---|
| `ai_task` | 任务主表（类型、状态、重试、耗时） |
| `ai_audio` | 录音文件元信息 |
| `ai_transcript` | ASR 转写文本 |
| `ai_fact` | 医疗事实提取结果 |
| `ai_draft` | 病历草稿（**未确认前不写 `record`**） |
| `ai_suggestion` | 处方建议 |
| `ai_risk_alert` | 风险提醒 |
| `ai_failure_log` | 失败与恢复日志 |

---

## 3. 模块级规则（最重要）

**R-M04-1 AI 产出不得直接落业务库。** AI 生成的病历内容只能写入 `ai_draft`，**经医生确认后**才通过 M03 的 `ai.draft.apply()` 写入正式病历。禁止 AI 直接写 `record` 表。

**R-M04-2 来源标记必须贯穿。** AI 生成 / AI 修改后 / 手工录入三态标记随内容一路带到归档（M11），**归档后不可消除**。合规要求：能区分哪些是 AI 写的、哪些是医生确认的。

**R-M04-3 AI 不做诊断结论。** ASR 与事实提取可以；但**舌象、脉象、证型、诊断结论**只能由医生填写。AI 可提供候选供参考，采纳需医生确认（M03 F108/F109/F17）。

**R-M04-4 离线降级是硬要求。** AI 服务不可用时，全部功能必须能降级到纯手工录入，**不阻塞任何业务流程**（F26）。

**R-M04-5 Worker 不直接写库。** AI Worker 是独立进程，**不打开业务数据库**，通过 IPC 调主程序（`05_RUNTIME_TOPOLOGY` §1.1 单写者原则）。

**R-M04-6 任务可恢复。** 每个 AI 任务有唯一 `idempotency_key`，崩溃后可重试且不产生重复副作用（F26）。

**R-M04-7 看门狗同步上线。** F97（AI Worker 看门狗）必须与本模块**同时**上线，不能等到第五阶段。

**R-M04-8 超时与重试可配。** 超时与重试次数在 `sys_config`（`ai.timeout_ms` / `ai.max_retry`），不硬编码。

**R-M04-9 隐私边界明确。** 录音与转写文本属于患者隐私数据：
- 存储位置可配（本地优先）
- 上传外部 ASR/AI 服务前**明确告知并在配置中可关闭**
- 删除患者数据时，录音与转写文本一并处理

**R-M04-10 AI 不参与审计绕过。** AI 触发的任何写操作，审计中的操作人必须是**确认的医生**，不能是"AI"或"system"。

---

## 4. 状态机引用

```
ai_task: CREATED → RUNNING → SUCCEEDED | FAILED
         FAILED → RETRYING → RUNNING
         FAILED → ABANDONED（超重试上限）
```

---

## 5. 对外接口

| 接口 | 说明 | 消费方 |
|---|---|---|
| `ai.startTranscribe(audio)` | 启动语音转写 | F20 |
| `ai.getDraft(visitId)` | 取草稿 | M03 工作台 |
| `ai.applyDraft(draftId, doctorId)` | 应用草稿（内部调 M03 的 `ai.draft.apply`） | M03 |
| `ai.suggestPrescription(visitId)` | 处方建议 | M06 |
| `ai.riskAlerts(visitId)` | 风险提醒 | M03 / M06 |
| `ai.taskStatus(taskId)` | 任务状态 | UI |

**被依赖的契约（M03 提供）**：`ai.draft.apply(visitId, draft)` — 已在第一阶段冻结。

---

## 6. 功能点清单

| 编号 | 功能点 | 类型 | 优先级 | 复杂度 |
|---|---|---|---|---|
| F20 | 语音录入 | FEATURE | P0 | M |
| F21 | ASR | FEATURE | P0 | M |
| F22 | 医疗事实提取 | FEATURE | P0 | M |
| F23 | AI 病历草稿 | FEATURE | P0 | M |
| F24 | AI 处方辅助 | FEATURE | P1 | M |
| F25 | AI 风险提醒 | FEATURE | P1 | M |
| F26 | AI 任务与失败恢复 | SERVICE | P0 | **L（拆 F26.1–F26.4）** |
