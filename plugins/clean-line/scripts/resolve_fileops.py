#!/usr/bin/env python3
"""Single Python entry point for file operation permission hooks.

Reads tool input JSON from stdin, extracts the file/directory path, and checks
it against allowed path patterns and deny lists. Outputs {"decision":"allow"}
if matched, otherwise exits silently (fail-closed).

Handles: Read, Edit, Write, Glob, Grep

Usage (called by approve-fileops.sh):
    echo "$INPUT" | python3 resolve_fileops.py "$SCRIPT_DIR"
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CONFIG_FILENAME = "permission-config.json"
AUDIT_LOG_FILENAME = "hook.jsonl"

# ============================================================================
# HARDCODED DENY LIST (not config-overridable)
# ============================================================================

HARDCODED_DENY: list[str] = [
    # Cryptographic keys & auth
    "~/.ssh/**",
    "~/.gnupg/**",
    "~/.aws/**",
    "~/.netrc",
    "~/.claude/credentials*",
    # Secret stores
    "~/.password-store/**",
    "~/.local/share/keyrings/**",
    # Container/cluster credentials
    "~/.kube/**",
    "~/.docker/**",
    # Environment secrets (recursive -- matches at any depth)
    "**/.env",
    "**/.env.*",
    # Git credential config
    "**/.git/config",
]

# Tools that require write access
WRITE_TOOLS = {"Edit", "Write"}
# Tools that require read access
READ_TOOLS = {"Read", "Glob", "Grep"}


# ============================================================================
# PATH NORMALIZATION
# ============================================================================


def normalize_path(raw: str) -> Path | None:
    """Expand ~, resolve symlinks, make absolute. Returns None on error."""
    try:
        expanded = os.path.expanduser(raw)
        resolved = Path(expanded).resolve()
        return resolved
    except (OSError, ValueError, RuntimeError):
        return None


# ============================================================================
# PATH EXTRACTION
# ============================================================================


def extract_path(tool_name: str, tool_input: dict) -> str | None:
    """Extract the path from tool input based on tool type.

    Read/Edit/Write use tool_input["file_path"] (required).
    Glob/Grep use tool_input["path"] (optional, defaults to cwd).
    """
    if tool_name in ("Read", "Edit", "Write"):
        return tool_input.get("file_path")
    if tool_name in ("Glob", "Grep"):
        return tool_input.get("path") or os.getcwd()
    return None


# ============================================================================
# PATTERN MATCHING
# ============================================================================


def _expand_pattern(pattern: str) -> str:
    """Expand ~ in a pattern string."""
    return os.path.expanduser(pattern)


def _resolve_pattern_prefix(prefix: str) -> str:
    """Resolve symlinks in a pattern's directory prefix.

    On macOS, /tmp -> /private/tmp, so patterns like /tmp/** need the
    prefix resolved to match paths that went through Path.resolve().
    """
    try:
        return str(Path(prefix).resolve())
    except (OSError, ValueError, RuntimeError):
        return prefix


def matches_pattern(path: Path, pattern: str) -> bool:
    """Check if resolved path matches a glob pattern.

    Handles:
      - Tilde-expanded patterns: ~/.claude/** -> /Users/x/.claude/**
      - ** recursive patterns: ~/.claude/** matches any depth
      - **/X recursive suffix patterns: match path suffix at any depth
      - Simple glob: *.py matches .py files in any context
    """
    path_str = str(path)

    # Handle **/X patterns (recursive suffix matching)
    if pattern.startswith("**/"):
        suffix = pattern[3:]  # strip "**/"
        # If suffix has no path separators, match only the filename
        if "/" not in suffix:
            if fnmatch.fnmatch(path.name, suffix):
                return True
            return False
        # Multi-component suffix (e.g., **/.git/config): check if path ends with it
        if path_str.endswith("/" + suffix) or path_str == suffix:
            return True
        return False

    expanded = _expand_pattern(pattern)

    # Handle ** recursive patterns: ~/.claude/** should match anything under ~/.claude/
    if expanded.endswith("/**"):
        prefix = expanded[:-3]  # strip /**
        resolved_prefix = _resolve_pattern_prefix(prefix)
        if path_str == resolved_prefix or path_str.startswith(resolved_prefix + "/"):
            return True
        return False

    # Handle patterns with * wildcards (but not **)
    if "*" in expanded:
        if fnmatch.fnmatch(path_str, expanded):
            return True
        return False

    # Exact match (also resolve the pattern for symlink consistency)
    resolved_expanded = _resolve_pattern_prefix(expanded)
    return path_str == resolved_expanded


# ============================================================================
# DENY CHECKING
# ============================================================================


def is_denied(path: Path, config: dict) -> str | None:
    """Check hardcoded deny list first, then config denyPaths.

    Returns the matched deny pattern, or None if not denied.
    """
    # Hardcoded deny -- not overridable by config
    for pattern in HARDCODED_DENY:
        if matches_pattern(path, pattern):
            return pattern

    # Config deny paths
    deny_paths = config.get("fileAccess", {}).get("denyPaths", [])
    for pattern in deny_paths:
        if matches_pattern(path, pattern):
            return pattern

    return None


# ============================================================================
# ACCESS CHECKING
# ============================================================================


def check_access(
    tool_name: str,
    tool_input: dict,
    config: dict,
) -> tuple[bool, str]:
    """Full access check pipeline: extract -> normalize -> deny -> allow.

    Returns (allowed, matched_rule).
    Rule format: "read:<pattern>", "write:<pattern>", "deny:<pattern>", "no_match"
    """
    file_access = config.get("fileAccess")
    if not file_access:
        return False, "no_match"

    raw_path = extract_path(tool_name, tool_input)
    if not raw_path:
        return False, "no_match"

    resolved = normalize_path(raw_path)
    if resolved is None:
        # Broken symlink or invalid path -- fail closed
        return False, "no_match"

    # Deny check first (overrides allow)
    deny_match = is_denied(resolved, config)
    if deny_match is not None:
        return False, f"deny:{deny_match}"

    # Determine access type
    if tool_name in WRITE_TOOLS:
        allowed_paths = file_access.get("writePaths", [])
        prefix = "write"
    else:
        allowed_paths = file_access.get("readPaths", [])
        prefix = "read"

    for pattern in allowed_paths:
        if matches_pattern(resolved, pattern):
            return True, f"{prefix}:{pattern}"

    return False, "no_match"


# ============================================================================
# CONFIG + LOGGING
# ============================================================================


def load_config(config_path: Path) -> dict:
    """Load permission-config.json. Returns empty dict on error."""
    try:
        with open(config_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def log_decision(
    hooks_dir: Path,
    tool: str,
    input_val: str,
    decision: str,
    matched_rule: str,
) -> None:
    """Append a JSONL audit entry to hook.jsonl."""
    audit_log = hooks_dir / AUDIT_LOG_FILENAME
    audit_log.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": tool,
        "input": input_val,
        "decision": decision,
        "matched_rule": matched_rule,
    }

    try:
        with open(audit_log, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")
    except OSError:
        pass


# ============================================================================
# MAIN
# ============================================================================


def main() -> None:
    """Main entry point. Reads stdin JSON, checks file access, outputs decision."""
    if len(sys.argv) < 2:
        return

    script_dir = sys.argv[1]
    hooks_dir = Path.home() / ".claude" / "hooks"
    config_path = hooks_dir / CONFIG_FILENAME

    # Read stdin
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if not tool_name or not tool_input:
        return

    # Load config
    config = load_config(config_path)

    # No fileAccess config -> passthrough silently
    if "fileAccess" not in config:
        return

    # Extract path for audit logging
    raw_path = extract_path(tool_name, tool_input)
    if not raw_path:
        return

    # Run access check
    allowed, matched_rule = check_access(tool_name, tool_input, config)

    if allowed:
        log_decision(hooks_dir, tool_name, raw_path, "allow", matched_rule)
        print('{"decision":"allow"}')
    elif matched_rule.startswith("deny:"):
        log_decision(hooks_dir, tool_name, raw_path, "passthrough", matched_rule)
    elif matched_rule == "no_match":
        log_decision(hooks_dir, tool_name, raw_path, "passthrough", "no_match")


if __name__ == "__main__":
    main()
