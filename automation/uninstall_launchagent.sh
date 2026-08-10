#!/bin/bash
# 卸载 literature-agent 定时任务
set -euo pipefail
DEST="$HOME/Library/LaunchAgents/com.jiaqi.literature-agent.plist"
if [ -f "$DEST" ]; then
    launchctl unload "$DEST" 2>/dev/null || true
    rm -f "$DEST"
    echo "已卸载 $DEST"
else
    echo "未找到 $DEST（无需卸载）"
fi
