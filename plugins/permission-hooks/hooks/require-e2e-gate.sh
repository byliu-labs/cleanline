#!/bin/bash
# Pre-commit hook: block git commit unless E2E testing gate was passed.
#
# Only enforced if the project has opted in by having .claude/e2e-required
# in the project root. User-facing products should have this file.
# Scripts and quick projects don't need it.
#
# Flow:
#   1. Claude runs E2E/integration tests, pastes output
#   2. Claude runs: echo "E2E_GATE_PASSED" > /tmp/.claude-e2e-gate
#   3. Claude runs: git commit ...
#   4. This hook checks for the marker — blocks if missing
#
# The post-commit hook deletes the marker so each commit needs fresh testing.

set -euo pipefail

INPUT=$(cat)

# Extract the command
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
if [ -z "$COMMAND" ]; then
  exit 0
fi

# Only intercept git commit commands
if ! echo "$COMMAND" | grep -qE '^\s*git commit'; then
  exit 0
fi

# Only enforce if the project has opted in
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
if [ -z "$CWD" ]; then
  CWD="$(pwd)"
fi

# Walk up to find the git root (where .claude/ would be)
PROJECT_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "$CWD")

if [ ! -f "$PROJECT_ROOT/.claude/e2e-required" ]; then
  exit 0  # Project hasn't opted in — allow freely
fi

GATE_FILE="/tmp/.claude-e2e-gate"

if [ ! -f "$GATE_FILE" ]; then
  echo "BLOCKED: E2E testing gate not passed." >&2
  echo "" >&2
  echo "This project requires E2E testing before commits (.claude/e2e-required)." >&2
  echo "" >&2

  # Check for a project-specific gate script
  GATE_SCRIPT="$PROJECT_ROOT/.claude/hooks/verify-builds.sh"
  if [ -x "$GATE_SCRIPT" ]; then
    echo "Run the project gate:  .claude/hooks/verify-builds.sh" >&2
  else
    echo "Before committing, you must:" >&2
    echo "  1. Fill in the behavior→test table (see pre-commit-checklist.md)" >&2
    echo "  2. Run the tests and paste the output in the conversation" >&2
    echo "  3. Run: echo \"E2E_GATE_PASSED\" > /tmp/.claude-e2e-gate" >&2
  fi

  echo "" >&2
  echo "Only then can you commit." >&2
  exit 2
fi

exit 0
