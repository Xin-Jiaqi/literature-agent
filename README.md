# literature-agent

个人科研文献雷达：后台自动完成 发现 → 去重 → 分级 → 全文获取 → A 建档，每天只把值得看的论文送给你。

## 这是什么

- 后台自动跑：collect → dedup → screen → MUST PDF/SI → A worker → A 正式完成 → 每日总览
- 用户唯一日常前台：Obsidian `Literatures/每日文献/YYYY-MM-DD 文献总览.md`
- 自动完成的 A 正式存放 `每日文献/_文献包/<work_id>/`（A.md + paper.pdf），不再堆进 `0_待处理`

## 今天怎么运行

```bash
cd <项目根目录>  # 本项目：literature-agent/
python3 literature_agent.py daily        # collect→screen→process→A queue→digest→总览
python3 literature_agent.py sync-overview # 用户勾选后立即生效（daily 会自动同步）
python3 literature_agent.py audit        # 快速一致性检查（--full 跑完整验收套件）
python3 literature_agent.py handoff      # 刷新 docs/HANDOFF.md + state/PROJECT_STATE.json
```

## 用户看哪里

- 每天只打开：`Literatures/每日文献/YYYY-MM-DD 文献总览.md`
- 勾选 要读/稍后/拒绝 + 附加标记（ChatGPT 深读 / 做 A / 竞品 / 存 Zotero）+ 批注
- 什么都不勾 = 什么都不做

## 出问题怎么办

```bash
python3 literature_agent.py audit        # 检查 DB/文件/总览/状态一致性，列出失败项
python3 literature_agent.py audit --full # 再加 golden path + 5 条用户路径 + 13 项故障恢复测试
python3 literature_agent.py doctor       # 来源健康
```

## 文档地图

- `docs/WORKFLOW.md` — 当前唯一流程契约（workflow_schema_version: 4）
- `docs/ARCHITECTURE.md` — 静态系统架构
- `docs/HANDOFF.md` — 新 Codex session 第一入口（handoff 自动刷新）
- `docs/CHANGELOG.md` — 变更记录
- `state/PROJECT_STATE.json` — 机器状态快照（handoff 自动生成）
- `reports/audit/` — 审计报告（BEFORE/AFTER、文件清单、目录树）
- `data/literature.db` — 机器事实来源
