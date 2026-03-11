"""Tests for conflict detection."""
from flow_state.conflicts import check_all_conflicts, detect_alias_conflicts, detect_mapping_conflicts


def test_no_conflicts_same_value() -> None:
    profiles = [
        {"name": "a", "bashAliases": {"python3": "python"}},
        {"name": "b", "bashAliases": {"python3": "python"}},
    ]
    conflicts = detect_alias_conflicts(profiles)
    assert conflicts == []


def test_alias_conflict_different_value() -> None:
    profiles = [
        {"name": "a", "bashAliases": {"p3": "python"}},
        {"name": "b", "bashAliases": {"p3": "python3"}},
    ]
    conflicts = detect_alias_conflicts(profiles)
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "p3"
    assert conflicts[0]["values"]["a"] == "python"
    assert conflicts[0]["values"]["b"] == "python3"


def test_mapping_conflict() -> None:
    profiles = [
        {"name": "a", "commandMappings": {"npm test": ["jest"]}},
        {"name": "b", "commandMappings": {"yarn test": ["jest"]}},
    ]
    conflicts = detect_mapping_conflicts(profiles)
    assert len(conflicts) == 1
    assert conflicts[0]["alias"] == "jest"


def test_no_mapping_conflict_same_canonical() -> None:
    profiles = [
        {"name": "a", "commandMappings": {"npm test": ["jest"]}},
        {"name": "b", "commandMappings": {"npm test": ["jest", "vitest"]}},
    ]
    conflicts = detect_mapping_conflicts(profiles)
    assert conflicts == []


def test_check_all_conflicts_returns_both() -> None:
    profiles = [
        {"name": "a", "bashAliases": {"x": "y"}, "commandMappings": {"a": ["z"]}},
        {"name": "b", "bashAliases": {"x": "w"}, "commandMappings": {"b": ["z"]}},
    ]
    alias_c, mapping_c = check_all_conflicts(profiles)
    assert len(alias_c) == 1
    assert len(mapping_c) == 1


def test_no_conflicts_empty() -> None:
    alias_c, mapping_c = check_all_conflicts([])
    assert alias_c == []
    assert mapping_c == []
