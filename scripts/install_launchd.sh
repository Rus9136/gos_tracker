#!/usr/bin/env bash
# Установка ежедневного launchd-агента (запуск в 06:00 локального времени).
# Запустить ОДИН РАЗ вручную.

set -euo pipefail

SRC="/Users/rus/Projects/goszakup/scripts/com.user.goszakup.daily.plist"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST="$DEST_DIR/com.user.goszakup.daily.plist"

mkdir -p "$DEST_DIR"
cp -f "$SRC" "$DEST"

# Перезагружаем агент (выгружаем, если уже стоял)
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "installed: $DEST"
echo "list:"
launchctl list | grep goszakup || true
echo
echo "  ручной запуск:  launchctl start com.user.goszakup.daily"
echo "  снять с авто:   launchctl unload $DEST"
