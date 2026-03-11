"""Export command: package user config as a shareable profile.

Reads user_config from the lockfile and produces a profile JSON file
that others can install via `cleanline init`. Default output is
public-safe: no internal domains, no writePaths, no absolute home paths.
"""
from __future__ import annotations

import json
import sys
from fnmatch import fnmatch
from pathlib import Path

from . import lockfile as lockfile_mod
from . import schema

# ============================================================================
# RISKY DOMAIN PATTERNS
# Matched via fnmatch against each domain entry.
# ============================================================================

RISKY_DOMAIN_PATTERNS = [
    # Loopback
    "localhost",
    "127.0.0.1",
    "::1",
    # RFC1918 private ranges
    "10.*",
    "172.16.*", "172.17.*", "172.18.*", "172.19.*",
    "172.20.*", "172.21.*", "172.22.*", "172.23.*",
    "172.24.*", "172.25.*", "172.26.*", "172.27.*",
    "172.28.*", "172.29.*", "172.30.*", "172.31.*",
    "192.168.*",
    # Internal-looking hostnames
    "*.internal.*",
    "*.local",
    "*.corp",
    "*.private",
]

# Prefixes that indicate absolute home directory paths
_HOME_PREFIXES = ("/Users/", "/home/", "/root/")


# ============================================================================
# PURE FUNCTIONS
# ============================================================================


def detect_risky_entries(profile: dict) -> list[dict]:
    """Scan profile for entries unsafe to publish.

    Returns list of {"field", "value", "reason"} dicts.
    """
    risky: list[dict] = []

    # Check domains
    domains = profile.get("webfetch", {}).get("extraDomains", [])
    for domain in domains:
        for pattern in RISKY_DOMAIN_PATTERNS:
            if fnmatch(domain, pattern):
                risky.append({
                    "field": "webfetch.extraDomains",
                    "value": domain,
                    "reason": f"matches risky pattern '{pattern}'",
                })
                break

    # Check file paths for absolute home directories
    fa = profile.get("fileAccess", {})
    for key in ("readPaths", "writePaths"):
        for path in fa.get(key, []):
            if any(path.startswith(prefix) for prefix in _HOME_PREFIXES):
                risky.append({
                    "field": f"fileAccess.{key}",
                    "value": path,
                    "reason": "absolute home directory path",
                })

    return risky


def strip_risky_entries(profile: dict, risky: list[dict]) -> dict:
    """Return a new profile with risky entries removed. Does not mutate input."""
    profile = json.loads(json.dumps(profile))

    # Build removal sets per field
    removals: dict[str, set[str]] = {}
    for entry in risky:
        removals.setdefault(entry["field"], set()).add(entry["value"])

    # Strip domains
    domain_removals = removals.get("webfetch.extraDomains", set())
    if domain_removals and "webfetch" in profile:
        domains = profile["webfetch"].get("extraDomains", [])
        profile["webfetch"]["extraDomains"] = [
            d for d in domains if d not in domain_removals
        ]
        if not profile["webfetch"]["extraDomains"]:
            del profile["webfetch"]

    # Strip file paths
    fa = profile.get("fileAccess", {})
    for key in ("readPaths", "writePaths"):
        path_removals = removals.get(f"fileAccess.{key}", set())
        if path_removals and key in fa:
            fa[key] = [p for p in fa[key] if p not in path_removals]
            if not fa[key]:
                del fa[key]
    if "fileAccess" in profile and not profile["fileAccess"]:
        del profile["fileAccess"]

    return profile


def apply_exclude_patterns(
    profile: dict, patterns: list[str],
) -> tuple[dict, list[str]]:
    """Apply glob exclusion patterns to all list fields in a profile.

    Returns (filtered_profile, list_of_excluded_descriptions).
    """
    profile = json.loads(json.dumps(profile))
    excluded: list[str] = []

    # Domains
    if "webfetch" in profile:
        domains = profile["webfetch"].get("extraDomains", [])
        kept: list[str] = []
        for d in domains:
            if any(fnmatch(d, pat) for pat in patterns):
                excluded.append(f"domain: {d}")
            else:
                kept.append(d)
        profile["webfetch"]["extraDomains"] = kept
        if not kept:
            del profile["webfetch"]

    # Aliases
    if "bashAliases" in profile:
        to_remove = [
            k for k in profile["bashAliases"]
            if any(fnmatch(k, pat) for pat in patterns)
        ]
        for k in to_remove:
            excluded.append(f"alias: {k}")
            del profile["bashAliases"][k]
        if not profile["bashAliases"]:
            del profile["bashAliases"]

    # Mappings
    if "commandMappings" in profile:
        to_remove = [
            k for k in profile["commandMappings"]
            if any(fnmatch(k, pat) for pat in patterns)
        ]
        for k in to_remove:
            excluded.append(f"mapping: {k}")
            del profile["commandMappings"][k]
        if not profile["commandMappings"]:
            del profile["commandMappings"]

    # File paths
    if "fileAccess" in profile:
        for key in ("readPaths", "writePaths"):
            paths = profile["fileAccess"].get(key, [])
            kept_paths: list[str] = []
            for p in paths:
                if any(fnmatch(p, pat) for pat in patterns):
                    excluded.append(f"{key}: {p}")
                else:
                    kept_paths.append(p)
            if kept_paths:
                profile["fileAccess"][key] = kept_paths
            elif key in profile["fileAccess"]:
                del profile["fileAccess"][key]
        if not profile["fileAccess"]:
            del profile["fileAccess"]

    return profile, excluded


def build_profile(
    user_config: dict,
    tier: str,
    *,
    name: str,
    version: str = "1.0.0",
    description: str = "",
    include_write_paths: bool = False,
    meta_fields: dict | None = None,
) -> dict:
    """Assemble a profile dict from user_config.

    Only exports user's own config — not resolvedCanonicals, denyPaths,
    cleanlineTier, or rules from installed profiles.
    """
    profile: dict = {
        "schema_version": 1,
        "name": name,
        "version": version,
    }
    if description:
        profile["description"] = description

    # Aliases
    aliases = user_config.get("bashAliases", {})
    if aliases:
        profile["bashAliases"] = dict(aliases)

    # Mappings
    mappings = user_config.get("commandMappings", {})
    if mappings:
        profile["commandMappings"] = {
            k: list(v) for k, v in mappings.items()
        }

    # Domains
    domains = user_config.get("webfetch", {}).get("extraDomains", [])
    if domains:
        profile["webfetch"] = {"extraDomains": list(domains)}

    # File access
    fa = user_config.get("fileAccess", {})
    read_paths = fa.get("readPaths", [])
    write_paths = fa.get("writePaths", [])

    file_access: dict = {}
    if read_paths:
        file_access["readPaths"] = list(read_paths)
    if include_write_paths and write_paths:
        file_access["writePaths"] = list(write_paths)
    if file_access:
        profile["fileAccess"] = file_access

    # Meta
    meta: dict = {"recommendedTier": tier}
    if meta_fields:
        meta.update(meta_fields)
    profile["meta"] = meta

    return profile


# ============================================================================
# ORCHESTRATOR
# ============================================================================


def run_export(
    *,
    output: str | None = None,
    name: str | None = None,
    description: str | None = None,
    exclude_patterns: list[str] | None = None,
    include_write_paths: bool = False,
    include_risky: bool = False,
    dry_run: bool = False,
    source: str | None = None,
    homepage: str | None = None,
    license_str: str | None = None,
    tags: str | None = None,
    interactive: bool = True,
) -> dict:
    """Export user config as a shareable profile.

    Returns a result dict with keys: profile, actions, warnings, errors.
    """
    lockfile_data = lockfile_mod.read_lockfile()
    user_config = lockfile_data.get("user_config", {})

    if not user_config:
        return {"errors": ["No user config found. Run 'cleanline setup' first."]}

    tier = lockfile_mod.get_tier(lockfile_data)

    # Prompt for name/description if interactive and not provided
    if name is None:
        if interactive:
            try:
                name = input("Profile name: ").strip()
            except (EOFError, KeyboardInterrupt):
                return {"errors": ["Cancelled."]}
        if not name:
            return {"errors": ["Profile name is required (use --name)."]}

    if description is None and interactive:
        try:
            description = input("Description (optional): ").strip()
        except (EOFError, KeyboardInterrupt):
            description = ""

    # Build meta fields from CLI flags
    meta_fields: dict = {}
    if source:
        meta_fields["source"] = source
    if homepage:
        meta_fields["homepage"] = homepage
    if license_str:
        meta_fields["license"] = license_str
    if tags:
        meta_fields["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    print("Exporting only local user rules (installed profiles are not re-exported)")

    # Build the profile
    profile = build_profile(
        user_config, tier,
        name=name,
        description=description or "",
        include_write_paths=include_write_paths,
        meta_fields=meta_fields if meta_fields else None,
    )

    warnings: list[str] = []
    actions: list[str] = []

    # Warn about writePaths exclusion
    has_write_paths = bool(user_config.get("fileAccess", {}).get("writePaths"))
    if has_write_paths and not include_write_paths:
        warnings.append("writePaths excluded (use --include-write-paths to include)")

    # Detect and strip risky entries
    if not include_risky:
        risky = detect_risky_entries(profile)
        if risky:
            for r in risky:
                warnings.append(f"Stripped risky entry: {r['value']} ({r['reason']})")
            profile = strip_risky_entries(profile, risky)

    # Apply exclude patterns
    if exclude_patterns:
        profile, excluded = apply_exclude_patterns(profile, exclude_patterns)
        for desc in excluded:
            actions.append(f"Excluded: {desc}")

    # Validate
    errors, val_warnings = schema.validate_profile(profile)
    if errors:
        return {"errors": errors, "warnings": warnings}
    warnings.extend(val_warnings)

    # Category counts
    counts: list[str] = []
    n_aliases = len(profile.get("bashAliases", {}))
    n_mappings = len(profile.get("commandMappings", {}))
    n_domains = len(profile.get("webfetch", {}).get("extraDomains", []))
    n_read = len(profile.get("fileAccess", {}).get("readPaths", []))
    if n_aliases:
        counts.append(f"{n_aliases} aliases")
    if n_mappings:
        counts.append(f"{n_mappings} mappings")
    if n_domains:
        counts.append(f"{n_domains} domains")
    if n_read:
        counts.append(f"{n_read} readPaths")
    if counts:
        actions.append(", ".join(counts))

    if dry_run:
        return {
            "profile": profile,
            "actions": actions,
            "warnings": warnings,
        }

    # Write output
    profile_json = json.dumps(profile, indent=2) + "\n"

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(profile_json)
        tmp.rename(out_path)
        actions.append(f"Wrote {out_path}")
    else:
        sys.stdout.write(profile_json)

    return {
        "profile": profile,
        "actions": actions,
        "warnings": warnings,
    }
