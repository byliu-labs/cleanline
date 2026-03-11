"""Shared test fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Create a temporary permission-config.json."""
    config = {
        "webfetch": {"extraDomains": ["*.example.com"]},
        "bashAliases": {"python3": "python"},
        "commandMappings": {"npm test": ["npx jest"]},
    }
    path = tmp_path / "permission-config.json"
    path.write_text(json.dumps(config))
    return path


@pytest.fixture
def tmp_lockfile(tmp_path: Path) -> Path:
    """Create a temporary lock file path (not written yet)."""
    return tmp_path / "profiles.lock.json"


@pytest.fixture
def sample_profile() -> dict:
    """A valid sample profile."""
    return {
        "name": "test-profile",
        "version": "1.0.0",
        "description": "Test profile",
        "bashAliases": {"cargo-nightly": "cargo"},
        "commandMappings": {"npm test": ["yarn test", "pnpm test"]},
        "webfetch": {"extraDomains": ["*.arxiv.org"]},
    }


@pytest.fixture
def sample_profile_b() -> dict:
    """A second valid profile for merge testing."""
    return {
        "name": "profile-b",
        "version": "2.0.0",
        "bashAliases": {"pip3": "pip"},
        "commandMappings": {"pip install": ["pip3 install"]},
        "webfetch": {"extraDomains": ["*.scipy.org", "*.arxiv.org"]},
    }


@pytest.fixture
def hooks_dir() -> Path:
    """Path to the scripts directory."""
    return Path(__file__).parent.parent / "plugins" / "flow-state" / "scripts"
