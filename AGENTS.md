# Agent working agreement

开始前必须按顺序阅读：

1. `README.md` — 项目定位与用户入口
2. `docs/HANDOFF.md` — 当前实时状态（handoff 自动刷新）
3. `docs/WORKFLOW.md` — 当前唯一流程契约（workflow_schema_version: 4）
4. `docs/ARCHITECTURE.md` — 静态系统架构

然后阅读用户 vault 中 `AI system/AI 辅助重要文献阅读系统总说明.md` 及 A/B/C/D Skill。

## Side project 推进前提（用户于 2026-11-01 确认）

- 盘活已有 GitHub 项目，优先复用已有成果。
- 目标为影响力、真实使用和可据实写入简历的成果；不以收入为目标。
- 科研优先，控制新增开发与维护负担；不主动扩大为 SaaS 或全套科研平台。
- 首轮免费发布独立的单篇论文阅读决策 Skill；准备工作以 6–10 小时为上限，达到上限时报告实际进度，不自动加码。
- Agensi 是候选分发渠道；不把平台邀约、曝光或上架等同于用户需求验证。
- 具体范围、进度和验证记录见 `docs/SKILL_LAUNCH.md`；接手此方向前必读。

## 流程契约（不可违反）

- 推荐优先级只能使用 `MUST/WEEKLY/RADAR/DROP`；A/B/C/D 只表示用户阅读工作流。
- 自动完成的 A 正式发布到 `每日文献/_文献包/<work_id>/`（A.md + paper.pdf），**不投递 `0_待处理`**。
- `0_待处理` 只表示用户明确选择“稍后”的论文；`1_处理中`=read；`00-已阅`=ignore；`2-可调用`=C 成熟且用户确认。
- 不得把 A 模板标记为完成；不得虚构未获取的 PDF、正文或 SI。
- 除用户显式决定外，不得移动 Obsidian 阶段文件。
- 不覆盖 vault 已有笔记，不删除用户数据，不把历史回放混入今日新增。
- `python3 literature_agent.py a-job --kind all` 只是生成 Codex 可消费的 job 清单，不等于已执行 A；A 由 Codex/ChatGPT executor 完成。
- 修改后运行相关 selftest，并用 `python3 literature_agent.py handoff` 刷新实时状态。
