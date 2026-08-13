# CHANGELOG

## 2026-08-13 — 校内邀请试点可直接试用

### Added
- 增加生产 WSGI 运行入口、Cloudflare HTTPS 试用隧道与 launchd 常驻配置。
- 增加限时/限次数邀请码列表与撤销、用户级联删除、SQLite 在线备份和完整性校验。
- 增加隔离 Web 虚拟环境与管理员/备份自动验收。

### Security
- 公网入口继续强制 Secure + httpOnly 会话、SameSite、CSRF、CSP 与 HSTS。
- Waitress 只监听 localhost，代理头只信任本机一跳；Quick Tunnel 不开放公网入站端口。

## 2026-08-13 — 校内试点多用户数据边界

- 新增 additive 产品数据层，现有个人版论文库与工作流保持兼容。
- 公共论文元数据继续按 `work_id` 全局复用；研究项目、画像、种子关系、推荐、反馈、私有资产和模型用量按用户隔离。
- 私有对象键必须位于所属用户命名空间；所有私有读写由 owner-scoped repository 校验。
- 新增双用户隔离验收测试，覆盖公共论文复用、跨用户项目/画像/推荐/PDF 拒绝和独立成本统计。
- 新增显式项目画像筛选与 framework-neutral 产品服务；同一公共论文可针对不同项目产生不同优先级，每日行动项受注意力预算约束。
- 推荐保存画像修订号与排名分数；重复运行幂等，未确认画像和未知输入字段 fail closed。
- 新增邀请制账户、本地管理入口、bcrypt 密码哈希、服务端会话、CSRF 防护和登录失败限制。
- 新增受保护的项目/画像/筛选/每日推荐/决策/用量 HTTP API，以及响应式每日网页总览。
- 新增双浏览器端到端验收，覆盖邀请码约束、会话撤销、安全响应头、跨用户拒绝和完整推荐闭环。

## 2026-08-10 — 公开 A/B/C/D Skill 到公共仓库（v0.1.0 发布后）

- 新增 `docs/skills/`：公开 A/B/C/D 四个阅读 Skill 与《AI 辅助重要文献阅读系统总说明》原稿（与 Obsidian vault `Literatures/AI system/` 对应文件逐字同步）。
- `docs/skills/README.md`：说明各 Skill 的阶段、作用、定位与 literature-agent 的关系。
- README：A/B/C/D 节、Obsidian 目录树注释、限制与文档地图同步指向 `docs/skills/`。
- `docs/articles/literature-agent-v0.1.0.md`：发布信息补充 Skill 公开路径；发布提交 SHA 修正为 tag `v0.1.0` 实际指向的 commit `30063c6`。
- `xhs-kami-template.md`（小红书排版模板）不属于 A–D 流程，未公开。

## 2026-08-10 — Personal Alpha 修正（文档与真实行为对齐）

- 统一投递语义：自动完成 A 正式发布到 `每日文献/_文献包/<work_id>/`（A.md + paper.pdf），不投递 `0_待处理`；`0_待处理` 只表示用户明确“稍后”。
- 修正 `la_aworker.py` 协议、`la_workflow.py` docstring/continuation contract、`la_overview.py` A_REQUEST 文案、A worker 产物 README 中的旧 `0_待处理` 描述。
- AGENTS.md 重写接手入口：`README.md → docs/HANDOFF.md → docs/WORKFLOW.md → docs/ARCHITECTURE.md` → vault Skill；明确 `a-job` 只生成清单、A 由 Codex/ChatGPT executor 完成。
- audit 改为不变量：移除 `work_count >= 5000`、`blocked_pdf == 1`、`n_blocks > 0` 快照硬编码；work count 与上次 PROJECT_STATE 比较异常下降，blocked_pdf 改为 DB 与 state 一致（0 也 PASS），允许合法空总览。
- 安全：`ssl_ctx()` 默认严格校验证书；仅 `LITAGENT_INSECURE_SSL=1` 显式关闭并输出 warning，无静默降级。
- arXiv API 全部改为 HTTPS（`https://export.arxiv.org/api/query`，registry / collect / replay / selftest mock）。
- source role 语义修正：`aps_accepted=discovery`（提前发现入口）、`aps_published=metadata_update`（更新正式出版状态，首次发现时作 discovery fallback）。
- 版本命名清理：运行时入口去掉 `v0` / `V2 (Research Radar)` 过期称呼；统一称 `literature-agent`（personal alpha）。
- MUST 术语统一：`docs/a_stage_rules.md` 中 `S 级论文` → `MUST 论文`；A/B/C/D 仅表示阅读阶段。
- `automation/README.md` 重写为真实调度：daily 07/08/09/10 四档 + 60s `sync-overview --all` agent、python3.12 绝对路径（TCC 说明）、digest v4 用户向。
- workflow_schema_version 保持 4（未改变 workflow schema）。

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
