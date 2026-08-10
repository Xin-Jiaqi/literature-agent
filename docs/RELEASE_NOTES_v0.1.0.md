# literature-agent v0.1.0 Release Notes

> first public release · personal alpha · 2026-08-10 · MIT License
> https://github.com/Xin-Jiaqi/literature-agent

## What is v0.1.0

A personal AI literature radar for research workflows: it discovers papers from multiple sources, deduplicates them, grades them, prepares full text, and delivers a daily Obsidian overview for the user to decide on. A 正文由 Codex / ChatGPT executor 完成，Python 后台不做模型调用。

## Main workflow

外部来源 → collect → normalize/dedup → MUST/WEEKLY/RADAR/DROP → MUST PDF/SI → A queue/job → Codex/ChatGPT executor 完成 A → canonical A（`_文献包`）→ Obsidian 每日总览 → read/later/ignore + flags → ChatGPT 深读 → B → C → D。

## Key features

- multi-source discovery（arXiv / APS / Nature family / Science Advances / Crossref / OpenAlex）
- work-level dedup
- MUST / WEEKLY / RADAR / DROP 分级
- PDF / SI preparation with validation（blocked 不伪造）
- Codex / ChatGPT A executor（`a-job` → `a-complete`）
- Obsidian daily overview（唯一用户前台，rerun 不覆盖批注）
- read / later / ignore + competitor / save_zotero / ChatGPT flags
- ChatGPT handoff（deep read / A request 交接包）
- Zotero integration（dedup、PDF attachment、collection、可选 WebDAV、fail closed）
- launchd automation（07/08/09/10 + 60s sync）
- audit / recovery tests（golden path + 5 用户路径 + 13 故障恢复）

## Reliability

- partial retry：只有 `status=ok` 才算当天完整成功，`partial`/`failed` 允许后续时间点重跑
- canonical A uniqueness：一篇论文只有一个正式 A（`_文献包`）
- annotation preservation：总览 rerun 0 lost USER block
- frontend refresh：状态变化后总览 / HANDOFF / PROJECT_STATE 立即刷新
- Zotero fail closed：查重失败不创建条目，children 查询失败不补 PDF
- multi-day overview：今日新文献 / 今日历史补回 / 之前未决定 / 今日已处理 由数据库事实决定

## Known limitations

- 主要针对 macOS（launchd automation）
- personal research profile 未公开，需按自己的研究方向配置
- A Skill 需要用户自行准备 / 配置
- 部分 publisher RSS 可能 blocked（Cloudflare / feed 不可用），系统记录但不绕过反爬
- Google Scholar、微信公众号尚未自动接入
- 当前重点是个人科研工作流，不保证开箱即适配所有领域

## Next

进入 5–7 天真实试用，根据实际使用收敛：A 完成率、用户决定比例、blocked PDF、historical backlog 等核心指标，再决定 v0.2.0 的调整方向。
