# 单篇论文阅读 Skill：公开发布记录

## 用户确认的方向（2026-11-01）

1. 盘活目前闲置的 GitHub 仓库，优先复用已有成果。
2. 追求影响力、真实使用和简历亮点，不以收入为目标。
3. 科研优先；这是 side project，限制新增开发和维护负担。

首次准备工作以 6–10 小时为上限，不自动扩大为付费产品、SaaS、多用户服务或完整科研平台。尚无完整累计工时统计。后续默认集中、低频维护，不承诺即时支持。

## 首发交付

免费、MIT 的 `paper-triage`：一篇论文 + 研究目标 → 带证据位置和下一步动作的阅读决策。英文指令，按用户语言输出；无个人领域画像、本机路径、模型端点或额外服务。

- 使用入口：[Paper Triage](paper-triage/README.md)。
- 技能源文件：`skills/paper-triage/SKILL.md`；repo-local 发现入口：`.agents/skills/paper-triage`。
- 两篇真实论文的精简案例：[理论](paper-triage/examples/theory.md)、[实验](paper-triage/examples/experiment.md)。
- 验证依据与限制：[VALIDATION.md](paper-triage/VALIDATION.md)。
- Agensi 表单逐项文案：[AGENSI_LISTING.md](paper-triage/AGENSI_LISTING.md)。
- 对外介绍、邮件及简历措辞草稿：[LAUNCH_COPY.md](paper-triage/LAUNCH_COPY.md)；均未发送。
- 图标 512 × 512、封面 1280 × 640，见 `docs/paper-triage/assets/`。
- 可复现打包：`python3 scripts/package_paper_triage.py`；仅包含 SKILL.md 与 MIT 许可，不附带论文或私人数据。

## 验证状态

- [x] Skill 格式校验通过；公开 Markdown 本地文件链接检查通过。
- [x] 两篇原文案例制作与证据核对；明确访问范围及未读 SI。
- [x] 同一 authoring chat 显式加载 Skill 并读取实际源文；未声称独立评测。
- [x] 临时目录解包安装结构检查、symlink 解析检查、确定性打包与防覆盖检查通过。
- [ ] 新会话自动发现／触发与其他智能体安装，尚未实际验证。
- [x] 三种合成边界请求的同作者检查记录：摘要可用、缺研究目标、源文夹带指令；不等同于独立安全测试。
- [ ] 外部研究者的首次与重复使用，尚未测量。

发布包 SHA256：`2eda1d0ac5c3ccf0f41a65d44be4a9ac039f7ebfec468323ac64faad2092e15d`。

完整项目 selftest：个人配置环境本轮返回 `SELFTEST ALL PASS`。公开 main 的干净检出在既有筛选回归守卫失败，原因为被忽略的本地金标集缺失（gold=0）。独立 Skill 校验、打包验证与完整雷达回归结果分别记录，不互相替代；本次不修改或公开私人金标数据。

## 发布状态与账号步骤

- 从公开 main 独立准备 `codex/paper-triage-launch`，仅提取技能、文档、发现入口和打包工具，未包含 campus pilot 开发。
- GitHub [PR #1](https://github.com/Xin-Jiaqi/literature-agent/pull/1) 已合并；合并提交 `d13f69ef87d13202a8a782efd0a86acbdb03c041`。
- [Paper Triage 0.1.0](https://github.com/Xin-Jiaqi/literature-agent/releases/tag/paper-triage-v0.1.0) 已公开发布，附件 `paper-triage-v0.1.0.zip` 为 3243 字节，GitHub 返回的 SHA256 与本地包一致。未将其设为整套 literature-agent 应用的新版本。
- staged diff 的 gitleaks 扫描未发现 secret；作者完成发布内容与打包脚本复核（非独立 reviewer）。handoff 已于北京时间 15:56:29 刷新。
- Agensi：用户已登录；Creator Mode 与公开资料已设置。Paper Triage 已正式提交审核，后台显示 **Pending Review / In review / Free**，统计为 1 Total Skills、0 Live、1 Pending；尚未获批公开上线。私有回执与截图保存在忽略的 `reports/release/agensi-submission-progress.json` 和 `reports/release/agensi-submitted.jpg`。
- Mac 解锁后已完成标签、使用场景与最终信息复核并确认提交。后续只需等待平台审核；未代用户接受新协议，未设置收款或付费功能。
- 邮件、私信、社交帖子均未发送；公开 GitHub 发布与人际传播分开记录。

平台要求核对日期：2026-11-01；依据 [打包要求](https://www.agensi.io/docs/packaging)、[提交表单](https://www.agensi.io/docs/submit-on-the-website)。

## 发布后取舍

只记录实际取得的用户数、再次使用和具体反馈，并区分平台自然流量与自行邀请用户。未测量的数据保持未测量，不写入简历；上架或被转发不等于需求验证。发布一个月后可手动复盘；未设置自动提醒。不主动开发 B/C/D 全家桶、付费收款或持续托管功能。
