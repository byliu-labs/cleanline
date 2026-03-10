"""Profile operations: init, status, update, remove, dry-run.

Each function corresponds to a CLI subcommand and operates on the lock file.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import audit as audit_mod
from . import conflicts as conflicts_mod
from . import fetch as fetch_mod
from . import lockfile as lockfile_mod
from . import schema as schema_mod
from . import setup_cmd


def init_profile(source: str) -> dict:
    """Fetch, validate, and merge a profile into the lock file.

    Returns a result dict with actions taken and any warnings/errors.
    """
    result: dict = {"actions": [], "warnings": [], "errors": []}

    # Fetch
    try:
        profile = fetch_mod.fetch_profile(source)
    except (RuntimeError, FileNotFoundError) as e:
        result["errors"].append(str(e))
        return result

    # Validate
    errors, warnings = schema_mod.validate_profile(profile)
    result["warnings"].extend(warnings)
    if errors:
        result["errors"].extend(errors)
        return result

    # Compatibility analysis
    settings_path = setup_cmd.find_settings_path()
    if settings_path:
        canonicals = setup_cmd.extract_canonicals(
            setup_cmd.parse_allow_list(settings_path)
        )
        ready, inert = setup_cmd.analyze_compatibility(profile, canonicals)
        result["ready"] = ready
        result["inert"] = inert

    # Conflict check
    lockfile_data = lockfile_mod.read_lockfile()
    existing_profiles = [p.get("content", {}) for p in lockfile_data.get("profiles", [])]
    all_profiles = existing_profiles + [profile]
    alias_conflicts, mapping_conflicts = conflicts_mod.check_all_conflicts(all_profiles)

    if alias_conflicts:
        for c in alias_conflicts:
            result["warnings"].append(
                f"alias conflict on '{c['key']}': {json.dumps(c['values'])}"
            )
    if mapping_conflicts:
        for c in mapping_conflicts:
            result["warnings"].append(
                f"mapping conflict on '{c['alias']}': {json.dumps(c['canonicals'])}"
            )

    # Merge
    lockfile_data = lockfile_mod.add_profile(lockfile_data, profile, source)
    lockfile_mod.write_lockfile(lockfile_data)
    result["actions"].append(f"added profile '{profile['name']}' v{profile['version']}")

    return result


def get_status(lockfile_path: Path | None = None) -> dict:
    """Get current status: installed profiles, audit summary, top rules."""
    lockfile_data = lockfile_mod.read_lockfile(lockfile_path)

    profiles_info = []
    for p in lockfile_data.get("profiles", []):
        profiles_info.append({
            "name": p.get("name"),
            "version": p.get("content", {}).get("version", "?"),
            "source": p.get("source", "?"),
        })

    # Audit summary
    events = audit_mod.read_audit_log()
    summary = audit_mod.summarize_decisions(events)
    top_allow = audit_mod.top_rules(events, "allow", limit=5)
    top_passthrough = audit_mod.top_rules(events, "passthrough", limit=5)

    # Hook health check
    settings_path = setup_cmd.find_settings_path()
    hook_health: list[str] = []
    if settings_path:
        hook_health = setup_cmd.check_hook_health(settings_path)

    return {
        "profiles": profiles_info,
        "merged_keys": list(lockfile_data.get("merged", {}).keys()),
        "audit_summary": summary,
        "top_auto_approved": top_allow,
        "top_passthroughs": top_passthrough,
        "hook_health": hook_health,
    }


def update_profiles(
    name: str | None = None,
    lockfile_path: Path | None = None,
) -> dict:
    """Re-fetch profiles from their sources and show diff."""
    lockfile_data = lockfile_mod.read_lockfile(lockfile_path)
    result: dict = {"updated": [], "errors": [], "unchanged": []}

    for entry in lockfile_data.get("profiles", []):
        pname = entry.get("name", "")
        if name and pname != name:
            continue

        source = entry.get("source", "")
        if not source:
            result["errors"].append(f"no source recorded for '{pname}'")
            continue

        try:
            new_profile = fetch_mod.fetch_profile(source)
        except (RuntimeError, FileNotFoundError) as e:
            result["errors"].append(f"failed to fetch '{pname}': {e}")
            continue

        old_version = entry.get("content", {}).get("version", "?")
        new_version = new_profile.get("version", "?")

        if old_version == new_version:
            result["unchanged"].append(pname)
            continue

        # Validate new version
        errors, _ = schema_mod.validate_profile(new_profile)
        if errors:
            result["errors"].append(f"new version of '{pname}' is invalid: {errors}")
            continue

        lockfile_data = lockfile_mod.add_profile(lockfile_data, new_profile, source)
        result["updated"].append({
            "name": pname,
            "old_version": old_version,
            "new_version": new_version,
        })

    if result["updated"]:
        lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    return result


def remove_profile(name: str, lockfile_path: Path | None = None) -> dict:
    """Remove a profile and show impact."""
    lockfile_data = lockfile_mod.read_lockfile(lockfile_path)
    result: dict = {"actions": [], "errors": []}

    existing_names = [p.get("name") for p in lockfile_data.get("profiles", [])]
    if name not in existing_names:
        result["errors"].append(f"profile '{name}' not found")
        return result

    old_merged = lockfile_data.get("merged", {})
    lockfile_data = lockfile_mod.remove_profile(lockfile_data, name)
    new_merged = lockfile_data.get("merged", {})

    # Calculate impact
    old_aliases = set(old_merged.get("bashAliases", {}).keys())
    new_aliases = set(new_merged.get("bashAliases", {}).keys())
    removed_aliases = old_aliases - new_aliases

    old_domains = set(old_merged.get("webfetch", {}).get("extraDomains", []))
    new_domains = set(new_merged.get("webfetch", {}).get("extraDomains", []))
    removed_domains = old_domains - new_domains

    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)
    result["actions"].append(f"removed profile '{name}'")

    if removed_aliases:
        result["removed_aliases"] = sorted(removed_aliases)
    if removed_domains:
        result["removed_domains"] = sorted(removed_domains)

    return result


def dry_run_profile(source: str) -> dict:
    """Show what would change without applying.

    Fetches the profile, validates it, checks conflicts, and shows
    the hypothetical merged state.
    """
    result: dict = {"errors": [], "warnings": []}

    try:
        profile = fetch_mod.fetch_profile(source)
    except (RuntimeError, FileNotFoundError) as e:
        result["errors"].append(str(e))
        return result

    errors, warnings = schema_mod.validate_profile(profile)
    result["warnings"].extend(warnings)
    if errors:
        result["errors"].extend(errors)
        return result

    result["profile"] = {
        "name": profile.get("name"),
        "version": profile.get("version"),
        "aliases": len(profile.get("bashAliases", {})),
        "mappings": len(profile.get("commandMappings", {})),
        "domains": len(profile.get("webfetch", {}).get("extraDomains", [])),
    }

    # Compatibility analysis
    settings_path = setup_cmd.find_settings_path()
    if settings_path:
        canonicals = setup_cmd.extract_canonicals(
            setup_cmd.parse_allow_list(settings_path)
        )
        ready, inert = setup_cmd.analyze_compatibility(profile, canonicals)
        result["ready"] = ready
        result["inert"] = inert

    # Conflict check against existing profiles
    lockfile_data = lockfile_mod.read_lockfile()
    existing = [p.get("content", {}) for p in lockfile_data.get("profiles", [])]
    alias_conflicts, mapping_conflicts = conflicts_mod.check_all_conflicts(existing + [profile])

    if alias_conflicts:
        result["alias_conflicts"] = alias_conflicts
    if mapping_conflicts:
        result["mapping_conflicts"] = mapping_conflicts

    # Show hypothetical merged state
    hypothetical = dict(lockfile_data)
    hypothetical = lockfile_mod.add_profile(hypothetical, profile, source)
    result["hypothetical_merged"] = hypothetical["merged"]

    return result
