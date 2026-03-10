#!/usr/bin/env python3
"""Single Python entry point for WebFetch permission hook.

Reads tool input JSON from stdin, extracts the URL hostname, and checks it
against allowed domain patterns. Outputs {"decision":"allow"} if matched,
otherwise exits silently (fail-closed).

Replaces: approve-webfetch-domains.sh + parse-url-host.py + log_event.py

Usage (called by approve-webfetch-domains.sh):
    echo "$INPUT" | python3 resolve_webfetch.py "$SCRIPT_DIR"
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CONFIG_FILENAME = "permission-config.json"
AUDIT_LOG_FILENAME = "hook.jsonl"


# ============================================================================
# HOSTNAME PARSING
# ============================================================================


def parse_hostname(url: str) -> str | None:
    """Extract and validate hostname from URL.

    Returns lowercase hostname, or None if invalid (IP, port, empty).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = parsed.hostname  # already lowercased by urlparse
    if not host:
        return None

    # Strip trailing dots (FQDN notation)
    host = host.rstrip(".")

    # Reject IP addresses and empty hosts
    if not host or host[0].isdigit() or ":" in host:
        return None

    return host


# ============================================================================
# DOMAIN MATCHING
# ============================================================================


def matches_domain(host: str, pattern: str) -> bool:
    """Check if host matches a domain pattern.

    Supports:
      - Exact match: "github.com" matches "github.com"
      - Wildcard: "*.github.com" matches "sub.github.com" (not "github.com")
    """
    if host == pattern:
        return True

    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".github.com"
        if host.endswith(suffix):
            return True

    return False


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
    """Main entry point. Reads stdin JSON, checks domain, outputs decision."""
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

    url = data.get("tool_input", {}).get("url", "")
    if not url:
        return

    # Parse hostname (fail closed)
    host = parse_hostname(url)
    if not host:
        return

    # Load config
    config = load_config(config_path)

    # Build domain list from config
    extra_domains = config.get("webfetch", {}).get("extraDomains", [])

    if not extra_domains:
        log_decision(hooks_dir, "WebFetch", host, "passthrough", "no_domains_configured")
        return

    # Check hostname against each domain pattern
    for pattern in extra_domains:
        if matches_domain(host, pattern):
            log_decision(hooks_dir, "WebFetch", host, "allow", f"domain:{pattern}")
            print('{"decision":"allow"}')
            return

    # No match
    log_decision(hooks_dir, "WebFetch", host, "passthrough", "no_match")


if __name__ == "__main__":
    main()
