#!/bin/bash
set -euo pipefail
AGENT_DIR="$HOME/Library/LaunchAgents"
UID_NOW="$(id -u)"
for NAME in pilot tunnel pilot-backup; do
    LABEL="com.jiaqi.literature-agent-$NAME"
    launchctl bootout "gui/$UID_NOW/$LABEL" 2>/dev/null || true
    rm -f "$AGENT_DIR/$LABEL.plist"
done
echo "试点服务已停止并卸载；数据库和备份未删除。"
