"""Tests for the export command."""
import json
from pathlib import Path
from unittest.mock import patch

from flow_state.export_cmd import (
    RISKY_DOMAIN_PATTERNS,
    apply_exclude_patterns,
    build_profile,
    detect_risky_entries,
    run_export,
    strip_risky_entries,
)


# ============================================================================
# DETECT RISKY ENTRIES
# ============================================================================


def test_detect_risky_localhost() -> None:
    profile = {"webfetch": {"extraDomains": ["localhost"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1
    assert risky[0]["value"] == "localhost"


def test_detect_risky_loopback_ip() -> None:
    profile = {"webfetch": {"extraDomains": ["127.0.0.1"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1
    assert risky[0]["value"] == "127.0.0.1"


def test_detect_risky_ipv6_loopback() -> None:
    profile = {"webfetch": {"extraDomains": ["::1"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_rfc1918_10() -> None:
    profile = {"webfetch": {"extraDomains": ["10.0.0.5"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_rfc1918_172() -> None:
    profile = {"webfetch": {"extraDomains": ["172.16.0.1"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_rfc1918_192() -> None:
    profile = {"webfetch": {"extraDomains": ["192.168.1.100"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_internal_domain() -> None:
    profile = {"webfetch": {"extraDomains": ["api.internal.company.com"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1
    assert "internal" in risky[0]["reason"]


def test_detect_risky_local_domain() -> None:
    profile = {"webfetch": {"extraDomains": ["myserver.local"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_corp_domain() -> None:
    profile = {"webfetch": {"extraDomains": ["gitlab.corp"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_private_domain() -> None:
    profile = {"webfetch": {"extraDomains": ["registry.private"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_absolute_home_path() -> None:
    profile = {"fileAccess": {"readPaths": ["/Users/alice/projects/**"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1
    assert risky[0]["value"] == "/Users/alice/projects/**"
    assert "absolute home" in risky[0]["reason"]


def test_detect_risky_linux_home_path() -> None:
    profile = {"fileAccess": {"readPaths": ["/home/bob/.config/**"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_root_home_path() -> None:
    profile = {"fileAccess": {"writePaths": ["/root/.config/**"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 1


def test_detect_risky_tilde_ok() -> None:
    """Tilde paths are portable and should not be flagged."""
    profile = {"fileAccess": {"readPaths": ["~/.claude/**", "~/.config/**"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 0


def test_detect_risky_tmp_ok() -> None:
    """/tmp paths are not home directories."""
    profile = {"fileAccess": {"readPaths": ["/tmp/**"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 0


def test_detect_risky_public_domain_ok() -> None:
    profile = {"webfetch": {"extraDomains": ["*.github.com", "docs.python.org"]}}
    risky = detect_risky_entries(profile)
    assert len(risky) == 0


def test_detect_risky_empty_profile() -> None:
    risky = detect_risky_entries({})
    assert risky == []


# ============================================================================
# STRIP RISKY ENTRIES
# ============================================================================


def test_strip_removes_flagged_domains() -> None:
    profile = {"webfetch": {"extraDomains": ["localhost", "*.github.com"]}}
    risky = [{"field": "webfetch.extraDomains", "value": "localhost", "reason": "test"}]
    result = strip_risky_entries(profile, risky)
    assert result["webfetch"]["extraDomains"] == ["*.github.com"]


def test_strip_removes_flagged_paths() -> None:
    profile = {"fileAccess": {"readPaths": ["/Users/x/foo", "~/.claude/**"]}}
    risky = [{"field": "fileAccess.readPaths", "value": "/Users/x/foo", "reason": "test"}]
    result = strip_risky_entries(profile, risky)
    assert result["fileAccess"]["readPaths"] == ["~/.claude/**"]


def test_strip_does_not_mutate_input() -> None:
    profile = {"webfetch": {"extraDomains": ["localhost", "ok.com"]}}
    risky = [{"field": "webfetch.extraDomains", "value": "localhost", "reason": "test"}]
    strip_risky_entries(profile, risky)
    assert "localhost" in profile["webfetch"]["extraDomains"]


def test_strip_noop_on_empty_risky() -> None:
    profile = {"webfetch": {"extraDomains": ["ok.com"]}}
    result = strip_risky_entries(profile, [])
    assert result == profile


def test_strip_removes_empty_sections() -> None:
    """When all domains are stripped, the webfetch key should be removed."""
    profile = {"webfetch": {"extraDomains": ["localhost"]}, "name": "test", "version": "1.0.0"}
    risky = [{"field": "webfetch.extraDomains", "value": "localhost", "reason": "test"}]
    result = strip_risky_entries(profile, risky)
    assert "webfetch" not in result


# ============================================================================
# APPLY EXCLUDE PATTERNS
# ============================================================================


def test_exclude_domain() -> None:
    profile = {"webfetch": {"extraDomains": ["*.internal.co", "*.github.com"]}}
    result, excluded = apply_exclude_patterns(profile, ["*.internal.*"])
    assert result["webfetch"]["extraDomains"] == ["*.github.com"]
    assert len(excluded) == 1
    assert "*.internal.co" in excluded[0]


def test_exclude_alias() -> None:
    profile = {"bashAliases": {"python3": "python", "node": "node"}}
    result, excluded = apply_exclude_patterns(profile, ["python*"])
    assert "python3" not in result.get("bashAliases", {})
    assert result["bashAliases"]["node"] == "node"
    assert any("python3" in e for e in excluded)


def test_exclude_mapping() -> None:
    profile = {"commandMappings": {"pip install": ["pip3 install"], "cargo build": ["cargo b"]}}
    result, excluded = apply_exclude_patterns(profile, ["pip*"])
    assert "pip install" not in result.get("commandMappings", {})
    assert "cargo build" in result["commandMappings"]


def test_exclude_paths() -> None:
    profile = {"fileAccess": {"readPaths": ["/tmp/**", "~/.config/**"]}}
    result, excluded = apply_exclude_patterns(profile, ["/tmp/**"])
    assert result["fileAccess"]["readPaths"] == ["~/.config/**"]
    assert len(excluded) == 1


def test_exclude_multiple_patterns() -> None:
    profile = {
        "webfetch": {"extraDomains": ["a.com", "b.com", "c.com"]},
        "bashAliases": {"x": "y"},
    }
    result, excluded = apply_exclude_patterns(profile, ["a.*", "b.*"])
    assert result["webfetch"]["extraDomains"] == ["c.com"]
    assert len(excluded) == 2


def test_exclude_returns_descriptions() -> None:
    profile = {"bashAliases": {"foo": "bar"}}
    _, excluded = apply_exclude_patterns(profile, ["foo"])
    assert excluded == ["alias: foo"]


# ============================================================================
# BUILD PROFILE
# ============================================================================


def test_build_profile_structure() -> None:
    uc = {
        "bashAliases": {"py": "python"},
        "webfetch": {"extraDomains": ["*.docs.rs"]},
    }
    p = build_profile(uc, "balanced", name="test", description="A test profile")
    assert p["schema_version"] == 1
    assert p["name"] == "test"
    assert p["version"] == "1.0.0"
    assert p["description"] == "A test profile"
    assert p["bashAliases"] == {"py": "python"}
    assert p["webfetch"]["extraDomains"] == ["*.docs.rs"]
    assert p["meta"]["recommendedTier"] == "balanced"


def test_build_profile_recommended_tier() -> None:
    p = build_profile({}, "flow", name="test")
    assert p["meta"]["recommendedTier"] == "flow"


def test_build_profile_excludes_write_paths_by_default() -> None:
    uc = {"fileAccess": {"readPaths": ["~/.claude/**"], "writePaths": ["/tmp/**"]}}
    p = build_profile(uc, "balanced", name="test")
    assert "readPaths" in p.get("fileAccess", {})
    assert "writePaths" not in p.get("fileAccess", {})


def test_build_profile_includes_write_paths_when_flagged() -> None:
    uc = {"fileAccess": {"writePaths": ["/tmp/**"]}}
    p = build_profile(uc, "balanced", name="test", include_write_paths=True)
    assert p["fileAccess"]["writePaths"] == ["/tmp/**"]


def test_build_profile_omits_empty_sections() -> None:
    p = build_profile({}, "balanced", name="test")
    assert "bashAliases" not in p
    assert "commandMappings" not in p
    assert "webfetch" not in p
    assert "fileAccess" not in p


def test_build_profile_meta_provenance() -> None:
    meta = {"source": "https://github.com/me/repo", "license": "MIT"}
    p = build_profile({}, "balanced", name="test", meta_fields=meta)
    assert p["meta"]["source"] == "https://github.com/me/repo"
    assert p["meta"]["license"] == "MIT"
    assert p["meta"]["recommendedTier"] == "balanced"


def test_build_profile_no_resolved_canonicals() -> None:
    """resolvedCanonicals should never appear in exported profiles."""
    uc = {"resolvedCanonicals": ["python", "git"], "bashAliases": {"py": "python"}}
    p = build_profile(uc, "balanced", name="test")
    assert "resolvedCanonicals" not in p


def test_build_profile_no_deny_paths() -> None:
    uc = {"fileAccess": {"denyPaths": ["~/.ssh/**"], "readPaths": ["~/.config/**"]}}
    p = build_profile(uc, "balanced", name="test")
    assert "denyPaths" not in p.get("fileAccess", {})


def test_build_profile_no_tier_field() -> None:
    """The tier field from user_config should not leak into the profile."""
    uc = {"tier": "flow", "bashAliases": {"py": "python"}}
    p = build_profile(uc, "flow", name="test")
    assert "tier" not in p
    assert "flowstateTier" not in p


def test_build_profile_validates_via_schema() -> None:
    """Built profiles should pass schema validation."""
    from flow_state.schema import validate_profile
    uc = {"bashAliases": {"py": "python"}, "webfetch": {"extraDomains": ["*.docs.rs"]}}
    p = build_profile(uc, "balanced", name="test", version="1.0.0")
    errors, _ = validate_profile(p)
    assert errors == []


# ============================================================================
# RUN EXPORT (integration)
# ============================================================================


def _make_lockfile(tmp_path: Path, user_config: dict, tier: str = "balanced") -> Path:
    """Helper to create a lockfile with user_config for testing."""
    lockfile_path = tmp_path / "profiles.lock.json"
    user_config_with_tier = {**user_config, "tier": tier}
    data = {"profiles": [], "merged": {}, "user_config": user_config_with_tier}
    lockfile_path.write_text(json.dumps(data, indent=2))
    return lockfile_path


def test_run_export_writes_file(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "bashAliases": {"py": "python"},
    })
    output_path = tmp_path / "out.json"

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            output=str(output_path),
            name="my-profile",
            description="test",
            interactive=False,
        )

    assert not result.get("errors")
    assert output_path.exists()
    profile = json.loads(output_path.read_text())
    assert profile["name"] == "my-profile"
    assert profile["schema_version"] == 1


def test_run_export_stdout(tmp_path: Path, capsys) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "bashAliases": {"py": "python"},
    })

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            name="my-profile",
            description="test",
            interactive=False,
        )

    assert not result.get("errors")
    captured = capsys.readouterr()
    profile = json.loads(captured.out.split("\n", 1)[1])  # skip "Exporting..." line
    assert profile["name"] == "my-profile"


def test_run_export_dry_run(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "bashAliases": {"py": "python"},
    })

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            name="test",
            description="test",
            dry_run=True,
            interactive=False,
        )

    assert "profile" in result
    assert not result.get("errors")


def test_run_export_strips_risky_with_warning(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "webfetch": {"extraDomains": ["localhost", "*.github.com"]},
    })

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            name="test",
            description="test",
            dry_run=True,
            interactive=False,
        )

    warnings = result.get("warnings", [])
    assert any("localhost" in w for w in warnings)
    profile = result["profile"]
    domains = profile.get("webfetch", {}).get("extraDomains", [])
    assert "localhost" not in domains
    assert "*.github.com" in domains


def test_run_export_include_risky(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "webfetch": {"extraDomains": ["localhost"]},
    })

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            name="test",
            description="test",
            include_risky=True,
            dry_run=True,
            interactive=False,
        )

    profile = result["profile"]
    assert "localhost" in profile["webfetch"]["extraDomains"]


def test_run_export_exclude_pattern(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "bashAliases": {"py": "python", "node18": "node"},
        "webfetch": {"extraDomains": ["*.internal.co", "*.github.com"]},
    })

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            name="test",
            description="test",
            exclude_patterns=["node*", "*.internal.*"],
            dry_run=True,
            interactive=False,
        )

    profile = result["profile"]
    assert "node18" not in profile.get("bashAliases", {})
    assert "py" in profile["bashAliases"]
    domains = profile.get("webfetch", {}).get("extraDomains", [])
    assert "*.internal.co" not in domains


def test_run_export_no_user_config_error(tmp_path: Path) -> None:
    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = {"profiles": [], "merged": {}}
        result = run_export(
            name="test",
            description="test",
            interactive=False,
        )

    assert result.get("errors")
    assert "No user config" in result["errors"][0]


def test_run_export_interactive_name_prompt(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {"bashAliases": {"py": "python"}})

    with (
        patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read,
        patch("builtins.input", side_effect=["my-export", "desc"]),
    ):
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            dry_run=True,
            interactive=True,
        )

    assert not result.get("errors")
    assert result["profile"]["name"] == "my-export"


def test_run_export_atomic_write(tmp_path: Path) -> None:
    """Output file should be written atomically (no partial writes)."""
    lockfile_path = _make_lockfile(tmp_path, {"bashAliases": {"py": "python"}})
    output_path = tmp_path / "out.json"

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        run_export(
            output=str(output_path),
            name="test",
            description="test",
            interactive=False,
        )

    # Verify no .tmp file remains
    assert not (tmp_path / "out.tmp").exists()
    assert output_path.exists()


def test_run_export_write_paths_warning(tmp_path: Path) -> None:
    lockfile_path = _make_lockfile(tmp_path, {
        "fileAccess": {"writePaths": ["/tmp/**"]},
    })

    with patch("flow_state.export_cmd.lockfile_mod.read_lockfile") as mock_read:
        mock_read.return_value = json.loads(lockfile_path.read_text())
        result = run_export(
            name="test",
            description="test",
            dry_run=True,
            interactive=False,
        )

    warnings = result.get("warnings", [])
    assert any("writePaths excluded" in w for w in warnings)
