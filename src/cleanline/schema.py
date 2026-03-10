"""Profile JSON schema validation.

A profile is a JSON document published by a community author that declares
bash aliases, command mappings, and webfetch domain rules. This module
validates profiles before they're merged into the lock file.
"""
from __future__ import annotations

# Hard caps prevent a single profile from dominating the merged config
MAX_ALIASES = 50
MAX_MAPPINGS = 30
MAX_DOMAINS = 50

# Warn when a profile uses more than this fraction of any cap
WARN_THRESHOLD = 0.5

REQUIRED_FIELDS = {"name", "version"}
OPTIONAL_FIELDS = {"description", "author", "bashAliases", "commandMappings", "webfetch"}


def validate_profile(profile: dict) -> tuple[list[str], list[str]]:
    """Validate a profile dict.

    Returns (errors, warnings). Empty errors list means valid.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in profile:
            errors.append(f"missing required field: {field}")

    if not isinstance(profile.get("name", ""), str) or not profile.get("name", "").strip():
        errors.append("'name' must be a non-empty string")

    if not isinstance(profile.get("version", ""), str) or not profile.get("version", "").strip():
        errors.append("'version' must be a non-empty string")

    # Validate bashAliases
    aliases = profile.get("bashAliases", {})
    if not isinstance(aliases, dict):
        errors.append("'bashAliases' must be an object")
    else:
        if len(aliases) > MAX_ALIASES:
            errors.append(f"bashAliases has {len(aliases)} entries, max is {MAX_ALIASES}")
        elif len(aliases) > MAX_ALIASES * WARN_THRESHOLD:
            warnings.append(f"bashAliases has {len(aliases)} entries ({MAX_ALIASES} max)")

    # Validate commandMappings
    mappings = profile.get("commandMappings", {})
    if not isinstance(mappings, dict):
        errors.append("'commandMappings' must be an object")
    else:
        if len(mappings) > MAX_MAPPINGS:
            errors.append(f"commandMappings has {len(mappings)} entries, max is {MAX_MAPPINGS}")
        elif len(mappings) > MAX_MAPPINGS * WARN_THRESHOLD:
            warnings.append(f"commandMappings has {len(mappings)} entries ({MAX_MAPPINGS} max)")
        for canonical, aliases_list in mappings.items():
            if not isinstance(aliases_list, list):
                errors.append(f"commandMappings['{canonical}'] must be a list")

    # Validate webfetch
    webfetch = profile.get("webfetch", {})
    if not isinstance(webfetch, dict):
        errors.append("'webfetch' must be an object")
    else:
        domains = webfetch.get("extraDomains", [])
        if not isinstance(domains, list):
            errors.append("'webfetch.extraDomains' must be a list")
        else:
            if len(domains) > MAX_DOMAINS:
                errors.append(f"webfetch.extraDomains has {len(domains)} entries, max is {MAX_DOMAINS}")
            elif len(domains) > MAX_DOMAINS * WARN_THRESHOLD:
                warnings.append(f"webfetch.extraDomains has {len(domains)} entries ({MAX_DOMAINS} max)")

    return errors, warnings
