# automation — macOS LaunchAgent 定时运行

- `com.jiaqi.literature-agent.plist.template`：plist 模板（`__BASE__/__HOUR__/__MINUTE__` 占位符）。
- `install_launchagent.sh [HOUR] [MINUTE]`：生成 plist 到 `~/Library/LaunchAgents/`（默认 08:30），**不自动 load**。
- `uninstall_launchagent.sh`：unload + 删除 plist。

## 手动启用/停用

```bash
launchctl load   ~/Library/LaunchAgents/com.jiaqi.literature-agent.plist
launchctl unload ~/Library/LaunchAgents/com.jiaqi.literature-agent.plist
```

## 说明

- 每天运行 `python3 literature_agent.py daily`（collect → screen → process → digest v2）。
- 日志：`logs/launchd.out.log` / `logs/launchd.err.log`（与 run-*.jsonl 分开，随系统轮转）。
- 建议先手动跑通 `python3 literature_agent.py daily` 再启用。
