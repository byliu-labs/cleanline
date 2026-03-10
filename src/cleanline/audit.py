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


def parse_rule(matched_rule: str) -> dict:
    """Parse a matched_rule string into a structured dict.

    Formats:
      "alias:python3.13->python"  -> {"type": "alias", "key": "python3.13", "canonical": "python"}
      "mapping:npm test"          -> {"type": "mapping", "canonical": "npm test"}
      "domain:*.docs.rs"          -> {"type": "domain", "pattern": "*.docs.rs"}
      "metacharacter"             -> {"type": "metacharacter"}
      "no_match"                  -> {"type": "no_match"}
      anything else               -> {"type": "unknown", "raw": original_string}
    """
    if matched_rule == "metacharacter":
        return {"type": "metacharacter"}
    if matched_rule == "no_match":
        return {"type": "no_match"}
    if matched_rule.startswith("alias:"):
        body = matched_rule[len("alias:"):]
        # Split on the last -> since alias keys can theoretically contain ->
        parts = body.rsplit("->", 1)
        if len(parts) == 2:
            return {"type": "alias", "key": parts[0], "canonical": parts[1]}
        return {"type": "unknown", "raw": matched_rule}
    if matched_rule.startswith("mapping:"):
        return {"type": "mapping", "canonical": matched_rule[len("mapping:"):]}
    if matched_rule.startswith("domain:"):
        return {"type": "domain", "pattern": matched_rule[len("domain:"):]}
    return {"type": "unknown", "raw": matched_rule}


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


def rotate_audit_log(path: Path | None = None, max_age_days: int = 30) -> int:
    """Remove audit entries older than max_age_days. Returns count removed."""
    from datetime import datetime, timedelta, timezone

    path = path or DEFAULT_AUDIT_LOG
    if not path.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    kept: list[str] = []
    removed = 0

    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("ts", "")
                if ts >= cutoff_str:
                    kept.append(stripped)
                else:
                    removed += 1
    except OSError:
        return 0

    if removed > 0:
        try:
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                for line in kept:
                    f.write(line)
                    f.write("\n")
            tmp.rename(path)
        except OSError:
            return 0

    return removed


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
