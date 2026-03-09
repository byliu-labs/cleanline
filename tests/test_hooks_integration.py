"""Integration tests for shell hooks.

Runs the actual bash hooks with crafted stdin JSON and verifies decisions.
Requires: bash, jq, Python 3.10+
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "plugins" / "permission-hooks" / "hooks"


def _run_hook(
    hook_name: str,
    stdin_data: dict,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a hook script with given stdin JSON."""
    hook_path = HOOKS_DIR / hook_name

    env = dict(os.environ)
    # Point to a temp audit log to avoid writing to real user dir
    env["HOME"] = env.get("TMPDIR", "/tmp")
    if env_overrides:
        env.update(env_overrides)

    result = subprocess.run(
        ["bash", str(hook_path)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class TestBashGate:
    def test_alias_auto_approve(self, tmp_path: Path) -> None:
        """python3.13 should resolve to python and auto-approve if Bash(python *) is allowed."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(python *)"]}
        }))

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3.13 test.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_metacharacter_passthrough(self, tmp_path: Path) -> None:
        """Commands with pipes should not produce an allow decision."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(ls *)"]}
        }))

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "ls | head"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_unknown_command_passthrough(self, tmp_path: Path) -> None:
        """Unknown commands should exit silently."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": []}
        }))

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "unknown-tool arg1"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_command_mapping_auto_approve(self, tmp_path: Path) -> None:
        """npx jest should map to npm test and auto-approve."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(npm test *)"]}
        }))

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "npx jest --coverage"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_lockfile_alias_auto_approve(self, tmp_path: Path) -> None:
        """Alias from lock file should also auto-approve."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(cargo *)"]}
        }))

        lockfile = tmp_path / ".claude" / "hooks" / "profiles.lock.json"
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        lockfile.write_text(json.dumps({
            "merged": {"bashAliases": {"cargo-nightly": "cargo"}}
        }))

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "cargo-nightly build"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out


class TestWebFetchDomains:
    def test_extra_domain_auto_approve(self, tmp_path: Path) -> None:
        """Domain from extraDomains should auto-approve."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({}))

        code, out, _ = _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://docs.w3.org/some/page"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_lockfile_domain_auto_approve(self, tmp_path: Path) -> None:
        """Domain from lock file should also auto-approve."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({}))

        lockfile = tmp_path / ".claude" / "hooks" / "profiles.lock.json"
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        lockfile.write_text(json.dumps({
            "merged": {"webfetch": {"extraDomains": ["*.custom-docs.io"]}}
        }))

        code, out, _ = _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://api.custom-docs.io/v2/help"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_unknown_domain_passthrough(self, tmp_path: Path) -> None:
        """Unknown domain should exit silently."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({}))

        code, out, _ = _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://evil.example.com/malware"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out
