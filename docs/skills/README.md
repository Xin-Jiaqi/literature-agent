# A / B / C / D 阅读 Skill

本目录公开 `literature-agent` 配套的文献阅读 Skill 与系统总说明（与 Obsidian vault `Literatures/AI system/` 中的个人版本保持同步）。

| 文件 | 阶段 | 作用 |
|---|---|---|
| `AI 辅助重要文献阅读系统总说明.md` | — | A–D 系统总览、文件夹语义、协作方式、上下文边界 |
| `A. 重要文献快速建档 Skill.md` | A | 正文 PDF → 快速建档，判断值不值得读 |
| `B. 处理中对话总结 Skill.md` | B | 精读中的自由问答 → 低认知负担处理中草稿 |
| `C. 最终文献笔记融合 Skill.md` | C | 快速建档 + 处理中草稿 → 最终可调用文献笔记 |
| `D. 文献发表与汇报转化 Skill.md` | D | 可调用笔记 → 知乎 / 公众号 / 小红书 / Beamer 大纲 |

## 定位

- 这是个人工作流基线：中文输出、按研究方向的偏好（例如 BPVE、交错磁、群论与对称性工程等）会体现在具体内容中，使用时请按自己的领域调整。
- A/B/C/D 是**阅读轴**，与推荐轴的 `MUST / WEEKLY / RADAR / DROP` 相互独立。
- 系统只保证“发现 → 去重 → 分级 → PDF/SI → A queue/job → 每日总览”的自动化；A 正文由 Codex / ChatGPT executor 按 A Skill 完成。
- 本目录中的 Skill 不包含任何本机路径、API key 或个人数据。

## 与 literature-agent 的关系

- `python3 literature_agent.py a-job --kind all` 生成 A job 清单，executor 读取 PDF 与本目录 A Skill 生成 A，再以 `a-complete` 校验发布。
- 每日总览中的“ChatGPT 深读 / ChatGPT 做 A”交接也以 A/B/C/D 的语义为准。
