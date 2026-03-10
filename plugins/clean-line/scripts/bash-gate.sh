#!/usr/bin/env bash
# PreToolUse hook for Bash: thin dispatcher to resolve.py
# All logic lives in resolve.py -- this script just reads stdin and delegates.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
echo "$INPUT" | python3 "$SCRIPT_DIR/resolve.py" "$SCRIPT_DIR" 2>/dev/null || true
