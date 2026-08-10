# CHANGELOG

## 2026-08-09 — SYSTEM AUDIT + SAFE REPOSITORY CLEANUP

- 新增 `python3 literature_agent.py audit`（18 项快速一致性检查）与 `audit --full`（+golden path +5 条用户路径 +13 项故障恢复）。
- 修复 acceptance 两处失败根因：`la_registry` 注册表路径改为运行时解析；`make_env` 增加环境快照/恢复，消除跨用例污染。
- 修复 audit 自身缺陷：works 查询补 `reading_status`；broken artifact 检查排除 `superseded`；stage 扫描缓存（audit 24s → 0.5s）。
- 真实数据修复：13 条指向旧 `0_待处理` 的 artifact 标记 `superseded`（保留记录）。
- 路径集中化：新建 `config/local.json` + `literature_config.py`；移除模块内个人机器路径硬编码。
- 目录重组（全部移动，无删除）：data/（DB、注册表、queues、gold、Zotero 快照）、reports/{daily,weekly,monthly,audit,historical}、docs/、state/、workspace/papers/、runtime/{logs,cache}、tests/、archive/。
- DB 路径同步：processing/artifacts 中 `papers/…` 与旧绝对路径统一更新到 `workspace/papers/…`；6118 works 无损。
- 文档：README 重写为短用户入口；WORKFLOW/HANDOFF/ARCHITECTURE/CHANGELOG 定位明确；archive/README 说明历史语义。
- workflow_schema_version 定为 4（代码 / state / WORKFLOW / HANDOFF 四处一致，audit 校验）。
