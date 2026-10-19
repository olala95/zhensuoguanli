# 基层中医诊所门诊管理系统

单机离线可用的中医诊所管理系统。覆盖患者从进门到离院的完整流程，含中医四诊辨证、中西药双轨处方、药房进销存、收费财务、病历归档与安全审计。

**零第三方依赖**：Python 3 标准库 + 原生 JS，不装任何包。

> ⚠️ **本项目未经临床验证，不含任何医疗决策支持保证**。请勿直接用于真实诊疗收费场景，
> 除非你已完整读过文档、跑通全部测试并做过本地验收。

---

## 它能做什么

```
建档 → 挂号 → 候诊 → 接诊 → 写病历（中医四诊 + 辨证论治 + AI 语音草稿）
     → 开方（西成药 / 中药饮片 / 颗粒）→ 收费 → 发药 → 打印（处方笺/凭条/药袋标签）
     → 病历归档封存 → 经营统计
```

配套：药房进销存（入库/拆零/报损/盘点/效期预警）、外送检验全流程、
处置执行（含中医治疗记录）、退款冲正、日结、安全审计、备份恢复、故障演练。

**15 个模块 / 128 个功能点**，详见 [`01_SYSTEM_OVERVIEW.md`](01_SYSTEM_OVERVIEW.md)。

---

## 快速开始

```bash
git clone <this-repo>
cd <repo>
python src/main.py            # 默认 8787 端口，自动打开浏览器
```

首次启动会走**初始化向导**，让你设诊所名与管理员账号。
数据库自动建在 `data/clinic.db`（已在 `.gitignore` 中，不会提交）。

其他常用参数：

```bash
python src/main.py --port 8790     # 换端口
python src/main.py --no-browser    # 不自动开浏览器
python src/main.py --selftest      # 只跑启动自检后退出
```

**环境要求**：Python 3.9+（用到了较新语法），无需 pip install。

---

## 架构

| 项 | 说明 |
|---|---|
| 终端 | 单机单用户，一台电脑一名操作员 |
| 数据库 | 本地 SQLite 单文件（WAL、busy_timeout=5000、外键开启） |
| 服务端 | `http.server` 自建路由层，无 Web 框架 |
| 前端 | 原生 JS 单页应用，无构建步骤 |
| 网络 | 核心流程完全离线；仅 AI 推理需联网，失败自动降级 |

```
src/
  core/            基础设施：db / api 路由 / 审计 / 会话 / 备份 / 自检
  modules/         业务模块 m01..m15（一个模块一个文件）
  main.py          启动入口
migrations/        SQL 迁移（0001 起，只增不改）
web/               SPA（index.html + app.js + style.css）
tests/             12 个测试文件
tools/             验收与诊断工具
M01_..M15_/        每个模块的 MODULE.md / Fxx.md / ACCEPTANCE.md
00_..05_*.md       全局契约、数据模型、开发顺序等
```

**几条约定的硬规则**（详见 [`00_AI_RULES.md`](00_AI_RULES.md)）：

- 跨模块**只能读视图或走服务接口**，禁止直接写非本模块拥有的表
- 金额一律整数「分」，禁用浮点
- 状态变更只走 `transition()`，禁止裸 `UPDATE`
- 审计表只允许 INSERT，数据库触发器兜底
- AI 产出必须医生确认才可落库，落库标记来源

---

## 测试与验收

```bash
# 单元 / 集成
python tests/smoke_test.py
python tests/http_smoke.py
python tests/phase3_test.py
python tests/final_acceptance_test.py

# 功能点全量验收：230 个接口逐个真跑（建议在全新库上跑）
python tools/run_fresh_full.py

# 前端契约：前端调用的每个接口后端都得接得住
python tests/api_contract_test.py
node   tools/js_api_check.js         # 需要 Node，验证前端 api() 的 GET/POST 行为
```

当前基线：**12 个测试文件全部通过；功能点全量验收 206 项通过 0 失败**
（5 项按业务不适用跳过，见 `tools/fn_full_test.py` 内注释）。

### 辅助工具

| 工具 | 用途 |
|---|---|
| `tools/fn_full_test.py` | 230 个接口逐个执行并验证业务结果 |
| `tools/run_fresh_full.py` | 在全新临时库上跑全量验收，排除历史数据干扰 |
| `tools/ui_flow_test.py` | 端到端主链路：建档 → 日结 |
| `tools/ui_probe.py` | 各页面首屏依赖的接口链 |
| `tools/ui_click_audit.py` | 扫描"点了没反应"的按钮（缺 try/catch） |
| `tools/api_audit.py` | 前后端接口对账（路径 / HTTP 方法 / 接入度） |
| `tools/inventory.py` | 功能点三方对账（文档 ↔ 实现 ↔ 测试） |
| `tools/dump_routes.py` | 导出路由清单 |

测试脚本默认用测试口令 `Clinic@2026`；生产环境请用环境变量覆盖：

```bash
export CLINIC_PWD='your-password'    # Windows: set CLINIC_PWD=...
```

---

## 数据安全

- `data/`（业务库）、`backups/`（备份）、`logs/` 均已在 `.gitignore` 中，**不会提交**
- 建议定期用系统内「立即备份」并另存到其他磁盘
- 病历归档含 Hash 链校验，可发现篡改；封存后不可修改

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [`00_AI_RULES.md`](00_AI_RULES.md) | 开发须知与硬约束 |
| [`01_SYSTEM_OVERVIEW.md`](01_SYSTEM_OVERVIEW.md) | 系统定位、边界、模块清单 |
| [`02_DEVELOPMENT_ORDER.md`](02_DEVELOPMENT_ORDER.md) | 五阶段开发顺序与回归基线 |
| [`03_GLOBAL_CONTRACTS.md`](03_GLOBAL_CONTRACTS.md) | 编号规则、状态机、金额精度、异常码、统计口径 |
| [`04_DATA_MODEL.md`](04_DATA_MODEL.md) | 表清单、表所有权、只读视图 |
| [`05_RUNTIME_TOPOLOGY.md`](05_RUNTIME_TOPOLOGY.md) | 进程构成、SQLite 策略、看门狗契约 |

---

## License

**私有项目，保留所有权利。** 未经作者许可，不得复制、分发或用于商业用途。
