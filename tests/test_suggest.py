"""Tests for the suggest command."""
import json
from pathlib import Path
from unittest.mock import patch

from cleanline.suggest import (
    apply_suggestions,
    find_domain_groups,
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
    from cleanline.cli import cmd_suggest

    events = (
        _make_events("Bash", ["python3.12 x"] * 5 + ["python3.13 y"] * 3)
        + [{"tool": "Bash", "input": "git status", "decision": "allow", "matched_rule": "direct"}] * 10
    )

    args = argparse.Namespace(apply=False)
    with patch("cleanline.cli.audit_mod.read_audit_log", return_value=events):
        cmd_suggest(args)

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "prompts saved" in captured.out.lower()
    assert "auto-approved" in captured.out.lower()


# ============================================================================
# APPLY SUGGESTIONS
# ============================================================================


def test_apply_suggestions_adds_aliases(tmp_path: Path) -> None:
    config_path = tmp_path / "permission-config.json"
    config_path.write_text(json.dumps({"bashAliases": {}, "webfetch": {"extraDomains": []}}))

    suggestions = {
        "command_groups": [
            {"canonical": "python", "variants": [("python3.12", 5), ("python3.13", 3)], "total": 8}
        ],
        "domain_groups": [],
    }

    with patch("builtins.input", return_value="y"):
        result = apply_suggestions(suggestions, config_path)

    assert not result["cancelled"]
    assert any("aliases" in a for a in result["actions"])

    config = json.loads(config_path.read_text())
    assert config["bashAliases"]["python3.12"] == "python"
    assert config["bashAliases"]["python3.13"] == "python"


def test_apply_suggestions_adds_domains(tmp_path: Path) -> None:
    config_path = tmp_path / "permission-config.json"
    config_path.write_text(json.dumps({"webfetch": {"extraDomains": []}}))

    suggestions = {
        "command_groups": [],
        "domain_groups": [
            {"pattern": "*.foo.com", "subdomains": [("docs.foo.com", 5), ("api.foo.com", 3)], "total": 8}
        ],
    }

    with patch("builtins.input", return_value="y"):
        result = apply_suggestions(suggestions, config_path)

    assert not result["cancelled"]
    config = json.loads(config_path.read_text())
    assert "*.foo.com" in config["webfetch"]["extraDomains"]


def test_apply_suggestions_cancelled(tmp_path: Path) -> None:
    config_path = tmp_path / "permission-config.json"
    config_path.write_text("{}")

    suggestions = {
        "command_groups": [
            {"canonical": "python", "variants": [("python3.12", 5), ("python3.13", 3)], "total": 8}
        ],
        "domain_groups": [],
    }

    with patch("builtins.input", return_value="n"):
        result = apply_suggestions(suggestions, config_path)

    assert result["cancelled"]


def test_apply_suggestions_no_duplicates(tmp_path: Path) -> None:
    """Should not add aliases that already exist."""
    config_path = tmp_path / "permission-config.json"
    config_path.write_text(json.dumps({
        "bashAliases": {"python3.12": "python"},
        "webfetch": {"extraDomains": ["*.foo.com"]},
    }))

    suggestions = {
        "command_groups": [
            {"canonical": "python", "variants": [("python3.12", 5), ("python3.13", 3)], "total": 8}
        ],
        "domain_groups": [
            {"pattern": "*.foo.com", "subdomains": [("docs.foo.com", 5)], "total": 5}
        ],
    }

    with patch("builtins.input", return_value="y"):
        result = apply_suggestions(suggestions, config_path)

    config = json.loads(config_path.read_text())
    # python3.12 was already there, only python3.13 should be added
    assert config["bashAliases"]["python3.12"] == "python"
    assert config["bashAliases"]["python3.13"] == "python"
    # *.foo.com was already there, count should be 1
    assert config["webfetch"]["extraDomains"].count("*.foo.com") == 1


def test_apply_suggestions_creates_config(tmp_path: Path) -> None:
    """Should create config file if it doesn't exist."""
    config_path = tmp_path / "permission-config.json"

    suggestions = {
        "command_groups": [
            {"canonical": "node", "variants": [("node18", 3), ("node20", 2)], "total": 5}
        ],
        "domain_groups": [],
    }

    with patch("builtins.input", return_value="y"):
        result = apply_suggestions(suggestions, config_path)

    assert config_path.exists()
    config = json.loads(config_path.read_text())
    assert config["bashAliases"]["node18"] == "node"
