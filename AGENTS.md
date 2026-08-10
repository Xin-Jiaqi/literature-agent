# Agent working agreement

开始前必须按顺序阅读：

1. `README.md` — 项目定位与用户入口
2. `docs/HANDOFF.md` — 当前实时状态（handoff 自动刷新）
3. `docs/WORKFLOW.md` — 当前唯一流程契约（workflow_schema_version: 4）
4. `docs/ARCHITECTURE.md` — 静态系统架构

然后阅读用户 vault 中 `AI system/AI 辅助重要文献阅读系统总说明.md` 及 A/B/C/D Skill。

## 流程契约（不可违反）

- 推荐优先级只能使用 `MUST/WEEKLY/RADAR/DROP`；A/B/C/D 只表示用户阅读工作流。
- 自动完成的 A 正式发布到 `每日文献/_文献包/<work_id>/`（A.md + paper.pdf），**不投递 `0_待处理`**。
- `0_待处理` 只表示用户明确选择“稍后”的论文；`1_处理中`=read；`00-已阅`=ignore；`2-可调用`=C 成熟且用户确认。
- 不得把 A 模板标记为完成；不得虚构未获取的 PDF、正文或 SI。
- 除用户显式决定外，不得移动 Obsidian 阶段文件。
- 不覆盖 vault 已有笔记，不删除用户数据，不把历史回放混入今日新增。
- `python3 literature_agent.py a-job --kind all` 只是生成 Codex 可消费的 job 清单，不等于已执行 A；A 由 Codex/ChatGPT executor 完成。
- 修改后运行相关 selftest，并用 `python3 literature_agent.py handoff` 刷新实时状态。
