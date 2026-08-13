#!/bin/bash
# Install the local invite-pilot web service, quick HTTPS tunnel and nightly backup.
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/literature-agent"
UID_NOW="$(id -u)"

test -x "$BASE/.venv/bin/python" || {
    echo "缺少 .venv；请先创建环境并安装 requirements-web.txt" >&2
    exit 1
}
test -x /opt/homebrew/bin/cloudflared || {
    echo "缺少 /opt/homebrew/bin/cloudflared" >&2
    exit 1
}
mkdir -p "$AGENT_DIR" "$LOG_DIR"

for NAME in pilot-backup pilot tunnel; do
    LABEL="com.jiaqi.literature-agent-$NAME"
    TEMPLATE="$BASE/automation/$LABEL.plist.template"
    DEST="$AGENT_DIR/$LABEL.plist"
    sed -e "s|__BASE__|$BASE|g" -e "s|__LOG_DIR__|$LOG_DIR|g" "$TEMPLATE" > "$DEST"
    plutil -lint "$DEST"
    launchctl bootout "gui/$UID_NOW/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_NOW" "$DEST"
    launchctl enable "gui/$UID_NOW/$LABEL"
    if [ "$NAME" = "pilot" ]; then
        sleep 2
    fi
done

echo "试点服务、HTTPS 隧道与每日备份已启用。"
echo "公开地址会记录在 $LOG_DIR/tunnel.err.log。"
