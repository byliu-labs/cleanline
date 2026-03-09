"""Suggest command: analyze audit log to propose config changes.

Reads passthrough events from the audit log, groups them intelligently,
and proposes concrete config additions the user can approve.
"""
from __future__ import annotations

import re
from collections import Counter

# Common version suffixes to group (e.g., python3.10, python3.11 → python3.*)
VERSION_PATTERN = re.compile(r"^(.+?)(\d+(?:\.\d+)*)$")


def group_passthroughs(events: list[dict]) -> dict[str, list[tuple[str, int]]]:
    """Group passthrough events into suggestion categories.

    Returns dict with keys:
      "commands": [(command, count), ...]  — top passthrough commands
      "domains": [(domain, count), ...]    — top passthrough domains
    """
    cmd_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()

    for event in events:
        if event.get("decision") != "passthrough":
            continue
        tool = event.get("tool", "")
        input_val = event.get("input", "")
        if not input_val:
            continue

        if tool == "Bash":
            # Extract the base command (first token)
            parts = input_val.split(None, 1)
            if parts:
                cmd_counts[parts[0]] += 1
        elif tool == "WebFetch":
            domain_counts[input_val] += 1

    return {
        "commands": cmd_counts.most_common(20),
        "domains": domain_counts.most_common(20),
    }


def find_version_groups(commands: list[tuple[str, int]]) -> list[dict]:
    """Identify commands that look like versioned variants of the same tool.

    E.g., python3.12 (15), python3.13 (10) → suggest alias group "python".
    """
    groups: dict[str, list[tuple[str, int]]] = {}

    for cmd, count in commands:
        match = VERSION_PATTERN.match(cmd)
        if match:
            base = match.group(1).rstrip(".")
            if base not in groups:
                groups[base] = []
            groups[base].append((cmd, count))

    # Only suggest groups with 2+ variants
    return [
        {"canonical": base, "variants": variants, "total": sum(c for _, c in variants)}
        for base, variants in groups.items()
        if len(variants) >= 2
    ]


def find_domain_groups(domains: list[tuple[str, int]]) -> list[dict]:
    """Group subdomains under common apex domains.

    E.g., docs.foo.com (5), api.foo.com (3) → suggest *.foo.com
    """
    # Extract apex (last two parts)
    apex_map: dict[str, list[tuple[str, int]]] = {}
    for domain, count in domains:
        parts = domain.split(".")
        if len(parts) >= 3:
            apex = ".".join(parts[-2:])
            if apex not in apex_map:
                apex_map[apex] = []
            apex_map[apex].append((domain, count))

    return [
        {"pattern": f"*.{apex}", "subdomains": subs, "total": sum(c for _, c in subs)}
        for apex, subs in apex_map.items()
        if len(subs) >= 2
    ]


def generate_suggestions(events: list[dict]) -> dict:
    """Analyze audit events and generate config suggestions.

    Returns:
      {
        "command_groups": [...],   # version-grouped command suggestions
        "domain_groups": [...],    # apex-grouped domain suggestions
        "top_commands": [...],     # ungrouped top commands
        "top_domains": [...],      # ungrouped top domains
      }
    """
    grouped = group_passthroughs(events)

    return {
        "command_groups": find_version_groups(grouped["commands"]),
        "domain_groups": find_domain_groups(grouped["domains"]),
        "top_commands": grouped["commands"][:10],
        "top_domains": grouped["domains"][:10],
    }
