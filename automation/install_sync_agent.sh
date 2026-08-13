#!/bin/bash
# 安装 literature-agent 批注同步轻量 agent：每 60 秒跑一次 sync-overview --all。
# 只读总览批注并应用（本地文件 + DB，无网络收集、无模型调用、不消耗 agent 额度）。
# 用法: ./install_sync_agent.sh
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.jiaqi.literature-agent-sync.plist"
LOG_DIR="$HOME/Library/Logs/literature-agent"
UID_NOW="$(id -u)"
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
sed -e "s|__BASE__|$BASE|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$BASE/automation/com.jiaqi.literature-agent-sync.plist.template" > "$DEST"
plutil -lint "$DEST"
launchctl bootout "gui/$UID_NOW/com.jiaqi.literature-agent-sync" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NOW" "$DEST"
launchctl enable "gui/$UID_NOW/com.jiaqi.literature-agent-sync"
echo "已安装并启用: ${DEST}（每 60 秒运行 sync-overview --all）"
echo "日志目录: $LOG_DIR"
echo "立即验证: launchctl kickstart -k gui/$UID_NOW/com.jiaqi.literature-agent-sync"
