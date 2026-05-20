#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/Users/rus/Projects/goszakup"
LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/daily-$STAMP.log"

cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" -m goszakup.cli daily >"$LOG" 2>&1
