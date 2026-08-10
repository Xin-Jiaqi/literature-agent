# automation — macOS LaunchAgent 定时运行

## 两个 agent（都已安装并 load）

1. **每日 daily**：`com.jiaqi.literature-agent.plist`
   - 每天 **07:00 / 08:00 / 09:00 / 10:00** 各跑一次 `literature_agent.py daily`。
   - 当天只有 **status=ok（完整成功）** 时，后续时间点才跳过重活，只做轻量批注同步；**partial（有来源失败）与 failed 都允许后续时间点重跑**，补课窗口按最后一次 ok 计算。
   - 错过（关机/睡眠/无网）的时间点：唤醒后 launchd 补跑；`daily` 内置自动补课（>1 天拉大窗口、>7 天 historical-replay）。

2. **批注近实时同步**：`com.jiaqi.literature-agent-sync.plist`
   - 每 60 秒跑 `literature_agent.py sync-overview --all`。
   - 纯本地：只读总览批注 + DB，无网络、无模型调用、不消耗 agent 额度；勾选后约 1 分钟内生效。

## 安装 / 卸载

```bash
./automation/install_launchagent.sh    # 生成并 lint daily plist（按脚本末尾提示手动 launchctl load）
./automation/install_sync_agent.sh     # 生成并 lint sync plist（按提示手动 launchctl load）
./automation/uninstall_launchagent.sh  # unload + 删除 daily plist
```

手动启用/停用：

```bash
launchctl load   ~/Library/LaunchAgents/com.jiaqi.literature-agent.plist
launchctl unload ~/Library/LaunchAgents/com.jiaqi.literature-agent.plist
```

## 关键注意

- **Python 解释器必须用 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`**（plist 模板已写死）。
  不要改回 `/usr/bin/python3`：CLT 二进制在 launchd 下会被 macOS TCC 拦截，无法读取 Desktop 目录（2026-08-10 已实测）。
- 日志：`runtime/logs/launchd.out.log` / `launchd.err.log`（daily）与 `sync.launchd.out.log` / `sync.launchd.err.log`（批注同步）。
- 建议先手动跑通 `python3 literature_agent.py daily` 再启用。
- `daily` 做的事：collect → dedup → screen → process（PDF/SI）→ A 队列准备 → digest（v4 用户向）→ 总览 → zotero-export → handoff。
  A 的生成本身由 Codex/ChatGPT executor 完成，不在 Python 后台内调用模型。
