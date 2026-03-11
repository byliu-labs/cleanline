"""Tests for allow list consolidation (clean command)."""
import json
from pathlib import Path

from flow_state.clean_cmd import (
    analyze_allow_list,
    apply_clean,
    find_flowstate_handled,
    find_consolidations,
    find_file_consolidations,
    find_redundant_entries,
    find_redundant_file_entries,
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


def test_find_flowstate_handled_alias_exists() -> None:
    allow = ["Bash(python3.12 *)", "Bash(python *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    result = find_flowstate_handled(allow, config)
    assert len(result) == 1
    assert result[0]["entry"] == "Bash(python3.12 *)"
    assert result[0]["canonical_entry"] == "Bash(python *)"


def test_find_flowstate_handled_no_canonical_entry() -> None:
    allow = ["Bash(python3.12 *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    result = find_flowstate_handled(allow, config)
    assert len(result) == 0


def test_find_flowstate_handled_not_in_removals() -> None:
    """Handled entries are separate from redundant entries."""
    allow = ["Bash(python3.12 *)", "Bash(python *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    handled = find_flowstate_handled(allow, config)
    redundant = find_redundant_entries(allow)
    # python3.12 != python — different commands, not redundant
    assert len(handled) == 1
    assert len(redundant) == 0


def test_find_flowstate_handled_no_aliases() -> None:
    allow = ["Bash(python *)"]
    result = find_flowstate_handled(allow, {})
    assert len(result) == 0


def test_find_flowstate_handled_specific_entry() -> None:
    """Specific entries are also detected, not just wildcards."""
    allow = ["Bash(python3.12 script.py)", "Bash(python *)"]
    config = {"bashAliases": {"python3.12": "python"}}
    result = find_flowstate_handled(allow, config)
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
    assert "file_redundant" in result
    assert "file_consolidations" in result
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


def test_apply_clean_file_redundant(tmp_path: Path) -> None:
    settings = {
        "permissions": {
            "allow": ["Read(src/main.py)", "Read(src/**)"]
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    file_redundant = [{"entry": "Read(src/main.py)", "covered_by": "Read(src/**)"}]
    result = apply_clean(settings_path, [], [], file_redundant, [])
    assert result["removed"] == 1
    reread = json.loads(settings_path.read_text())
    assert "Read(src/main.py)" not in reread["permissions"]["allow"]
    assert "Read(src/**)" in reread["permissions"]["allow"]


def test_apply_clean_file_consolidation(tmp_path: Path) -> None:
    settings = {
        "permissions": {
            "allow": ["Read(src/foo.py)", "Read(src/bar.py)"]
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    file_cons = [{
        "entries": ["Read(src/foo.py)", "Read(src/bar.py)"],
        "proposed": "Read(src/**)",
        "saves": 2,
    }]
    result = apply_clean(settings_path, [], [], [], file_cons)
    assert result["consolidated"] == 2
    assert result["added"] == 1
    reread = json.loads(settings_path.read_text())
    assert "Read(src/**)" in reread["permissions"]["allow"]
    assert "Read(src/foo.py)" not in reread["permissions"]["allow"]


def test_apply_clean_mixed_bash_and_file(tmp_path: Path) -> None:
    """Both Bash and file path entries cleaned in a single apply."""
    settings = {
        "permissions": {
            "allow": [
                "Bash(git log)", "Bash(git *)",
                "Read(src/a.py)", "Read(src/**)",
            ]
        }
    }
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings))

    redundant = [{"entry": "Bash(git log)", "covered_by": "Bash(git *)"}]
    file_redundant = [{"entry": "Read(src/a.py)", "covered_by": "Read(src/**)"}]
    result = apply_clean(settings_path, redundant, [], file_redundant, [])
    assert result["removed"] == 2
    reread = json.loads(settings_path.read_text())
    assert len(reread["permissions"]["allow"]) == 2


def test_analyze_no_permissions_key() -> None:
    """Settings without 'permissions' key → empty analysis."""
    allow: list[str] = []  # parse_allow_list returns [] for missing key
    result = analyze_allow_list(allow, {})
    assert result["redundant"] == []
    assert result["consolidations"] == []
    assert result["handled"] == []
    assert result["file_redundant"] == []
    assert result["file_consolidations"] == []


# ============================================================================
# FILE PATH REDUNDANCY DETECTION
# ============================================================================


def test_file_redundant_recursive_wildcard() -> None:
    """Read(src/main.py) is redundant when Read(src/**) exists."""
    allow = ["Read(src/main.py)", "Read(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Read(src/main.py)"
    assert result[0]["covered_by"] == "Read(src/**)"


def test_file_redundant_star_wildcard() -> None:
    """Read(src/main.py) is redundant when Read(src/*) exists."""
    allow = ["Read(src/main.py)", "Read(src/*)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Read(src/main.py)"


def test_file_redundant_nested_path() -> None:
    """Read(src/components/Foo.tsx) is redundant when Read(src/**) exists."""
    allow = ["Read(src/components/Foo.tsx)", "Read(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Read(src/components/Foo.tsx)"


def test_file_redundant_no_cross_tool() -> None:
    """Read(src/main.py) is NOT redundant when Edit(src/**) exists."""
    allow = ["Read(src/main.py)", "Edit(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 0


def test_file_redundant_same_tool_only() -> None:
    """Edit(src/a.py) is redundant when Edit(src/**) exists."""
    allow = ["Edit(src/a.py)", "Edit(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Edit(src/a.py)"


def test_file_redundant_wildcard_not_self() -> None:
    """Read(src/**) is not redundant with itself."""
    allow = ["Read(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 0


def test_file_redundant_bash_entries_ignored() -> None:
    """Bash entries are not processed by file redundancy."""
    allow = ["Bash(git log)", "Bash(git *)", "Read(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 0


def test_file_redundant_ext_wildcard() -> None:
    """Write(src/foo.py) redundant when Write(src/*.py) exists."""
    allow = ["Write(src/foo.py)", "Write(src/*.py)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 1
    assert result[0]["entry"] == "Write(src/foo.py)"


def test_file_redundant_multiple() -> None:
    """Multiple entries redundant under same wildcard."""
    allow = ["Read(src/a.py)", "Read(src/b.py)", "Read(src/**)"]
    result = find_redundant_file_entries(allow)
    assert len(result) == 2
    entries = {r["entry"] for r in result}
    assert entries == {"Read(src/a.py)", "Read(src/b.py)"}


# ============================================================================
# FILE PATH CONSOLIDATION
# ============================================================================


def test_file_consolidation_same_dir() -> None:
    """Two Read entries in same dir → consolidation."""
    allow = ["Read(src/a.py)", "Read(src/b.py)"]
    result = find_file_consolidations(allow)
    assert len(result) == 1
    assert result[0]["proposed"] == "Read(src/**)"
    assert len(result[0]["entries"]) == 2
    assert result[0]["saves"] == 2


def test_file_consolidation_different_tools_no_merge() -> None:
    """Read and Edit in same dir are NOT merged together."""
    allow = ["Read(src/a.py)", "Edit(src/b.py)"]
    result = find_file_consolidations(allow)
    assert len(result) == 0


def test_file_consolidation_different_dirs() -> None:
    """Entries in different dirs are separate groups."""
    allow = [
        "Read(src/a.py)", "Read(src/b.py)",
        "Read(lib/x.py)", "Read(lib/y.py)",
    ]
    result = find_file_consolidations(allow)
    assert len(result) == 2
    proposed = {r["proposed"] for r in result}
    assert "Read(src/**)" in proposed
    assert "Read(lib/**)" in proposed


def test_file_consolidation_min_group() -> None:
    """Single entry in a dir doesn't consolidate (min_group=2)."""
    allow = ["Read(src/a.py)"]
    result = find_file_consolidations(allow)
    assert len(result) == 0


def test_file_consolidation_custom_min_group() -> None:
    """min_group=3 requires 3 entries."""
    allow = ["Read(src/a.py)", "Read(src/b.py)"]
    result = find_file_consolidations(allow, min_group=3)
    assert len(result) == 0


def test_file_consolidation_skip_covered() -> None:
    """Entries already covered by a wildcard are not consolidated."""
    allow = ["Read(src/**)", "Read(src/a.py)", "Read(src/b.py)"]
    result = find_file_consolidations(allow)
    assert len(result) == 0


def test_file_consolidation_wildcards_not_grouped() -> None:
    """Wildcard entries are not included in consolidation groups."""
    allow = ["Read(src/*.py)", "Read(src/*.ts)"]
    result = find_file_consolidations(allow)
    assert len(result) == 0


def test_file_consolidation_root_files_skipped() -> None:
    """Root-level files (no parent dir) are not consolidated."""
    allow = ["Read(CLAUDE.md)", "Read(README.md)"]
    result = find_file_consolidations(allow)
    assert len(result) == 0


def test_file_consolidation_nested_dir() -> None:
    """Consolidation works with nested paths."""
    allow = [
        "Edit(src/components/Foo.tsx)",
        "Edit(src/components/Bar.tsx)",
        "Edit(src/components/Baz.tsx)",
    ]
    result = find_file_consolidations(allow)
    assert len(result) == 1
    assert result[0]["proposed"] == "Edit(src/components/**)"


def test_file_consolidation_saves_count() -> None:
    allow = ["Glob(tests/a.py)", "Glob(tests/b.py)", "Glob(tests/c.py)"]
    result = find_file_consolidations(allow)
    assert len(result) == 1
    assert result[0]["saves"] == 3


# ============================================================================
# ANALYZE_ALLOW_LIST — FILE PATH INTEGRATION
# ============================================================================


def test_analyze_includes_file_results() -> None:
    allow = [
        "Bash(git log)", "Bash(git *)",
        "Read(src/a.py)", "Read(src/**)",
        "Edit(lib/x.py)", "Edit(lib/y.py)",
    ]
    result = analyze_allow_list(allow, {})
    assert len(result["redundant"]) == 1  # Bash(git log)
    assert len(result["file_redundant"]) == 1  # Read(src/a.py)
    assert len(result["file_consolidations"]) == 1  # Edit(lib/**)
