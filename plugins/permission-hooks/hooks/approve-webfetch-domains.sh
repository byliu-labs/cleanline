#!/bin/bash
# PreToolUse hook for WebFetch: auto-approve URLs to known-safe domains.
#
# Reads tool input JSON from stdin, extracts the URL, parses the hostname
# via parse-url-host.py, then checks against:
#   1. sandbox.network.allowedDomains from ~/.claude/settings.json
#   2. webfetch.extraDomains from permission-config.json
#
# If matched: outputs {"decision":"allow"} JSON
# If not matched or any error: exits silently (normal permission flow)

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOOK_DIR/permission-config.json"

INPUT=$(cat)

# Extract URL from tool input
URL=$(echo "$INPUT" | jq -r '.tool_input.url // empty' 2>/dev/null)
if [ -z "$URL" ]; then
  exit 0
fi

# Parse hostname via Python helper (fail closed)
HOST=$("$HOOK_DIR/parse-url-host.py" "$URL" 2>/dev/null) || exit 0
if [ -z "$HOST" ]; then
  exit 0
fi

# Build combined domain list: sandbox allowedDomains + extraDomains
DOMAINS=""
if [ -f "$SETTINGS" ]; then
  DOMAINS=$(jq -r '.sandbox.network.allowedDomains // [] | .[]' "$SETTINGS" 2>/dev/null) || true
fi
if [ -f "$CONFIG" ]; then
  EXTRA=$(jq -r '.webfetch.extraDomains // [] | .[]' "$CONFIG" 2>/dev/null) || true
  if [ -n "$EXTRA" ]; then
    DOMAINS="$DOMAINS"$'\n'"$EXTRA"
  fi
fi

if [ -z "$DOMAINS" ]; then
  exit 0
fi

# Check hostname against each domain pattern
while IFS= read -r pattern; do
  [ -z "$pattern" ] && continue

  # Exact match (e.g., "github.com" matches "github.com")
  if [ "$HOST" = "$pattern" ]; then
    echo '{"decision":"allow"}'
    exit 0
  fi

  # Wildcard match: *.example.com matches sub.example.com but NOT example.com
  if [[ "$pattern" == \*.* ]]; then
    # Strip the leading "*" to get ".example.com"
    suffix="${pattern#\*}"
    if [[ "$HOST" == *"$suffix" ]]; then
      echo '{"decision":"allow"}'
      exit 0
    fi
  fi
done <<< "$DOMAINS"

# No match — exit silently, let normal permission flow handle it
exit 0
