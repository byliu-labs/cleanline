"""Setup command: first-time onboarding for the permission hooks system.

Scans the user's Claude Code allow list, cross-references against known
aliases, and generates a baseline permission-config.json + hooks registration.
"""
from __future__ import annotations

import json
import re
from importlib.resources import files as pkg_files
from pathlib import Path

# Default doc domains that are universally useful
DEFAULT_EXTRA_DOMAINS = [
    "*.w3.org",
    "*.rust-lang.org",
    "*.docs.rs",
    "*.developer.apple.com",
    "*.learn.microsoft.com",
    "*.devdocs.io",
    "*.man7.org",
    "*.cppreference.com",
]

# Pattern to extract canonical from allow list entries like "Bash(python *)"
BASH_ALLOW_PATTERN = re.compile(r"^Bash\((\S+?)(?:\s+\*)?\)$")


def load_known_aliases() -> dict[str, list[str]]:
    """Load the curated alias mapping table."""
    data_file = pkg_files("claude_hooks").joinpath("known_aliases.json")
    return json.loads(data_file.read_text())


def find_settings_path() -> Path | None:
    """Find the Claude settings.json file."""
    path = Path.home() / ".claude" / "settings.json"
    return path if path.exists() else None


def parse_allow_list(settings_path: Path) -> list[str]:
    """Extract Bash(...) patterns from the settings allow list."""
    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    return settings.get("permissions", {}).get("allow", [])


def extract_canonicals(allow_list: list[str]) -> set[str]:
    """Extract canonical command names from allow list patterns."""
    canonicals = set()
    for entry in allow_list:
        m = BASH_ALLOW_PATTERN.match(entry)
        if m:
            canonicals.add(m.group(1))
    return canonicals


def generate_aliases(canonicals: set[str], known_aliases: dict[str, list[str]]) -> dict[str, str]:
    """Generate bashAliases by cross-referencing canonicals against known variants."""
    aliases: dict[str, str] = {}
    for canonical in sorted(canonicals):
        if canonical in known_aliases:
            for variant in known_aliases[canonical]:
                aliases[variant] = canonical
    return aliases


def generate_config(canonicals: set[str]) -> dict:
    """Generate a starter permission-config.json."""
    known = load_known_aliases()
    aliases = generate_aliases(canonicals, known)

    config: dict = {
        "webfetch": {"extraDomains": DEFAULT_EXTRA_DOMAINS},
    }
    if aliases:
        config["bashAliases"] = aliases
    config["commandMappings"] = {}

    return config


def write_config(config: dict, config_path: Path) -> None:
    """Write permission-config.json."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def analyze_compatibility(
    profile: dict,
    canonicals: set[str],
) -> tuple[list[str], list[str]]:
    """Analyze how a profile's rules map against the user's allow list.

    Returns (ready, inert):
      - ready: rules where the canonical IS in the allow list (will auto-approve)
      - inert: rules where the canonical is NOT in the allow list (recorded but inactive)
    """
    ready: list[str] = []
    inert: list[str] = []

    for alias_key, canonical in profile.get("bashAliases", {}).items():
        desc = f"alias: {alias_key} -> {canonical}"
        if canonical in canonicals:
            ready.append(desc)
        else:
            inert.append(desc)

    for canonical, aliases in profile.get("commandMappings", {}).items():
        # The canonical in a mapping is the command we check against settings
        # Parse the first token as the binary
        binary = canonical.split()[0] if canonical else ""
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            desc = f"mapping: {alias} -> {canonical}"
            if binary in canonicals:
                ready.append(desc)
            else:
                inert.append(desc)

    return ready, inert


def run_setup(
    config_dir: Path,
    *,
    profile_source: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Execute the setup command.

    Returns a summary dict of what was done.
    """
    from . import fetch as fetch_mod
    from . import lockfile as lockfile_mod
    from . import schema as schema_mod

    result: dict = {"actions": [], "warnings": []}

    # Step 1: Find and parse settings
    settings_path = find_settings_path()
    if settings_path:
        allow_list = parse_allow_list(settings_path)
        canonicals = extract_canonicals(allow_list)
        result["canonicals_found"] = sorted(canonicals)
    else:
        canonicals = set()
        result["warnings"].append("~/.claude/settings.json not found")

    # Step 2: Generate config
    config = generate_config(canonicals)
    config_path = config_dir / "permission-config.json"

    if not dry_run:
        write_config(config, config_path)
        result["actions"].append(f"wrote {config_path}")
    else:
        result["actions"].append(f"would write {config_path}")

    result["config"] = config

    # Step 3: Optionally init a profile
    if profile_source:
        profile = fetch_mod.fetch_profile(profile_source)
        errors, warnings = schema_mod.validate_profile(profile)
        result["warnings"].extend(warnings)

        if errors:
            result["profile_errors"] = errors
        else:
            ready, inert = analyze_compatibility(profile, canonicals)
            result["profile_ready"] = ready
            result["profile_inert"] = inert

            if not dry_run:
                lockfile_data = lockfile_mod.read_lockfile()
                lockfile_data = lockfile_mod.add_profile(lockfile_data, profile, profile_source)
                lockfile_mod.write_lockfile(lockfile_data)
                result["actions"].append(f"added profile '{profile['name']}' to lock file")

    return result
