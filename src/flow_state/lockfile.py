"""Lock file management for merged profile state.

The lock file lives at ~/.claude/hooks/profiles.lock.json and contains:
  - "profiles": list of installed profiles with metadata
  - "merged": unified view of all profile rules (what hooks actually read)
  - "user_config": user's own aliases/domains/mappings (set by setup, suggest, tighten)

Hooks read only permission-config.json. The CLI manages lockfile + regenerates
permission-config.json from user_config + merged + resolvedCanonicals.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .tiers import DEFAULT_TIER, validate_tier

DEFAULT_LOCKFILE = Path.home() / ".claude" / "hooks" / "profiles.lock.json"
BASH_ALLOW_PATTERN = re.compile(r"^Bash\((\S+?)(?:\s+\*)?\)$")


def get_lockfile_path() -> Path:
    """Return the global lock file path."""
    return DEFAULT_LOCKFILE


def get_tier(lockfile_data: dict) -> str:
    """Read tier from lockfile user_config. Returns DEFAULT_TIER if absent or invalid."""
    tier = lockfile_data.get("user_config", {}).get("tier", DEFAULT_TIER)
    if not validate_tier(tier):
        return DEFAULT_TIER
    return tier


def read_lockfile(path: Path | None = None) -> dict:
    """Read and parse the lock file. Returns empty structure if missing."""
    path = path or get_lockfile_path()
    if not path.exists():
        return {"profiles": [], "merged": {}}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"profiles": [], "merged": {}}

    if "profiles" not in data:
        data["profiles"] = []
    if "merged" not in data:
        data["merged"] = {}
    return data


def write_lockfile(data: dict, path: Path | None = None) -> None:
    """Write the lock file atomically (write to temp, then rename)."""
    path = path or get_lockfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.rename(path)


def merge_profiles(profiles: list[dict]) -> dict:
    """Merge multiple profile dicts into a single 'merged' section.

    Merge rules:
      - webfetch.extraDomains: set union (deduplicated, order preserved)
      - bashAliases: dict merge (last profile wins on conflict)
      - commandMappings: dict merge with alias list union per canonical
    """
    merged_domains: list[str] = []
    seen_domains: set[str] = set()
    merged_aliases: dict[str, str] = {}
    merged_mappings: dict[str, list[str]] = {}

    for profile in profiles:
        # Domains: set union
        for domain in profile.get("webfetch", {}).get("extraDomains", []):
            if domain not in seen_domains:
                seen_domains.add(domain)
                merged_domains.append(domain)

        # Aliases: direct merge
        for key, val in profile.get("bashAliases", {}).items():
            merged_aliases[key] = val

        # Mappings: merge alias lists per canonical
        for canonical, aliases in profile.get("commandMappings", {}).items():
            if not isinstance(aliases, list):
                continue
            if canonical not in merged_mappings:
                merged_mappings[canonical] = []
            existing = set(merged_mappings[canonical])
            for alias in aliases:
                if alias not in existing:
                    merged_mappings[canonical].append(alias)
                    existing.add(alias)

    # File access: union readPaths across profiles. writePaths go to pendingWritePaths.
    merged_read_paths: list[str] = []
    seen_read_paths: set[str] = set()
    merged_pending_write: list[str] = []
    seen_pending_write: set[str] = set()

    for profile in profiles:
        fa = profile.get("fileAccess", {})
        for rp in fa.get("readPaths", []):
            if rp not in seen_read_paths:
                seen_read_paths.add(rp)
                merged_read_paths.append(rp)
        for wp in fa.get("writePaths", []):
            if wp not in seen_pending_write:
                seen_pending_write.add(wp)
                merged_pending_write.append(wp)

    merged: dict = {}
    if merged_domains:
        merged["webfetch"] = {"extraDomains": merged_domains}
    if merged_aliases:
        merged["bashAliases"] = merged_aliases
    if merged_mappings:
        merged["commandMappings"] = merged_mappings

    file_access: dict = {}
    if merged_read_paths:
        file_access["readPaths"] = merged_read_paths
    if merged_pending_write:
        file_access["pendingWritePaths"] = merged_pending_write
    if file_access:
        merged["fileAccess"] = file_access

    return merged


def apply_overrides(merged: dict, overrides: dict) -> dict:
    """Filter suppressed rules out of the merged section.

    Overrides come from lockfile_data["user_overrides"]["removed_rules"].
    Each entry has type (bashAlias/domain/commandMapping) and value.
    """
    removed_rules = overrides.get("removed_rules", [])
    if not removed_rules:
        return merged

    # Work on a copy
    merged = json.loads(json.dumps(merged))

    for rule in removed_rules:
        rtype = rule.get("type")
        value = rule.get("value")
        if not rtype or not value:
            continue

        if rtype == "bashAlias":
            merged.get("bashAliases", {}).pop(value, None)
        elif rtype == "domain":
            domains = merged.get("webfetch", {}).get("extraDomains", [])
            if value in domains:
                domains.remove(value)
        elif rtype == "commandMapping":
            merged.get("commandMappings", {}).pop(value, None)
        elif rtype == "fileAccessRead":
            read_paths = merged.get("fileAccess", {}).get("readPaths", [])
            if value in read_paths:
                read_paths.remove(value)
        elif rtype == "fileAccessWrite":
            write_paths = merged.get("fileAccess", {}).get("writePaths", [])
            if value in write_paths:
                write_paths.remove(value)

    return merged


def add_override(lockfile_data: dict, override_entry: dict) -> dict:
    """Append an override entry, avoiding duplicates by type+value."""
    overrides = lockfile_data.setdefault("user_overrides", {})
    removed = overrides.setdefault("removed_rules", [])

    # Check for duplicate
    for existing in removed:
        if (existing.get("type") == override_entry.get("type")
                and existing.get("value") == override_entry.get("value")):
            return lockfile_data

    removed.append(override_entry)
    return lockfile_data


def remove_redundant_overrides(lockfile_data: dict) -> tuple[dict, list[dict]]:
    """Remove overrides for rules no longer in any profile.

    Returns (updated lockfile_data, list of cleaned override entries).
    """
    overrides = lockfile_data.get("user_overrides", {})
    removed_rules = overrides.get("removed_rules", [])
    if not removed_rules:
        return lockfile_data, []

    # Build a set of all rules across all profiles
    profile_aliases: set[str] = set()
    profile_domains: set[str] = set()
    profile_mappings: set[str] = set()
    for p in lockfile_data.get("profiles", []):
        content = p.get("content", {})
        profile_aliases.update(content.get("bashAliases", {}).keys())
        profile_domains.update(
            content.get("webfetch", {}).get("extraDomains", [])
        )
        profile_mappings.update(content.get("commandMappings", {}).keys())

    kept: list[dict] = []
    cleaned: list[dict] = []
    for rule in removed_rules:
        rtype = rule.get("type")
        value = rule.get("value")
        still_exists = False

        if rtype == "bashAlias" and value in profile_aliases:
            still_exists = True
        elif rtype == "domain" and value in profile_domains:
            still_exists = True
        elif rtype == "commandMapping" and value in profile_mappings:
            still_exists = True

        if still_exists:
            kept.append(rule)
        else:
            cleaned.append(rule)

    overrides["removed_rules"] = kept
    lockfile_data["user_overrides"] = overrides
    return lockfile_data, cleaned


def rebuild_merged(lockfile_data: dict) -> dict:
    """Rebuild the 'merged' section from all installed profiles.

    Applies user_overrides after merging to suppress rules.
    """
    profiles = [p.get("content", {}) for p in lockfile_data.get("profiles", [])]
    merged = merge_profiles(profiles)
    overrides = lockfile_data.get("user_overrides", {})
    lockfile_data["merged"] = apply_overrides(merged, overrides)
    return lockfile_data


def add_profile(lockfile_data: dict, profile: dict, source: str) -> dict:
    """Add a profile to the lock file and rebuild merged."""
    entry = {
        "name": profile["name"],
        "version": profile["version"],
        "source": source,
        "content": profile,
    }

    # Replace existing profile with same name
    lockfile_data["profiles"] = [
        p for p in lockfile_data["profiles"]
        if p.get("name") != profile["name"]
    ]
    lockfile_data["profiles"].append(entry)
    return rebuild_merged(lockfile_data)


def remove_profile(lockfile_data: dict, name: str) -> dict:
    """Remove a profile by name and rebuild merged."""
    lockfile_data["profiles"] = [
        p for p in lockfile_data["profiles"]
        if p.get("name") != name
    ]
    return rebuild_merged(lockfile_data)


# ============================================================================
# PERMISSION CONFIG GENERATION
# ============================================================================


def _extract_canonicals_from_settings(settings_path: Path) -> list[str]:
    """Read settings.json and extract canonical command names from allow list."""
    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    canonicals = set()
    for entry in settings.get("permissions", {}).get("allow", []):
        m = BASH_ALLOW_PATTERN.match(entry)
        if m:
            canonicals.add(m.group(1))
    return sorted(canonicals)


def write_permission_config(
    config_path: Path,
    lockfile_data: dict,
    settings_path: Path | None = None,
) -> None:
    """Merge user_config + profile rules + resolvedCanonicals into permission-config.json.

    The merge flow:
    1. Start with user_config from lockfile
    2. Merge profile rules (from merged section) on top
    3. Compute resolvedCanonicals from settings.json
    4. Write to permission-config.json
    """
    user_config = lockfile_data.get("user_config", {})
    merged = lockfile_data.get("merged", {})

    # Start with user config as base
    config: dict = {}

    # Merge aliases: user first, then profiles on top (profiles extend, don't override)
    combined_aliases = dict(user_config.get("bashAliases", {}))
    for key, val in merged.get("bashAliases", {}).items():
        if key not in combined_aliases:
            combined_aliases[key] = val
    if combined_aliases:
        config["bashAliases"] = combined_aliases

    # Merge mappings: union alias lists per canonical
    combined_mappings: dict[str, list[str]] = {}
    for source in [user_config, merged]:
        for canonical, aliases in source.get("commandMappings", {}).items():
            if not isinstance(aliases, list):
                continue
            if canonical not in combined_mappings:
                combined_mappings[canonical] = []
            existing = set(combined_mappings[canonical])
            for alias in aliases:
                if alias not in existing:
                    combined_mappings[canonical].append(alias)
                    existing.add(alias)
    if combined_mappings:
        config["commandMappings"] = combined_mappings

    # Merge domains: set union, order preserved
    combined_domains: list[str] = []
    seen_domains: set[str] = set()
    for source in [user_config, merged]:
        for domain in source.get("webfetch", {}).get("extraDomains", []):
            if domain not in seen_domains:
                seen_domains.add(domain)
                combined_domains.append(domain)
    if combined_domains:
        config["webfetch"] = {"extraDomains": combined_domains}

    # Merge fileAccess: user first, then profile readPaths (writePaths from profiles NOT auto-merged)
    user_fa = user_config.get("fileAccess", {})
    merged_fa = merged.get("fileAccess", {})
    combined_read: list[str] = []
    seen_read: set[str] = set()
    combined_write: list[str] = []
    seen_write: set[str] = set()
    combined_deny: list[str] = []
    seen_deny: set[str] = set()

    for source_fa in [user_fa, merged_fa]:
        for rp in source_fa.get("readPaths", []):
            if rp not in seen_read:
                seen_read.add(rp)
                combined_read.append(rp)
    for wp in user_fa.get("writePaths", []):
        if wp not in seen_write:
            seen_write.add(wp)
            combined_write.append(wp)
    # Profile writePaths are NOT merged automatically (they're in pendingWritePaths)
    for dp in user_fa.get("denyPaths", []):
        if dp not in seen_deny:
            seen_deny.add(dp)
            combined_deny.append(dp)

    if combined_read or combined_write or combined_deny:
        fa: dict = {}
        if combined_read:
            fa["readPaths"] = combined_read
        if combined_write:
            fa["writePaths"] = combined_write
        if combined_deny:
            fa["denyPaths"] = combined_deny
        config["fileAccess"] = fa

    # Compute resolvedCanonicals from settings.json
    if settings_path is None:
        settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        config["resolvedCanonicals"] = _extract_canonicals_from_settings(settings_path)
    else:
        config["resolvedCanonicals"] = []

    # Write atomically
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    tmp.rename(config_path)
