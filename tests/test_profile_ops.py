"""Integration tests for profile operations."""
import json
from pathlib import Path
from unittest.mock import patch

from flow_state import lockfile as lockfile_mod
from flow_state import profile_ops


def test_init_local_profile(tmp_path: Path, sample_profile: dict) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(sample_profile))
    lockfile_path = tmp_path / "profiles.lock.json"

    with patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path):
        result = profile_ops.init_profile(str(profile_path))

    assert not result["errors"]
    assert any("test-profile" in a for a in result["actions"])
    assert lockfile_path.exists()

    data = json.loads(lockfile_path.read_text())
    assert data["merged"]["bashAliases"]["cargo-nightly"] == "cargo"


def test_status_with_profile(tmp_path: Path, sample_profile: dict) -> None:
    lockfile_path = tmp_path / "profiles.lock.json"
    data = lockfile_mod.read_lockfile(lockfile_path)
    data = lockfile_mod.add_profile(data, sample_profile, "local")
    lockfile_mod.write_lockfile(data, lockfile_path)

    result = profile_ops.get_status(lockfile_path)
    assert len(result["profiles"]) == 1
    assert result["profiles"][0]["name"] == "test-profile"


def test_remove_shrinks_merged(
    tmp_path: Path, sample_profile: dict, sample_profile_b: dict
) -> None:
    lockfile_path = tmp_path / "profiles.lock.json"

    with patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path):
        data = lockfile_mod.read_lockfile(lockfile_path)
        data = lockfile_mod.add_profile(data, sample_profile, "src-a")
        data = lockfile_mod.add_profile(data, sample_profile_b, "src-b")
        lockfile_mod.write_lockfile(data, lockfile_path)

        result = profile_ops.remove_profile("test-profile", lockfile_path)

    assert not result["errors"]
    assert "cargo-nightly" in result.get("removed_aliases", [])

    data = json.loads(lockfile_path.read_text())
    assert "cargo-nightly" not in data["merged"].get("bashAliases", {})


def test_init_invalid_profile(tmp_path: Path) -> None:
    profile_path = tmp_path / "bad.json"
    profile_path.write_text(json.dumps({"version": "1.0"}))  # missing name
    lockfile_path = tmp_path / "profiles.lock.json"

    with patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path):
        result = profile_ops.init_profile(str(profile_path))

    assert result["errors"]
    assert not lockfile_path.exists()


def test_init_nonexistent_file() -> None:
    result = profile_ops.init_profile("/nonexistent/profile.json")
    assert result["errors"]


def test_remove_nonexistent_profile(tmp_path: Path) -> None:
    lockfile_path = tmp_path / "profiles.lock.json"
    result = profile_ops.remove_profile("nonexistent", lockfile_path)
    assert result["errors"]


def test_dry_run_local_profile(tmp_path: Path, sample_profile: dict) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(sample_profile))
    lockfile_path = tmp_path / "profiles.lock.json"

    with patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path):
        result = profile_ops.dry_run_profile(str(profile_path))

    assert not result["errors"]
    assert result["profile"]["name"] == "test-profile"
    assert "bashAliases" in result["hypothetical_merged"]
    # Lock file should NOT be created
    assert not lockfile_path.exists()


# ============================================================================
# UPDATE RECONCILIATION
# ============================================================================


def test_update_reconciles_redundant_overrides(tmp_path: Path) -> None:
    """Profile v1 has rule X, user suppresses X, profile v2 removes X → override cleaned."""
    lockfile_path = tmp_path / "profiles.lock.json"
    profile_v1 = {
        "name": "ml", "version": "1.0",
        "bashAliases": {"py.test": "pytest", "pip3": "pip"},
    }
    profile_v2 = {
        "name": "ml", "version": "2.0",
        "bashAliases": {"pip3": "pip"},  # py.test removed by author
    }

    # Set up lockfile with v1 + user override suppressing py.test
    data = lockfile_mod.read_lockfile(lockfile_path)
    data = lockfile_mod.add_profile(data, profile_v1, "local:ml.json")
    data["user_overrides"] = {
        "removed_rules": [
            {"type": "bashAlias", "value": "py.test", "profile": "ml", "source": "tighten"}
        ]
    }
    lockfile_mod.write_lockfile(data, lockfile_path)

    # Write v2 to a file for fetch
    profile_path = tmp_path / "ml.json"
    profile_path.write_text(json.dumps(profile_v2))

    with (
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
        patch("flow_state.profile_ops.fetch_mod.fetch_profile", return_value=profile_v2),
    ):
        result = profile_ops.update_profiles(lockfile_path=lockfile_path)

    assert result["updated"]
    # The override for py.test should have been cleaned
    assert "reconciled_overrides" in result
    assert any("py.test" in r for r in result["reconciled_overrides"])

    # Verify the lockfile no longer has the override
    data = lockfile_mod.read_lockfile(lockfile_path)
    remaining = data.get("user_overrides", {}).get("removed_rules", [])
    remaining_values = [r["value"] for r in remaining]
    assert "py.test" not in remaining_values


def test_update_preserves_valid_overrides(tmp_path: Path) -> None:
    """Profile v2 still has rule Y, user suppressed Y → override preserved."""
    lockfile_path = tmp_path / "profiles.lock.json"
    profile_v1 = {
        "name": "ml", "version": "1.0",
        "bashAliases": {"pip3": "pip"},
    }
    profile_v2 = {
        "name": "ml", "version": "2.0",
        "bashAliases": {"pip3": "pip"},  # pip3 still there
    }

    data = lockfile_mod.read_lockfile(lockfile_path)
    data = lockfile_mod.add_profile(data, profile_v1, "local:ml.json")
    data["user_overrides"] = {
        "removed_rules": [
            {"type": "bashAlias", "value": "pip3", "profile": "ml", "source": "tighten"}
        ]
    }
    lockfile_mod.write_lockfile(data, lockfile_path)

    with (
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
        patch("flow_state.profile_ops.fetch_mod.fetch_profile", return_value=profile_v2),
    ):
        result = profile_ops.update_profiles(lockfile_path=lockfile_path)

    assert result["updated"]
    # Override should be preserved since pip3 is still in v2
    assert "reconciled_overrides" not in result or not result["reconciled_overrides"]

    data = lockfile_mod.read_lockfile(lockfile_path)
    remaining = data.get("user_overrides", {}).get("removed_rules", [])
    remaining_values = [r["value"] for r in remaining]
    assert "pip3" in remaining_values


def test_init_warns_when_profile_tier_exceeds_user_tier(tmp_path: Path) -> None:
    """Profile with higher recommendedTier than user's current tier triggers warning."""
    lockfile_path = tmp_path / "profiles.lock.json"
    config_path = tmp_path / "permission-config.json"

    # User is on cautious tier
    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {"tier": "cautious"},
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    profile = {
        "name": "ml-research",
        "version": "1.0.0",
        "meta": {"recommendedTier": "balanced"},
        "bashAliases": {"pip3": "pip"},
    }

    with (
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
        patch("flow_state.profile_ops.fetch_mod.fetch_profile", return_value=profile),
        patch("flow_state.setup_cmd.find_settings_path", return_value=None),
    ):
        result = profile_ops.init_profile("local:ml.json")

    assert any("recommends tier" in w and "cautious" in w for w in result["warnings"])


def test_init_no_warning_when_profile_tier_matches(tmp_path: Path) -> None:
    """No warning when profile tier matches or is below user tier."""
    lockfile_path = tmp_path / "profiles.lock.json"

    # User is on flow tier
    lockfile_data = {
        "profiles": [], "merged": {},
        "user_config": {"tier": "flow"},
    }
    lockfile_mod.write_lockfile(lockfile_data, lockfile_path)

    profile = {
        "name": "basic",
        "version": "1.0.0",
        "meta": {"recommendedTier": "balanced"},
        "bashAliases": {"pip3": "pip"},
    }

    with (
        patch.object(lockfile_mod, "get_lockfile_path", return_value=lockfile_path),
        patch("flow_state.profile_ops.fetch_mod.fetch_profile", return_value=profile),
        patch("flow_state.setup_cmd.find_settings_path", return_value=None),
    ):
        result = profile_ops.init_profile("local:basic.json")

    # No tier-related warnings
    tier_warnings = [w for w in result["warnings"] if "recommends tier" in w]
    assert len(tier_warnings) == 0
