"""Tests for the suggest command."""
from claude_hooks.suggest import (
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
    # Only python3.12, no pair → excluded
    assert len(groups) == 0


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


def test_generate_suggestions_empty() -> None:
    result = generate_suggestions([])
    assert result["command_groups"] == []
    assert result["domain_groups"] == []
