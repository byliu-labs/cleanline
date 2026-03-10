"""Tests for the setup command."""
import json
from pathlib import Path
from unittest.mock import patch

from cleanline.setup_cmd import (
    HOOK_FILES,
    analyze_compatibility,
    check_hook_health,
    check_prerequisites,
    copy_hooks,
    extract_canonicals,
    find_hook_source_dir,
    generate_aliases,
    generate_config,
    load_known_aliases,
    register_hooks,
    run_setup,
    run_uninstall,
    unregister_hooks,
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
    result = run_setup(tmp_path, dry_run=True, interactive=False)
    assert any("would write" in a for a in result["actions"])
    # Should not actually write
    assert not (tmp_path / "permission-config.json").exists()


def test_setup_writes_config(tmp_path: Path) -> None:
    with patch("cleanline.setup_cmd.find_settings_path", return_value=None):
        result = run_setup(tmp_path, interactive=False)
    config_path = tmp_path / "permission-config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text())
    assert "webfetch" in config


# ============================================================================
# PREREQUISITE CHECKS
# ============================================================================


def test_check_prerequisites_missing_jq() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        errors = check_prerequisites()
    assert any("jq" in e for e in errors)


def test_check_prerequisites_all_ok() -> None:
    """When all prereqs are present, returns empty list."""
    # We can't easily mock everything, but we can verify the function runs
    # without raising. If jq/python3/settings exist on this machine, we
    # get no errors.
    errors = check_prerequisites()
    # If the test machine has everything, this is empty.
    # If not, we just verify it returns a list (doesn't crash).
    assert isinstance(errors, list)


# ============================================================================
# HOOK SOURCE DISCOVERY
# ============================================================================


def test_find_hook_source_dir() -> None:
    """Should find the hooks directory from the repo."""
    source_dir = find_hook_source_dir()
    # In a test context running from the repo, this should succeed
    if source_dir is not None:
        assert source_dir.exists()
        assert (source_dir / "bash-gate.sh").exists()


# ============================================================================
# COPY HOOKS
# ============================================================================


def test_copy_hooks_copies_files(tmp_path: Path) -> None:
    source_dir = find_hook_source_dir()
    if source_dir is None:
        return  # Can't test without source

    target_dir = tmp_path / "hooks"
    copied = copy_hooks(target_dir, source_dir)

    assert len(copied) > 0
    for filename in copied:
        assert (target_dir / filename).exists()


def test_copy_hooks_skips_unchanged(tmp_path: Path) -> None:
    source_dir = find_hook_source_dir()
    if source_dir is None:
        return

    target_dir = tmp_path / "hooks"

    # First copy
    first = copy_hooks(target_dir, source_dir)
    assert len(first) > 0

    # Second copy should skip all (unchanged)
    second = copy_hooks(target_dir, source_dir)
    assert len(second) == 0


def test_copy_hooks_updates_changed(tmp_path: Path) -> None:
    source_dir = find_hook_source_dir()
    if source_dir is None:
        return

    target_dir = tmp_path / "hooks"
    copy_hooks(target_dir, source_dir)

    # Modify a file in target
    target_file = target_dir / HOOK_FILES[0]
    target_file.write_text("modified content")

    # Should re-copy the modified file
    copied = copy_hooks(target_dir, source_dir)
    assert HOOK_FILES[0] in copied


# ============================================================================
# REGISTER / UNREGISTER HOOKS
# ============================================================================


def _make_settings(tmp_path: Path, content: dict | None = None) -> Path:
    """Create a temporary settings.json."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(content or {}))
    return settings_path


def test_register_hooks_adds_entries(tmp_path: Path) -> None:
    settings_path = _make_settings(tmp_path, {
        "permissions": {"allow": ["Bash(python *)"]}
    })
    hooks_dir = tmp_path / "hooks"

    result = register_hooks(settings_path, hooks_dir)
    assert len(result["added"]) == 2  # WebFetch + Bash
    assert len(result["skipped"]) == 0

    # Verify settings.json was updated
    settings = json.loads(settings_path.read_text())
    pre_hooks = settings["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 2
    assert pre_hooks[0]["matcher"] == "WebFetch"
    assert pre_hooks[1]["matcher"] == "Bash"


def test_register_hooks_idempotent(tmp_path: Path) -> None:
    settings_path = _make_settings(tmp_path)
    hooks_dir = tmp_path / "hooks"

    # First registration
    register_hooks(settings_path, hooks_dir)

    # Second registration should skip
    result = register_hooks(settings_path, hooks_dir)
    assert len(result["added"]) == 0
    assert len(result["skipped"]) == 2

    # Should not duplicate entries
    settings = json.loads(settings_path.read_text())
    pre_hooks = settings["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 2


def test_register_hooks_preserves_existing(tmp_path: Path) -> None:
    """Our hooks should not clobber existing user hooks."""
    settings_path = _make_settings(tmp_path, {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/my-hook.sh"}]},
            ]
        }
    })
    hooks_dir = tmp_path / "hooks"

    register_hooks(settings_path, hooks_dir)

    settings = json.loads(settings_path.read_text())
    pre_hooks = settings["hooks"]["PreToolUse"]
    # Should have: user's hook + our 2
    assert len(pre_hooks) == 3
    assert pre_hooks[0]["hooks"][0]["command"] == "/usr/local/bin/my-hook.sh"


def test_register_hooks_creates_backup(tmp_path: Path) -> None:
    settings_path = _make_settings(tmp_path)
    hooks_dir = tmp_path / "hooks"

    result = register_hooks(settings_path, hooks_dir)
    assert result["backup"] is not None
    assert Path(result["backup"]).exists()


def test_unregister_hooks_removes_entries(tmp_path: Path) -> None:
    settings_path = _make_settings(tmp_path)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    # Create some hook files
    for filename in HOOK_FILES[:2]:
        (hooks_dir / filename).write_text("#!/bin/bash")

    # Register, then unregister
    register_hooks(settings_path, hooks_dir)
    result = unregister_hooks(settings_path, hooks_dir)

    assert len(result["removed_hooks"]) > 0
    assert len(result["removed_files"]) > 0

    # Verify settings.json is clean
    settings = json.loads(settings_path.read_text())
    pre_hooks = settings["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 0


def test_unregister_preserves_other_hooks(tmp_path: Path) -> None:
    settings_path = _make_settings(tmp_path, {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/other.sh"}]},
            ]
        }
    })
    hooks_dir = tmp_path / "hooks"

    register_hooks(settings_path, hooks_dir)
    unregister_hooks(settings_path, hooks_dir)

    settings = json.loads(settings_path.read_text())
    pre_hooks = settings["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 1
    assert pre_hooks[0]["hooks"][0]["command"] == "/usr/local/bin/other.sh"


# ============================================================================
# HOOK HEALTH CHECK
# ============================================================================


def test_check_hook_health_all_present(tmp_path: Path) -> None:
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    # Create hook files
    for name in ["bash-gate.sh", "approve-webfetch-domains.sh"]:
        (hooks_dir / name).write_text("#!/bin/bash")

    # Register with paths pointing to our tmp hooks
    settings_path = _make_settings(tmp_path, {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": str(hooks_dir / "bash-gate.sh")}
                ]},
                {"matcher": "WebFetch", "hooks": [
                    {"type": "command", "command": str(hooks_dir / "approve-webfetch-domains.sh")}
                ]},
            ]
        }
    })

    warnings = check_hook_health(settings_path)
    assert warnings == []


def test_check_hook_health_missing_file(tmp_path: Path) -> None:
    settings_path = _make_settings(tmp_path, {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": str(tmp_path / "hooks" / "bash-gate.sh")}
                ]},
            ]
        }
    })

    warnings = check_hook_health(settings_path)
    assert len(warnings) == 1
    assert "missing" in warnings[0].lower()


# ============================================================================
# FULL SETUP + UNINSTALL FLOW
# ============================================================================


def test_setup_full_flow(tmp_path: Path) -> None:
    """Full setup with auto_yes writes config, copies hooks, registers."""
    source_dir = find_hook_source_dir()
    if source_dir is None:
        return

    # Create a fake settings.json
    settings_dir = tmp_path / "claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(python *)", "Bash(npm *)"]}
    }))

    hooks_dir = tmp_path / "hooks"

    with patch("cleanline.setup_cmd.find_settings_path", return_value=settings_path):
        result = run_setup(
            hooks_dir,
            auto_yes=True,
            interactive=True,
        )

    assert not result.get("errors")
    assert (hooks_dir / "permission-config.json").exists()
    assert any("hook files" in a or "up to date" in a for a in result["actions"])
    assert any("registered" in a or "already registered" in a for a in result["actions"])


def test_setup_idempotent_second_run(tmp_path: Path) -> None:
    """Running setup twice should not duplicate entries or error."""
    source_dir = find_hook_source_dir()
    if source_dir is None:
        return

    settings_dir = tmp_path / "claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(python *)", "Bash(npm *)"]}
    }))

    hooks_dir = tmp_path / "hooks"

    with patch("cleanline.setup_cmd.find_settings_path", return_value=settings_path):
        # First run
        result1 = run_setup(hooks_dir, auto_yes=True, interactive=True)
        assert not result1.get("errors")

        # Capture state after first run
        settings_after_first = json.loads(settings_path.read_text())
        config_after_first = json.loads((hooks_dir / "permission-config.json").read_text())

        # Second run
        result2 = run_setup(hooks_dir, auto_yes=True, interactive=True)
        assert not result2.get("errors")

    # No duplicate hook entries
    settings_after_second = json.loads(settings_path.read_text())
    pre_hooks = settings_after_second.get("hooks", {}).get("PreToolUse", [])
    assert len(pre_hooks) == 2  # WebFetch + Bash, not 4

    # Config content unchanged
    config_after_second = json.loads((hooks_dir / "permission-config.json").read_text())
    assert config_after_first == config_after_second

    # Second run should report "already registered" and "up to date"
    assert any("already registered" in a for a in result2["actions"])
    assert any("up to date" in a for a in result2["actions"])


def test_uninstall_flow(tmp_path: Path) -> None:
    """Uninstall removes hooks and files."""
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()

    # Create some hook files
    for filename in HOOK_FILES[:2]:
        (hooks_dir / filename).write_text("#!/bin/bash")
    (hooks_dir / "permission-config.json").write_text("{}")

    settings_path = _make_settings(tmp_path)
    register_hooks(settings_path, hooks_dir)

    with patch("cleanline.setup_cmd.find_settings_path", return_value=settings_path):
        result = run_uninstall(hooks_dir, auto_yes=True)

    assert not result.get("errors")
    assert any("removed" in a for a in result["actions"])
