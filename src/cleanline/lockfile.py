"""Lock file management for merged profile state.

The lock file lives at ~/.claude/hooks/profiles.lock.json and contains:
  - "profiles": list of installed profiles with metadata
  - "merged": unified view of all profile rules (what hooks actually read)

Hooks only read "merged". The CLI manages everything.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LOCKFILE = Path.home() / ".claude" / "hooks" / "profiles.lock.json"


def get_lockfile_path() -> Path:
    """Return the global lock file path."""
    return DEFAULT_LOCKFILE


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

    merged: dict = {}
    if merged_domains:
        merged["webfetch"] = {"extraDomains": merged_domains}
    if merged_aliases:
        merged["bashAliases"] = merged_aliases
    if merged_mappings:
        merged["commandMappings"] = merged_mappings

    return merged


def rebuild_merged(lockfile_data: dict) -> dict:
    """Rebuild the 'merged' section from all installed profiles."""
    profiles = [p.get("content", {}) for p in lockfile_data.get("profiles", [])]
    lockfile_data["merged"] = merge_profiles(profiles)
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
