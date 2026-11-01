# literature-agent

> Personal AI literature radar for research workflows.

个人科研文献雷达：后台完成 发现 → 去重 → 分级 → 全文获取 → A 队列准备 → 每日总览；A 正文由 Codex / ChatGPT executor 完成。每天只把值得看的论文送到你面前，由你做最终决定。

当前版本：**v0.1.0**（personal alpha / first public release）· License：**MIT** · 发布：2026-08-10

## What it does / 功能

- 多来源自动发现文献，按论文级别去重
- MUST / WEEKLY / RADAR / DROP 分级，MUST 自动进入 A 队列
- PDF / SI 获取与校验（获取失败如实标注，不伪造）
- Obsidian 每日总览：唯一日常前台，勾选即决定
- Zotero 归档（查重 + PDF 附件 + collection，可选 WebDAV）
- Codex / ChatGPT 交接：深读 / 独立执行 A
- macOS launchd 定时运行 + 自动补课 + 审计/恢复测试

## Main workflow / 主流程

外部来源 → collect → normalize/dedup → MUST/WEEKLY/RADAR/DROP → MUST PDF/SI → A queue/job → Codex/ChatGPT executor 完成 A → canonical A（`_文献包`）→ Obsidian 每日总览 → read/later/ignore + flags → ChatGPT 深读 → B → C → D。

**重要边界**：Python 后台自动到 `collect → dedup → screen → PDF/SI → A queue/job → overview` 为止；**A 正文由 Codex / ChatGPT executor 完成**，模板不算完成，Python 不会自动生成 A 全文。

## Architecture roles / 角色

| 角色 | 位置 |
|---|---|
| 机器事实来源 | `data/literature.db` |
| 流程契约 | `docs/WORKFLOW.md`（workflow_schema_version: 4） |
| 运行时状态快照 | `state/PROJECT_STATE.json`（`handoff` 自动刷新） |
| 用户唯一前台 | Obsidian `每日文献/YYYY-MM-DD 文献总览.md` |
| canonical A | `每日文献/_文献包/<work_id>/`（A.md + paper.pdf） |
| 用户确认后的正式文献库 | Zotero |

## Standalone paper-reading skill / 单篇论文阅读

**[Paper Triage](docs/paper-triage/README.md)** — one paper + your research goal → a reading decision with traceable evidence, limitations and a concrete next step. Free and open source; use it without installing the full radar system.

把一篇论文和研究目标交给智能体，得到贡献判断、证据位置、结论边界及下一步阅读动作。已提供[理论案例](docs/paper-triage/examples/theory.md)、[实验案例](docs/paper-triage/examples/experiment.md)与[验证范围](docs/paper-triage/VALIDATION.md)。案例为 Codex 内制作并核对的展示，不代表独立评测或跨智能体兼容性认证。

→ [Try it / 使用方法](docs/paper-triage/README.md#try-it-without-installing-anything) · [Skill source](skills/paper-triage/SKILL.md)

## Requirements / 环境要求

- Python 3.9+（开发/验证环境：macOS + Python 3.12）
- Obsidian（用户前台）；Zotero（可选，归档）
- macOS launchd（自动化，可选）；GitHub CLI `gh`（仅发布流程）
- 网络：可访问 arXiv / Crossref / OpenAlex / publisher feeds

## Quick start / 快速开始

```bash
git clone https://github.com/Xin-Jiaqi/literature-agent.git
cd literature-agent
cp config/local.example.json config/local.json   # 填写本机路径与可选 Zotero 配置
python3 literature_agent.py selftest             # 快速自检
python3 literature_agent.py collect              # 首次抓取
python3 literature_agent.py daily                # 日常全流程（collect→screen→process→A queue→digest→总览）
python3 literature_agent.py audit --full         # 完整验收套件
```

常用命令：

```bash
python3 literature_agent.py overview                  # 生成/刷新今天的总览
python3 literature_agent.py sync-overview             # 应用总览批注（daily 也会自动同步）
python3 literature_agent.py a-job --kind all          # 生成 A 执行任务清单（供 Codex/ChatGPT 消费）
python3 literature_agent.py a-complete <work_id>      # executor 完成后核验并发布 A
python3 literature_agent.py decide <work_id> read|later|ignore
python3 literature_agent.py zotero-export             # 半自动 Zotero 归档（.bib）
python3 literature_agent.py handoff                   # 刷新接手状态文档
```

## Discovery sources / 发现来源

主要：

- arXiv
- APS accepted / APS published（含 PRM）
- npj Computational Materials
- Nature Communications / Nature Materials / Nature Nanotechnology
- Science Advances

补充发现 / 历史回补：

- Crossref（期刊回补）
- OpenAlex author watch / citation watch

注意：部分 publisher RSS 可能受 Cloudflare / feed availability 影响，系统会记录为 `blocked`，**不绕过反爬**。详细 source registry 见 `data/sources.json`。

## Obsidian setup / 最小目录

```
Literatures/
├── 每日文献/
│   ├── YYYY-MM-DD 文献总览.md   # 用户唯一日常前台
│   ├── _文献包/                 # canonical A backend storage
│   └── _ChatGPT交接/            # ChatGPT/Codex 交接区
├── 0_待处理/   # 用户明确 later
├── 1_处理中/   # read
├── 2-可调用/   # 用户确认后的成熟 C
├── 00-已阅/    # ignore / 已处理
└── AI system/  # 个人 Skill 原文（仓库公开版：docs/skills/）
```

## A / B / C / D

- **A**：快速建档（发现后由 Codex/ChatGPT executor 完成，六节结构）
- **B**：精读中的增量总结
- **C**：最终可调用文献笔记（用户确认后进入 `2-可调用`）
- **D**：发表 / 汇报 / 社交媒体转化

注意：`MUST / WEEKLY / RADAR / DROP`（推荐轴）与 `A / B / C / D`（阅读轴）是**两条独立维度**。

A/B/C/D 四个 Skill 与《AI 辅助重要文献阅读系统总说明》已随仓库公开：`docs/skills/`（个人基线，可按研究方向自行调整）。

## Zotero setup

半自动（默认，无需 API key）：

```bash
python3 literature_agent.py zotero-export
```

把生成的 `data/queues/zotero_export.bib` 拖入 Zotero 即自动建档。

全自动（可选）：在 `config/local.json` 配置 `zotero.api_key` / `user_id` / `auto_sync: true`。支持：

- 条目创建（DOI / title / arXiv 查重，命中已有条目不重复建档）
- PDF attachment 上传（Zotero File Storage 或可选 WebDAV）
- collection 归类
- fail-closed retry：查重/查询失败时不创建条目，进入退避重试

**不自动写任何 Zotero tag**，tag 由用户自己管理。

## Codex / ChatGPT boundary / 分工

- Python 后台：发现 → 去重 → 分级 → PDF/SI → A queue/job → 总览 → 同步
- Codex / ChatGPT executor：读取全文 → 按 A Skill 生成完整 A → `a-complete`
- 用户：只做最终决定（read / later / ignore + 附加标记）

## Automation / 定时运行（macOS）

- daily：每天 **07:00 / 08:00 / 09:00 / 10:00** 各跑一次；只有 `status=ok`（当天完整成功）才让后续时间点 skip，`partial` / `failed` 继续允许重跑
- 批注同步：每 **60 秒** `sync-overview --all`（纯本地，无网络、无模型调用）
- 错过的时间点：唤醒后 launchd 补跑；`daily` 内置自动补课（>1 天拉大窗口，>7 天 historical-replay）

```bash
./automation/install_launchagent.sh   # daily（按脚本提示手动 launchctl load）
./automation/install_sync_agent.sh    # 批注同步
```

注意：LaunchAgent 模板中的 Python 绝对路径是当前开发机验证配置，其他用户需按自己的 Python 路径修改。

## Privacy / 隐私

- `config/local.json`（含 Zotero API key / WebDAV 密码）**不进入 Git**，只保存在本地
- GitHub 仓库不含：私人数据库、PDF、API key、个人研究画像、Zotero 快照
- 发布前经过全历史 secret scan（gitleaks）

## Audit / reliability / 审计与可靠性

```bash
python3 literature_agent.py audit        # 快速一致性检查（DB/文件/总览/状态）
python3 literature_agent.py audit --full # 完整验收：golden path + 5 条用户路径 + 13 项故障恢复测试
python3 literature_agent.py doctor       # 来源健康
```

内置保障：partial 重试语义、canonical A 唯一、rerun 不覆盖用户批注、状态变化后前台立即刷新、Zotero fail closed。

## Current limitations / 当前限制

- 主要针对 macOS（automation 使用 launchd）
- personal research profile 未公开（需要按自己的研究方向配置）
- A/B/C/D Skill 随仓库提供（`docs/skills/`），但需按个人研究方向与领域自行调整
- 部分 publisher RSS 可能 blocked（Cloudflare / feed 不可用）
- Google Scholar、微信公众号尚未自动接入
- 当前重点是个人科研工作流，不保证开箱即适配所有领域

## Documentation map / 文档地图

- `docs/WORKFLOW.md` — 当前唯一流程契约
- `docs/ARCHITECTURE.md` — 静态系统架构
- `docs/HANDOFF.md` — 新 Codex session 第一入口（`handoff` 自动刷新）
- `docs/RELEASE_NOTES_v0.1.0.md` — v0.1.0 发布说明
- `docs/skills/` — A/B/C/D 阅读 Skill 与系统总说明（个人基线）
- `docs/CHANGELOG.md` — 变更记录
- `state/PROJECT_STATE.json` — 机器状态快照
- `reports/audit/` — 审计报告；`reports/release/` — 发布扫描记录
- `data/sources.json` — source registry
