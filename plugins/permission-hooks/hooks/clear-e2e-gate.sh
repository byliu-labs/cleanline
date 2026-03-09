#!/bin/bash
# Global post-commit hook: clear the E2E gate marker after a successful
# git commit so the next commit requires fresh testing.

set -euo pipefail

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
if [ -z "$COMMAND" ]; then
  exit 0
fi

if echo "$COMMAND" | grep -qE '^\s*git commit'; then
  rm -f /tmp/.claude-e2e-gate
fi

exit 0
