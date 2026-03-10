#!/usr/bin/env python3
"""Audit log writer with proper JSON escaping.

Usage: log_event.py <tool> <input> <decision> <matched_rule>

Appends a single JSONL line to ~/.claude/hooks/hook.jsonl with all values
properly escaped via json.dumps(). This replaces the shell printf approach
which produces broken JSON when commands contain quotes, backslashes, or
other special characters.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 5:
        return

    tool = sys.argv[1]
    input_val = sys.argv[2]
    decision = sys.argv[3]
    matched_rule = sys.argv[4]

    audit_log = Path.home() / ".claude" / "hooks" / "hook.jsonl"
    audit_log.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool,
        "input": input_val,
        "decision": decision,
        "matched_rule": matched_rule,
    }

    with open(audit_log, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")


if __name__ == "__main__":
    main()
