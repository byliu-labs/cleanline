"""Integration tests for profile operations."""
import json
from pathlib import Path
from unittest.mock import patch

from cleanline import lockfile as lockfile_mod
from cleanline import profile_ops


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
