"""Permission decay analysis: identify and remove stale rules.

Analyzes the audit log to find rules that haven't been triggered recently,
then removes user-config rules or suppresses profile rules via overrides.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audit as audit_mod
from . import lockfile as lockfile_mod
from . import suggest as suggest_mod


def build_usage_map(events: list[dict]) -> dict[str, str | None]:
    """Build {rule_key: last_used_ts} from audit events.

    Rule keys: "alias:python3.13", "mapping:npm test", "domain:*.docs.rs"
    Only tracks allow decisions with meaningful matched_rule values.
    """
    usage: dict[str, str | None] = {}
    for event in events:
        if event.get("decision") != "allow":
            continue
        raw_rule = event.get("matched_rule", "")
        parsed = audit_mod.parse_rule(raw_rule)
        rtype = parsed.get("type")
        ts = event.get("ts")

        key: str | None = None
        if rtype == "alias":
            key = f"alias:{parsed['key']}"
        elif rtype == "mapping":
            key = f"mapping:{parsed['canonical']}"
        elif rtype == "domain":
            key = f"domain:{parsed['pattern']}"
        elif rtype == "read":
            key = f"read:{parsed['pattern']}"
        elif rtype == "write":
            key = f"write:{parsed['pattern']}"

        if key is None:
            continue

        # Track latest timestamp per rule
        existing = usage.get(key)
        if existing is None or (ts and (existing is None or ts > existing)):
            usage[key] = ts

    return usage


def _get_audit_span_days(events: list[dict]) -> int:
    """Compute days between oldest and newest event timestamps."""
    timestamps: list[str] = []
    for e in events:
        ts = e.get("ts")
        if ts:
            timestamps.append(ts)
    if len(timestamps) < 2:
        return 0

    oldest = min(timestamps)
    newest = max(timestamps)
    try:
        dt_old = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
        dt_new = datetime.fromisoformat(newest.replace("Z", "+00:00"))
        return max(0, (dt_new - dt_old).days)
    except (ValueError, TypeError):
        return 0


def _get_family_note(
    alias_key: str,
    usage_map: dict[str, str | None],
    known_aliases: dict[str, list[str]],
) -> str | None:
    """For a stale alias, find active siblings in the same known_aliases family.

    Returns a string like "python3.11, python3.12 are active" or None.
    """
    # Build reverse: variant -> canonical
    reverse: dict[str, str] = {}
    for canonical, variants in known_aliases.items():
        for v in variants:
            reverse[v] = canonical

    canonical = reverse.get(alias_key)
    if canonical is None:
        return None

    # Find all siblings in the same family
    siblings = known_aliases.get(canonical, [])
    active = [
        s for s in siblings
        if s != alias_key and f"alias:{s}" in usage_map
    ]

    if not active:
        return None
    return f"{', '.join(active)} {'is' if len(active) == 1 else 'are'} active"


def _find_profile_for_rule(
    rule_type: str,
    rule_value: str,
    profiles: list[dict],
) -> str:
    """Determine which profile owns a rule by scanning profile content."""
    for p in profiles:
        content = p.get("content", {})
        pname = p.get("name", "<unknown>")
        if rule_type == "alias" and rule_value in content.get("bashAliases", {}):
            return pname
        if rule_type == "domain":
            if rule_value in content.get("webfetch", {}).get("extraDomains", []):
                return pname
        if rule_type == "mapping" and rule_value in content.get("commandMappings", {}):
            return pname
    return "<unknown>"


def find_stale_rules(
    events: list[dict],
    config: dict,
    lockfile_data: dict,
    min_age_days: int = 30,
) -> dict:
    """Identify rules with no recent audit hits.

    Returns structured dict with user_stale, profile_stale, active_counts,
    audit_span_days, and insufficient_data flag.
    """
    usage_map = build_usage_map(events)
    audit_span = _get_audit_span_days(events)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min_age_days)

    # Load known aliases for family context
    try:
        known_aliases = suggest_mod._load_known_aliases()
    except Exception:
        known_aliases = {}

    profiles = lockfile_data.get("profiles", [])
    merged = lockfile_data.get("merged", {})

    user_stale: dict[str, list[dict]] = {"aliases": [], "mappings": [], "domains": [], "file_paths": []}
    profile_stale: dict[str, list[dict]] = {"aliases": [], "mappings": [], "domains": []}
    active_counts = {"aliases": 0, "mappings": 0, "domains": 0, "file_paths": 0}

    def _is_stale(rule_key: str) -> tuple[bool, str | None]:
        """Check if a rule key is stale. Returns (is_stale, last_used)."""
        last_used = usage_map.get(rule_key)
        if last_used is None:
            return True, None
        try:
            dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
            return dt < cutoff, last_used
        except (ValueError, TypeError):
            return True, last_used

    # --- User config aliases ---
    for key, canonical in config.get("bashAliases", {}).items():
        stale, last_used = _is_stale(f"alias:{key}")
        if stale:
            user_stale["aliases"].append({
                "key": key,
                "canonical": canonical,
                "last_used": last_used,
                "family_note": _get_family_note(key, usage_map, known_aliases),
            })
        else:
            active_counts["aliases"] += 1

    # --- User config mappings ---
    for canonical in config.get("commandMappings", {}):
        stale, last_used = _is_stale(f"mapping:{canonical}")
        if stale:
            user_stale["mappings"].append({
                "canonical": canonical,
                "last_used": last_used,
            })
        else:
            active_counts["mappings"] += 1

    # --- User config domains ---
    for domain in config.get("webfetch", {}).get("extraDomains", []):
        stale, last_used = _is_stale(f"domain:{domain}")
        if stale:
            user_stale["domains"].append({
                "pattern": domain,
                "last_used": last_used,
            })
        else:
            active_counts["domains"] += 1

    # --- User config file read paths ---
    for rp in config.get("fileAccess", {}).get("readPaths", []):
        stale, last_used = _is_stale(f"read:{rp}")
        if stale:
            user_stale["file_paths"].append({
                "pattern": rp,
                "access": "read",
                "last_used": last_used,
            })
        else:
            active_counts["file_paths"] += 1

    # --- User config file write paths ---
    for wp in config.get("fileAccess", {}).get("writePaths", []):
        stale, last_used = _is_stale(f"write:{wp}")
        if stale:
            user_stale["file_paths"].append({
                "pattern": wp,
                "access": "write",
                "last_used": last_used,
            })
        else:
            active_counts["file_paths"] += 1

    # --- Profile rules (from merged section) ---
    # Only check rules that come from profiles, not user config
    user_alias_keys = set(config.get("bashAliases", {}).keys())
    user_mapping_keys = set(config.get("commandMappings", {}).keys())
    user_domain_set = set(config.get("webfetch", {}).get("extraDomains", []))

    for key, canonical in merged.get("bashAliases", {}).items():
        if key in user_alias_keys:
            continue  # Already counted in user section
        stale, last_used = _is_stale(f"alias:{key}")
        if stale:
            profile_stale["aliases"].append({
                "key": key,
                "canonical": canonical,
                "last_used": last_used,
                "profile": _find_profile_for_rule("alias", key, profiles),
                "family_note": _get_family_note(key, usage_map, known_aliases),
            })
        else:
            active_counts["aliases"] += 1

    for canonical in merged.get("commandMappings", {}):
        if canonical in user_mapping_keys:
            continue
        stale, last_used = _is_stale(f"mapping:{canonical}")
        if stale:
            profile_stale["mappings"].append({
                "canonical": canonical,
                "last_used": last_used,
                "profile": _find_profile_for_rule("mapping", canonical, profiles),
            })
        else:
            active_counts["mappings"] += 1

    for domain in merged.get("webfetch", {}).get("extraDomains", []):
        if domain in user_domain_set:
            continue
        stale, last_used = _is_stale(f"domain:{domain}")
        if stale:
            profile_stale["domains"].append({
                "pattern": domain,
                "last_used": last_used,
                "profile": _find_profile_for_rule("domain", domain, profiles),
            })
        else:
            active_counts["domains"] += 1

    return {
        "user_stale": user_stale,
        "profile_stale": profile_stale,
        "active_counts": active_counts,
        "audit_span_days": audit_span,
        "insufficient_data": audit_span < 7,
    }


def select_rules_to_remove(stale: dict) -> dict:
    """Select which stale rules to remove/suppress.

    Returns the full stale dict as-is (all candidates selected).
    Exists for testability and future interactive selection.
    """
    return stale


def apply_tighten_user(removals: dict, config_path: Path) -> dict:
    """Remove stale rules from user_config in lockfile and regenerate permission-config.json.

    Returns {"actions": [...], "cancelled": False}
    """
    result: dict = {"actions": [], "cancelled": False}

    lockfile_data = lockfile_mod.read_lockfile()
    user_config = lockfile_data.get("user_config", {})

    # Remove stale aliases from user_config
    aliases = user_config.get("bashAliases", {})
    removed_aliases = 0
    for entry in removals.get("user_stale", {}).get("aliases", []):
        key = entry["key"]
        if key in aliases:
            del aliases[key]
            removed_aliases += 1

    # Remove stale mappings from user_config
    mappings = user_config.get("commandMappings", {})
    removed_mappings = 0
    for entry in removals.get("user_stale", {}).get("mappings", []):
        canonical = entry["canonical"]
        if canonical in mappings:
            del mappings[canonical]
            removed_mappings += 1

    # Remove stale domains from user_config
    domains = user_config.get("webfetch", {}).get("extraDomains", [])
    removed_domains = 0
    for entry in removals.get("user_stale", {}).get("domains", []):
        pattern = entry["pattern"]
        if pattern in domains:
            domains.remove(pattern)
            removed_domains += 1

    # Remove stale file paths from user_config
    fa = user_config.get("fileAccess", {})
    removed_file_paths = 0
    for entry in removals.get("user_stale", {}).get("file_paths", []):
        pattern = entry["pattern"]
        access = entry.get("access", "read")
        key = "readPaths" if access == "read" else "writePaths"
        path_list = fa.get(key, [])
        if pattern in path_list:
            path_list.remove(pattern)
            removed_file_paths += 1

    # Write lockfile + regenerate permission-config.json
    lockfile_mod.write_lockfile(lockfile_data)
    lockfile_mod.write_permission_config(config_path, lockfile_data)

    if removed_aliases:
        result["actions"].append(f"removed {removed_aliases} aliases")
    if removed_mappings:
        result["actions"].append(f"removed {removed_mappings} mappings")
    if removed_domains:
        result["actions"].append(f"removed {removed_domains} domains")
    if removed_file_paths:
        result["actions"].append(f"removed {removed_file_paths} file paths")

    return result


def apply_tighten_profile(suppressions: dict, lockfile_path: Path) -> dict:
    """Write override entries to profiles.lock.json for suppressed profile rules.

    Returns {"actions": [...], "cancelled": False}
    """
    result: dict = {"actions": [], "cancelled": False}

    lockfile_data = lockfile_mod.read_lockfile(lockfile_path)
    now_utc = datetime.now(timezone.utc).isoformat()

    profile_stale = suppressions.get("profile_stale", {})
    count = 0

    for entry in profile_stale.get("aliases", []):
        override = {
            "type": "bashAlias",
            "value": entry["key"],
            "profile": entry.get("profile", "<unknown>"),
            "reason": None,
            "source": "tighten",
            "scope": "global",
            "project_path": None,
            "suppressed_at": now_utc,
        }
        lockfile_data = lockfile_mod.add_override(lockfile_data, override)
        count += 1

    for entry in profile_stale.get("mappings", []):
        override = {
            "type": "commandMapping",
            "value": entry["canonical"],
            "profile": entry.get("profile", "<unknown>"),
            "reason": None,
            "source": "tighten",
            "scope": "global",
            "project_path": None,
            "suppressed_at": now_utc,
        }
        lockfile_data = lockfile_mod.add_override(lockfile_data, override)
        count += 1

    for entry in profile_stale.get("domains", []):
        override = {
            "type": "domain",
            "value": entry["pattern"],
            "profile": entry.get("profile", "<unknown>"),
            "reason": None,
            "source": "tighten",
            "scope": "global",
            "project_path": None,
            "suppressed_at": now_utc,
        }
        lockfile_data = lockfile_mod.add_override(lockfile_data, override)
        count += 1

    # Rebuild merged with overrides applied
    lockfile_data = lockfile_mod.rebuild_merged(lockfile_data)

    # Atomic write
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    if count:
        result["actions"].append(f"suppressed {count} profile rules via overrides")

    return result
