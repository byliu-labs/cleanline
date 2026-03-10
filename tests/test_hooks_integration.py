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


    def test_audit_log_escapes_special_characters(self, tmp_path: Path) -> None:
        """Commands with quotes and special chars should produce valid JSONL audit entries."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": []}
        }))

        # Command with embedded quotes and backslashes
        command = 'python3 -c "import sys; print(sys.path)"'
        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": command}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0

        # Verify the audit log contains valid JSON
        audit_log = tmp_path / ".claude" / "hooks" / "hook.jsonl"
        assert audit_log.exists(), "Audit log should have been created"
        for line in audit_log.read_text().strip().splitlines():
            entry = json.loads(line)  # Should NOT raise JSONDecodeError
            assert entry["tool"] == "Bash"
            assert entry["input"] == command
            assert entry["decision"] == "passthrough"

    def test_no_chaining_transitive_aliases(self, tmp_path: Path) -> None:
        """Alias resolution must NOT recurse through multiple levels.

        If mypy3 -> python3 and python3 -> python are both aliases,
        running "mypy3 script.py" should NOT resolve to python through
        two alias hops. Only one level of indirection is allowed.
        """
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        # Only "python" is in the allow list (not python3, not mypy3)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(python *)"]}
        }))

        # Config with transitive aliases:
        # mypy3 -> python3 (first hop)
        # python3 -> python (second hop, if chaining were allowed)
        config = {
            "bashAliases": {"mypy3": "python3", "python3": "python"},
            "commandMappings": {},
        }
        config_path = HOOKS_DIR / "permission-config.json"
        original_config = config_path.read_text() if config_path.exists() else None

        try:
            config_path.write_text(json.dumps(config))

            # "mypy3 script.py" -> alias lookup finds mypy3 -> python3
            # check_settings_allow("python3") -> False (only "python" allowed)
            # If chaining existed, it would then try python3 -> python -> allowed
            # But chaining is NOT implemented, so this should NOT be allowed.
            code, out, _ = _run_hook(
                "bash-gate.sh",
                {"tool_input": {"command": "mypy3 script.py"}},
                {"HOME": str(tmp_path)},
            )
            assert code == 0
            assert "allow" not in out

            # Verify that python3 itself DOES resolve (single hop works)
            code2, out2, _ = _run_hook(
                "bash-gate.sh",
                {"tool_input": {"command": "python3 script.py"}},
                {"HOME": str(tmp_path)},
            )
            assert code2 == 0
            assert '"decision":"allow"' in out2 or '{"decision":"allow"}' in out2
        finally:
            if original_config is not None:
                config_path.write_text(original_config)
            elif config_path.exists():
                config_path.unlink()


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
