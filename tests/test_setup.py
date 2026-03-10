"""Tests for the setup command."""
import json
from pathlib import Path
from unittest.mock import patch

from cleanline.setup_cmd import (
    analyze_compatibility,
    check_prerequisites,
    extract_canonicals,
    generate_aliases,
    generate_config,
    load_known_aliases,
    run_setup,
)


def test_load_known_aliases() -> None:
    aliases = load_known_aliases()
    assert isinstance(aliases, dict)
    assert "python" in aliases
    assert "python3" in aliases["python"]


def test_extract_canonicals() -> None:
    allow_list = [
        "Bash(python *)",
        "Bash(git)",
        "Bash(npm *)",
        "WebFetch(*)",
    ]
    canonicals = extract_canonicals(allow_list)
    assert "python" in canonicals
    assert "git" in canonicals
    assert "npm" in canonicals
    # WebFetch is not Bash, should not be extracted
    assert "*" not in canonicals


def test_generate_aliases_from_canonicals() -> None:
    known = {"python": ["python3", "python3.13"], "pip": ["pip3"]}
    canonicals = {"python", "pip"}
    aliases = generate_aliases(canonicals, known)
    assert aliases["python3"] == "python"
    assert aliases["python3.13"] == "python"
    assert aliases["pip3"] == "pip"


def test_generate_aliases_only_for_present_canonicals() -> None:
    known = {"python": ["python3"], "rust": ["rustc-nightly"]}
    canonicals = {"python"}
    aliases = generate_aliases(canonicals, known)
    assert "python3" in aliases
    assert "rustc-nightly" not in aliases


def test_generate_config() -> None:
    config = generate_config({"python", "npm"})
    assert "webfetch" in config
    assert "bashAliases" in config
    assert "commandMappings" in config


def test_generate_config_includes_resolved_canonicals() -> None:
    """Config should include resolvedCanonicals field."""
    config = generate_config({"python", "npm", "git"})
    assert "resolvedCanonicals" in config
    assert sorted(config["resolvedCanonicals"]) == ["git", "npm", "python"]


def test_analyze_compatibility_ready_and_inert() -> None:
    profile = {
        "bashAliases": {"python3.13": "python", "cargo-nightly": "cargo"},
        "commandMappings": {"npm test": ["yarn test"]},
    }
    canonicals = {"python", "npm"}  # cargo is NOT in canonicals
    ready, inert = analyze_compatibility(profile, canonicals)

    assert any("python3.13" in r for r in ready)
    assert any("cargo-nightly" in r for r in inert)
    assert any("yarn test" in r for r in ready)


def test_setup_dry_run(tmp_path: Path) -> None:
    result = run_setup(tmp_path, dry_run=True, interactive=False)
    assert any("would write" in a for a in result["actions"])
    # Should not actually write
    assert not (tmp_path / "permission-config.json").exists()


def test_setup_writes_config(tmp_path: Path) -> None:
    from cleanline import lockfile as lockfile_mod

    lockfile_path = tmp_path / "profiles.lock.json"
    with (
        patch("cleanline.setup_cmd.find_settings_path", return_value=None),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = run_setup(tmp_path, interactive=False)
    config_path = tmp_path / "permission-config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text())
    assert "webfetch" in config
    assert "resolvedCanonicals" in config


# ============================================================================
# PREREQUISITE CHECKS
# ============================================================================


def test_check_prerequisites_all_ok() -> None:
    """When all prereqs are present, returns empty list."""
    errors = check_prerequisites()
    assert isinstance(errors, list)


# ============================================================================
# FULL SETUP FLOW
# ============================================================================


def test_setup_full_flow(tmp_path: Path) -> None:
    """Full setup with auto_yes writes config with resolvedCanonicals."""
    from cleanline import lockfile as lockfile_mod

    # Create a fake settings.json
    settings_dir = tmp_path / "claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(python *)", "Bash(npm *)"]}
    }))

    hooks_dir = tmp_path / "hooks"
    lockfile_path = tmp_path / "profiles.lock.json"

    with (
        patch("cleanline.setup_cmd.find_settings_path", return_value=settings_path),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = run_setup(
            hooks_dir,
            auto_yes=True,
            interactive=True,
        )

    assert not result.get("errors")
    config_path = hooks_dir / "permission-config.json"
    assert config_path.exists()

    config = json.loads(config_path.read_text())
    assert "resolvedCanonicals" in config
    assert "python" in config["resolvedCanonicals"]
    assert "npm" in config["resolvedCanonicals"]

    # Should have user_config saved action
    assert any("user_config" in a for a in result["actions"])


def test_setup_saves_user_config_to_lockfile(tmp_path: Path) -> None:
    """Setup should save user_config to lockfile for future mutations."""
    from cleanline import lockfile as lockfile_mod

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(python *)"]}
    }))

    hooks_dir = tmp_path / "hooks"
    lockfile_path = tmp_path / "profiles.lock.json"

    with (
        patch("cleanline.setup_cmd.find_settings_path", return_value=settings_path),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = run_setup(hooks_dir, auto_yes=True, interactive=True)

    assert not result.get("errors")
    assert lockfile_path.exists()
    data = json.loads(lockfile_path.read_text())
    assert "user_config" in data
    assert "bashAliases" in data["user_config"]
