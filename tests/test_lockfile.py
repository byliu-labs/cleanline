"""Tests for lock file operations."""
import json
from pathlib import Path

from cleanline.lockfile import (
    add_profile,
    merge_profiles,
    read_lockfile,
    rebuild_merged,
    remove_profile,
    write_lockfile,
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
