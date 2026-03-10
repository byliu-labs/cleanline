#!/bin/bash
# PreToolUse hook for Bash: metacharacter rejection + alias resolution +
# multi-word command mapping.
#
# Auto-approves aliased commands (e.g., python3.13 → python) and equivalent
# multi-word commands (e.g., "npx jest" → "npm test") when the canonical
# form is already in the settings.json allow list.
#
# Reads from two config sources:
#   1. permission-config.json (user's own rules)
#   2. ~/.claude/hooks/profiles.lock.json (profile-managed rules under "merged")
#
# Decision flow:
#   Step 1: METACHARACTER REJECTION — compound commands → exit silently
#   Step 2: NORMALIZE — shlex-based Python helper extracts binary name
#   Step 3: ALIAS LOOKUP — check bashAliases in config, then lock file
#   Step 4: MULTI-WORD COMMAND MAPPING — check commandMappings in config, then lock file
#
# If no decision made: exits silently (normal permission flow)
#
# NOTE: E2e commit gate is handled separately by require-e2e-gate.sh
# under the Bash(git commit*) matcher.

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOOK_DIR/permission-config.json"
LOCKFILE="$HOME/.claude/hooks/profiles.lock.json"

INPUT=$(cat)

# ============================================================================
# AUDIT LOGGING
# Uses Python helper for proper JSON escaping of special characters in
# commands (quotes, backslashes, etc.). Non-blocking via subshell.
# ============================================================================
log_decision() {
  local tool="$1" input="$2" decision="$3" rule="$4"
  ( "$HOOK_DIR/log_event.py" "$tool" "$input" "$decision" "$rule" ) 2>/dev/null || true
}

# ============================================================================
# SETTINGS CHECK HELPER
# Returns 0 if the canonical command is in the settings.json allow list.
# ============================================================================
check_settings_allow() {
  local canon="$1"
  if [ ! -f "$SETTINGS" ]; then
    return 1
  fi
  local matched
  matched=$(jq -r --arg canon "$canon" \
    '[.permissions.allow // [] | .[] |
      select(
        . == "Bash(" + $canon + " *)" or
        . == "Bash(" + $canon + ")"
      )] | length' "$SETTINGS" 2>/dev/null) || return 1
  [ "$matched" -gt 0 ]
}

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
  log_decision "Bash" "$COMMAND" "passthrough" "metacharacter"
  exit 0
fi

# ============================================================================
# STEP 2: NORMALIZE via Python helper
# ============================================================================
BINARY=$("$HOOK_DIR/normalize-bash-cmd.py" "$COMMAND" 2>/dev/null) || exit 0
if [ -z "$BINARY" ]; then
  exit 0
fi

# ============================================================================
# STEP 3: ALIAS LOOKUP
# Check if binary is an alias for a canonical name that's in the allow list.
# First check permission-config.json, then fall through to lock file.
# ============================================================================
CANONICAL=""
if [ -f "$CONFIG" ]; then
  CANONICAL=$(jq -r --arg bin "$BINARY" '.bashAliases[$bin] // empty' "$CONFIG" 2>/dev/null)
fi

# Fall through to lock file if no match in local config
if [ -z "$CANONICAL" ] && [ -f "$LOCKFILE" ]; then
  CANONICAL=$(jq -r --arg bin "$BINARY" '.merged.bashAliases[$bin] // empty' "$LOCKFILE" 2>/dev/null)
fi

if [ -n "$CANONICAL" ] && check_settings_allow "$CANONICAL"; then
  log_decision "Bash" "$COMMAND" "allow" "alias:$BINARY->$CANONICAL"
  echo '{"decision":"allow"}'
  exit 0
fi

# ============================================================================
# STEP 4: MULTI-WORD COMMAND MAPPING
# Check if command matches a multi-word alias (e.g., "npx jest" → "npm test")
# First check permission-config.json, then fall through to lock file.
# ============================================================================
CANONICAL=""
if [ -f "$CONFIG" ]; then
  CANONICAL=$("$HOOK_DIR/match-command-equiv.py" "$COMMAND" "$CONFIG" 2>/dev/null) || true
fi

# Fall through to lock file if no match in local config
if [ -z "$CANONICAL" ] && [ -f "$LOCKFILE" ]; then
  CANONICAL=$("$HOOK_DIR/match-command-equiv.py" "$COMMAND" "$LOCKFILE" "merged" 2>/dev/null) || true
fi

if [ -n "$CANONICAL" ] && check_settings_allow "$CANONICAL"; then
  log_decision "Bash" "$COMMAND" "allow" "mapping:$CANONICAL"
  echo '{"decision":"allow"}'
  exit 0
fi

# No match — log passthrough and defer to normal permission flow
log_decision "Bash" "$COMMAND" "passthrough" "no_match"
exit 0
