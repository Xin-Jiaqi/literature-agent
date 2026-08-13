#!/bin/bash
# 安装 literature-agent 每日定时任务：每天 07:00 / 08:00 / 09:00 / 10:00 各跑一次 daily。
# 多时间点兜底：某次错过（关机/无网）时下一次自动补；daily 幂等，重复跑无害。
# 用法: ./install_launchagent.sh
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.jiaqi.literature-agent.plist"
LOG_DIR="$HOME/Library/Logs/literature-agent"
UID_NOW="$(id -u)"
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
sed -e "s|__BASE__|$BASE|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$BASE/automation/com.jiaqi.literature-agent.plist.template" > "$DEST"
plutil -lint "$DEST"
launchctl bootout "gui/$UID_NOW/com.jiaqi.literature-agent" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NOW" "$DEST"
launchctl enable "gui/$UID_NOW/com.jiaqi.literature-agent"
echo "已安装并启用: ${DEST}（每天 07:00/08:00/09:00/10:00 运行 daily）"
echo "日志目录: $LOG_DIR"
echo "立即验证: launchctl kickstart -k gui/$UID_NOW/com.jiaqi.literature-agent"
