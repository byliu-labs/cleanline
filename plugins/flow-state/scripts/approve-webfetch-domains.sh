#!/usr/bin/env bash
# PreToolUse hook for WebFetch: thin dispatcher to resolve_webfetch.py
# All logic lives in resolve_webfetch.py -- this script just reads stdin and delegates.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
echo "$INPUT" | python3 "$SCRIPT_DIR/resolve_webfetch.py" "$SCRIPT_DIR" 2>/dev/null || true
