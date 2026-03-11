#!/usr/bin/env python3
"""Single Python entry point for Bash permission hook.

Reads tool input JSON from stdin, resolves the command through alias lookup,
command mapping, and canonical matching. Outputs {"decision":"allow"} if the
command is safe to auto-approve, otherwise exits silently (fail-closed).

Replaces the multi-subprocess pipeline: bash-gate.sh -> normalize-bash-cmd.py
-> match-command-equiv.py -> bash_utils.py -> log_event.py

Usage (called by bash-gate.sh):
    echo "$INPUT" | python3 resolve.py "$SCRIPT_DIR"
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ============================================================================
# CONSTANTS
# ============================================================================

# Metacharacters that indicate unsafe compound commands.
# Pipes, backticks, and process substitution are always rejected.
# && and ; are handled by chain splitting (see split_chain).
DANGEROUS_METACHARACTERS = ("|", "`", "$(", ">(", "<(")

# Pattern to extract canonical from allow list entries like "Bash(python *)"
BASH_ALLOW_PATTERN = re.compile(r"^Bash\((\S+?)(?:\s+\*)?\)$")

# Maximum sub-commands in a && / ; chain
MAX_CHAIN_LENGTH = 5

AUDIT_LOG_FILENAME = "hook.jsonl"
CONFIG_FILENAME = "permission-config.json"


# ============================================================================
# METACHARACTER + CHAIN SPLITTING
# ============================================================================


def has_dangerous_metacharacters(cmd: str) -> bool:
    """Check for metacharacters that always cause rejection."""
    for meta in DANGEROUS_METACHARACTERS:
        if meta in cmd:
            return True
    return False


def split_chain(command: str) -> list[str] | None:
    """Split command on && and ;, respecting quotes.

    Returns None if:
      - Dangerous metacharacters found (pipes, backticks, $(), etc.)
      - shlex parsing fails (malformed quoting)
      - More than MAX_CHAIN_LENGTH sub-commands

    Uses shlex to tokenize first, so quoted strings like
    echo "hello && world" stay intact.
    """
    if has_dangerous_metacharacters(command):
        return None

    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if not tokens:
        return None

    # Rebuild sub-commands by splitting on && and ; tokens
    sub_commands: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in ("&&", ";"):
            if current:
                sub_commands.append(" ".join(current))
                current = []
        else:
            current.append(token)
    if current:
        sub_commands.append(" ".join(current))

    if not sub_commands:
        return None

    if len(sub_commands) > MAX_CHAIN_LENGTH:
        return None

    return sub_commands


# ============================================================================
# COMMAND NORMALIZATION
# ============================================================================


def unwrap_command(argv: list[str]) -> list[str]:
    """Strip env/timeout wrappers to get the real command tokens."""
    if not argv:
        return []

    binary = os.path.basename(argv[0])

    if binary == "env" and len(argv) > 1:
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                return argv[i + 1 :]
            if arg.startswith("-") or "=" in arg:
                i += 1
                continue
            return argv[i:]
        return []

    if binary == "timeout" and len(argv) > 1:
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                return argv[i + 1 :]
            if arg.startswith("-"):
                i += 1
                continue
            # First non-flag arg is the duration -- skip it
            return argv[i + 1 :]
        return []

    return argv


def normalize_binary(command: str) -> str | None:
    """Extract the base binary name from a simple command string.

    Returns None if the command can't be parsed.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return None

    if not argv:
        return None

    unwrapped = unwrap_command(argv)
    if not unwrapped:
        return None

    binary = os.path.basename(unwrapped[0])
    return binary if binary else None


# ============================================================================
# CONFIG LOADING
# ============================================================================


def load_config(config_path: Path) -> dict:
    """Load permission-config.json. Returns empty dict on any error."""
    try:
        with open(config_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def ensure_config(hooks_dir: Path, script_dir: str) -> Path:
    """Ensure permission-config.json exists, creating from defaults if needed.

    First-run logic:
    1. Copy default-config.json from the script directory
    2. Scan ~/.claude/settings.json for resolvedCanonicals
    3. Write updated config with real canonicals
    """
    config_path = hooks_dir / CONFIG_FILENAME

    if config_path.exists():
        return config_path

    # Copy defaults from script directory
    default_path = Path(script_dir) / "default-config.json"
    if not default_path.exists():
        return config_path

    try:
        config = json.loads(default_path.read_text())
    except (OSError, json.JSONDecodeError):
        return config_path

    # Scan settings.json for resolvedCanonicals
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            allow_list = settings.get("permissions", {}).get("allow", [])
            canonicals = set()
            for entry in allow_list:
                m = BASH_ALLOW_PATTERN.match(entry)
                if m:
                    canonicals.add(m.group(1))
            config["resolvedCanonicals"] = sorted(canonicals)
        except (OSError, json.JSONDecodeError):
            pass

    # Write atomically
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        tmp.rename(config_path)
    except OSError:
        pass

    return config_path


# ============================================================================
# RESOLUTION LOGIC
# ============================================================================


def check_canonical_in_list(canonical: str, resolved_canonicals: list[str]) -> bool:
    """Check if a canonical command is in the resolved canonicals list."""
    return canonical in resolved_canonicals


def resolve_alias(binary: str, config: dict) -> str | None:
    """Look up binary in bashAliases. Returns canonical or None."""
    return config.get("bashAliases", {}).get(binary)


def resolve_mapping(command: str, config: dict) -> str | None:
    """Match command against multi-word commandMappings.

    Supports both new key name (commandMappings) and legacy (commandEquivalences).
    Uses longest-prefix-first matching.
    """
    mappings = config.get("commandMappings", config.get("commandEquivalences", {}))
    if not mappings:
        return None

    try:
        cmd_argv = shlex.split(command)
    except ValueError:
        return None

    if not cmd_argv:
        return None

    unwrapped = unwrap_command(cmd_argv)
    if not unwrapped:
        return None

    # Normalize basenames for matching
    unwrapped[0] = os.path.basename(unwrapped[0])

    # Build inverted lookup: {alias_tokens_tuple: canonical}
    # Sort longest-first for greedy matching
    lookup: list[tuple[list[str], str]] = []
    for canonical, aliases in mappings.items():
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            try:
                alias_tokens = shlex.split(alias)
            except ValueError:
                continue
            if alias_tokens:
                lookup.append((alias_tokens, canonical))

    lookup.sort(key=lambda x: len(x[0]), reverse=True)

    for alias_tokens, canonical in lookup:
        if len(unwrapped) >= len(alias_tokens) and unwrapped[: len(alias_tokens)] == alias_tokens:
            return canonical

    return None


def resolve_single(command: str, config: dict, resolved_canonicals: list[str]) -> tuple[bool, str]:
    """Resolve a single (non-chained) command.

    Returns (allowed, matched_rule).
    matched_rule is one of:
      "alias:<binary>-><canonical>"
      "mapping:<canonical>"
      "direct:<binary>"
      "no_match"
    """
    binary = normalize_binary(command)
    if binary is None:
        return False, "no_match"

    # Step 1: Direct canonical check -- is the binary itself in the allow list?
    if check_canonical_in_list(binary, resolved_canonicals):
        return True, f"direct:{binary}"

    # Step 2: Alias lookup
    canonical = resolve_alias(binary, config)
    if canonical and check_canonical_in_list(canonical, resolved_canonicals):
        return True, f"alias:{binary}->{canonical}"

    # Step 3: Multi-word command mapping
    canonical = resolve_mapping(command, config)
    if canonical:
        mapping_binary = canonical.split()[0] if canonical else ""
        if check_canonical_in_list(mapping_binary, resolved_canonicals):
            return True, f"mapping:{canonical}"

    return False, "no_match"


# ============================================================================
# AUDIT LOGGING
# ============================================================================


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
    """Main entry point. Reads stdin JSON, resolves command, outputs decision."""
    if len(sys.argv) < 2:
        return

    script_dir = sys.argv[1]
    hooks_dir = Path.home() / ".claude" / "hooks"

    # Read stdin
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        return

    # Ensure config exists (first-run logic)
    config_path = ensure_config(hooks_dir, script_dir)
    config = load_config(config_path)
    resolved_canonicals = config.get("resolvedCanonicals", [])

    # If no resolvedCanonicals, we can't make decisions
    if not resolved_canonicals:
        log_decision(hooks_dir, "Bash", command, "passthrough", "no_canonicals")
        return

    # Check for dangerous metacharacters (always reject)
    if has_dangerous_metacharacters(command):
        log_decision(hooks_dir, "Bash", command, "passthrough", "metacharacter")
        return

    # Try chain splitting (handles && and ;)
    sub_commands = split_chain(command)
    if sub_commands is None:
        log_decision(hooks_dir, "Bash", command, "passthrough", "metacharacter")
        return

    # Resolve each sub-command
    all_rules: list[str] = []
    for sub_cmd in sub_commands:
        allowed, rule = resolve_single(sub_cmd, config, resolved_canonicals)
        if not allowed:
            # Any sub-command failing means the whole chain falls through
            log_decision(hooks_dir, "Bash", command, "passthrough", f"chain_fail:{rule}")
            return
        all_rules.append(rule)

    # All sub-commands resolved successfully
    combined_rule = "+".join(all_rules) if len(all_rules) > 1 else all_rules[0]
    log_decision(hooks_dir, "Bash", command, "allow", combined_rule)
    print('{"decision":"allow"}')


if __name__ == "__main__":
    main()
