"""Tests for permission decay analysis (tighten)."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cleanline.tighten import (
    apply_tighten_profile,
    apply_tighten_user,
    build_usage_map,
    find_stale_rules,
    select_rules_to_remove,
)


def _ts(days_ago: int = 0) -> str:
    """Generate ISO timestamp N days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _event(rule: str, ts: str | None = None, decision: str = "allow") -> dict:
    return {
        "ts": ts or _ts(0),
        "tool": "Bash",
        "input": "test",
        "decision": decision,
        "matched_rule": rule,
    }


# ============================================================================
# BUILD_USAGE_MAP
# ============================================================================


def test_build_usage_map_aliases() -> None:
    events = [
        _event("alias:python3.12->python", _ts(5)),
        _event("alias:python3.13->python", _ts(2)),
    ]
    usage = build_usage_map(events)
    assert "alias:python3.12" in usage
    assert "alias:python3.13" in usage


def test_build_usage_map_domains() -> None:
    events = [_event("domain:*.docs.rs", _ts(3))]
    usage = build_usage_map(events)
    assert "domain:*.docs.rs" in usage


def test_build_usage_map_mappings() -> None:
    events = [_event("mapping:npm test", _ts(1))]
    usage = build_usage_map(events)
    assert "mapping:npm test" in usage


def test_build_usage_map_ignores_passthroughs() -> None:
    events = [_event("no_match", _ts(0), decision="passthrough")]
    usage = build_usage_map(events)
    assert len(usage) == 0


def test_build_usage_map_tracks_latest_timestamp() -> None:
    events = [
        _event("alias:python3.12->python", _ts(10)),
        _event("alias:python3.12->python", _ts(2)),
    ]
    usage = build_usage_map(events)
    # Should have the more recent timestamp (2 days ago)
    assert usage["alias:python3.12"] == events[1]["ts"]


# ============================================================================
# FIND_STALE_RULES — USER ALIASES
# ============================================================================


def test_find_stale_rules_user_aliases() -> None:
    """Aliases not in usage map should be stale."""
    events = [_event("alias:python3.12->python", _ts(5))]
    config = {"bashAliases": {"python3.12": "python", "ruby3.0": "ruby"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale_keys = [a["key"] for a in result["user_stale"]["aliases"]]
    assert "ruby3.0" in stale_keys
    assert "python3.12" not in stale_keys


def test_find_stale_rules_user_domains() -> None:
    events = [_event("domain:*.docs.rs", _ts(5))]
    config = {"webfetch": {"extraDomains": ["*.docs.rs", "*.cppreference.com"]}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale_patterns = [d["pattern"] for d in result["user_stale"]["domains"]]
    assert "*.cppreference.com" in stale_patterns
    assert "*.docs.rs" not in stale_patterns


def test_find_stale_rules_user_mappings() -> None:
    events = [_event("mapping:npm test", _ts(5))]
    config = {"commandMappings": {"npm test": ["jest"], "pip install": ["pip3 install"]}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale_canonicals = [m["canonical"] for m in result["user_stale"]["mappings"]]
    assert "pip install" in stale_canonicals
    assert "npm test" not in stale_canonicals


# ============================================================================
# FIND_STALE_RULES — PROFILE RULES
# ============================================================================


def test_find_stale_rules_profile_rules() -> None:
    """Stale rules from merged section should be attributed to correct profile."""
    events = []  # No usage at all
    config = {}
    lockfile_data = {
        "profiles": [
            {"name": "ml-researcher", "content": {
                "name": "ml-researcher", "version": "1.0",
                "bashAliases": {"py.test": "pytest"},
            }}
        ],
        "merged": {"bashAliases": {"py.test": "pytest"}},
    }

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale = result["profile_stale"]["aliases"]
    assert len(stale) == 1
    assert stale[0]["key"] == "py.test"
    assert stale[0]["profile"] == "ml-researcher"


# ============================================================================
# FIND_STALE_RULES — TIMESTAMP LOGIC
# ============================================================================


def test_find_stale_rules_last_used_timestamp() -> None:
    """Rule used 40 days ago with --days=30 → stale; 20 days ago → active."""
    events = [
        _event("alias:old->python", _ts(40)),
        _event("alias:recent->python", _ts(20)),
    ]
    config = {"bashAliases": {"old": "python", "recent": "python"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale_keys = [a["key"] for a in result["user_stale"]["aliases"]]
    assert "old" in stale_keys
    assert "recent" not in stale_keys


def test_find_stale_rules_never_triggered() -> None:
    """Rule with no events → stale with last_used=None."""
    events = []
    config = {"bashAliases": {"ruby3.0": "ruby"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale = result["user_stale"]["aliases"]
    assert len(stale) == 1
    assert stale[0]["last_used"] is None


# ============================================================================
# FIND_STALE_RULES — INSUFFICIENT DATA
# ============================================================================


def test_find_stale_rules_insufficient_data() -> None:
    """Audit log < 7 days → insufficient_data=True."""
    events = [
        _event("alias:python3.12->python", _ts(2)),
        _event("alias:python3.13->python", _ts(0)),
    ]
    config = {"bashAliases": {"python3.12": "python"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    assert result["insufficient_data"] is True
    assert result["audit_span_days"] < 7


def test_find_stale_rules_sufficient_data() -> None:
    events = [
        _event("alias:python3.12->python", _ts(45)),
        _event("alias:python3.13->python", _ts(0)),
    ]
    config = {"bashAliases": {"python3.12": "python"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    assert result["insufficient_data"] is False
    assert result["audit_span_days"] >= 7


# ============================================================================
# FIND_STALE_RULES — FAMILY CONTEXT
# ============================================================================


def test_find_stale_rules_family_note() -> None:
    """Stale python3.10 with active python3.12 → family_note populated."""
    events = [
        _event("alias:python3.12->python", _ts(5)),  # python3.12 is active
    ]
    config = {"bashAliases": {"python3.10": "python", "python3.12": "python"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale = result["user_stale"]["aliases"]
    stale_310 = [a for a in stale if a["key"] == "python3.10"]
    assert len(stale_310) == 1
    assert stale_310[0]["family_note"] is not None
    assert "python3.12" in stale_310[0]["family_note"]


def test_find_stale_rules_no_family_note() -> None:
    """Stale ruby3.0 with no active siblings → family_note is None."""
    events = []
    config = {"bashAliases": {"ruby3.0": "ruby"}}
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    stale = result["user_stale"]["aliases"]
    assert len(stale) == 1
    assert stale[0]["family_note"] is None


# ============================================================================
# SELECT_RULES_TO_REMOVE
# ============================================================================


def test_select_rules_to_remove_returns_all() -> None:
    stale = {"user_stale": {"aliases": [{"key": "x"}]}}
    result = select_rules_to_remove(stale)
    assert result == stale


# ============================================================================
# APPLY_TIGHTEN_USER
# ============================================================================


def test_apply_tighten_user_removes_aliases(tmp_path: Path) -> None:
    config_path = tmp_path / "permission-config.json"
    config_path.write_text(json.dumps({
        "bashAliases": {"ruby3.0": "ruby", "python3.12": "python"},
        "webfetch": {"extraDomains": []},
    }))

    removals = {
        "user_stale": {
            "aliases": [{"key": "ruby3.0", "canonical": "ruby"}],
            "mappings": [],
            "domains": [],
        }
    }
    result = apply_tighten_user(removals, config_path)
    assert not result["cancelled"]
    assert any("1 aliases" in a for a in result["actions"])

    config = json.loads(config_path.read_text())
    assert "ruby3.0" not in config["bashAliases"]
    assert "python3.12" in config["bashAliases"]


def test_apply_tighten_user_removes_domains(tmp_path: Path) -> None:
    config_path = tmp_path / "permission-config.json"
    config_path.write_text(json.dumps({
        "webfetch": {"extraDomains": ["*.cppreference.com", "*.docs.rs"]},
    }))

    removals = {
        "user_stale": {
            "aliases": [],
            "mappings": [],
            "domains": [{"pattern": "*.cppreference.com"}],
        }
    }
    result = apply_tighten_user(removals, config_path)
    assert any("1 domains" in a for a in result["actions"])

    config = json.loads(config_path.read_text())
    assert "*.cppreference.com" not in config["webfetch"]["extraDomains"]
    assert "*.docs.rs" in config["webfetch"]["extraDomains"]


def test_apply_tighten_user_removes_mappings(tmp_path: Path) -> None:
    config_path = tmp_path / "permission-config.json"
    config_path.write_text(json.dumps({
        "commandMappings": {"npm test": ["jest"], "pip install": ["pip3 install"]},
    }))

    removals = {
        "user_stale": {
            "aliases": [],
            "mappings": [{"canonical": "pip install"}],
            "domains": [],
        }
    }
    result = apply_tighten_user(removals, config_path)
    assert any("1 mappings" in a for a in result["actions"])

    config = json.loads(config_path.read_text())
    assert "pip install" not in config["commandMappings"]
    assert "npm test" in config["commandMappings"]


# ============================================================================
# APPLY_TIGHTEN_PROFILE
# ============================================================================


def test_apply_tighten_profile_creates_overrides(tmp_path: Path) -> None:
    lockfile_path = tmp_path / "profiles.lock.json"
    lockfile_data = {
        "profiles": [
            {"name": "ml-researcher", "content": {
                "name": "ml-researcher", "version": "1.0",
                "bashAliases": {"py.test": "pytest"},
            }}
        ],
        "merged": {"bashAliases": {"py.test": "pytest"}},
    }
    lockfile_path.write_text(json.dumps(lockfile_data))

    suppressions = {
        "profile_stale": {
            "aliases": [{"key": "py.test", "canonical": "pytest", "profile": "ml-researcher"}],
            "mappings": [],
            "domains": [],
        }
    }
    result = apply_tighten_profile(suppressions, lockfile_path)
    assert any("suppressed" in a for a in result["actions"])

    data = json.loads(lockfile_path.read_text())
    overrides = data.get("user_overrides", {}).get("removed_rules", [])
    assert len(overrides) == 1
    assert overrides[0]["type"] == "bashAlias"
    assert overrides[0]["value"] == "py.test"
    assert overrides[0]["source"] == "tighten"
    # Merged section should no longer have py.test
    assert "py.test" not in data["merged"].get("bashAliases", {})


def test_apply_tighten_profile_no_duplicates(tmp_path: Path) -> None:
    """Suppressing same rule twice shouldn't create duplicate override."""
    lockfile_path = tmp_path / "profiles.lock.json"
    lockfile_data = {
        "profiles": [
            {"name": "ml", "content": {
                "name": "ml", "version": "1.0",
                "bashAliases": {"py.test": "pytest"},
            }}
        ],
        "merged": {"bashAliases": {"py.test": "pytest"}},
        "user_overrides": {
            "removed_rules": [{
                "type": "bashAlias", "value": "py.test",
                "profile": "ml", "source": "tighten",
            }]
        },
    }
    lockfile_path.write_text(json.dumps(lockfile_data))

    suppressions = {
        "profile_stale": {
            "aliases": [{"key": "py.test", "canonical": "pytest", "profile": "ml"}],
            "mappings": [],
            "domains": [],
        }
    }
    apply_tighten_profile(suppressions, lockfile_path)

    data = json.loads(lockfile_path.read_text())
    overrides = data.get("user_overrides", {}).get("removed_rules", [])
    assert len(overrides) == 1


# ============================================================================
# ACTIVE COUNTS
# ============================================================================


def test_find_stale_rules_active_counts() -> None:
    """Active rules should be counted correctly."""
    events = [
        _event("alias:python3.12->python", _ts(5)),
        _event("domain:*.docs.rs", _ts(3)),
        _event("mapping:npm test", _ts(1)),
    ]
    config = {
        "bashAliases": {"python3.12": "python", "ruby3.0": "ruby"},
        "commandMappings": {"npm test": ["jest"]},
        "webfetch": {"extraDomains": ["*.docs.rs"]},
    }
    lockfile_data = {"profiles": [], "merged": {}}

    result = find_stale_rules(events, config, lockfile_data, min_age_days=30)
    assert result["active_counts"]["aliases"] == 1
    assert result["active_counts"]["mappings"] == 1
    assert result["active_counts"]["domains"] == 1


# ============================================================================
# CLI GATE TESTS
# ============================================================================


def test_cmd_tighten_refuses_apply_insufficient_data(capsys: object) -> None:
    """--apply without --force when data < 7 days should refuse."""
    from cleanline.cli import cmd_tighten
    from cleanline import lockfile as lockfile_mod

    events = [
        _event("alias:python3.12->python", _ts(2)),
        _event("alias:python3.13->python", _ts(0)),
    ]
    config = {"bashAliases": {"ruby3.0": "ruby"}}

    args = argparse.Namespace(apply=True, days=30, force=False)
    with (
        patch("cleanline.cli.audit_mod.read_audit_log", return_value=events),
        patch("cleanline.cli._default_hooks_dir") as mock_dir,
        patch.object(lockfile_mod, "get_lockfile_path") as mock_lf,
        patch("cleanline.cli.lockfile_mod.read_lockfile", return_value={"profiles": [], "merged": {}}),
    ):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as td:
            td_path = P(td)
            config_path = td_path / "permission-config.json"
            config_path.write_text(json.dumps(config))
            mock_dir.return_value = td_path
            mock_lf.return_value = td_path / "profiles.lock.json"
            exit_code = cmd_tighten(args)

    assert exit_code == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "refused" in captured.out.lower() or "Refused" in captured.out


def test_cmd_tighten_allows_apply_with_force(capsys: object) -> None:
    """--apply --force when data < 7 days should proceed."""
    from cleanline.cli import cmd_tighten
    from cleanline import lockfile as lockfile_mod

    events = [
        _event("alias:python3.12->python", _ts(2)),
        _event("alias:python3.13->python", _ts(0)),
    ]
    config = {"bashAliases": {"ruby3.0": "ruby"}}

    args = argparse.Namespace(apply=True, days=30, force=True)
    with (
        patch("cleanline.cli.audit_mod.read_audit_log", return_value=events),
        patch("cleanline.cli._default_hooks_dir") as mock_dir,
        patch.object(lockfile_mod, "get_lockfile_path") as mock_lf,
        patch("cleanline.cli.lockfile_mod.read_lockfile", return_value={"profiles": [], "merged": {}}),
        patch("builtins.input", return_value="y"),
    ):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as td:
            td_path = P(td)
            config_path = td_path / "permission-config.json"
            config_path.write_text(json.dumps(config))
            mock_dir.return_value = td_path
            mock_lf.return_value = td_path / "profiles.lock.json"
            exit_code = cmd_tighten(args)

    assert exit_code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "removed" in captured.out.lower()
