"""Tests for lock file operations."""
import json
from pathlib import Path

from flow_state.lockfile import (
    add_override,
    add_profile,
    apply_overrides,
    get_tier,
    merge_profiles,
    read_lockfile,
    rebuild_merged,
    remove_profile,
    remove_redundant_overrides,
    write_lockfile,
    write_permission_config,
)


def test_read_missing_lockfile(tmp_path: Path) -> None:
    data = read_lockfile(tmp_path / "nonexistent.json")
    assert data == {"profiles": [], "merged": {}}


def test_write_read_roundtrip(tmp_lockfile: Path) -> None:
    data = {
        "profiles": [{"name": "test", "version": "1.0", "source": "local", "content": {}}],
        "merged": {"bashAliases": {"p3": "python"}},
    }
    write_lockfile(data, tmp_lockfile)
    assert tmp_lockfile.exists()

    loaded = read_lockfile(tmp_lockfile)
    assert loaded["profiles"][0]["name"] == "test"
    assert loaded["merged"]["bashAliases"]["p3"] == "python"


def test_merge_two_profiles_domains_unioned(
    sample_profile: dict, sample_profile_b: dict
) -> None:
    merged = merge_profiles([sample_profile, sample_profile_b])
    domains = merged["webfetch"]["extraDomains"]
    assert "*.arxiv.org" in domains
    assert "*.scipy.org" in domains
    # No duplicates
    assert domains.count("*.arxiv.org") == 1


def test_merge_two_profiles_aliases_merged(
    sample_profile: dict, sample_profile_b: dict
) -> None:
    merged = merge_profiles([sample_profile, sample_profile_b])
    aliases = merged["bashAliases"]
    assert aliases["cargo-nightly"] == "cargo"
    assert aliases["pip3"] == "pip"


def test_merge_command_mappings_union(
    sample_profile: dict, sample_profile_b: dict
) -> None:
    merged = merge_profiles([sample_profile, sample_profile_b])
    mappings = merged["commandMappings"]
    assert "yarn test" in mappings["npm test"]
    assert "pnpm test" in mappings["npm test"]
    assert "pip3 install" in mappings["pip install"]


def test_add_profile_replaces_same_name(
    tmp_lockfile: Path, sample_profile: dict
) -> None:
    data = read_lockfile(tmp_lockfile)
    data = add_profile(data, sample_profile, "local:test")

    # Add again with updated version
    updated = dict(sample_profile)
    updated["version"] = "2.0.0"
    data = add_profile(data, updated, "local:test")

    assert len(data["profiles"]) == 1
    assert data["profiles"][0]["content"]["version"] == "2.0.0"


def test_remove_profile_rebuilds_merged(
    tmp_lockfile: Path, sample_profile: dict, sample_profile_b: dict
) -> None:
    data = read_lockfile(tmp_lockfile)
    data = add_profile(data, sample_profile, "src-a")
    data = add_profile(data, sample_profile_b, "src-b")
    assert "cargo-nightly" in data["merged"]["bashAliases"]

    data = remove_profile(data, "test-profile")
    assert "cargo-nightly" not in data["merged"].get("bashAliases", {})
    assert "pip3" in data["merged"]["bashAliases"]


def test_rebuild_merged_from_profiles() -> None:
    data = {
        "profiles": [
            {
                "name": "a",
                "content": {"bashAliases": {"x": "y"}, "name": "a", "version": "1"},
            }
        ],
        "merged": {},
    }
    data = rebuild_merged(data)
    assert data["merged"]["bashAliases"]["x"] == "y"


def test_merge_empty_profiles() -> None:
    merged = merge_profiles([])
    assert merged == {}


# ============================================================================
# OVERRIDES
# ============================================================================


def test_apply_overrides_removes_alias() -> None:
    merged = {"bashAliases": {"cargo-nightly": "cargo", "pip3": "pip"}}
    overrides = {"removed_rules": [
        {"type": "bashAlias", "value": "cargo-nightly"}
    ]}
    result = apply_overrides(merged, overrides)
    assert "cargo-nightly" not in result["bashAliases"]
    assert result["bashAliases"]["pip3"] == "pip"


def test_apply_overrides_removes_domain() -> None:
    merged = {"webfetch": {"extraDomains": ["*.arxiv.org", "*.scipy.org"]}}
    overrides = {"removed_rules": [
        {"type": "domain", "value": "*.arxiv.org"}
    ]}
    result = apply_overrides(merged, overrides)
    assert "*.arxiv.org" not in result["webfetch"]["extraDomains"]
    assert "*.scipy.org" in result["webfetch"]["extraDomains"]


def test_apply_overrides_removes_mapping() -> None:
    merged = {"commandMappings": {"npm test": ["jest"], "pip install": ["pip3 install"]}}
    overrides = {"removed_rules": [
        {"type": "commandMapping", "value": "npm test"}
    ]}
    result = apply_overrides(merged, overrides)
    assert "npm test" not in result["commandMappings"]
    assert "pip install" in result["commandMappings"]


def test_apply_overrides_no_overrides() -> None:
    merged = {"bashAliases": {"x": "y"}}
    result = apply_overrides(merged, {})
    assert result == {"bashAliases": {"x": "y"}}


def test_add_override_appends() -> None:
    data: dict = {"profiles": [], "merged": {}}
    entry = {"type": "bashAlias", "value": "py.test", "profile": "ml"}
    data = add_override(data, entry)
    assert len(data["user_overrides"]["removed_rules"]) == 1
    assert data["user_overrides"]["removed_rules"][0]["value"] == "py.test"


def test_add_override_no_duplicate() -> None:
    data: dict = {"profiles": [], "merged": {}, "user_overrides": {
        "removed_rules": [{"type": "bashAlias", "value": "py.test"}]
    }}
    entry = {"type": "bashAlias", "value": "py.test"}
    data = add_override(data, entry)
    assert len(data["user_overrides"]["removed_rules"]) == 1


def test_rebuild_merged_applies_overrides() -> None:
    data = {
        "profiles": [
            {"name": "a", "content": {
                "name": "a", "version": "1",
                "bashAliases": {"py.test": "pytest", "pip3": "pip"},
            }}
        ],
        "merged": {},
        "user_overrides": {
            "removed_rules": [{"type": "bashAlias", "value": "py.test"}]
        },
    }
    data = rebuild_merged(data)
    assert "py.test" not in data["merged"].get("bashAliases", {})
    assert data["merged"]["bashAliases"]["pip3"] == "pip"


def test_remove_redundant_overrides() -> None:
    """Override for rule no longer in any profile should be cleaned up."""
    data = {
        "profiles": [
            {"name": "a", "content": {
                "name": "a", "version": "2",
                "bashAliases": {"pip3": "pip"},  # py.test removed in v2
            }}
        ],
        "user_overrides": {
            "removed_rules": [
                {"type": "bashAlias", "value": "py.test", "profile": "a"},
                {"type": "bashAlias", "value": "pip3", "profile": "a"},
            ]
        },
    }
    data, cleaned = remove_redundant_overrides(data)
    # py.test was removed from profile -> override is redundant
    assert len(cleaned) == 1
    assert cleaned[0]["value"] == "py.test"
    # pip3 still in profile -> override preserved
    remaining_values = [r["value"] for r in data["user_overrides"]["removed_rules"]]
    assert "pip3" in remaining_values
    assert "py.test" not in remaining_values


# ============================================================================
# USER_CONFIG
# ============================================================================


def test_read_lockfile_preserves_user_config(tmp_lockfile: Path) -> None:
    data = {
        "profiles": [],
        "merged": {},
        "user_config": {
            "bashAliases": {"python3": "python"},
            "webfetch": {"extraDomains": ["*.example.com"]},
        },
    }
    write_lockfile(data, tmp_lockfile)
    loaded = read_lockfile(tmp_lockfile)
    assert loaded["user_config"]["bashAliases"]["python3"] == "python"
    assert "*.example.com" in loaded["user_config"]["webfetch"]["extraDomains"]


# ============================================================================
# WRITE_PERMISSION_CONFIG
# ============================================================================


def test_write_permission_config_merges_user_and_profiles(tmp_path: Path) -> None:
    """permission-config.json should contain both user_config and profile rules."""
    config_path = tmp_path / "permission-config.json"
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Bash(python *)", "Bash(npm *)"]}
    }))

    lockfile_data = {
        "profiles": [],
        "merged": {
            "bashAliases": {"cargo-nightly": "cargo"},
            "webfetch": {"extraDomains": ["*.docs.rs"]},
        },
        "user_config": {
            "bashAliases": {"python3": "python"},
            "commandMappings": {"npm test": ["npx jest"]},
            "webfetch": {"extraDomains": ["*.example.com"]},
        },
    }

    write_permission_config(config_path, lockfile_data, settings_path)

    assert config_path.exists()
    config = json.loads(config_path.read_text())

    # User aliases present
    assert config["bashAliases"]["python3"] == "python"
    # Profile aliases present
    assert config["bashAliases"]["cargo-nightly"] == "cargo"
    # User mappings present
    assert "npm test" in config["commandMappings"]
    # Both domain sources merged
    assert "*.example.com" in config["webfetch"]["extraDomains"]
    assert "*.docs.rs" in config["webfetch"]["extraDomains"]
    # Canonicals from settings.json
    assert "python" in config["resolvedCanonicals"]
    assert "npm" in config["resolvedCanonicals"]


def test_write_permission_config_user_aliases_take_priority(tmp_path: Path) -> None:
    """User aliases should not be overwritten by profile aliases."""
    config_path = tmp_path / "permission-config.json"

    lockfile_data = {
        "profiles": [],
        "merged": {
            "bashAliases": {"python3": "python3-profile-canonical"},
        },
        "user_config": {
            "bashAliases": {"python3": "python"},
        },
    }

    write_permission_config(config_path, lockfile_data)

    config = json.loads(config_path.read_text())
    # User's alias should win
    assert config["bashAliases"]["python3"] == "python"


def test_write_permission_config_merges_file_access(tmp_path: Path) -> None:
    """permission-config.json should include fileAccess from user_config and merged."""
    config_path = tmp_path / "permission-config.json"

    lockfile_data = {
        "profiles": [],
        "merged": {
            "fileAccess": {
                "readPaths": ["~/profiles/**"],
            },
        },
        "user_config": {
            "fileAccess": {
                "readPaths": ["~/.claude/**"],
                "writePaths": ["/tmp/**"],
                "denyPaths": ["~/.ssh/**"],
            },
        },
    }

    write_permission_config(config_path, lockfile_data)

    config = json.loads(config_path.read_text())
    fa = config["fileAccess"]
    # User readPaths merged with profile readPaths
    assert "~/.claude/**" in fa["readPaths"]
    assert "~/profiles/**" in fa["readPaths"]
    # User writePaths present (profile writePaths NOT auto-merged)
    assert "/tmp/**" in fa["writePaths"]
    # User denyPaths present
    assert "~/.ssh/**" in fa["denyPaths"]


def test_write_permission_config_profile_write_not_merged(tmp_path: Path) -> None:
    """Profile writePaths should NOT be auto-merged into permission-config.json."""
    config_path = tmp_path / "permission-config.json"

    lockfile_data = {
        "profiles": [],
        "merged": {
            "fileAccess": {
                "pendingWritePaths": ["/opt/cache/**"],
            },
        },
        "user_config": {
            "fileAccess": {
                "writePaths": ["/tmp/**"],
            },
        },
    }

    write_permission_config(config_path, lockfile_data)

    config = json.loads(config_path.read_text())
    fa = config.get("fileAccess", {})
    write_paths = fa.get("writePaths", [])
    assert "/tmp/**" in write_paths
    # Profile pending write paths should NOT be in writePaths
    assert "/opt/cache/**" not in write_paths


def test_merge_profiles_file_access_read_union(
    sample_profile: dict, sample_profile_b: dict
) -> None:
    """File access readPaths should be unioned across profiles."""
    sample_profile["fileAccess"] = {"readPaths": ["~/a/**"]}
    sample_profile_b["fileAccess"] = {"readPaths": ["~/b/**", "~/a/**"]}
    merged = merge_profiles([sample_profile, sample_profile_b])
    fa = merged.get("fileAccess", {})
    assert "~/a/**" in fa.get("readPaths", [])
    assert "~/b/**" in fa.get("readPaths", [])
    # No duplicates
    assert fa["readPaths"].count("~/a/**") == 1


def test_merge_profiles_write_paths_go_to_pending(
    sample_profile: dict,
) -> None:
    """Profile writePaths should go to pendingWritePaths in merged."""
    sample_profile["fileAccess"] = {"writePaths": ["/opt/cache/**"]}
    merged = merge_profiles([sample_profile])
    fa = merged.get("fileAccess", {})
    assert "/opt/cache/**" in fa.get("pendingWritePaths", [])
    assert "writePaths" not in fa


def test_get_tier_default() -> None:
    """Missing tier in user_config returns balanced."""
    assert get_tier({"profiles": [], "merged": {}}) == "balanced"
    assert get_tier({"user_config": {}}) == "balanced"


def test_get_tier_from_user_config() -> None:
    """Reads tier from user_config."""
    assert get_tier({"user_config": {"tier": "cautious"}}) == "cautious"
    assert get_tier({"user_config": {"tier": "flow"}}) == "flow"


def test_get_tier_invalid_falls_back() -> None:
    """Invalid tier values fall back to balanced."""
    assert get_tier({"user_config": {"tier": "extreme"}}) == "balanced"
    assert get_tier({"user_config": {"tier": ""}}) == "balanced"
    assert get_tier({"user_config": {"tier": 42}}) == "balanced"


def test_write_permission_config_no_settings(tmp_path: Path) -> None:
    """Without settings.json, resolvedCanonicals should be empty."""
    config_path = tmp_path / "permission-config.json"

    lockfile_data = {
        "profiles": [],
        "merged": {},
        "user_config": {"bashAliases": {"python3": "python"}},
    }

    write_permission_config(config_path, lockfile_data, tmp_path / "nonexistent.json")

    config = json.loads(config_path.read_text())
    assert config["resolvedCanonicals"] == []
