"""Tests for the setup command."""
import json
from pathlib import Path

from claude_hooks.setup_cmd import (
    analyze_compatibility,
    extract_canonicals,
    generate_aliases,
    generate_config,
    load_known_aliases,
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
    from claude_hooks.setup_cmd import run_setup

    result = run_setup(tmp_path, dry_run=True)
    assert any("would write" in a for a in result["actions"])
    # Should not actually write
    assert not (tmp_path / "permission-config.json").exists()


def test_setup_writes_config(tmp_path: Path) -> None:
    from claude_hooks.setup_cmd import run_setup

    result = run_setup(tmp_path)
    config_path = tmp_path / "permission-config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text())
    assert "webfetch" in config
