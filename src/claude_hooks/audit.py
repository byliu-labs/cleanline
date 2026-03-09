"""Audit log reader and provenance enrichment.

The audit log is a JSONL file at ~/.claude/hooks/hook.jsonl written by hooks.
Each line: {"ts", "tool", "input", "decision", "matched_rule"}

This module reads the log and enriches events with provenance info by joining
against the lock file (which profile contributed each rule).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DEFAULT_AUDIT_LOG = Path.home() / ".claude" / "hooks" / "hook.jsonl"


def read_audit_log(path: Path | None = None, max_lines: int = 10000) -> list[dict]:
    """Read the audit log, returning most recent entries first."""
    path = path or DEFAULT_AUDIT_LOG
    if not path.exists():
        return []

    lines = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []

    # Return most recent first, capped
    return list(reversed(lines[-max_lines:]))


def summarize_decisions(events: list[dict]) -> dict[str, int]:
    """Count events by decision type."""
    counts: Counter[str] = Counter()
    for event in events:
        counts[event.get("decision", "unknown")] += 1
    return dict(counts)


def top_rules(events: list[dict], decision: str, limit: int = 10) -> list[tuple[str, int]]:
    """Top matched rules for a given decision type."""
    counts: Counter[str] = Counter()
    for event in events:
        if event.get("decision") == decision:
            rule = event.get("matched_rule", "unknown")
            counts[rule] += 1
    return counts.most_common(limit)


def enrich_with_provenance(
    events: list[dict],
    lockfile_data: dict,
) -> list[dict]:
    """Add 'source_profile' field to events by matching rules against profiles.

    For alias rules like "alias:python3.13->python", checks which profile
    contributed that alias. For mapping rules, checks which profile has
    that canonical command.
    """
    profiles = lockfile_data.get("profiles", [])

    # Build reverse lookup: alias_key → profile_name
    alias_sources: dict[str, str] = {}
    mapping_sources: dict[str, str] = {}
    for p in profiles:
        pname = p.get("name", "<unknown>")
        content = p.get("content", {})
        for alias_key in content.get("bashAliases", {}):
            alias_sources[alias_key] = pname
        for canonical in content.get("commandMappings", {}):
            mapping_sources[canonical] = pname

    enriched = []
    for event in events:
        event = dict(event)  # copy
        rule = event.get("matched_rule", "")

        if rule.startswith("alias:"):
            # "alias:python3.13->python" → key is "python3.13"
            parts = rule[len("alias:"):].split("->", 1)
            if parts:
                event["source_profile"] = alias_sources.get(parts[0], "user")
        elif rule.startswith("mapping:"):
            canonical = rule[len("mapping:"):]
            event["source_profile"] = mapping_sources.get(canonical, "user")
        else:
            event["source_profile"] = "user"

        enriched.append(event)
    return enriched
