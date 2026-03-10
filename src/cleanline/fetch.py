"""Profile fetching from GitHub and local files.

Supports two source formats:
  - github:<owner>/<repo>[/path]  → fetches profile.json from GitHub raw content
  - local file path               → reads profile.json from disk

Uses only stdlib (urllib) — no external dependencies.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


def parse_source(source: str) -> tuple[str, str]:
    """Parse a source string into (type, location).

    Returns:
      ("github", "owner/repo/path") or ("local", "/absolute/path")
    """
    if source.startswith("github:"):
        return "github", source[len("github:"):]
    return "local", source


def build_github_url(spec: str) -> str:
    """Build a raw GitHub URL from owner/repo[/path] spec.

    If no file path is given, defaults to profile.json at repo root.
    """
    parts = spec.split("/", 2)
    if len(parts) < 2:
        raise ValueError(f"invalid github spec: {spec!r} (need owner/repo)")

    owner, repo = parts[0], parts[1]
    path = parts[2] if len(parts) > 2 else "profile.json"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path}"


def fetch_github(spec: str) -> dict:
    """Fetch a profile from GitHub."""
    url = build_github_url(spec)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cleanline/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub fetch failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from {url}: {e}") from e


def fetch_local(path_str: str) -> dict:
    """Load a profile from a local file."""
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {path}")
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in {path}: {e}") from e


def fetch_profile(source: str) -> dict:
    """Fetch a profile from any supported source."""
    source_type, location = parse_source(source)
    if source_type == "github":
        return fetch_github(location)
    return fetch_local(location)
