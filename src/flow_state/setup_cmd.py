"""Setup command: first-time onboarding for the permission hooks system.

Scans the user's Claude Code allow list, cross-references against known
aliases, and generates a baseline permission-config.json with resolvedCanonicals.

Plugin installation is handled by /plugin install -- this module only manages
the permission-config.json configuration file.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib.resources import files as pkg_files
from pathlib import Path

from .tiers import DEFAULT_TIER, get_tier_config, validate_tier


def _load_domains_for_tier(tier: str) -> list[str]:
    """Load the cumulative domain list for a tier.

    Cautious = base 8 docs domains.
    Balanced = cautious + 7 popular domains.
    Flow = balanced + 3 more domains.
    """
    base = json.loads(pkg_files("flow_state").joinpath("known_domains.json").read_text())
    if tier == "cautious":
        return base
    balanced_extras = json.loads(
        pkg_files("flow_state").joinpath("known_domains_balanced.json").read_text()
    )
    if tier == "balanced":
        return base + balanced_extras
    flow_extras = json.loads(
        pkg_files("flow_state").joinpath("known_domains_flow.json").read_text()
    )
    return base + balanced_extras + flow_extras


# Pattern to extract canonical from allow list entries like "Bash(python *)"
BASH_ALLOW_PATTERN = re.compile(r"^Bash\((\S+?)(?:\s+\*)?\)$")

# Pattern to extract file paths from allow list entries like "Read(src/**)"
FILE_ALLOW_PATTERN = re.compile(r"^(Read|Edit|Write|Glob|Grep)\((.+)\)$")


def load_known_aliases() -> dict[str, list[str]]:
    """Load the curated alias mapping table."""
    data_file = pkg_files("flow_state").joinpath("known_aliases.json")
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


def extract_file_paths(allow_list: list[str]) -> dict[str, set[str]]:
    """Extract file paths from allow list entries like Read(src/**), Edit(*.py).

    Returns {"readPaths": set(...), "writePaths": set(...)}.
    Read/Glob/Grep -> readPaths, Edit/Write -> writePaths.
    """
    read_paths: set[str] = set()
    write_paths: set[str] = set()

    for entry in allow_list:
        m = FILE_ALLOW_PATTERN.match(entry)
        if m:
            tool, path = m.group(1), m.group(2)
            if tool in ("Read", "Glob", "Grep"):
                read_paths.add(path)
            elif tool in ("Edit", "Write"):
                write_paths.add(path)

    return {"readPaths": read_paths, "writePaths": write_paths}


def generate_aliases(canonicals: set[str], known_aliases: dict[str, list[str]]) -> dict[str, str]:
    """Generate bashAliases by cross-referencing canonicals against known variants."""
    aliases: dict[str, str] = {}
    for canonical in sorted(canonicals):
        if canonical in known_aliases:
            for variant in known_aliases[canonical]:
                aliases[variant] = canonical
    return aliases


def generate_config(
    canonicals: set[str],
    file_paths: dict[str, set[str]] | None = None,
    tier: str = DEFAULT_TIER,
) -> dict:
    """Generate a permission-config.json parameterized by tier."""
    tier_cfg = get_tier_config(tier)
    known = load_known_aliases()
    aliases = generate_aliases(canonicals, known)
    domains = _load_domains_for_tier(tier)

    config: dict = {
        "flowstateTier": tier,
        "webfetch": {"extraDomains": domains},
    }
    if aliases:
        config["bashAliases"] = aliases
    config["commandMappings"] = dict(tier_cfg["command_mappings"])

    # File access: tier baseline + scanned paths from settings.json
    read_paths = set(tier_cfg["read_paths"])
    write_paths = set(tier_cfg["write_paths"])
    if file_paths:
        read_paths.update(file_paths.get("readPaths", set()))
        write_paths.update(file_paths.get("writePaths", set()))

    config["fileAccess"] = {
        "readPaths": sorted(read_paths),
        "writePaths": sorted(write_paths),
        "denyPaths": ["~/.ssh/**", "~/.gnupg/**", "~/.aws/**"],
    }

    config["resolvedCanonicals"] = sorted(canonicals)

    return config


def write_config(config: dict, config_path: Path) -> None:
    """Write permission-config.json atomically."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    tmp.rename(config_path)


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


# ============================================================================
# PREREQUISITE CHECKS
# ============================================================================


def check_prerequisites() -> list[str]:
    """Check that required tools are available. Returns error messages (empty = OK)."""
    errors: list[str] = []

    # Check python3 version
    try:
        result = subprocess.run(
            ["python3", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        version_str = result.stdout.strip().split()[-1]
        parts = version_str.split(".")
        major, minor = int(parts[0]), int(parts[1])
        if major < 3 or (major == 3 and minor < 10):
            errors.append(f"Python 3.10+ required, found {version_str}")
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, IndexError):
        errors.append("python3 is required but not found")

    # Check settings.json exists
    if not find_settings_path():
        errors.append(
            "~/.claude/settings.json not found. " "Run Claude Code at least once to generate it."
        )

    return errors


# ============================================================================
# INTERACTIVE SUMMARY
# ============================================================================


_SAMPLE_SIZE = 3


def _print_sample(items: list[str], fmt: str = "    {item}") -> None:
    """Print up to _SAMPLE_SIZE items with an '... and N more' footer."""
    if not items:
        return
    shown = items[:_SAMPLE_SIZE]
    for item in shown:
        print(fmt.format(item=item))
    remaining = len(items) - len(shown)
    if remaining > 0:
        print(f"    ... and {remaining} more")


def print_setup_summary(
    canonicals: set[str],
    config: dict,
) -> None:
    """Print a clear summary of what setup will do, with sample rules."""
    aliases = config.get("bashAliases", {})
    domains = config.get("webfetch", {}).get("extraDomains", [])
    mappings = config.get("commandMappings", {})

    print("\nScanning ~/.claude/settings.json...")
    print(f"  Found {len(canonicals)} commands in your allow list")

    if aliases:
        print(f"\n  {len(aliases)} alias rules:")
        alias_lines = [f"{v} -> {aliases[v]}" for v in sorted(aliases)]
        _print_sample(alias_lines)

    if mappings:
        print(f"\n  {len(mappings)} command mappings:")
        _print_sample(sorted(mappings.keys()))

    if domains:
        print(f"\n  {len(domains)} domains:")
        _print_sample(sorted(domains))

    file_access = config.get("fileAccess", {})
    read_paths = file_access.get("readPaths", [])
    write_paths = file_access.get("writePaths", [])
    if read_paths or write_paths:
        total = len(read_paths) + len(write_paths)
        print(f"\n  {total} file access rules ({len(read_paths)} read, {len(write_paths)} write):")
        if read_paths:
            _print_sample(sorted(read_paths), fmt="    read:  {item}")
        if write_paths:
            _print_sample(sorted(write_paths), fmt="    write: {item}")

    print(f"\n  Config will be written to ~/.claude/hooks/permission-config.json")


def confirm_proceed(auto_yes: bool = False) -> bool:
    """Ask user to confirm. Returns True if they agree."""
    if auto_yes:
        return True

    print()
    try:
        answer = input("Proceed? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    return answer in ("", "y", "yes")


# ============================================================================
# MAIN SETUP FLOW
# ============================================================================


def run_setup(
    config_dir: Path,
    *,
    tier: str = DEFAULT_TIER,
    profile_source: str | None = None,
    dry_run: bool = False,
    auto_yes: bool = False,
    interactive: bool = True,
) -> dict:
    """Execute the setup command.

    Scans settings.json allow list, generates permission-config.json with
    resolvedCanonicals. Plugin installation is handled separately.

    Returns a summary dict of what was done.
    """
    from . import fetch as fetch_mod
    from . import lockfile as lockfile_mod
    from . import schema as schema_mod

    result: dict = {"actions": [], "warnings": [], "errors": []}

    # Step 0: Prerequisites
    if interactive and not dry_run:
        prereq_errors = check_prerequisites()
        if prereq_errors:
            result["errors"].extend(prereq_errors)
            return result

    # Step 1: Find and parse settings
    settings_path = find_settings_path()
    file_paths: dict[str, set[str]] | None = None
    if settings_path:
        allow_list = parse_allow_list(settings_path)
        canonicals = extract_canonicals(allow_list)
        file_paths = extract_file_paths(allow_list)
        result["canonicals_found"] = sorted(canonicals)
    else:
        canonicals = set()
        if not dry_run:
            result["warnings"].append("~/.claude/settings.json not found")

    # Step 2: Generate config parameterized by tier
    config = generate_config(canonicals, file_paths=file_paths, tier=tier)
    result["config"] = config

    # Step 3: Interactive summary
    if interactive and not dry_run:
        print_setup_summary(canonicals, config)
        if not confirm_proceed(auto_yes):
            result["actions"].append("cancelled by user")
            return result

    # Step 4: Write config
    config_path = config_dir / "permission-config.json"
    if not dry_run:
        write_config(config, config_path)
        result["actions"].append(f"wrote {config_path}")

        # Write user_config to lockfile for future mutations
        lockfile_data = lockfile_mod.read_lockfile()
        lockfile_data["user_config"] = {
            "tier": tier,
            "bashAliases": config.get("bashAliases", {}),
            "commandMappings": config.get("commandMappings", {}),
            "webfetch": config.get("webfetch", {}),
            "fileAccess": config.get("fileAccess", {}),
        }
        lockfile_mod.write_lockfile(lockfile_data)
        result["actions"].append("saved user_config to lockfile")
    else:
        result["actions"].append(f"would write {config_path}")

    # Step 5: Optionally init a profile
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

    # Print success message (interactive mode)
    if interactive and not dry_run and not result["errors"]:
        print()
        for action in result["actions"]:
            print(f"  + {action}")
        print()
        print("Done! Your next Claude Code session will have fewer permission prompts.")
        print("Run 'flowstate status' after a few sessions to see the impact.")

    return result
