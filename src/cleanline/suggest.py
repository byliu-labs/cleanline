"""Suggest command: analyze audit log to propose config changes.

Reads passthrough events from the audit log, groups them intelligently,
and proposes concrete config additions the user can approve.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from importlib.resources import files as pkg_files
from pathlib import Path

# Minimum total passthrough count to include in suggestions
MIN_SUGGEST_COUNT = 3

# Common version suffixes to group (e.g., python3.10, python3.11 → python3.*)
VERSION_PATTERN = re.compile(r"^(.+?)(\d+(?:\.\d+)*)$")


def _load_known_aliases() -> dict[str, list[str]]:
    """Load the curated alias mapping table."""
    data_file = pkg_files("cleanline").joinpath("known_aliases.json")
    return json.loads(data_file.read_text())


def _build_reverse_alias_map() -> dict[str, str]:
    """Build variant -> canonical mapping from known_aliases.json."""
    known = _load_known_aliases()
    reverse: dict[str, str] = {}
    for canonical, variants in known.items():
        for variant in variants:
            reverse[variant] = canonical
    return reverse


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


def _confidence_label(total: int) -> str:
    """Assign confidence label based on total passthrough count."""
    if total >= 10:
        return "high"
    if total >= 5:
        return "medium"
    return "low"


def find_version_groups(
    commands: list[tuple[str, int]],
    min_count: int = MIN_SUGGEST_COUNT,
) -> list[dict]:
    """Identify commands that are variants of the same canonical tool.

    Uses two strategies:
      1. Known aliases lookup (cargo-clippy, cargo-fmt -> cargo)
      2. Version regex fallback (python3.12, python3.13 -> python)

    Groups with 2+ variants and total >= min_count are returned, sorted
    by total descending with confidence labels.
    """
    reverse_aliases = _build_reverse_alias_map()
    groups: dict[str, list[tuple[str, int]]] = {}

    for cmd, count in commands:
        canonical = None

        # Strategy 1: known aliases table
        if cmd in reverse_aliases:
            canonical = reverse_aliases[cmd]

        # Strategy 2: regex version pattern
        if canonical is None:
            match = VERSION_PATTERN.match(cmd)
            if match:
                canonical = match.group(1).rstrip(".")

        if canonical is not None:
            if canonical not in groups:
                groups[canonical] = []
            groups[canonical].append((cmd, count))

    result = []
    for base, variants in groups.items():
        total = sum(c for _, c in variants)
        if len(variants) >= 2 and total >= min_count:
            result.append({
                "canonical": base,
                "variants": variants,
                "total": total,
                "confidence": _confidence_label(total),
            })

    result.sort(key=lambda g: g["total"], reverse=True)
    return result


def find_domain_groups(
    domains: list[tuple[str, int]],
    min_count: int = MIN_SUGGEST_COUNT,
) -> list[dict]:
    """Group subdomains under common apex domains.

    E.g., docs.foo.com (5), api.foo.com (3) → suggest *.foo.com

    Groups with 2+ subdomains and total >= min_count are returned,
    sorted by total descending with confidence labels.
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

    result = []
    for apex, subs in apex_map.items():
        total = sum(c for _, c in subs)
        if len(subs) >= 2 and total >= min_count:
            result.append({
                "pattern": f"*.{apex}",
                "subdomains": subs,
                "total": total,
                "confidence": _confidence_label(total),
            })

    result.sort(key=lambda g: g["total"], reverse=True)
    return result


def generate_suggestions(
    events: list[dict],
    min_count: int = MIN_SUGGEST_COUNT,
) -> dict:
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
        "command_groups": find_version_groups(grouped["commands"], min_count=min_count),
        "domain_groups": find_domain_groups(grouped["domains"], min_count=min_count),
        "top_commands": grouped["commands"][:10],
        "top_domains": grouped["domains"][:10],
    }


def apply_suggestions(suggestions: dict, config_path: Path) -> dict:
    """Apply suggested aliases and domains to permission-config.json.

    Prompts for confirmation, then updates the config file.
    Returns {"actions": [...], "cancelled": bool}
    """
    result: dict = {"actions": [], "cancelled": False}

    # Collect what we'd add
    new_aliases: dict[str, str] = {}
    for group in suggestions.get("command_groups", []):
        canonical = group["canonical"]
        for variant, _ in group["variants"]:
            new_aliases[variant] = canonical

    new_domains: list[str] = []
    for group in suggestions.get("domain_groups", []):
        new_domains.append(group["pattern"])

    if not new_aliases and not new_domains:
        result["actions"].append("nothing to apply")
        return result

    # Show what would change
    print("\nChanges to apply:")
    if new_aliases:
        print(f"  + {len(new_aliases)} alias rules:")
        for variant, canonical in sorted(new_aliases.items()):
            print(f"      {variant} -> {canonical}")
    if new_domains:
        print(f"  + {len(new_domains)} domain patterns:")
        for domain in new_domains:
            print(f"      {domain}")

    try:
        answer = input("\nApply these changes? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        result["cancelled"] = True
        return result

    if answer not in ("", "y", "yes"):
        result["cancelled"] = True
        return result

    # Read current config
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Merge aliases
    existing_aliases = config.setdefault("bashAliases", {})
    added_aliases = 0
    for variant, canonical in new_aliases.items():
        if variant not in existing_aliases:
            existing_aliases[variant] = canonical
            added_aliases += 1

    # Merge domains
    existing_domains = config.setdefault("webfetch", {}).setdefault("extraDomains", [])
    existing_set = set(existing_domains)
    added_domains = 0
    for domain in new_domains:
        if domain not in existing_set:
            existing_domains.append(domain)
            existing_set.add(domain)
            added_domains += 1

    # Write atomically
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    tmp.rename(config_path)

    if added_aliases:
        result["actions"].append(f"added {added_aliases} aliases to {config_path}")
    if added_domains:
        result["actions"].append(f"added {added_domains} domains to {config_path}")

    return result
