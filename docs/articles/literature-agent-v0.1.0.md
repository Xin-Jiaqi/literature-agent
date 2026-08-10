# 我搭了一套个人 AI 文献雷达：从自动发现、筛选到 Obsidian 深读与 Zotero 归档

> literature-agent：把持续增长的论文流压缩成每天少量真正值得看的文献，再接入已有的 A/B/C/D 阅读系统。

<!-- 【发布状态】项目已以 v0.1.0 公开发布：Public 仓库 + MIT License + GitHub Release。v0.1.0 tag 固定在首个公开快照；发布后 main 仅补充公开文档/Skill，不改变 v0.1.0 的运行逻辑。 -->

<!-- 【图 1｜需手动截图：Obsidian 每日文献总览】
建议文件：docs/assets/v0.1.0/01-obsidian-daily-overview.png
截图内容：一页完整的“YYYY-MM-DD 文献总览”，至少同时出现“今日新文献 / 之前未决定 / 我的决定 / 附加标记 / A 建档嵌入”。
建议裁切：保留 Obsidian 左侧文件树的一小部分 + 正文主体；隐藏 iCloud/用户真实路径。
发布用途：文章开头第一张图，让读者先看到最终使用界面。 -->

我过去的文献阅读方式很常见：Google Scholar 邮件、期刊更新、公众号、别人推荐，看到有价值的论文后放进 Zotero，再挑重要的交给 ChatGPT 做快速建档。真正进入精读以后，我会在 Obsidian 里继续问图、公式、概念和与自己课题的关系，最后整理成可调用笔记。

这个流程本身有效，问题出在入口。论文越来越多之后，最耗时间的环节逐渐变成了“今天到底该看什么”。如果每天先浏览几十篇标题和摘要，再逐篇决定是否下载、是否读、是否进 Zotero，注意力已经在真正阅读之前被消耗掉了。

因此我最近把入口重新做了一遍，形成了一个本地运行的 `literature-agent`。它承担发现、去重、筛选、全文准备、每日总览、状态同步和归档这些机械工作；Codex 和 ChatGPT继续承担需要模型理解的阅读任务；最终决定仍然由人来做。

当前代码保留在 GitHub：

**https://github.com/Xin-Jiaqi/literature-agent**

项目已经以 v0.1.0 公开发布（MIT License，2026-08-10，personal alpha / first public release），对应 `workflow_schema_version = 4`。

---

## 一条完整的主流程

整个系统可以压缩成下面这条链：

    外部文献源
    → collect
    → normalize / dedup
    → MUST / WEEKLY / RADAR / DROP
    → MUST 获取 PDF / SI
    → A queue / job
    → Codex 或 ChatGPT 完成 A
    → canonical A 发布到 Obsidian _文献包
    → 每日文献总览
    → 用户决定 read / later / ignore
    → ChatGPT 深读
    → B → C → D

这里有两条彼此独立的轴。

第一条是**推荐优先级**：

| 等级 | 含义 | 系统动作 |
|---|---|---|
| MUST | 今天值得占用注意力 | 获取全文，进入 A 队列 |
| WEEKLY | 本周值得读 | 进入周候选，不自动做 A |
| RADAR | 值得知道的邻接信号 | 保留在雷达层 |
| DROP | 当前不值得继续占注意力 | 入库但不推到前台 |

第二条是我原来已经在用的**阅读阶段**：

| 阶段 | 作用 |
|---|---|
| A | 快速建档，先判断文章讲什么、为什么重要、值不值得深入 |
| B | 把精读过程中的自由问答压缩成增量草稿 |
| C | 融合 A 和历次 B，形成成熟、可调用的最终文献笔记 |
| D | 把 C 转成知乎、公众号、小红书或汇报材料 |

这两个维度分开以后，逻辑清楚很多。`MUST` 表示机器对“注意力优先级”的判断；`A` 表示论文进入阅读流程后的第一阶段。过去容易混在一起的 S/A/B/C 分级，也因此被彻底拆开。

<!-- 【图 2｜建议制作：主流程图】
建议文件：docs/assets/v0.1.0/02-workflow-overview.png
内容：External sources → Python backend → Codex/ChatGPT → Obsidian daily overview → Zotero / B→C→D。
建议视觉：横向 4 个角色分区，8–10 个节点，不要把所有模块名都画进去。
这张图后续可以由我直接根据最终文章生成，也可以从 GitHub 流程图导出。 -->

## 发现层：先把“可能重要的文献”收进来

目前的发现层以官方或公开接口为主，核心包括 arXiv、APS accepted/published、Nature 系列和 Science Advances 等期刊源，同时保留 Crossref 和 OpenAlex 作为元数据回补、作者关注和引用网络发现工具。

这里有几个我后来认为很重要的设计选择。

**APS accepted 和 published 要分开。** accepted 更适合作为提前发现入口，published 更适合更新正式出版状态。这样一篇论文可以先以 accepted 版本进入系统，正式出版以后再更新 DOI、期刊状态等信息，而不会被当成两篇论文。

**被出版社反爬挡住的源不强行绕过。** 某些 RSS 会遇到 Cloudflare、失效 feed 或网络限制。系统会把它们标记为 blocked，并通过 Crossref/OpenAlex 等渠道补回，同时保留“当前渠道不可用”这一事实。

**作者关注和引用图谱只是发现信号。** 一个重点作者发了新论文，并不意味着这篇文章一定和我的当前课题相关。最终仍然要经过研究画像和主题门槛。

另外，我没有把普通关键词做成硬门槛。关键词很容易漏掉跨领域工作，例如 quantum geometry、rectification、sum rule 之类的新理论可能和 BPVE 强相关，却未必在标题里直接出现熟悉的术语。当前系统会结合项目画像、方法、作者、来源、引用关系和创新信号做综合判断。

## 去重层：把 arXiv、accepted 和 published 看成同一项工作

文献自动化最容易产生的一类垃圾，是同一篇论文被不同来源重复推送。

因此数据库的基本单位是 `work`，而非“某个 URL”。系统会利用 DOI、arXiv ID、规范化标题、作者和版本信息做 work-level dedup。arXiv v1、v2、APS accepted 和正式 published 可以被串成同一个 work，同时保留版本变化。

存在不确定性的匹配不会强行合并，而会进入 `dedup_review`。这一点对长期数据库很重要，因为一次错误 merge 比多看一条重复记录更难修复。

## A 阶段：模型真正介入的位置

Python 后台负责到这里：

    发现 → 筛选 → PDF/SI → A queue/job 准备

A 正文本身由 **Codex 或 ChatGPT executor** 完成。

这个边界后来专门修过一次。最早的文档容易把“进入 A 队列”写成“后台自动完成 A”，实际运行边界更清楚：

    python3 literature_agent.py a-job --kind all

只负责生成可由 Codex 消费的任务清单。Codex 读取实际 PDF 和 A Skill，逐篇生成完整 A，再通过 `a-complete` 校验并发布。A/B/C/D 四个 Skill 与《AI 辅助重要文献阅读系统总说明》也随仓库公开（`docs/skills/`），本文描述的是当前个人基线。

A 的目标也很有限：快速回答“这篇讲什么、旧问题是什么、核心证据在哪里、对我的研究有什么用、下一步读哪里”。它不承担完整精读。

一个很重要的保护规则是：**拿不到 PDF 就不能伪造 A。** HTML challenge、失效链接或不完整文件都会进入 blocked 状态。SI 没拿到也必须明确标注。

<!-- 【图 3｜需手动截图：Codex A job / A 完成过程】
建议文件：docs/assets/v0.1.0/03-codex-a-worker.png
二选一：
A. Terminal 中 `a-job --kind all` + Codex 消费一篇 MUST 后完成 A；
B. `_文献包/<work_id>/` 中 A.md + paper.pdf + supplement.pdf 的实际文件结构。
隐私：裁掉用户名、绝对路径、API key、远程机器信息。
发布用途：说明“Python 准备任务，模型负责读正文”的边界。 -->

## Obsidian 前台：每天只打开一页

我最终把用户前台收敛成了一个文件：

    Literatures/每日文献/YYYY-MM-DD 文献总览.md

每天真正需要看的东西都在这里。

当前总览分成四个区：

1. 今日新文献
2. 今日历史补回
3. 之前未决定
4. 今日已处理

这样昨天没来得及决定的论文会进入“之前未决定”，不会第二天继续冒充“今日新文献”。已经处理过的论文也不会在后续日报里不断重复出现。

每篇论文下面直接嵌入 A 建档，并给出三类决定：

- 要读
- 稍后
- 拒绝

还有可以叠加的标记：

- ChatGPT 深读
- ChatGPT 做 A
- 竞品
- 存 Zotero

这里我刻意把 **disposition** 和 **flag** 分开。一篇论文可以同时是“要读 + 竞品 + 存 Zotero”，这些信息没有必要互相排斥。

Obsidian 的 Wikilink 和 embed 很适合做这种前台。`![[...]]` 可以把 A 原文直接嵌进总览，source note 更新后嵌入内容也同步变化。Obsidian 官方文档同样建议避免文件名中的 `:` 等可能破坏内部链接的字符，因此 `_文献包` 的 work 目录也专门做了安全化处理。

### canonical A 只保留一份

自动生成 A 后，它的唯一正式位置是：

    每日文献/_文献包/<work_id>/A.md

同目录保留 `paper.pdf`，如果有 SI，再保留 `supplement.pdf`。

每日总览只 transclude 这份 A，不复制第二份正式笔记。用户真正做出决定以后，文件才进入阅读阶段：

    read   → 1_处理中
    later  → 0_待处理
    ignore → 00-已阅

C 成熟并由用户确认后，才进入：

    2-可调用

这也修正了我早期工作流里的一个问题：原来的 `0_待处理` 同时承担“所有自动 A 的堆放区”和“我明确想稍后处理的论文”，时间久了又会变成新的垃圾堆。现在 `_文献包` 承担机器后台存储，`0_待处理` 只表示一个清晰的人类决定：**稍后再看。**

<!-- 【图 4｜需手动截图：Obsidian 文件阶段】
建议文件：docs/assets/v0.1.0/04-obsidian-stage-folders.png
截图内容：左侧文件树同时显示 `每日文献/_文献包`、`0_待处理`、`1_处理中`、`00-已阅`、`2-可调用`。
最好让右侧打开一篇已经进入 `1_处理中` 的 A/B 笔记。
隐私：隐藏 vault 上层真实路径。
用途：解释“机器存储”和“用户阅读阶段”的分工。 -->

## 批注同步：文件移动后，前台必须立即正确

总览里的勾选每 60 秒由一个本地 LaunchAgent 同步一次。

这部分看起来简单，实际踩过一个很典型的问题：用户勾选“要读”后，A 已经被移动到 `1_处理中`，但原总览还指向 `_文献包`，于是会短暂出现 broken link。

现在状态变化后统一调用 `refresh_frontend_state()`：

    sync / decide / flag / a-complete
    → 更新 DB
    → 移动或发布文件
    → 重绘相关 overview
    → 刷新 HANDOFF
    → 刷新 PROJECT_STATE

因此用户完成一次操作以后，Markdown 前台、文件系统和数据库会在同一个流程里重新对齐。

## Zotero：只保存我真正决定要留下的论文

我一直希望 Zotero 保持干净，所以没有采用“发现一篇就自动塞进 Zotero”的方案。

现在的顺序是：

    外部世界
    → literature-agent 筛选
    → A
    → 用户决定“存 Zotero”
    → Zotero

默认模式仍然可以导出 BibTeX 手动拖入。可选的全自动模式使用 Zotero Web API v3 创建条目，并上传 PDF。

Zotero 这一段后来改得比较多，主要解决了四类问题：

**API v3 的响应和文件上传流程。** 当前实现显式使用 Web API v3，并按官方推荐的 attachment → upload authorization → file upload → register 流程处理 Zotero File Storage。

**WebDAV。** 我的 Zotero 文件同步实际使用 WebDAV，因此仅把文件上传到 Zotero 官方存储并不能让本地客户端按相同方式取得附件。对当前坚果云配置，我又对真实库做了验证，按 Zotero 7+ 实际产生的 `<itemKey>.zip + <itemKey>.prop` 形式写入 WebDAV，并同步 md5/mtime。这个部分属于针对当前 WebDAV 环境验证过的实现细节，换其他服务时应重新核对。

**导入前查重。** 系统按 DOI → 标题 → arXiv 编号检查 Zotero 里是否已有条目。已有论文不会再创建一份，只在确认缺少 PDF 时补附件。

**fail closed。** 如果查重 API 返回 500、网络异常或 JSON 解析失败，系统会直接停止这次创建，把条目留在本地队列等待重试。查询失败不会被解释成“Zotero 里没有这篇论文”。children 查询失败时也不会盲目再挂一个 PDF。

这类行为看起来保守，但长期运行的自动化系统里，避免产生难以清理的重复数据，比某一次同步快几秒更重要。

<!-- 【图 5｜需手动截图：Zotero 自动归档结果】
建议文件：docs/assets/v0.1.0/05-zotero-item.png
截图内容：一个由 literature-agent 保存的真实条目，能看到：
1）标题/作者/期刊或 DOI；
2）所在 collection；
3）PDF attachment。
不要截：API key、WebDAV 用户名/密码、账号邮箱、私人注释。
用途：证明“存 Zotero”最终落到真实资料库。 -->

## 自动运行：多时间点重试和自动补课

在 macOS 上，我用 `launchd` 管理两个 LaunchAgent。

第一个负责 daily：

    07:00
    08:00
    09:00
    10:00

当天只有 `status=ok` 时，后续时间点才跳过重活。如果 07:00 因某个来源失败得到 `partial`，08:00 仍会继续重跑。`failed` 同理。

这个细节也专门修过。把 `partial` 当“成功”会导致第一次运行缺一部分来源以后，后面三个重试点全部失效。

第二个 LaunchAgent 每 60 秒运行：

    sync-overview --all

它只读取本地 Markdown 和 SQLite，不调用模型，也不访问外部论文源，因此成本很低。

系统还记录最后一次完整 `ok` 的 daily。如果电脑几天没有运行，下一次会扩大回溯窗口；缺口更长时触发 historical replay。Apple 对 `launchd` 的文档也说明了基于 `StartCalendarInterval` 的任务在睡眠和唤醒场景下有自己的调度行为，因此我没有把可靠性押在单一时间点上。

## 可审计性：自动化不能只看一句 PASS

这套系统真正稳定下来，是从“功能能跑”转向“工作流可以被重新证明”之后。

现在数据库、状态和文档各自有明确职责：

| 对象 | 角色 |
|---|---|
| `data/literature.db` | 机器事实来源 |
| `state/PROJECT_STATE.json` | 当前状态快照 |
| `docs/WORKFLOW.md` | 当前唯一流程契约 |
| `docs/ARCHITECTURE.md` | 静态架构说明 |
| `docs/HANDOFF.md` | 新 Codex session 的实时接手入口 |
| Obsidian 每日总览 | 用户真正操作的前台 |

审计也尽量检查 invariant，而不检查“今天碰巧应该有几篇”。例如 blocked PDF 可以从 1 变成 0，只要数据库、状态文件和实际 artifact 保持一致，系统仍然健康。

测试层包含：

- golden path
- read / later / ignore / 无操作 / ChatGPT 做 A 等用户路径
- 网络失败、403、invalid PDF、DB rollback、重复运行等恢复测试
- 多日 overview 语义
- partial daily 重试
- Zotero fail-closed
- canonical A 唯一性
- broken link / orphan file 检查
- USER annotation rerun 保护

另外，测试环境会把 DB、Obsidian vault、handoff 和 Zotero queue 全部指向临时目录，避免“测试通过了，同时把真实笔记或 Zotero 库污染了”这种更麻烦的问题。

## 这套系统目前解决了什么

到当前阶段，我真正想解决的只有一件事：**降低文献流对注意力的占用。**

每天的工作被压缩成：

    打开一页每日总览
    → 看 A
    → 勾选决定
    → 重要论文进入 ChatGPT / 1_处理中

后台的 RSS、API、去重、下载、文件移动、状态同步、Zotero 建档和日志，不需要每天手工处理。

它也保留了我很看重的几个边界：

- AI 可以推荐，最终读不读由我决定；
- 一次“拒绝”不会自动改写长期研究兴趣；
- PDF 获取失败不能伪造阅读完成；
- 普通批注不会直接训练或修改 research profile；
- Zotero 保持精选库，而非原始抓取仓库；
- ChatGPT 主要承担交互式深读，Codex 更适合后台批处理和代码维护；
- A/B/C/D 最终仍然服务于“可复述、可引用、可继续研究”的文献理解。

---

# 附录一：工具角色与背景

## Obsidian

Obsidian 是这套系统的人类前台和长期知识库。

用到的核心能力很少：

- Markdown 文件；
- vault-relative internal link；
- Wikilink；
- `![[...]]` embed；
- 文件移动后的链接管理。

官方文档说明，Wikilink 可以指向 vault 内的 note 和 attachment，前置 `!` 可以把被链接内容直接嵌入当前 note；Obsidian 也能在重命名时自动更新内部链接。

推荐目录：

    Literatures/
    ├── 每日文献/
    │   ├── YYYY-MM-DD 文献总览.md
    │   ├── _文献包/
    │   └── _ChatGPT交接/
    ├── 0_待处理/
    ├── 1_处理中/
    ├── 2-可调用/
    ├── 00-已阅/
    └── AI system/

其中：

- `_文献包`：自动完成 A 后的 canonical storage；
- `0_待处理`：用户明确选择 later；
- `1_处理中`：用户明确选择 read；
- `00-已阅`：ignore / 已扫过；
- `2-可调用`：C 已成熟且用户确认。

## Zotero

Zotero 负责正式的文献收藏、元数据和 PDF 管理。

当前系统支持两种方式。

**半自动模式**：

    python3 literature_agent.py zotero-export

生成 BibTeX，再拖进 Zotero。

**全自动模式**：在 `config/local.json` 配置 Zotero API key 和 user ID，并打开 `auto_sync`。

最小结构：

    {
      "zotero": {
        "api_key": "YOUR_API_KEY",
        "user_id": "YOUR_USER_ID",
        "auto_sync": true
      }
    }

如果使用 WebDAV，还可以增加：

    {
      "zotero": {
        "api_key": "YOUR_API_KEY",
        "user_id": "YOUR_USER_ID",
        "auto_sync": true,
        "webdav": {
          "url": "https://your-webdav.example/dav",
          "username": "YOUR_USERNAME",
          "password": "YOUR_PASSWORD"
        }
      }
    }

这些敏感配置只应该存在本机 `config/local.json`，不能提交到 GitHub。

Zotero 官方当前推荐 Web API v3；Web API 使用 HTTPS，写入需要有相应权限的 API key。官方也提供本地 API，可以在 Zotero 设置中允许本机其他应用通信后，通过 localhost 读取本地库。我的系统当前把本地只读和 Web API 写入分别用于不同环节。

## Codex CLI

Codex 是这套系统里的后台 executor 和维护工具。

Python 会准备 A job，Codex 再读取 PDF、Skill 和任务上下文完成 A。它也适合执行批量测试、修复 repo、更新 workflow 和 audit。

OpenAI 官方提供 Codex CLI，可以通过 npm 安装：

    npm install -g @openai/codex

安装和认证方式应以 OpenAI 官方文档的当前版本为准。

## ChatGPT

ChatGPT 主要负责交互式深读。

当每日总览中勾选“要读 + ChatGPT 深读”后，系统会生成交接包：

    paper.pdf
    supplement.pdf     # 如果有
    A.md
    CONTEXT.md

如果勾选“ChatGPT 做 A”，则生成：

    paper.pdf
    supplement.pdf     # 如果有
    CONTEXT.md
    A_REQUEST.md

这样 ChatGPT 不需要依赖某个旧聊天窗口才能理解论文当前处于什么状态。

## macOS launchd

`launchd` 是后台定时器。

当前项目用一个 LaunchAgent 跑 daily，另一个以 60 秒间隔同步总览。Apple 官方文档将 LaunchAgent 定义为代表当前登录用户运行、由 `launchd` 管理的后台进程，并支持 `StartCalendarInterval` 和 `StartInterval` 这类调度方式。

当前项目实际使用 Python 3.12 的绝对路径。原因是本机测试中 `/usr/bin/python3` 在 launchd 环境下遇到 macOS TCC 对 Desktop 目录的访问限制。这个路径属于机器配置，其他用户应该根据自己的 Python 环境修改模板。

---

# 附录二：本机最小配置

克隆项目后，核心机器路径统一写在：

    config/local.json

示意：

    {
      "project_root": "/path/to/literature-agent",
      "obsidian_vault": "/path/to/your/Obsidian/Vault",
      "literatures_root": "/path/to/your/Vault/orange/Literatures",
      "a_skill_path": "/path/to/your/A skill.md",
      "zotero": {
        "api_key": "YOUR_API_KEY",
        "user_id": "YOUR_USER_ID",
        "auto_sync": false
      }
    }

个人研究画像、Zotero taxonomy、tracked authors、citation seeds、数据库、PDF、日志和备份都属于个人数据，不应该提交公共仓库。

当前 `.gitignore` 已排除：

- `config/local.json`
- `data/literature.db*`
- `research_profile.md`
- Zotero 快照和 taxonomy
- tracked entities / citation seeds
- queues / imports
- state / reports / backups / runtime / workspace
- 自动生成的 HANDOFF

---

# 附录三：常用命令

日常入口：

    python3 literature_agent.py daily

手动刷新总览：

    python3 literature_agent.py overview

同步用户勾选：

    python3 literature_agent.py sync-overview --all

查看 A 队列：

    python3 literature_agent.py a-queue

生成 Codex A job：

    python3 literature_agent.py a-job --kind all

标记 A 完成：

    python3 literature_agent.py a-complete <work_id>

单独决定：

    python3 literature_agent.py decide <work_id> read|later|ignore

附加标签：

    python3 literature_agent.py flag <work_id> save_zotero|competitor

Zotero：

    python3 literature_agent.py zotero-export
    python3 literature_agent.py zotero-sync

健康检查：

    python3 literature_agent.py doctor
    python3 literature_agent.py audit
    python3 literature_agent.py audit --full

状态交接：

    python3 literature_agent.py handoff

---

# 附录四：可靠性和隐私边界

这类本地 Agent 很容易在“能跑”以后忽略长期可靠性。我目前重点保留以下约束：

1. 默认 HTTPS 和证书校验；只有显式设置 insecure 环境变量才允许关闭。
2. PDF 必须通过真实 PDF 校验，HTML challenge 不算全文。
3. blocked PDF 单独保留，不伪造 A。
4. work-level dedup，模糊匹配进入人工 review。
5. canonical A 唯一，handoff 中的 A 只是 snapshot。
6. 用户批注区域 rerun 后原样保留。
7. 文件移动后立即刷新 overview / HANDOFF / state。
8. daily 只有 `ok` 才算完整成功，`partial` 继续重试。
9. Zotero dedup 和 PDF child 查询采用 fail-closed。
10. 测试使用临时 DB、临时 vault 和 mock Zotero，禁止污染真实资料库。
11. 普通 read/later/ignore 不自动修改长期 research profile。
12. 本机路径、API key、个人研究画像、数据库和 PDF 不进入 GitHub。

这里还有一个容易忽略的点：`.gitignore` 只能保护当前和未来没有被提交的文件。如果 secret 曾进入 Git 历史，即使后来删除，也可能从历史 commit 找回。因此 v0.1.0 公开前额外做了**完整 Git 历史 secret scan**。

---

# 附录五：当前 GitHub 发布状态

仓库：

**https://github.com/Xin-Jiaqi/literature-agent**

项目已经以 v0.1.0 发布：

**https://github.com/Xin-Jiaqi/literature-agent**

发布信息：

- Release：v0.1.0（personal alpha / first public release）
- License：MIT（Copyright 2026 Xin Jiaqi）
- 发布日期：2026-08-10
- 仓库可见性：Public
- `v0.1.0` tag：`30063c64a95a76ce0a53af56f2f8222ffb57959f`
- 当前 `main` 在 tag 之后仅增加公开文档/Skill，运行代码未改变
- A/B/C/D Skill 与阅读系统总说明：位于 `docs/skills/`

当前版本优先解决个人科研工作流：文献发现、筛选、A 建档入口、Obsidian 每日总览、Zotero 归档和 ChatGPT/Codex 交接。当前并不追求做成通用 SaaS 或跨领域成品。

发布前完成的检查：

1. 本地完整 release gate：selftest / selftest-v2 / overview-test / daily retry / multi-day overview / Zotero fail-closed / audit --full / doctor 全部 PASS；
2. 完整 Git history secret scan（gitleaks 8.30.1 + 真实凭据逐字比对）：0 命中；
3. 决定 LICENSE：MIT；
4. `config/local.example.json` 补充可选 WebDAV 配置，所有敏感字段只放本机 `config/local.json`；
5. README 扩充为公开入口；
6. 仓库 visibility 从 Private 改为 Public；
7. 打 `v0.1.0` release/tag，固定本文对应的首个公开快照；
8. 本文发布时保留 GitHub 链接。

<!-- 【图 6｜需手动截图：GitHub v0.1.0】
建议文件：docs/assets/v0.1.0/06-github-release.png
截图内容：仓库首页或 Releases 页面，能够看见 Public、MIT、v0.1.0。
如果首页无法同时展示三项，可以优先截右侧 Releases + About/License 区域。
用途：文章结尾作为项目公开状态展示。 -->

---

这里刻意不移动已经发布的 `v0.1.0` tag。Release tag 应该保持一个可复现快照；文章润色、截图和后续文档补充继续进入 `main`。

---

# 下一阶段的工作

完成这次发布以后，我计划先停止继续堆功能，用真实工作流跑 5–7 天。

这段时间只记录几个问题：

- 每日总览里真正值得读的比例有多高；
- 是否经常出现“为什么把这篇推给我”；
- 有没有后来从 Google Scholar、公众号或同事那里看到、但 Agent 漏掉的重要论文；
- MUST 是否过多或过少；
- A 是否足以帮助我在几十秒内决定 read/later/ignore；
- ChatGPT 深读交接是否真的降低恢复成本；
- Zotero 自动建档有没有重复、错误 collection 或附件问题。

等积累一段真实使用数据以后，再决定后续功能。

优先级大致是：

1. **先修真实使用中出现的可靠性问题。**
2. **用漏检率评估来源是否够用。** Google Scholar 和公众号先作为外部 benchmark，不急着硬接入。
3. **如果系统推荐过窄，再加入个性化推荐层。** 重点解决未知来源、邻接主题和 serendipity，避免重复现有 research profile 筛选。
4. **最后再做 public/reusable 的工程化。** 包括更通用的研究画像模板、安装器、CI、模块迁移和跨平台支持。

我更希望这套系统最后形成的是一套稳定的个人科研基础设施：后台持续工作，前台保持很短，重要论文能快速进入深读，读完以后又能自然沉淀成以后真正能调用的知识。

---

<!-- 【截图协作清单｜发布前保留，正式粘贴到知乎/公众号前删除】
最终建议 4–6 张图：
1. Obsidian 每日总览（必须）
2. 主流程图（必须）
3. Codex A worker 或 _文献包（可选其一）
4. Obsidian 阶段目录（推荐）
5. Zotero 自动归档结果（必须）
6. GitHub v0.1.0（推荐）

统一放入：docs/assets/v0.1.0/
用户提供截图后，再将这些 HTML 注释替换为正常 Markdown 图片引用并补统一图注。
】 -->

# 参考资料与背景工具官方文档

- Obsidian Internal links：<https://obsidian.md/help/links>
- Obsidian Embed files：<https://obsidian.md/help/embeds>
- Zotero Web API v3：<https://www.zotero.org/support/dev/web_api/v3/>
- Zotero Web API basics：<https://www.zotero.org/support/dev/web_api/v3/basics>
- Zotero file uploads：<https://www.zotero.org/support/dev/web_api/v3/file_upload>
- Zotero Local API：<https://www.zotero.org/support/dev/web_api/v3/local_api>
- Apple Service Management / LaunchAgents：<https://developer.apple.com/documentation/servicemanagement/>
- Apple launchd timed jobs：<https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html>
- OpenAI Codex CLI Getting Started：<https://help.openai.com/en/articles/11096431>

<!-- 编辑备注：公开发布时可以保留官方参考资料，也可以只在公众号文末保留 GitHub 与 3–4 个最核心工具链接。知乎正文不建议把参考链接插进主体段落。 -->
