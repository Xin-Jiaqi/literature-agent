#!/bin/bash
# 安装 literature-agent 每日定时任务：每天 07:00 / 08:00 / 09:00 / 10:00 各跑一次 daily。
# 多时间点兜底：某次错过（关机/无网）时下一次自动补；daily 幂等，重复跑无害。
# 用法: ./install_launchagent.sh
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.jiaqi.literature-agent.plist"
mkdir -p "$BASE/runtime/logs"
sed -e "s|__BASE__|$BASE|g" \
    "$BASE/automation/com.jiaqi.literature-agent.plist.template" > "$DEST"
plutil -lint "$DEST"
echo "plist 已生成: ${DEST}（每天 07:00/08:00/09:00/10:00 运行 daily）"
echo "启用："
echo "  launchctl load $DEST"
echo "取消："
echo "  launchctl unload $DEST"
