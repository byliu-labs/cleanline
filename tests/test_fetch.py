"""Tests for profile fetching."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_hooks.fetch import (
    build_github_url,
    fetch_local,
    fetch_profile,
    parse_source,
)


def test_parse_source_github() -> None:
    source_type, loc = parse_source("github:user/repo")
    assert source_type == "github"
    assert loc == "user/repo"


def test_parse_source_local() -> None:
    source_type, loc = parse_source("/path/to/file.json")
    assert source_type == "local"
    assert loc == "/path/to/file.json"


def test_build_github_url_default_path() -> None:
    url = build_github_url("user/repo")
    assert url == "https://raw.githubusercontent.com/user/repo/main/profile.json"


def test_build_github_url_custom_path() -> None:
    url = build_github_url("user/repo/profiles/rust.json")
    assert url == "https://raw.githubusercontent.com/user/repo/main/profiles/rust.json"


def test_build_github_url_invalid() -> None:
    with pytest.raises(ValueError, match="invalid github spec"):
        build_github_url("justowner")


def test_fetch_local_valid(tmp_path: Path) -> None:
    profile = {"name": "test", "version": "1.0"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))

    result = fetch_local(str(path))
    assert result["name"] == "test"


def test_fetch_local_missing() -> None:
    with pytest.raises(FileNotFoundError):
        fetch_local("/nonexistent/profile.json")


def test_fetch_local_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json")
    with pytest.raises(RuntimeError, match="Invalid JSON"):
        fetch_local(str(path))


def test_fetch_profile_local(tmp_path: Path) -> None:
    profile = {"name": "test", "version": "1.0"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))

    result = fetch_profile(str(path))
    assert result["name"] == "test"


def test_fetch_profile_github_mock() -> None:
    profile = {"name": "remote-test", "version": "2.0"}
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(profile).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = fetch_profile("github:user/repo")

    assert result["name"] == "remote-test"
