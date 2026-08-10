#!/bin/bash
# 安装 literature-agent 批注同步轻量 agent：每 60 秒跑一次 sync-overview --all。
# 只读总览批注并应用（本地文件 + DB，无网络收集、无模型调用、不消耗 agent 额度）。
# 用法: ./install_sync_agent.sh
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.jiaqi.literature-agent-sync.plist"
mkdir -p "$BASE/runtime/logs"
sed -e "s|__BASE__|$BASE|g" \
    "$BASE/automation/com.jiaqi.literature-agent-sync.plist.template" > "$DEST"
plutil -lint "$DEST"
echo "plist 已生成: ${DEST}（每 60 秒运行 sync-overview --all）"
echo "启用："
echo "  launchctl load $DEST"
echo "取消："
echo "  launchctl unload $DEST"
