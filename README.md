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

Windows 用户也可以直接**双击 `start.bat`**（会自动找可用的 Python）。

首次启动会走**初始化向导**，让你设诊所名与管理员账号。
数据库自动建在 `data/clinic.db`（已在 `.gitignore` 中，不会提交）。

### 账号与权限（单机单用户）

初始化创建的管理员**同时被授予 医生 / 药师 / 收费员 / 管理员 四个角色**，
因为一台电脑就是一个操作员，一个人要干完全部岗位。

- **能做什么** = 该账号被授予的所有角色的权限**并集**
- 登录页那个「本次以什么身份操作」**只影响审计记录**，不限制功能
- 审计表里记录的是**实际行使的角色**（例如以医生身份开的方，审计记 DOCTOR）

如果你需要严格分权（如护士只能录病历、不能收费），在「系统管理 → 用户与角色」
里建只带单一角色的账号即可，越权会被拒绝并留痕。

### 只允许一个实例

端口被占用时新进程会**直接拒绝启动**并提示「系统已经在运行」，
避免两个实例同时写同一个数据库（Windows 默认允许端口复用，
早期版本会出现请求随机落到旧进程、一会儿正常一会儿报错）。
重复双击 `start.bat` 只会帮你打开已运行的那个。

其他常用参数：

```bash
python src/main.py --port 8790     # 换端口
python src/main.py --no-browser    # 不自动开浏览器
python src/main.py --selftest      # 只跑启动自检后退出
```

### 环境要求：Python 3.9 或更高

**Python 3.8 不可用**，不是性能问题而是会直接启动失败：
3.8 的 `socket.getfqdn()` 在**中文计算机名**的 Windows 上会抛
`UnicodeDecodeError`，而 `http.server` 绑定端口时一定会调它。

```
File "Python38\lib\socket.py", line 756, in getfqdn
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xcb ...
```

3.9+ 已修复该问题。`start.bat` 会在启动前检查版本，遇到 3.8 直接给出提示而不是让你
面对一个起不来的服务。

无需 `pip install`——全项目零第三方依赖。

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
python tests/first_run_test.py     # 全新库首装 → 立刻走完整门诊（防"装好了不能用"）
python tests/phase3_test.py
python tests/final_acceptance_test.py

# 功能点全量验收：230 个接口逐个真跑（建议在全新库上跑）
python tools/run_fresh_full.py

# 前端契约：前端调用的每个接口后端都得接得住
python tests/api_contract_test.py
node   tools/js_api_check.js         # 需要 Node，验证前端 api() 的 GET/POST 行为

# 真实浏览器点一遍（需要 Edge 开 --remote-debugging-port=9222）
python tools/ui_e2e.py
```

当前基线：**13 个测试文件全部通过；功能点全量验收 206 项通过 0 失败**；
`tools/ui_e2e.py` 53 项界面点击全通过（含 0 个失败请求、0 控制台报错）。

### 辅助工具

| 工具 | 用途 |
|---|---|
| `tools/fn_full_test.py` | 230 个接口逐个执行并验证业务结果 |
| `tools/run_fresh_full.py` | 在全新临时库上跑全量验收，排除历史数据干扰 |
| `tools/ui_e2e.py` | **真实浏览器点完整流程**（建档→接诊→病历→开方→收费→发药→打印→完成） |
| `tools/cdp.py` | 标准库实现的浏览器调试客户端，供 ui_e2e/ui_walk 使用 |
| `tools/ui_walk.py` | 逐页渲染 + 控制台错误 + 截图 |
| `tools/ui_flow_test.py` | 端到端主链路（直接打接口，不走界面） |
| `tools/ui_probe.py` | 各页面首屏依赖的接口链 |
| `tools/ui_click_audit.py` | 扫描"点了没反应"的按钮（缺 try/catch） |
| `tools/api_audit.py` | 前后端接口对账（路径 / HTTP 方法 / 接入度） |
| `tools/db_slim.py` | 审计表归档瘦身（先导出 gzip 再从主库移出） |
| `tools/backup_cleanup.py` | 备份目录清理（默认只列清单） |
| `tools/inventory.py` | 功能点三方对账（文档 ↔ 实现 ↔ 测试） |
| `tools/dump_routes.py` | 导出路由清单 |

测试脚本默认用测试口令 `Clinic@2026`；生产环境请用环境变量覆盖：

```bash
export CLINIC_PWD='your-password'    # Windows: set CLINIC_PWD=...
```

---

## 日常使用要点

### 一次门诊的标准动作

1. 工作台「+ 新建患者」→ 自动挂号
2. 点该患者行的「接诊」→ 进入病历
3. 填主诉 / 现病史 / 中医四诊 / 辨证 / 诊断 →「保存全部」→「确认签名」（输口令）
4. 「开方」→ 选就诊 → 选成药/饮片/颗粒 →「建方」→ 搜药加明细 →「保存明细」→「提交」→「确认」
5. 「收费」→ 选就诊 →「生成账单」→ 填金额收款
6. 「药房库存」→ 选就诊 → 选处方 →「中药发药 / 成药发药」
7. 「打印硬件」→ 选就诊 → 选处方 →「打处方笺」；在任务行点「打印」用系统打印机出纸
8. 工作台「完成」

**所有单据都从下拉里选，不需要记任何 ID。**

### 日结

日结后当天账单锁定，只能红字冲正。**误点日结可以用「撤销今日日结」恢复**
（仅限当天、只能撤最近一次、须填原因并写高风险审计）。

### 审计表会变大

审计只允许 INSERT、不可改不可删。行数多了启动自检会提示，
用 `python tools/db_slim.py` 先归档成 gzip 再从主库移出（会先做安全备份）。

### 备份

备份目录会累积。`python tools/backup_cleanup.py` 先看清单，
确认后加 `--apply` 清理（保留最近 5 份 + 每月 1 份）。

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
