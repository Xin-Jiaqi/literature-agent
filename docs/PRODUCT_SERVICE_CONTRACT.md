# 校内试点产品服务契约（Week 1）

> 本文定义 Web 路由下一步应调用的应用服务边界。当前实现是 framework-neutral Python service，不开放公网，也不包含登录实现。

## 1. 信任边界

- Web 层负责验证会话，并得到可信的 `actor=user_id`；
- Web 层不得接受客户端传入的 `owner_user_id`；
- 所有私有操作必须把 `actor` 传给 `ProductService`；
- 服务层和数据库层都校验项目归属；
- 论文标题、摘要、PDF 与模型输出均视为不可信内容，不能成为 SQL、路径或命令；
- 校园统一认证密码、出版商账号、模型密钥不进入当前产品表。

## 2. 已实现的最小服务

| 产品操作 | 服务方法 | 输入上限与规则 |
|---|---|---|
| 创建研究项目 | `create_project(actor, payload)` | 仅允许标题、说明、每日预算；每日预算 1–20 |
| 确认研究画像 | `confirm_profile(actor, project_id, payload)` | 至少一个 include term；未知字段拒绝 |
| 添加种子论文 | `add_seed(actor, project_id, payload)` | work 必须已存在于公共论文库 |
| 生成项目推荐 | `screen_project(actor, project_id, work_ids, date)` | 每批 1–500 篇；画像必须已确认 |
| 查看每日推荐 | `daily_feed(actor, date)` | 只返回 actor 自己的数据 |
| 作出阅读决定 | `decide(actor, recommendation_id, payload)` | 仅 `read/later/ignore` |
| 查看成本 | `usage_summary(actor)` | 只累计本用户成功事件 |

## 3. 推荐契约

- 优先级只允许 `MUST / WEEKLY / RADAR / DROP`；
- A/B/C/D 不进入推荐优先级字段；
- 相同 `work_id` 可对不同项目产生不同优先级；
- 推荐保存生成时使用的 `profile_revision` 与 `rank_score`，便于回放和解释；
- 同一用户、项目、论文、日期重复运行时更新原推荐，不新增重复记录；
- 每日 `MUST + WEEKLY` 不超过项目的 `daily_attention_limit`，超出的候选降为 `RADAR`；
- 未确认画像不得产生推荐。

## 4. 当前筛选算法的定位

Week 1 使用确定性关键词评分验证多用户边界：

- 标题命中权重大于摘要命中；
- 新颖性词只能增强已经命中项目主题的论文，不能独立制造相关性；
- 排除主题会显著降权；
- 输出包含命中词、排除词、置信度、分数与画像修订号。

该算法不是最终推荐质量方案。后续可以在保持输入输出契约不变的前提下，接入种子论文相似度、领域规则、embedding 或模型复核。任何模型输出仍只是候选数据，最终枚举、额度和权限由代码约束。

## 5. 后续 HTTP 映射建议

```text
POST /v1/projects
PUT  /v1/projects/{project_id}/profile
POST /v1/projects/{project_id}/seeds
POST /v1/projects/{project_id}/screen
GET  /v1/recommendations/daily?date=YYYY-MM-DD
POST /v1/recommendations/{recommendation_id}/decision
GET  /v1/usage/summary
```

路由上线前仍需补齐：邀请登录、httpOnly 会话、CSRF/CORS、安全响应头、速率限制、统一错误格式和分页。当前服务契约不应被误认为已经具备公网安全条件。
