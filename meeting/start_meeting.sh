#!/usr/bin/env bash
# ── 腾讯会议自动参会 — 快捷启动脚本 ────────────────────────────────
#  用法：
#   ./start_meeting.sh --url "https://meeting.tencent.com/dm/xxxx" --title "周例会"
#   ./start_meeting.sh --code "123456789" --title "周例会" --duration 90
#   ./start_meeting.sh --transcribe-only recordings/meeting.wav --title "历史会议"
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT="$SCRIPT_DIR/meeting_bot.py"

# 杀掉可能残留的虚拟显示/录音进-
pkill -f Xvfb         2>/dev/null || true
pkill -f pulseaudio    2>/dev/null || true
pkill -f ffmpeg        2>/dev/null || true
sleep 0.5

echo "🚀 启动腾讯会议机器人..."
exec /opt/hermes/project/.venv/bin/python "$BOT" "$@"