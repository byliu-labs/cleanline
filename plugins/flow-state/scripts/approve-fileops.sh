#!/usr/bin/env bash
# PreToolUse hook for file operations: thin dispatcher to resolve_fileops.py
# All logic lives in resolve_fileops.py -- this script just reads stdin and delegates.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
echo "$INPUT" | python3 "$SCRIPT_DIR/resolve_fileops.py" "$SCRIPT_DIR" 2>/dev/null || true
