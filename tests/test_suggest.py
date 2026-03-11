"""Tests for the suggest command."""
import json
from pathlib import Path
from unittest.mock import patch

from flow_state.suggest import (
    apply_suggestions,
    find_domain_groups,
    find_path_groups,
    find_version_groups,
    generate_suggestions,
    group_passthroughs,
)


def _make_events(tool: str, inputs: list[str]) -> list[dict]:
    return [
        {"tool": tool, "input": inp, "decision": "passthrough", "matched_rule": "no_match"}
        for inp in inputs
    ]


def test_group_passthroughs_commands() -> None:
    events = _make_events("Bash", ["python3.12 foo", "python3.13 bar", "cargo build"])
    result = group_passthroughs(events)
    cmds = dict(result["commands"])
    assert cmds.get("python3.12") == 1
    assert cmds.get("python3.13") == 1
    assert cmds.get("cargo") == 1


def test_group_passthroughs_domains() -> None:
    events = _make_events("WebFetch", ["docs.foo.com", "api.foo.com", "docs.foo.com"])
    result = group_passthroughs(events)
    doms = dict(result["domains"])
    assert doms.get("docs.foo.com") == 2
    assert doms.get("api.foo.com") == 1


def test_find_version_groups() -> None:
    commands = [("python3.12", 10), ("python3.13", 8), ("cargo", 5)]
    groups = find_version_groups(commands)
    assert len(groups) == 1
    # Regex splits "python3.12" as base="python" + version="3.12"
    assert groups[0]["canonical"] == "python"
    assert groups[0]["total"] == 18


def test_find_version_groups_single_variant_excluded() -> None:
    commands = [("python3.12", 10), ("cargo", 5)]
    groups = find_version_groups(commands)
    # Only python3.12, no pair -> excluded
    assert len(groups) == 0


def test_find_version_groups_known_aliases() -> None:
    """Commands that share a canonical via known_aliases.json should be grouped."""
    commands = [("cargo-clippy", 10), ("cargo-fmt", 8), ("rustc", 5)]
    groups = find_version_groups(commands)
    # cargo-clippy and cargo-fmt are both in known_aliases.json under "cargo"
    assert len(groups) == 1
    assert groups[0]["canonical"] == "cargo"
    assert groups[0]["total"] == 18


def test_find_version_groups_mixed_strategies() -> None:
    """Both regex and known aliases should contribute to the same group."""
    # python3.12 matches via regex, python3 matches via known_aliases.json
    commands = [("python3.12", 10), ("python3", 5)]
    groups = find_version_groups(commands)
    assert len(groups) == 1
    assert groups[0]["canonical"] == "python"
    assert groups[0]["total"] == 15


def test_find_domain_groups() -> None:
    domains = [("docs.foo.com", 5), ("api.foo.com", 3), ("bar.com", 1)]
    groups = find_domain_groups(domains)
    assert len(groups) == 1
    assert groups[0]["pattern"] == "*.foo.com"
    assert groups[0]["total"] == 8


def test_find_domain_groups_no_group() -> None:
    domains = [("foo.com", 5), ("bar.com", 3)]
    groups = find_domain_groups(domains)
    assert groups == []


def test_generate_suggestions_full() -> None:
    events = (
        _make_events("Bash", ["python3.12 x"] * 5 + ["python3.13 y"] * 3)
        + _make_events("WebFetch", ["docs.foo.com"] * 4 + ["api.foo.com"] * 2)
    )
    result = generate_suggestions(events)
    assert len(result["command_groups"]) >= 1
    assert len(result["domain_groups"]) >= 1


def test_generate_suggestions_totals() -> None:
    """Each suggestion group should carry the total passthrough count."""
    events = (
        _make_events("Bash", ["python3.12 x"] * 5 + ["python3.13 y"] * 3)
        + _make_events("WebFetch", ["docs.foo.com"] * 4 + ["api.foo.com"] * 2)
    )
    result = generate_suggestions(events)
    cmd_saved = sum(g["total"] for g in result["command_groups"])
    dom_saved = sum(g["total"] for g in result["domain_groups"])
    assert cmd_saved == 8  # 5 + 3
    assert dom_saved == 6  # 4 + 2


def test_generate_suggestions_empty() -> None:
    result = generate_suggestions([])
    assert result["command_groups"] == []
    assert result["domain_groups"] == []


# ============================================================================
# GRADUATED TRUST: CONFIDENCE + SORTING + MIN COUNT
# ============================================================================


def test_find_version_groups_below_min_count() -> None:
    """Groups with total < MIN_SUGGEST_COUNT should be excluded."""
    commands = [("python3.12", 1), ("python3.13", 1)]
    groups = find_version_groups(commands)
    assert len(groups) == 0  # total=2, below default min_count=3


def test_generate_suggestions_confidence_high() -> None:
    events = _make_events("Bash", ["python3.12 x"] * 8 + ["python3.13 y"] * 7)
    result = generate_suggestions(events)
    assert len(result["command_groups"]) == 1
    assert result["command_groups"][0]["confidence"] == "high"


def test_generate_suggestions_confidence_medium() -> None:
    events = _make_events("Bash", ["python3.12 x"] * 4 + ["python3.13 y"] * 3)
    result = generate_suggestions(events)
    assert len(result["command_groups"]) == 1
    assert result["command_groups"][0]["confidence"] == "medium"


def test_generate_suggestions_confidence_low() -> None:
    events = _make_events("Bash", ["python3.12 x"] * 2 + ["python3.13 y"] * 1)
    result = generate_suggestions(events)
    assert len(result["command_groups"]) == 1
    assert result["command_groups"][0]["confidence"] == "low"


def test_generate_suggestions_sorted() -> None:
    """Groups should be sorted by total descending."""
    events = (
        _make_events("Bash", ["python3.12 x"] * 3 + ["python3.13 y"] * 2)  # total=5
        + _make_events("Bash", ["cargo-clippy z"] * 8 + ["cargo-fmt w"] * 7)  # total=15
    )
    result = generate_suggestions(events)
    groups = result["command_groups"]
    assert len(groups) == 2
    assert groups[0]["total"] >= groups[1]["total"]
    assert groups[0]["canonical"] == "cargo"


def test_generate_suggestions_custom_min_count() -> None:
    """Custom min_count should filter groups below threshold."""
    events = _make_events("Bash", ["python3.12 x"] * 3 + ["python3.13 y"] * 1)  # total=4
    result = generate_suggestions(events, min_count=5)
    assert len(result["command_groups"]) == 0


# ============================================================================
# CLI OUTPUT: PROMPTS SAVED
# ============================================================================


def test_cmd_suggest_shows_prompts_saved(capsys: object) -> None:
    """cmd_suggest should show 'prompts saved' in its output."""
    from unittest.mock import patch
    import argparse
    from flow_state.cli import cmd_suggest

    events = (
        _make_events("Bash", ["python3.12 x"] * 5 + ["python3.13 y"] * 3)
        + [{"tool": "Bash", "input": "git status", "decision": "allow", "matched_rule": "direct"}] * 10
    )

    args = argparse.Namespace(apply=False)
    with patch("flow_state.cli.audit_mod.read_audit_log", return_value=events):
        cmd_suggest(args)

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "prompts saved" in captured.out.lower()
    assert "auto-approved" in captured.out.lower()


# ============================================================================
# APPLY SUGGESTIONS
# ============================================================================


def test_apply_suggestions_adds_aliases(tmp_path: Path) -> None:
    from flow_state import lockfile as lockfile_mod

    config_path = tmp_path / "permission-config.json"
    lockfile_path = tmp_path / "profiles.lock.json"

    # Set up lockfile with user_config
    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {"bashAliases": {}, "webfetch": {"extraDomains": []}},
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    suggestions = {
        "command_groups": [
            {"canonical": "python", "variants": [("python3.12", 5), ("python3.13", 3)], "total": 8}
        ],
        "domain_groups": [],
    }

    with (
        patch("builtins.input", return_value="y"),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = apply_suggestions(suggestions, config_path)

    assert not result["cancelled"]
    assert any("aliases" in a for a in result["actions"])

    # Check lockfile user_config was updated
    data = lockfile_mod.read_lockfile(lockfile_path)
    assert data["user_config"]["bashAliases"]["python3.12"] == "python"
    assert data["user_config"]["bashAliases"]["python3.13"] == "python"


def test_apply_suggestions_adds_domains(tmp_path: Path) -> None:
    from flow_state import lockfile as lockfile_mod

    config_path = tmp_path / "permission-config.json"
    lockfile_path = tmp_path / "profiles.lock.json"

    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {"webfetch": {"extraDomains": []}},
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    suggestions = {
        "command_groups": [],
        "domain_groups": [
            {"pattern": "*.foo.com", "subdomains": [("docs.foo.com", 5), ("api.foo.com", 3)], "total": 8}
        ],
    }

    with (
        patch("builtins.input", return_value="y"),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = apply_suggestions(suggestions, config_path)

    assert not result["cancelled"]
    data = lockfile_mod.read_lockfile(lockfile_path)
    assert "*.foo.com" in data["user_config"]["webfetch"]["extraDomains"]


def test_apply_suggestions_cancelled(tmp_path: Path) -> None:
    from flow_state import lockfile as lockfile_mod

    config_path = tmp_path / "permission-config.json"
    lockfile_path = tmp_path / "profiles.lock.json"
    lockfile_mod.write_lockfile({"profiles": [], "merged": {}}, lockfile_path)

    suggestions = {
        "command_groups": [
            {"canonical": "python", "variants": [("python3.12", 5), ("python3.13", 3)], "total": 8}
        ],
        "domain_groups": [],
    }

    with (
        patch("builtins.input", return_value="n"),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = apply_suggestions(suggestions, config_path)

    assert result["cancelled"]


# ============================================================================
# FILE PATH GROUPING
# ============================================================================


def test_group_passthroughs_file_paths() -> None:
    events = _make_events("Read", ["/home/user/src/a.py", "/home/user/src/b.py", "/tmp/foo.txt"])
    result = group_passthroughs(events)
    paths = dict(result["file_paths"])
    assert paths.get("/home/user/src/a.py") == 1
    assert paths.get("/home/user/src/b.py") == 1
    assert paths.get("/tmp/foo.txt") == 1


def test_group_passthroughs_file_tools() -> None:
    """Read tools go to file_paths, write tools go to write_file_paths."""
    events = (
        _make_events("Read", ["/src/a.py"])
        + _make_events("Edit", ["/src/b.py"])
        + _make_events("Write", ["/src/c.py"])
        + _make_events("Glob", ["/src"])
        + _make_events("Grep", ["/src"])
    )
    result = group_passthroughs(events)
    read_paths = dict(result["file_paths"])
    write_paths = dict(result["write_file_paths"])
    assert len(read_paths) == 2  # /src/a.py, /src (Read + Glob/Grep)
    assert len(write_paths) == 2  # /src/b.py, /src/c.py (Edit + Write)


def test_find_path_groups() -> None:
    file_paths = [("/home/user/src/a.py", 5), ("/home/user/src/b.py", 3), ("/tmp/foo.txt", 1)]
    groups = find_path_groups(file_paths)
    assert len(groups) == 1
    assert groups[0]["pattern"] == "/home/user/src/**"
    assert groups[0]["total"] == 8


def test_find_path_groups_no_group() -> None:
    file_paths = [("/a/file.py", 5), ("/b/file.py", 3)]
    groups = find_path_groups(file_paths)
    # Each dir has only 1 file, so no groups (need 2+ per dir)
    assert groups == []


def test_find_path_groups_below_min_count() -> None:
    file_paths = [("/home/user/src/a.py", 1), ("/home/user/src/b.py", 1)]
    groups = find_path_groups(file_paths)
    assert len(groups) == 0  # total=2, below default min_count=3


def test_find_path_groups_confidence() -> None:
    file_paths = [("/src/a.py", 8), ("/src/b.py", 7)]
    groups = find_path_groups(file_paths)
    assert len(groups) == 1
    assert groups[0]["confidence"] == "high"


def test_generate_suggestions_includes_file_paths() -> None:
    events = _make_events("Read", ["/home/user/src/a.py"] * 5 + ["/home/user/src/b.py"] * 3)
    result = generate_suggestions(events)
    assert len(result["file_path_groups"]) >= 1
    assert result["file_path_groups"][0]["pattern"] == "/home/user/src/**"


def test_generate_suggestions_top_file_paths() -> None:
    events = _make_events("Read", ["/unique/path.py"] * 2)
    result = generate_suggestions(events)
    assert len(result["top_file_paths"]) >= 1


def test_apply_suggestions_adds_read_paths(tmp_path: Path) -> None:
    from flow_state import lockfile as lockfile_mod

    config_path = tmp_path / "permission-config.json"
    lockfile_path = tmp_path / "profiles.lock.json"

    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {"fileAccess": {"readPaths": [], "writePaths": []}},
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    suggestions = {
        "command_groups": [],
        "domain_groups": [],
        "file_path_groups": [
            {"pattern": "/home/user/src/**", "paths": [("/home/user/src/a.py", 5)], "total": 5}
        ],
    }

    with (
        patch("builtins.input", return_value="y"),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = apply_suggestions(suggestions, config_path)

    assert not result["cancelled"]
    assert any("read paths" in a for a in result["actions"])

    data = lockfile_mod.read_lockfile(lockfile_path)
    assert "/home/user/src/**" in data["user_config"]["fileAccess"]["readPaths"]


def test_apply_suggestions_no_duplicates(tmp_path: Path) -> None:
    """Should not add aliases that already exist in user_config."""
    from flow_state import lockfile as lockfile_mod

    config_path = tmp_path / "permission-config.json"
    lockfile_path = tmp_path / "profiles.lock.json"

    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {
            "bashAliases": {"python3.12": "python"},
            "webfetch": {"extraDomains": ["*.foo.com"]},
        },
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    suggestions = {
        "command_groups": [
            {"canonical": "python", "variants": [("python3.12", 5), ("python3.13", 3)], "total": 8}
        ],
        "domain_groups": [
            {"pattern": "*.foo.com", "subdomains": [("docs.foo.com", 5)], "total": 5}
        ],
    }

    with (
        patch("builtins.input", return_value="y"),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = apply_suggestions(suggestions, config_path)

    data = lockfile_mod.read_lockfile(lockfile_path)
    # python3.12 was already there, only python3.13 should be added
    assert data["user_config"]["bashAliases"]["python3.12"] == "python"
    assert data["user_config"]["bashAliases"]["python3.13"] == "python"
    # *.foo.com was already there, count should be 1
    assert data["user_config"]["webfetch"]["extraDomains"].count("*.foo.com") == 1


# ============================================================================
# TIER-AWARE TESTS
# ============================================================================


def test_confidence_label_cautious_uses_higher_thresholds() -> None:
    """Cautious tier requires more hits for high confidence."""
    from flow_state.suggest import _confidence_label

    # 10 hits: high in balanced, but medium in cautious (needs 15 for high)
    assert _confidence_label(10, tier="balanced") == "high"
    assert _confidence_label(10, tier="cautious") == "medium"


def test_confidence_label_flow_uses_lower_thresholds() -> None:
    """Flow tier needs fewer hits for high confidence."""
    from flow_state.suggest import _confidence_label

    # 7 hits: medium in balanced, high in flow
    assert _confidence_label(7, tier="balanced") == "medium"
    assert _confidence_label(7, tier="flow") == "high"


def test_generate_suggestions_cautious_higher_threshold() -> None:
    """Cautious tier requires 5 hits to suggest (not 3)."""
    events = [
        {"decision": "passthrough", "tool": "Bash", "input": "python3.12 test.py"},
        {"decision": "passthrough", "tool": "Bash", "input": "python3.12 test.py"},
        {"decision": "passthrough", "tool": "Bash", "input": "python3.12 test.py"},
        {"decision": "passthrough", "tool": "Bash", "input": "python3.13 test.py"},
    ]
    # With balanced (min_count=3), this would produce a suggestion (total=4 >= 3)
    balanced = generate_suggestions(events, tier="balanced")
    # With cautious (min_count=5), total=4 < 5, no suggestion
    cautious = generate_suggestions(events, tier="cautious")

    assert len(balanced["command_groups"]) >= 1
    assert len(cautious["command_groups"]) == 0


def test_generate_suggestions_flow_lower_threshold() -> None:
    """Flow tier accepts 2 hits as sufficient."""
    events = [
        {"decision": "passthrough", "tool": "Bash", "input": "python3.12 test.py"},
        {"decision": "passthrough", "tool": "Bash", "input": "python3.13 test.py"},
    ]
    # Flow min_count=2, total=2 >= 2
    flow = generate_suggestions(events, tier="flow")
    # Balanced min_count=3, total=2 < 3
    balanced = generate_suggestions(events, tier="balanced")

    assert len(flow["command_groups"]) >= 1
    assert len(balanced["command_groups"]) == 0


def test_generate_suggestions_explicit_min_count_overrides_tier() -> None:
    """Explicit min_count always overrides the tier default."""
    events = [
        {"decision": "passthrough", "tool": "Bash", "input": "python3.12 test.py"},
        {"decision": "passthrough", "tool": "Bash", "input": "python3.13 test.py"},
    ]
    # Even on cautious tier, explicit min_count=1 should find suggestions
    result = generate_suggestions(events, min_count=1, tier="cautious")
    assert len(result["command_groups"]) >= 1


# ============================================================================
# WRITE PATH SEPARATION (suggest_write_min_count)
# ============================================================================


def test_group_passthroughs_separates_read_write() -> None:
    """Edit/Write events go to write_file_paths, not file_paths."""
    events = (
        _make_events("Read", ["/src/a.py"] * 3)
        + _make_events("Edit", ["/src/b.py"] * 4)
        + _make_events("Write", ["/src/c.py"] * 2)
        + _make_events("Glob", ["/src"] * 1)
    )
    result = group_passthroughs(events)
    read_paths = dict(result["file_paths"])
    write_paths = dict(result["write_file_paths"])

    assert "/src/a.py" in read_paths
    assert "/src" in read_paths
    assert "/src/b.py" in write_paths
    assert "/src/c.py" in write_paths
    # Write paths should NOT be in read paths
    assert "/src/b.py" not in read_paths
    assert "/src/c.py" not in read_paths


def test_generate_suggestions_write_paths_higher_threshold() -> None:
    """Write paths below suggest_write_min_count but above suggest_min_count are not suggested."""
    # Balanced tier: suggest_min_count=3, suggest_write_min_count=5
    # Create 4 write events per path in same dir (total=4, above 3 but below 5)
    events = (
        _make_events("Edit", ["/proj/src/a.py"] * 2)
        + _make_events("Write", ["/proj/src/b.py"] * 2)
    )
    result = generate_suggestions(events, tier="balanced")
    # Total=4, above balanced suggest_min_count=3 but below suggest_write_min_count=5
    assert len(result["write_path_groups"]) == 0

    # Same events for read tools would be suggested
    read_events = (
        _make_events("Read", ["/proj/src/a.py"] * 2)
        + _make_events("Glob", ["/proj/src/b.py"] * 2)
    )
    read_result = generate_suggestions(read_events, tier="balanced")
    assert len(read_result["file_path_groups"]) >= 1


def test_generate_suggestions_write_paths_above_threshold() -> None:
    """Write paths above suggest_write_min_count are suggested with write-prefixed confidence."""
    # Balanced: suggest_write_min_count=5
    events = (
        _make_events("Edit", ["/proj/src/a.py"] * 3)
        + _make_events("Write", ["/proj/src/b.py"] * 3)
    )
    result = generate_suggestions(events, tier="balanced")
    assert len(result["write_path_groups"]) >= 1
    group = result["write_path_groups"][0]
    assert group["access"] == "write"
    assert group["confidence"].startswith("write-")


def test_generate_suggestions_write_groups_have_access_label() -> None:
    """Both read and write groups carry an access label."""
    events = (
        _make_events("Read", ["/proj/src/a.py"] * 3 + ["/proj/src/b.py"] * 3)
        + _make_events("Edit", ["/proj/lib/x.py"] * 3 + ["/proj/lib/y.py"] * 3)
    )
    result = generate_suggestions(events, tier="balanced")
    for group in result["file_path_groups"]:
        assert group["access"] == "read"
    for group in result["write_path_groups"]:
        assert group["access"] == "write"


def test_generate_suggestions_flow_write_threshold() -> None:
    """Flow tier uses suggest_write_min_count=3 for write paths."""
    events = (
        _make_events("Edit", ["/proj/src/a.py"] * 2)
        + _make_events("Write", ["/proj/src/b.py"] * 2)
    )
    # Total=4, flow write threshold=3
    flow_result = generate_suggestions(events, tier="flow")
    assert len(flow_result["write_path_groups"]) >= 1
    # Total=4, balanced write threshold=5
    balanced_result = generate_suggestions(events, tier="balanced")
    assert len(balanced_result["write_path_groups"]) == 0


def test_apply_suggestions_adds_write_paths(tmp_path: Path) -> None:
    from flow_state import lockfile as lockfile_mod

    config_path = tmp_path / "permission-config.json"
    lockfile_path = tmp_path / "profiles.lock.json"

    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {"fileAccess": {"readPaths": [], "writePaths": []}},
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    suggestions = {
        "command_groups": [],
        "domain_groups": [],
        "file_path_groups": [],
        "write_path_groups": [
            {"pattern": "/proj/src/**", "paths": [("/proj/src/a.py", 5)],
             "total": 5, "access": "write", "confidence": "write-medium"}
        ],
    }

    with (
        patch("builtins.input", return_value="y"),
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
    ):
        result = apply_suggestions(suggestions, config_path)

    assert not result["cancelled"]
    assert any("write paths" in a for a in result["actions"])

    data = lockfile_mod.read_lockfile(lockfile_path)
    assert "/proj/src/**" in data["user_config"]["fileAccess"]["writePaths"]
