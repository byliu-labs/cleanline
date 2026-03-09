#!/bin/bash
# PreToolUse hook for WebFetch: auto-approve URLs to known-safe domains.
#
# Reads tool input JSON from stdin, extracts the URL, parses the hostname
# via parse-url-host.py, then checks against:
#   1. sandbox.network.allowedDomains from ~/.claude/settings.json
#   2. webfetch.extraDomains from permission-config.json
#   3. merged.webfetch.extraDomains from ~/.claude/hooks/profiles.lock.json
#
# If matched: outputs {"decision":"allow"} JSON
# If not matched or any error: exits silently (normal permission flow)

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOOK_DIR/permission-config.json"
LOCKFILE="$HOME/.claude/hooks/profiles.lock.json"
AUDIT_LOG="$HOME/.claude/hooks/hook.jsonl"

INPUT=$(cat)

# ============================================================================
# AUDIT LOGGING
# ============================================================================
log_decision() {
  local tool="$1" input="$2" decision="$3" rule="$4"
  (
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null
    printf '{"ts":"%s","tool":"%s","input":"%s","decision":"%s","matched_rule":"%s"}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$tool" "$input" "$decision" "$rule" \
      >> "$AUDIT_LOG"
  ) 2>/dev/null || true
}

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

# Build combined domain list: sandbox allowedDomains + extraDomains + profile domains
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
if [ -f "$LOCKFILE" ]; then
  PROFILE_EXTRA=$(jq -r '.merged.webfetch.extraDomains // [] | .[]' "$LOCKFILE" 2>/dev/null) || true
  if [ -n "$PROFILE_EXTRA" ]; then
    DOMAINS="$DOMAINS"$'\n'"$PROFILE_EXTRA"
  fi
fi

if [ -z "$DOMAINS" ]; then
  log_decision "WebFetch" "$HOST" "passthrough" "no_domains_configured"
  exit 0
fi

# Check hostname against each domain pattern
while IFS= read -r pattern; do
  [ -z "$pattern" ] && continue

  # Exact match (e.g., "github.com" matches "github.com")
  if [ "$HOST" = "$pattern" ]; then
    log_decision "WebFetch" "$HOST" "allow" "domain:$pattern"
    echo '{"decision":"allow"}'
    exit 0
  fi

  # Wildcard match: *.example.com matches sub.example.com but NOT example.com
  if [[ "$pattern" == \*.* ]]; then
    # Strip the leading "*" to get ".example.com"
    suffix="${pattern#\*}"
    if [[ "$HOST" == *"$suffix" ]]; then
      log_decision "WebFetch" "$HOST" "allow" "domain:$pattern"
      echo '{"decision":"allow"}'
      exit 0
    fi
  fi
done <<< "$DOMAINS"

# No match — exit silently, let normal permission flow handle it
log_decision "WebFetch" "$HOST" "passthrough" "no_match"
exit 0
