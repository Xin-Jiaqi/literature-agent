# ARCHITECTURE（静态系统架构）

> 静态说明：描述模块与数据流。当前流程契约以 docs/WORKFLOW.md 为准；实时状态以 state/PROJECT_STATE.json 为准。

## 1. 目录布局

```
literature-agent/
├── README.md                 # 用户快速入口（本仓库唯一）
├── literature_agent.py       # 稳定 CLI shim（launcher/cron 依赖）
├── literature_config.py      # 路径唯一配置入口（env → config/local.json → 默认）
├── config/local.json         # 唯一用户配置文件（本机路径）
├── la_*.py                   # 运行模块（阶段 2 将逐步迁入 src/literature_agent/）
├── data/                     # 机器事实来源
│   ├── literature.db         #   DB（works/processing/artifacts/feedback…）
│   ├── sources.json          #   来源注册表
│   ├── tracked_entities.json / citation_seeds.json / journal_identifiers.json
│   ├── zotero_library.json / zotero_snapshot.json   # Zotero 只读快照
│   ├── research_profile.md / zotero_taxonomy.md     # 语义配置
│   ├── gold/                 #   screening_gold / coverage_gold / gold fixture
│   ├── queues/               #   a_queue / zotero_queue / feedback / a_jobs
│   └── imports/              #   scholar 导入区
├── workspace/papers/         # A 工作副本（非 canonical；canonical 在 vault _文献包）
├── reports/                  # 运行时报告
│   ├── daily/ weekly/ monthly/
│   ├── audit/                #   doctor/coverage/eval/gold 候选/审计
│   └── historical/           #   replay / missed 报告
├── docs/                     # WORKFLOW / HANDOFF / ARCHITECTURE / CHANGELOG / CODEX_HANDOFF
├── state/PROJECT_STATE.json  # handoff 自动生成的机器状态快照
├── tests/                    # acceptance / overview-test / selftest-v2（临时环境，不碰真实数据）
├── runtime/logs + cache      # 运行日志与缓存
├── automation/               # launchd 每日定时安装脚本
├── backups/                  # 自动备份（保留最近 10 份）
└── archive/                  # 历史产物（只归档，不删除）
```

## 2. 模块职责

| 模块 | 职责 |
|---|---|
| literature_agent.py | CLI 编排：daily/collect/screen/process/overview/audit/selftest… |
| la_db / la_db2 | DB 连接、schema、migrate、反馈/金标读写 |
| la_registry | sources.json 注册表 → SOURCES 兼容结构 |
| la_dedup | 归一化 + 去重索引 + dedup_review |
| la_discovery / la_v2collect | arXiv/Crossref/OpenAlex 采集 |
| la_workflow | PDF/SI 获取、A 完成核验、canonical 发布 _文献包、阶段迁移、disposition/flags |
| la_aworker | A worker 一次性任务清单（Codex 连续消费） |
| la_overview | 每日总览生成/同步、ChatGPT 交接、链接验证 |
| la_digest / la_monthly | daily/weekly/monthly 工程报告 |
| la_handoff | HANDOFF.md + PROJECT_STATE.json 自动刷新 |
| la_audit | 快速一致性审计（18 项） |
| la_product_db | 校内试点多用户数据边界：用户 / 项目 / 画像 / 推荐 / 私有资产 / 用量 |
| la_project_screen | 使用显式、已确认的项目画像生成用户级优先级，不读取个人全局画像 |
| la_product_service | framework-neutral 产品服务：项目 / 画像 / 种子 / 推荐 / 决策 / 用量 |
| tests/la_acceptance | golden path + 5 条用户路径 + 13 项故障恢复（临时环境） |

校内试点采用“公共论文事实 + 用户私有上下文”模型：`works` 等论文元数据继续全局去重；研究项目、画像、种子关系、推荐、反馈、PDF/笔记对象键和模型用量全部携带 `owner_user_id`，并通过 owner-scoped repository 访问。首轮在 SQLite 验证边界，服务端部署时迁移至 PostgreSQL 与对象存储。

## 3. canonical 语义（唯一事实版本）

| 名称 | 位置 | 说明 |
|---|---|---|
| canonical A | vault `每日文献/_文献包/<work_id>/A.md` | 唯一官方 A（transclude 到总览） |
| canonical PDF | 同上目录 `paper.pdf` | 正式全文副本 |
| working copy | `workspace/papers/<slug>/` | 生成阶段工作副本，非官方 |
| handoff snapshot | `每日文献/_ChatGPT交接/<date>/<paper>/` | 交接快照（A.md 是 snapshot/copy） |

同一篇论文只允许一个 canonical A（audit `canonical_a_unique` 检查）。

## 4. 数据流

外部来源 → collect → normalize/dedup → MUST/WEEKLY/RADAR/DROP → MUST PDF/SI →
A worker（Codex/ChatGPT）→ A 校验 → `_文献包` → 每日总览 → 用户批注 → sync →
read→1_处理中 / later→0_待处理 / ignore→00-已阅 / 无操作→留 `_文献包`；
competitor/save_zotero/ChatGPT 深读是附加标记，与 disposition 并存。

## 5. 机器相关路径

全部集中在 `config/local.json`（project_root / obsidian_vault / literatures_root / a_skill_path）。
模块经 `literature_config.py` 读取；测试与迁移用 `LITAGENT_*` 环境变量覆盖。
