# automation — macOS LaunchAgent 定时运行

## 个人版两个 agent

1. **每日 daily**：`com.jiaqi.literature-agent.plist`
   - 每天 **07:00 / 08:00 / 09:00 / 10:00** 各跑一次 `literature_agent.py daily`。
   - 当天只有 **status=ok（完整成功）** 时，后续时间点才跳过重活，只做轻量批注同步；**partial（有来源失败）与 failed 都允许后续时间点重跑**，补课窗口按最后一次 ok 计算。
   - 错过（关机/睡眠/无网）的时间点：唤醒后 launchd 补跑；`daily` 内置自动补课（>1 天拉大窗口、>7 天 historical-replay）。

2. **批注近实时同步**：`com.jiaqi.literature-agent-sync.plist`
   - 每 60 秒跑 `literature_agent.py sync-overview --all`。
   - 纯本地：只读总览批注 + DB，无网络、无模型调用、不消耗 agent 额度；勾选后约 1 分钟内生效。

## 安装 / 卸载

```bash
./automation/install_launchagent.sh    # 生成、校验、安装并启用 daily agent
./automation/install_sync_agent.sh     # 生成、校验、安装并启用 sync agent
./automation/uninstall_launchagent.sh  # unload + 删除 daily plist
```

安装脚本会自动重载同名服务，不需要再手动 `launchctl load`。手动触发验证：

```bash
launchctl kickstart -k gui/$(id -u)/com.jiaqi.literature-agent
launchctl kickstart -k gui/$(id -u)/com.jiaqi.literature-agent-sync
```

## 关键注意

- **Python 解释器必须用 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`**（plist 模板已写死）。
  不要改回 `/usr/bin/python3`：CLT 二进制在 launchd 下会被 macOS TCC 拦截，无法读取 Desktop 目录（2026-08-10 已实测）。
- LaunchAgent 日志：`~/Library/Logs/literature-agent/`。不要把 stdout/stderr 放在 Desktop
  项目目录；macOS TCC 可能在 Python 启动前拒绝 `xpcproxy` 打开日志，表现为
  `launchctl` 的 `last exit code = 78: EX_CONFIG`，而项目日志仍为空。
- Python 内部的逐阶段运行日志仍在 `runtime/logs/run-*.jsonl`。
- 建议先手动跑通 `python3 literature_agent.py daily` 再启用。
- `daily` 做的事：collect → dedup → screen → process（PDF/SI）→ A 队列准备 → digest（v4 用户向）→ 总览 → zotero-export → handoff。
  A 的生成本身由 Codex/ChatGPT executor 完成，不在 Python 后台内调用模型。

## 邀请试点服务

首次安装 Web 依赖后，可启用三个独立的本机服务：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
./automation/install_pilot_service.sh
```

- `pilot`：只监听本机 `127.0.0.1:8765`，使用生产 WSGI server；
- `tunnel`：通过 Cloudflare Quick Tunnel 提供 HTTPS 试用地址，不开放路由器入站端口；
- `pilot-backup`：每天 02:30 做 SQLite 一致性备份并校验，保留 14 份。

Quick Tunnel 每次重启会更换地址且无可用性保证，只适合首轮熟人测试。当前地址可在
`~/Library/Logs/literature-agent/tunnel.err.log` 搜索 `trycloudflare.com`。正式扩展时应换为具名
Tunnel + 自有域名，或迁移到托管服务器。

停止服务不会删除数据库和备份：

```bash
./automation/uninstall_pilot_service.sh
```
