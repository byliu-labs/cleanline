"""Tests for allow list consolidation (clean command)."""
import json
from pathlib import Path

from cleanline.clean_cmd import (
    analyze_allow_list,
    apply_clean,
    find_cleanline_handled,
    find_consolidations,
    find_redundant_entries,
)


# ============================================================================
# REDUNDANCY DETECTION
# ============================================================================


def test_find_redundant_wildcard_covers_specific() -> None:
    allow = ["Bash(git log)", "Bash(git *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(git log)"
    assert result[0]["covered_by"] == "Bash(git *)"


def test_find_redundant_wildcard_covers_with_args() -> None:
    allow = ["Bash(git -C /foo log --oneline)", "Bash(git *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(git -C /foo log --oneline)"
    assert result[0]["covered_by"] == "Bash(git *)"


def test_find_redundant_no_cross_command() -> None:
    allow = ["Bash(git log)", "Bash(grep *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 0


def test_find_redundant_non_bash_ignored() -> None:
    allow = ["Read(**)", "Bash(git *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 0


def test_find_redundant_wildcard_not_self_redundant() -> None:
    allow = ["Bash(git *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 0


def test_find_redundant_bare_command_covered() -> None:
    """Bash(git) (bare, no args) is covered by Bash(git *)."""
    allow = ["Bash(git)", "Bash(git *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(git)"


def test_find_redundant_multi_word_wildcard() -> None:
    """Bash(docker compose *) covers Bash(docker compose up)."""
    allow = ["Bash(docker compose up)", "Bash(docker compose *)"]
    result = find_redundant_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(docker compose up)"


# ============================================================================
# CLEAN LINE HANDLED (INFORMATIONAL)
# ============================================================================


def test_find_cleanline_handled_alias_exists() -> None:
    allow = ["Bash(python3.12 *)", "Bash(python *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    result = find_cleanline_handled(allow, config)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(python3.12 *)"
    assert result[0]["canonical_entry"] == "Bash(python *)"


def test_find_cleanline_handled_no_canonical_entry() -> None:
    allow = ["Bash(python3.12 *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    result = find_cleanline_handled(allow, config)
    assert len(result) == 0


def test_find_cleanline_handled_not_in_removals() -> None:
    """Handled entries are separate from redundant entries."""
    allow = ["Bash(python3.12 *)", "Bash(python *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    handled = find_cleanline_handled(allow, config)
    redundant = find_redundant_entries(allow)
    # python3.12 != python — different commands, not redundant
    assert len(handled) == 1
    assert len(redundant) == 0


def test_find_cleanline_handled_no_aliases() -> None:
    allow = ["Bash(python *)"]
    result = find_cleanline_handled(allow, {})
    assert len(result) == 0


def test_find_cleanline_handled_specific_entry() -> None:
    """Specific entries are also detected, not just wildcards."""
    allow = ["Bash(python3.12 script.py)", "Bash(python *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    result = find_cleanline_handled(allow, config)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(python3.12 script.py)"


# ============================================================================
# CONSOLIDATION
# ============================================================================


def test_find_consolidations_narrowest_prefix() -> None:
    allow = [
        "Bash(docker compose up)",
        "Bash(docker compose down)",
        "Bash(docker compose build)",
    ]
    result = find_consolidations(allow)
    assert len(result) == 1
    assert result[0]["proposed"] == "Bash(docker compose *)"
    assert len(result[0]["entries"]) == 3


def test_find_consolidations_root_prefix() -> None:
    allow = [
        "Bash(git log)",
        "Bash(git status)",
        "Bash(git diff)",
    ]
    result = find_consolidations(allow)
    assert len(result) == 1
    assert result[0]["proposed"] == "Bash(git *)"


def test_find_consolidations_min_group() -> None:
    allow = [
        "Bash(git log)",
        "Bash(git status)",
    ]
    result = find_consolidations(allow, min_group=3)
    assert len(result) == 0


def test_find_consolidations_skip_already_wildcarded() -> None:
    allow = [
        "Bash(git *)",
        "Bash(git log)",
        "Bash(git status)",
        "Bash(git diff)",
    ]
    result = find_consolidations(allow)
    assert len(result) == 0


def test_find_consolidations_multiple_groups() -> None:
    allow = [
        "Bash(git log)", "Bash(git status)", "Bash(git diff)",
        "Bash(npm install)", "Bash(npm test)", "Bash(npm run)",
    ]
    result = find_consolidations(allow)
    assert len(result) == 2
    proposed = {r["proposed"] for r in result}
    assert "Bash(git *)" in proposed
    assert "Bash(npm *)" in proposed


def test_find_consolidations_saves_count() -> None:
    allow = [
        "Bash(git log)", "Bash(git status)", "Bash(git diff)",
    ]
    result = find_consolidations(allow)
    assert result[0]["saves"] == 3


# ============================================================================
# ANALYZE_ALLOW_LIST
# ============================================================================


def test_analyze_allow_list_returns_all_categories() -> None:
    allow = ["Bash(git log)", "Bash(git *)"]
    config = {"bashAliases": {"git2": "git"}}
    result = analyze_allow_list(allow, config)
    assert "redundant" in result
    assert "consolidations" in result
    assert "handled" in result
    assert len(result["redundant"]) == 1


def test_analyze_allow_list_empty_config() -> None:
    allow = ["Bash(git log)", "Bash(git status)", "Bash(git diff)"]
    result = analyze_allow_list(allow, {})
    assert len(result["consolidations"]) == 1
    assert len(result["handled"]) == 0


# ============================================================================
# APPLY_CLEAN
# ============================================================================


def test_apply_clean_removes_redundant(tmp_path: Path) -> None:
    settings = {"permissions": {"allow": ["Bash(git log)", "Bash(git *)"]}}
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    redundant = [{"entry": "Bash(git log)", "covered_by": "Bash(git *)"}]
    result = apply_clean(settings_path, redundant, [])
    assert result["removed"] == 1
    reread = json.loads(settings_path.read_text())
    assert "Bash(git log)" not in reread["permissions"]["allow"]
    assert "Bash(git *)" in reread["permissions"]["allow"]


def test_apply_clean_consolidation_adds_wildcard(tmp_path: Path) -> None:
    settings = {
        "permissions": {
            "allow": ["Bash(git log)", "Bash(git status)", "Bash(git diff)"]
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    consolidations = [{
        "entries": ["Bash(git log)", "Bash(git status)", "Bash(git diff)"],
        "proposed": "Bash(git *)",
        "saves": 3,
    }]
    result = apply_clean(settings_path, [], consolidations)
    assert result["consolidated"] == 3
    assert result["added"] == 1
    reread = json.loads(settings_path.read_text())
    assert "Bash(git *)" in reread["permissions"]["allow"]
    assert "Bash(git log)" not in reread["permissions"]["allow"]


def test_apply_clean_preserves_deny(tmp_path: Path) -> None:
    settings = {
        "permissions": {
            "allow": ["Bash(git log)", "Bash(git *)"],
            "deny": ["Bash(rm *)"],
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    redundant = [{"entry": "Bash(git log)", "covered_by": "Bash(git *)"}]
    apply_clean(settings_path, redundant, [])
    reread = json.loads(settings_path.read_text())
    assert reread["permissions"]["deny"] == ["Bash(rm *)"]


def test_apply_clean_preserves_other_fields(tmp_path: Path) -> None:
    settings = {
        "model": "sonnet",
        "hooks": {"some": "hook"},
        "permissions": {"allow": ["Bash(git log)", "Bash(git *)"]},
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    redundant = [{"entry": "Bash(git log)", "covered_by": "Bash(git *)"}]
    apply_clean(settings_path, redundant, [])
    reread = json.loads(settings_path.read_text())
    assert reread["model"] == "sonnet"
    assert reread["hooks"] == {"some": "hook"}


def test_apply_clean_no_changes(tmp_path: Path) -> None:
    settings = {"permissions": {"allow": ["Bash(git *)", "Bash(npm *)"]}}
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    result = apply_clean(settings_path, [], [])
    assert result["removed"] == 0
    assert result["consolidated"] == 0
    assert result["actions"] == []


def test_analyze_no_permissions_key() -> None:
    """Settings without 'permissions' key → empty analysis."""
    allow: list[str] = []  # parse_allow_list returns [] for missing key
    result = analyze_allow_list(allow, {})
    assert result["redundant"] == []
    assert result["consolidations"] == []
    assert result["handled"] == []
