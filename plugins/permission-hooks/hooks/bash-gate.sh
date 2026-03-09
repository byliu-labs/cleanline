#!/bin/bash
# PreToolUse hook for Bash: metacharacter rejection + alias resolution.
#
# Auto-approves aliased commands (e.g., python3.13 → python) when the
# canonical binary is already in the settings.json allow list.
#
# Decision flow:
#   Step 1: METACHARACTER REJECTION — compound commands → exit silently
#   Step 2: NORMALIZE — shlex-based Python helper extracts binary name
#   Step 3: ALIAS LOOKUP — check against bashAliases → allow if canonical is permitted
#
# If no decision made: exits silently (normal permission flow)
#
# NOTE: E2e commit gate is handled separately by require-e2e-gate.sh
# under the Bash(git commit*) matcher.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOOK_DIR/permission-config.json"

INPUT=$(cat)

# Extract the command
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
if [ -z "$COMMAND" ]; then
  exit 0
fi

# ============================================================================
# STEP 1: METACHARACTER REJECTION
# Compound commands can't be safely parsed — fall through to normal permissions.
# ============================================================================
if echo "$COMMAND" | grep -qF '&&' || \
   echo "$COMMAND" | grep -qF '||' || \
   echo "$COMMAND" | grep -qF '|'  || \
   echo "$COMMAND" | grep -qF ';'  || \
   echo "$COMMAND" | grep -qF '`'  || \
   echo "$COMMAND" | grep -qF '$(' || \
   echo "$COMMAND" | grep -qF '>(' || \
   echo "$COMMAND" | grep -qF '<(' || \
   echo "$COMMAND" | grep -qF '{'  || \
   echo "$COMMAND" | grep -qF '}'; then
  exit 0
fi

# ============================================================================
# STEP 3: NORMALIZE via Python helper
# ============================================================================
BINARY=$("$HOOK_DIR/normalize-bash-cmd.py" "$COMMAND" 2>/dev/null) || exit 0
if [ -z "$BINARY" ]; then
  exit 0
fi

# ============================================================================
# STEP 4: ALIAS LOOKUP
# Check if binary is an alias for a canonical name that's in the allow list.
# ============================================================================
if [ ! -f "$CONFIG" ]; then
  exit 0
fi

CANONICAL=$(jq -r --arg bin "$BINARY" '.bashAliases[$bin] // empty' "$CONFIG" 2>/dev/null)
if [ -z "$CANONICAL" ]; then
  exit 0
fi

# Check if the canonical binary is in the settings.json allow list
# Match patterns like "Bash(python *)" or "Bash(python)"
if [ -f "$SETTINGS" ]; then
  # Check for "Bash(<canonical> *)" or "Bash(<canonical>)" in the allow list
  MATCHED=$(jq -r --arg canon "$CANONICAL" \
    '[.permissions.allow // [] | .[] |
      select(
        . == "Bash(" + $canon + " *)" or
        . == "Bash(" + $canon + ")"
      )] | length' "$SETTINGS" 2>/dev/null) || exit 0

  if [ "$MATCHED" -gt 0 ]; then
    echo '{"decision":"allow"}'
    exit 0
  fi
fi

exit 0
