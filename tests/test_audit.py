"""Tests for audit log reading and provenance enrichment."""
import json
from pathlib import Path

from cleanline.audit import (
    enrich_with_provenance,
    read_audit_log,
    summarize_decisions,
    top_rules,
)


def _write_audit_log(path: Path, events: list[dict]) -> None:
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def test_read_missing_log(tmp_path: Path) -> None:
    events = read_audit_log(tmp_path / "nonexistent.jsonl")
    assert events == []


def test_read_audit_log(tmp_path: Path) -> None:
    log_path = tmp_path / "hook.jsonl"
    entries = [
        {"ts": "2025-01-01T00:00:00Z", "tool": "Bash", "input": "python3 x", "decision": "allow", "matched_rule": "alias:python3->python"},
        {"ts": "2025-01-01T00:01:00Z", "tool": "Bash", "input": "cargo build", "decision": "passthrough", "matched_rule": "no_match"},
    ]
    _write_audit_log(log_path, entries)

    events = read_audit_log(log_path)
    # Most recent first
    assert len(events) == 2
    assert events[0]["input"] == "cargo build"
    assert events[1]["input"] == "python3 x"


def test_summarize_decisions() -> None:
    events = [
        {"decision": "allow"},
        {"decision": "allow"},
        {"decision": "passthrough"},
    ]
    summary = summarize_decisions(events)
    assert summary["allow"] == 2
    assert summary["passthrough"] == 1


def test_top_rules() -> None:
    events = [
        {"decision": "allow", "matched_rule": "alias:python3->python"},
        {"decision": "allow", "matched_rule": "alias:python3->python"},
        {"decision": "allow", "matched_rule": "mapping:npm test"},
    ]
    top = top_rules(events, "allow", limit=2)
    assert top[0] == ("alias:python3->python", 2)
    assert len(top) == 2


def test_enrich_with_provenance() -> None:
    events = [
        {"matched_rule": "alias:cargo-nightly->cargo"},
        {"matched_rule": "mapping:npm test"},
        {"matched_rule": "no_match"},
    ]
    lockfile_data = {
        "profiles": [
            {
                "name": "rust-profile",
                "content": {
                    "bashAliases": {"cargo-nightly": "cargo"},
                    "commandMappings": {},
                },
            },
            {
                "name": "js-profile",
                "content": {
                    "bashAliases": {},
                    "commandMappings": {"npm test": ["jest"]},
                },
            },
        ]
    }
    enriched = enrich_with_provenance(events, lockfile_data)
    assert enriched[0]["source_profile"] == "rust-profile"
    assert enriched[1]["source_profile"] == "js-profile"
    assert enriched[2]["source_profile"] == "user"


def test_read_audit_log_with_bad_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "hook.jsonl"
    with open(log_path, "w") as f:
        f.write('{"valid": true}\n')
        f.write("not json\n")
        f.write('{"also": "valid"}\n')

    events = read_audit_log(log_path)
    assert len(events) == 2
