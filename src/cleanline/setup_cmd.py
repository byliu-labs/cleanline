"""Setup command: first-time onboarding for the permission hooks system.

Scans the user's Claude Code allow list, cross-references against known
aliases, and generates a baseline permission-config.json + hooks registration.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from importlib.resources import files as pkg_files
from pathlib import Path

def _load_known_domains() -> list[str]:
    """Load the default documentation domain list."""
    data_file = pkg_files("cleanline").joinpath("known_domains.json")
    return json.loads(data_file.read_text())

# Pattern to extract canonical from allow list entries like "Bash(python *)"
BASH_ALLOW_PATTERN = re.compile(r"^Bash\((\S+?)(?:\s+\*)?\)$")

# Hook scripts to copy to ~/.claude/hooks/
HOOK_FILES = [
    "bash-gate.sh",
    "normalize-bash-cmd.py",
    "match-command-equiv.py",
    "bash_utils.py",
    "approve-webfetch-domains.sh",
    "parse-url-host.py",
    "log_event.py",
]

# Hook entries to register in settings.json
HOOK_ENTRIES = {
    "PreToolUse": [
        {
            "matcher": "WebFetch",
            "hooks": [{"type": "command", "command": "~/.claude/hooks/approve-webfetch-domains.sh"}],
        },
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "~/.claude/hooks/bash-gate.sh"}],
        },
    ],
    "PostToolUse": [],
}


def load_known_aliases() -> dict[str, list[str]]:
    """Load the curated alias mapping table."""
    data_file = pkg_files("cleanline").joinpath("known_aliases.json")
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
    domains = _load_known_domains()

    config: dict = {
        "webfetch": {"extraDomains": domains},
    }
    if aliases:
        config["bashAliases"] = aliases
    config["commandMappings"] = {}

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

    # Check jq
    try:
        subprocess.run(
            ["jq", "--version"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        errors.append(
            "jq is required but not found. "
            "Install: brew install jq (macOS) or apt install jq (Linux)"
        )

    # Check python3 version
    try:
        result = subprocess.run(
            ["python3", "--version"],
            capture_output=True, text=True, check=True,
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
            "~/.claude/settings.json not found. "
            "Run Claude Code at least once to generate it."
        )

    return errors


# ============================================================================
# HOOK SOURCE DISCOVERY + FILE COPYING
# ============================================================================


def find_hook_source_dir() -> Path | None:
    """Find the hooks/ directory relative to the installed package."""
    pkg_dir = Path(__file__).parent  # src/cleanline/
    repo_root = pkg_dir.parent.parent  # clean-line/
    hooks_dir = repo_root / "plugins" / "permission-hooks" / "hooks"
    if hooks_dir.exists():
        return hooks_dir
    return None


def _file_md5(path: Path) -> str:
    """Compute MD5 of a file."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def copy_hooks(target_dir: Path, source_dir: Path) -> list[str]:
    """Copy hook scripts to target directory.

    Returns list of filenames that were actually copied (skips unchanged).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    for filename in HOOK_FILES:
        src = source_dir / filename
        dst = target_dir / filename
        if not src.exists():
            continue

        # Skip if target exists with same content
        if dst.exists() and _file_md5(src) == _file_md5(dst):
            continue

        shutil.copy2(src, dst)
        copied.append(filename)

    return copied


# ============================================================================
# HOOK REGISTRATION IN SETTINGS.JSON
# ============================================================================


def _is_our_hook(entry: dict) -> bool:
    """Check if a hook entry was registered by us (by command path)."""
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if "/.claude/hooks/bash-gate.sh" in cmd or "/.claude/hooks/approve-webfetch-domains.sh" in cmd:
            return True
    return False


def register_hooks(settings_path: Path, hooks_dir: Path) -> dict:
    """Add hook entries to settings.json. Idempotent.

    Returns summary: {"added": [...], "skipped": [...], "backup": str|None}
    """
    result: dict = {"added": [], "skipped": [], "backup": None}

    # Read current settings
    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        settings = {}

    # Backup before modifying
    backup_path = settings_path.with_suffix(".json.backup")
    shutil.copy2(settings_path, backup_path)
    result["backup"] = str(backup_path)

    hooks = settings.setdefault("hooks", {})

    # Register PreToolUse hooks
    pre_hooks = hooks.setdefault("PreToolUse", [])

    for entry in HOOK_ENTRIES["PreToolUse"]:
        command_path = entry["hooks"][0]["command"]
        # Expand ~ for comparison
        expanded = str(Path(command_path.replace("~", str(Path.home()))))

        already_registered = False
        for existing in pre_hooks:
            for h in existing.get("hooks", []):
                existing_expanded = h.get("command", "").replace("~", str(Path.home()))
                if existing_expanded == expanded:
                    already_registered = True
                    break
            if already_registered:
                break

        if already_registered:
            result["skipped"].append(entry["matcher"])
        else:
            pre_hooks.append(entry)
            result["added"].append(entry["matcher"])

    # Write back atomically
    tmp = settings_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    tmp.rename(settings_path)

    return result


def unregister_hooks(settings_path: Path, hooks_dir: Path) -> dict:
    """Remove our hook entries from settings.json and clean up hook files.

    Returns summary: {"removed_hooks": [...], "removed_files": [...]}
    """
    result: dict = {"removed_hooks": [], "removed_files": []}

    # Remove from settings.json
    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return result

    hooks = settings.get("hooks", {})
    for phase in ["PreToolUse", "PostToolUse"]:
        entries = hooks.get(phase, [])
        filtered = [e for e in entries if not _is_our_hook(e)]
        removed_count = len(entries) - len(filtered)
        if removed_count > 0:
            result["removed_hooks"].append(f"{removed_count} from {phase}")
        hooks[phase] = filtered

    # Write back
    tmp = settings_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    tmp.rename(settings_path)

    # Remove hook files
    for filename in HOOK_FILES:
        path = hooks_dir / filename
        if path.exists():
            path.unlink()
            result["removed_files"].append(filename)

    # Remove permission-config.json
    config_path = hooks_dir / "permission-config.json"
    if config_path.exists():
        config_path.unlink()
        result["removed_files"].append("permission-config.json")

    return result


# ============================================================================
# HOOK HEALTH CHECK
# ============================================================================


def check_hook_health(settings_path: Path) -> list[str]:
    """Verify that registered hook command paths exist on disk. Returns warnings."""
    warnings: list[str] = []

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return warnings

    hooks = settings.get("hooks", {})
    for phase in ["PreToolUse", "PostToolUse"]:
        for entry in hooks.get(phase, []):
            for hook in entry.get("hooks", []):
                if hook.get("type") != "command":
                    continue
                cmd = hook.get("command", "")
                if not cmd:
                    continue
                expanded = Path(cmd.replace("~", str(Path.home())))
                if not expanded.exists():
                    warnings.append(
                        f"Hook registered but file missing: {cmd} "
                        "-- run 'cleanline setup' to repair"
                    )

    return warnings


# ============================================================================
# INTERACTIVE SUMMARY
# ============================================================================


def print_setup_summary(
    canonicals: set[str],
    config: dict,
    hooks_to_register: list[str],
    files_to_copy: int,
) -> None:
    """Print a clear summary of what setup will do."""
    aliases = config.get("bashAliases", {})
    domains = config.get("webfetch", {}).get("extraDomains", [])

    print(f"\nScanning ~/.claude/settings.json...")
    print(f"  Found {len(canonicals)} commands in your allow list")

    if aliases:
        print(f"\nGenerating alias rules...")
        print(f"  {len(aliases)} alias rules from known variants")

    if domains:
        print(f"\nGenerating domain rules...")
        print(f"  {len(domains)} documentation domains")

    print(f"\nHook registration:")
    for hook_name in hooks_to_register:
        print(f"  + {hook_name}")
    print(f"  {files_to_copy} hook files -> ~/.claude/hooks/")


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
    profile_source: str | None = None,
    dry_run: bool = False,
    auto_yes: bool = False,
    interactive: bool = True,
) -> dict:
    """Execute the setup command.

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
    if settings_path:
        allow_list = parse_allow_list(settings_path)
        canonicals = extract_canonicals(allow_list)
        result["canonicals_found"] = sorted(canonicals)
    else:
        canonicals = set()
        if not dry_run:
            result["warnings"].append("~/.claude/settings.json not found")

    # Step 2: Generate config
    config = generate_config(canonicals)
    result["config"] = config

    # Step 3: Find hook sources
    source_dir = find_hook_source_dir()
    if not source_dir:
        result["warnings"].append(
            "Hook source files not found. "
            "Are you running from the clean-line repo?"
        )

    # Step 4: Interactive summary
    hooks_to_register = [e["matcher"] for e in HOOK_ENTRIES.get("PreToolUse", [])]
    files_count = len(HOOK_FILES)

    if interactive and not dry_run:
        print_setup_summary(canonicals, config, hooks_to_register, files_count)
        if not confirm_proceed(auto_yes):
            result["actions"].append("cancelled by user")
            return result

    # Step 5: Write config
    config_path = config_dir / "permission-config.json"
    if not dry_run:
        write_config(config, config_path)
        result["actions"].append(f"wrote {config_path}")
    else:
        result["actions"].append(f"would write {config_path}")

    # Step 6: Copy hooks
    if source_dir and not dry_run:
        copied = copy_hooks(config_dir, source_dir)
        if copied:
            result["actions"].append(f"copied {len(copied)} hook files to {config_dir}")
            result["copied_files"] = copied
        else:
            result["actions"].append("all hook files already up to date")
    elif source_dir and dry_run:
        result["actions"].append(f"would copy {files_count} hook files to {config_dir}")

    # Step 7: Register hooks in settings.json
    if settings_path and not dry_run:
        reg_result = register_hooks(settings_path, config_dir)
        if reg_result["added"]:
            result["actions"].append(
                f"registered {len(reg_result['added'])} hooks in settings.json "
                f"({', '.join(reg_result['added'])})"
            )
        if reg_result["skipped"]:
            result["actions"].append(
                f"hooks already registered: {', '.join(reg_result['skipped'])}"
            )
        if reg_result["backup"]:
            result["actions"].append(f"backed up settings.json to {reg_result['backup']}")
    elif settings_path and dry_run:
        result["actions"].append("would register 2 hooks in settings.json")

    # Step 8: Optionally init a profile
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
        print("Run 'cleanline status' after a few sessions to see the impact.")

    return result


def run_uninstall(
    hooks_dir: Path,
    *,
    auto_yes: bool = False,
    interactive: bool = True,
) -> dict:
    """Remove hooks from settings.json and clean up hook files."""
    result: dict = {"actions": [], "errors": []}

    settings_path = find_settings_path()
    if not settings_path:
        result["errors"].append("~/.claude/settings.json not found")
        return result

    if interactive:
        print("\nThis will:")
        print("  - Remove hook entries from ~/.claude/settings.json")
        print(f"  - Remove hook files from {hooks_dir}")
        print("  - Leave profiles.lock.json intact (your profile data)")
        if not confirm_proceed(auto_yes):
            result["actions"].append("cancelled by user")
            return result

    unreg = unregister_hooks(settings_path, hooks_dir)
    if unreg["removed_hooks"]:
        result["actions"].append(f"removed hooks: {', '.join(unreg['removed_hooks'])}")
    if unreg["removed_files"]:
        result["actions"].append(f"removed {len(unreg['removed_files'])} files from {hooks_dir}")

    if not unreg["removed_hooks"] and not unreg["removed_files"]:
        result["actions"].append("nothing to remove (hooks not installed)")

    return result
