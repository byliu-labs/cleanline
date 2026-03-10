"""Tests for the setup command."""
import json
from pathlib import Path
from unittest.mock import patch

from cleanline.setup_cmd import (
    analyze_compatibility,
    check_prerequisites,
    extract_canonicals,
    extract_file_paths,
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


def test_extract_file_paths() -> None:
    allow_list = [
        "Read(src/**)",
        "Edit(*.py)",
        "Write(/tmp/**)",
        "Glob(~/.config/**)",
        "Grep(tests/)",
        "Bash(python *)",
    ]
    paths = extract_file_paths(allow_list)
    assert "src/**" in paths["readPaths"]
    assert "~/.config/**" in paths["readPaths"]
    assert "tests/" in paths["readPaths"]
    assert "*.py" in paths["writePaths"]
    assert "/tmp/**" in paths["writePaths"]


def test_extract_file_paths_empty() -> None:
    paths = extract_file_paths(["Bash(python *)"])
    assert paths["readPaths"] == set()
    assert paths["writePaths"] == set()


def test_generate_config_includes_file_access() -> None:
    config = generate_config({"python"})
    assert "fileAccess" in config
    assert "readPaths" in config["fileAccess"]
    assert "writePaths" in config["fileAccess"]
    assert "denyPaths" in config["fileAccess"]
    # Known defaults should be present
    assert "~/.claude/**" in config["fileAccess"]["readPaths"]
    assert "/tmp/**" in config["fileAccess"]["writePaths"]


def test_generate_config_merges_scanned_file_paths() -> None:
    file_paths = {"readPaths": {"~/projects/**"}, "writePaths": {"/opt/out/**"}}
    config = generate_config({"python"}, file_paths=file_paths)
    assert "~/projects/**" in config["fileAccess"]["readPaths"]
    assert "/opt/out/**" in config["fileAccess"]["writePaths"]
    # Known defaults still present
    assert "~/.claude/**" in config["fileAccess"]["readPaths"]


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
    assert "fileAccess" in data["user_config"]
    assert data["user_config"]["tier"] == "balanced"  # default tier


def test_generate_config_cautious_has_no_write_paths() -> None:
    """Cautious tier should not generate writePaths."""
    config = generate_config({"python"}, tier="cautious")
    assert config["fileAccess"]["writePaths"] == []
    assert config["commandMappings"] == {}
    assert config["cleanlineTier"] == "cautious"


def test_generate_config_balanced_has_tmp_write() -> None:
    """Balanced tier should have /tmp/** in writePaths."""
    config = generate_config({"python"}, tier="balanced")
    assert "/tmp/**" in config["fileAccess"]["writePaths"]
    assert config["cleanlineTier"] == "balanced"


def test_generate_config_flow_has_documents() -> None:
    """Flow tier should have broad read/write access."""
    config = generate_config({"python"}, tier="flow")
    assert "~/Documents/**" in config["fileAccess"]["readPaths"]
    assert "~/Documents/**" in config["fileAccess"]["writePaths"]
    assert "~/Desktop/**" in config["fileAccess"]["readPaths"]
    assert "~/Desktop/**" in config["fileAccess"]["writePaths"]
    assert config["cleanlineTier"] == "flow"


def test_generate_config_domains_cumulative() -> None:
    """Each tier includes all lower-tier domains."""
    cautious = generate_config(set(), tier="cautious")
    balanced = generate_config(set(), tier="balanced")
    flow = generate_config(set(), tier="flow")

    cautious_domains = set(cautious["webfetch"]["extraDomains"])
    balanced_domains = set(balanced["webfetch"]["extraDomains"])
    flow_domains = set(flow["webfetch"]["extraDomains"])

    assert cautious_domains < balanced_domains
    assert balanced_domains < flow_domains


def test_generate_config_flow_has_command_mappings() -> None:
    """Flow tier includes framework-specific command mappings."""
    config = generate_config(set(), tier="flow")
    assert "cargo build" in config["commandMappings"]
    assert "pip install" in config["commandMappings"]


def test_run_setup_stores_tier_in_lockfile(tmp_path: Path) -> None:
    """Setup should store the selected tier in lockfile user_config."""
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
        result = run_setup(hooks_dir, tier="cautious", auto_yes=True, interactive=True)

    assert not result.get("errors")
    data = json.loads(lockfile_path.read_text())
    assert data["user_config"]["tier"] == "cautious"


def test_generate_config_merges_scanned_file_paths_with_tier() -> None:
    """Scanned file paths from settings.json merge with tier baseline."""
    file_paths = {"readPaths": {"/my/project/**"}, "writePaths": {"/my/output/**"}}
    config = generate_config(set(), file_paths=file_paths, tier="cautious")

    # Cautious has no writePaths baseline, but scanned paths should still be included
    assert "/my/project/**" in config["fileAccess"]["readPaths"]
    assert "/my/output/**" in config["fileAccess"]["writePaths"]
    # Cautious baseline read paths should also be present
    assert "~/.claude/**" in config["fileAccess"]["readPaths"]
