"""Conflict detection when merging multiple profiles.

Rules:
  - webfetch.extraDomains: set union, no conflicts possible
  - bashAliases: same key → different canonical = conflict
  - commandMappings: same alias string under different canonicals = conflict
  - Same key → same value = deduplicated, no conflict
"""
from __future__ import annotations


def detect_alias_conflicts(
    profiles: list[dict],
) -> list[dict]:
    """Detect bashAlias conflicts across profiles.

    Returns list of conflict dicts:
      {"key": alias_key, "values": {profile_name: canonical, ...}}
    """
    # Collect all alias → (canonical, profile_name) pairs
    alias_map: dict[str, dict[str, str]] = {}
    for profile in profiles:
        name = profile.get("name", "<unknown>")
        for alias_key, canonical in profile.get("bashAliases", {}).items():
            if alias_key not in alias_map:
                alias_map[alias_key] = {}
            alias_map[alias_key][name] = canonical

    conflicts = []
    for key, sources in alias_map.items():
        values = set(sources.values())
        if len(values) > 1:
            conflicts.append({"key": key, "values": sources})

    return conflicts


def detect_mapping_conflicts(
    profiles: list[dict],
) -> list[dict]:
    """Detect commandMapping conflicts across profiles.

    An alias string appearing under different canonical commands is a conflict.

    Returns list of conflict dicts:
      {"alias": alias_string, "canonicals": {profile_name: canonical, ...}}
    """
    # Invert: alias_string → {profile_name: canonical}
    alias_to_canonical: dict[str, dict[str, str]] = {}
    for profile in profiles:
        name = profile.get("name", "<unknown>")
        for canonical, aliases in profile.get("commandMappings", {}).items():
            if not isinstance(aliases, list):
                continue
            for alias in aliases:
                if alias not in alias_to_canonical:
                    alias_to_canonical[alias] = {}
                alias_to_canonical[alias][name] = canonical

    conflicts = []
    for alias, sources in alias_to_canonical.items():
        values = set(sources.values())
        if len(values) > 1:
            conflicts.append({"alias": alias, "canonicals": sources})

    return conflicts


def check_all_conflicts(profiles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Run all conflict checks. Returns (alias_conflicts, mapping_conflicts)."""
    return detect_alias_conflicts(profiles), detect_mapping_conflicts(profiles)
