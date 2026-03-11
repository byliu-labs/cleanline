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

from .tiers import DEFAULT_TIER, get_tier_config

# Minimum total passthrough count to include in suggestions
# (backward-compatible default; actual value comes from tier when available)
MIN_SUGGEST_COUNT = 3

# Common version suffixes to group (e.g., python3.10, python3.11 → python3.*)
VERSION_PATTERN = re.compile(r"^(.+?)(\d+(?:\.\d+)*)$")


def _load_known_aliases() -> dict[str, list[str]]:
    """Load the curated alias mapping table."""
    data_file = pkg_files("flow_state").joinpath("known_aliases.json")
    return json.loads(data_file.read_text())


def _build_reverse_alias_map() -> dict[str, str]:
    """Build variant -> canonical mapping from known_aliases.json."""
    known = _load_known_aliases()
    reverse: dict[str, str] = {}
    for canonical, variants in known.items():
        for variant in variants:
            reverse[variant] = canonical
    return reverse


READ_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Edit", "Write"}
FILE_TOOLS = READ_TOOLS | WRITE_TOOLS


def group_passthroughs(events: list[dict]) -> dict[str, list[tuple[str, int]]]:
    """Group passthrough events into suggestion categories.

    Returns dict with keys:
      "commands": [(command, count), ...]  — top passthrough commands
      "domains": [(domain, count), ...]    — top passthrough domains
      "file_paths": [(path, count), ...]   — top passthrough file paths (read)
      "write_file_paths": [(path, count), ...] — top passthrough file paths (write)
    """
    cmd_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    read_path_counts: Counter[str] = Counter()
    write_path_counts: Counter[str] = Counter()

    for event in events:
        if event.get("decision") != "passthrough":
            continue
        tool = event.get("tool", "")
        input_val = event.get("input", "")
        if not input_val:
            continue

        if tool == "Bash":
            parts = input_val.split(None, 1)
            if parts:
                cmd_counts[parts[0]] += 1
        elif tool == "WebFetch":
            domain_counts[input_val] += 1
        elif tool in READ_TOOLS:
            read_path_counts[input_val] += 1
        elif tool in WRITE_TOOLS:
            write_path_counts[input_val] += 1

    return {
        "commands": cmd_counts.most_common(20),
        "domains": domain_counts.most_common(20),
        "file_paths": read_path_counts.most_common(20),
        "write_file_paths": write_path_counts.most_common(20),
    }


def _confidence_label(total: int, tier: str = DEFAULT_TIER) -> str:
    """Assign confidence label using tier-appropriate thresholds."""
    cfg = get_tier_config(tier)
    if total >= cfg["suggest_confidence_high"]:
        return "high"
    if total >= cfg["suggest_confidence_medium"]:
        return "medium"
    return "low"


def find_version_groups(
    commands: list[tuple[str, int]],
    min_count: int = MIN_SUGGEST_COUNT,
    tier: str = DEFAULT_TIER,
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
                "confidence": _confidence_label(total, tier),
            })

    result.sort(key=lambda g: g["total"], reverse=True)
    return result


def find_domain_groups(
    domains: list[tuple[str, int]],
    min_count: int = MIN_SUGGEST_COUNT,
    tier: str = DEFAULT_TIER,
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
                "confidence": _confidence_label(total, tier),
            })

    result.sort(key=lambda g: g["total"], reverse=True)
    return result


def find_path_groups(
    file_paths: list[tuple[str, int]],
    min_count: int = MIN_SUGGEST_COUNT,
    tier: str = DEFAULT_TIER,
) -> list[dict]:
    """Group file paths by common directory prefix.

    E.g., /home/user/src/a.py (5), /home/user/src/b.py (3) → suggest /home/user/src/**

    Groups with 2+ paths and total >= min_count are returned,
    sorted by total descending with confidence labels.
    """
    import os

    dir_map: dict[str, list[tuple[str, int]]] = {}
    for path_str, count in file_paths:
        parent = os.path.dirname(path_str)
        if parent:
            if parent not in dir_map:
                dir_map[parent] = []
            dir_map[parent].append((path_str, count))

    result = []
    for parent_dir, paths in dir_map.items():
        total = sum(c for _, c in paths)
        if len(paths) >= 2 and total >= min_count:
            result.append({
                "pattern": f"{parent_dir}/**",
                "paths": paths,
                "total": total,
                "confidence": _confidence_label(total, tier),
            })

    result.sort(key=lambda g: g["total"], reverse=True)
    return result


def generate_suggestions(
    events: list[dict],
    min_count: int | None = None,
    tier: str = DEFAULT_TIER,
) -> dict:
    """Analyze audit events and generate config suggestions.

    When min_count is None, uses the tier's default threshold.

    Returns:
      {
        "command_groups": [...],   # version-grouped command suggestions
        "domain_groups": [...],    # apex-grouped domain suggestions
        "top_commands": [...],     # ungrouped top commands
        "top_domains": [...],      # ungrouped top domains
      }
    """
    cfg = get_tier_config(tier)
    effective_min = min_count if min_count is not None else cfg["suggest_min_count"]
    write_min = cfg["suggest_write_min_count"]
    grouped = group_passthroughs(events)

    # Write path groups use higher threshold and always get a warning label
    write_groups = find_path_groups(
        grouped.get("write_file_paths", []), min_count=write_min, tier=tier,
    )
    for group in write_groups:
        group["access"] = "write"
        group["confidence"] = "write-" + group.get("confidence", "low")

    read_groups = find_path_groups(
        grouped.get("file_paths", []), min_count=effective_min, tier=tier,
    )
    for group in read_groups:
        group["access"] = "read"

    return {
        "command_groups": find_version_groups(
            grouped["commands"], min_count=effective_min, tier=tier,
        ),
        "domain_groups": find_domain_groups(
            grouped["domains"], min_count=effective_min, tier=tier,
        ),
        "file_path_groups": read_groups,
        "write_path_groups": write_groups,
        "top_commands": grouped["commands"][:10],
        "top_domains": grouped["domains"][:10],
        "top_file_paths": grouped.get("file_paths", [])[:10],
        "top_write_paths": grouped.get("write_file_paths", [])[:10],
    }


def apply_suggestions(suggestions: dict, config_path: Path) -> dict:
    """Apply suggested aliases and domains via lockfile user_config.

    Prompts for confirmation, then updates user_config in lockfile and
    regenerates permission-config.json.

    Returns {"actions": [...], "cancelled": bool}
    """
    from . import lockfile as lockfile_mod

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

    new_read_paths: list[str] = []
    for group in suggestions.get("file_path_groups", []):
        new_read_paths.append(group["pattern"])

    new_write_paths: list[str] = []
    for group in suggestions.get("write_path_groups", []):
        new_write_paths.append(group["pattern"])

    if not new_aliases and not new_domains and not new_read_paths and not new_write_paths:
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
    if new_read_paths:
        print(f"  + {len(new_read_paths)} file read paths:")
        for path in new_read_paths:
            print(f"      {path}")
    if new_write_paths:
        print(f"  + {len(new_write_paths)} file write paths:")
        for path in new_write_paths:
            print(f"      {path}")

    try:
        answer = input("\nApply these changes? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        result["cancelled"] = True
        return result

    if answer not in ("", "y", "yes"):
        result["cancelled"] = True
        return result

    # Update user_config in lockfile
    lockfile_data = lockfile_mod.read_lockfile()
    user_config = lockfile_data.setdefault("user_config", {})

    # Merge aliases into user_config
    existing_aliases = user_config.setdefault("bashAliases", {})
    added_aliases = 0
    for variant, canonical in new_aliases.items():
        if variant not in existing_aliases:
            existing_aliases[variant] = canonical
            added_aliases += 1

    # Merge domains into user_config
    existing_domains = user_config.setdefault("webfetch", {}).setdefault("extraDomains", [])
    existing_set = set(existing_domains)
    added_domains = 0
    for domain in new_domains:
        if domain not in existing_set:
            existing_domains.append(domain)
            existing_set.add(domain)
            added_domains += 1

    # Merge read paths into user_config.fileAccess
    existing_fa = user_config.setdefault("fileAccess", {})
    existing_read = existing_fa.setdefault("readPaths", [])
    existing_read_set = set(existing_read)
    added_paths = 0
    for path in new_read_paths:
        if path not in existing_read_set:
            existing_read.append(path)
            existing_read_set.add(path)
            added_paths += 1

    # Merge write paths into user_config.fileAccess
    existing_write = existing_fa.setdefault("writePaths", [])
    existing_write_set = set(existing_write)
    added_write_paths = 0
    for path in new_write_paths:
        if path not in existing_write_set:
            existing_write.append(path)
            existing_write_set.add(path)
            added_write_paths += 1

    # Write lockfile + regenerate permission-config.json
    lockfile_mod.write_lockfile(lockfile_data)
    lockfile_mod.write_permission_config(config_path, lockfile_data)

    if added_aliases:
        result["actions"].append(f"added {added_aliases} aliases")
    if added_domains:
        result["actions"].append(f"added {added_domains} domains")
    if added_paths:
        result["actions"].append(f"added {added_paths} read paths")
    if added_write_paths:
        result["actions"].append(f"added {added_write_paths} write paths")

    return result
