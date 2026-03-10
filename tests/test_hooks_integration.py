"""Integration tests for the new shell hook dispatchers.

Runs the actual bash hooks (bash-gate.sh, approve-webfetch-domains.sh) with
crafted stdin JSON and verifies decisions. These test the full pipeline:
shell dispatcher -> resolve.py / resolve_webfetch.py -> audit log.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "clean-line" / "scripts"


def _run_hook(
    hook_name: str,
    stdin_data: dict,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a hook script with given stdin JSON."""
    hook_path = SCRIPTS_DIR / hook_name

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


def _setup_config(tmp_path: Path, config: dict) -> None:
    """Write a permission-config.json to the hooks dir."""
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    config_path = hooks_dir / "permission-config.json"
    config_path.write_text(json.dumps(config))


class TestBashGate:
    def test_alias_auto_approve(self, tmp_path: Path) -> None:
        """python3.13 should resolve to python and auto-approve."""
        _setup_config(tmp_path, {
            "bashAliases": {"python3.13": "python"},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3.13 test.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_direct_canonical_auto_approve(self, tmp_path: Path) -> None:
        """Command that is itself a canonical should auto-approve."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python script.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_pipe_passthrough(self, tmp_path: Path) -> None:
        """Commands with pipes should not produce an allow decision."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": ["ls"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "ls | head"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_chain_both_resolve(self, tmp_path: Path) -> None:
        """python3 a.py && echo done -- both should resolve."""
        _setup_config(tmp_path, {
            "bashAliases": {"python3": "python"},
            "commandMappings": {},
            "resolvedCanonicals": ["python", "echo"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3 a.py && echo done"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_chain_partial_fail(self, tmp_path: Path) -> None:
        """If any sub-command fails to resolve, entire chain falls through."""
        _setup_config(tmp_path, {
            "bashAliases": {"python3": "python"},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3 a.py && unknown-cmd foo"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_chain_too_many(self, tmp_path: Path) -> None:
        """More than 5 sub-commands should fall through."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": ["echo"],
        })

        cmd = " && ".join([f"echo {i}" for i in range(10)])
        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": cmd}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_unknown_command_passthrough(self, tmp_path: Path) -> None:
        """Unknown commands should exit silently."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "unknown-tool arg1"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_command_mapping_auto_approve(self, tmp_path: Path) -> None:
        """npx jest should map to npm test and auto-approve."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {"npm test": ["npx jest", "yarn test"]},
            "resolvedCanonicals": ["npm"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "npx jest --coverage"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_env_wrapper_resolved(self, tmp_path: Path) -> None:
        """env wrapper should be stripped before resolution."""
        _setup_config(tmp_path, {
            "bashAliases": {"python3.13": "python"},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "env PYTHONPATH=/tmp python3.13 script.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_full_path_resolved(self, tmp_path: Path) -> None:
        """Full path binaries should be resolved by basename."""
        _setup_config(tmp_path, {
            "bashAliases": {"python3": "python"},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "/usr/bin/python3 test.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_audit_log_written(self, tmp_path: Path) -> None:
        """Hook should write valid JSONL to audit log."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python script.py"}},
            {"HOME": str(tmp_path)},
        )

        audit_log = tmp_path / ".claude" / "hooks" / "hook.jsonl"
        assert audit_log.exists()
        for line in audit_log.read_text().strip().splitlines():
            entry = json.loads(line)  # Should NOT raise JSONDecodeError
            assert entry["tool"] == "Bash"
            assert "decision" in entry

    def test_audit_log_escapes_special_characters(self, tmp_path: Path) -> None:
        """Commands with quotes should produce valid JSONL."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": [],
        })

        command = 'python3 -c "import sys; print(sys.path)"'
        _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": command}},
            {"HOME": str(tmp_path)},
        )

        audit_log = tmp_path / ".claude" / "hooks" / "hook.jsonl"
        assert audit_log.exists()
        for line in audit_log.read_text().strip().splitlines():
            entry = json.loads(line)
            assert entry["tool"] == "Bash"

    def test_no_chaining_transitive_aliases(self, tmp_path: Path) -> None:
        """Alias resolution must NOT recurse through multiple levels."""
        _setup_config(tmp_path, {
            "bashAliases": {"mypy3": "python3", "python3": "python"},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        # mypy3 -> python3 (alias), python3 is NOT in canonicals
        # If chaining existed: mypy3 -> python3 -> python. But we don't chain.
        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "mypy3 script.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

        # python3 itself should resolve (single hop)
        code2, out2, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3 script.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code2 == 0
        assert '"decision":"allow"' in out2 or '{"decision":"allow"}' in out2

    def test_no_canonicals_passthrough(self, tmp_path: Path) -> None:
        """When resolvedCanonicals is empty, everything falls through."""
        _setup_config(tmp_path, {
            "bashAliases": {"python3": "python"},
            "commandMappings": {},
            "resolvedCanonicals": [],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3 script.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_config_missing_first_run(self, tmp_path: Path) -> None:
        """When config doesn't exist, first-run should create it."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(python *)"]}
        }))

        # Don't create permission-config.json -- let first-run logic handle it
        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "python3 script.py"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        # First run creates config from defaults and scans settings.json
        config_path = tmp_path / ".claude" / "hooks" / "permission-config.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "python" in config.get("resolvedCanonicals", [])

    def test_shlex_malformed_passthrough(self, tmp_path: Path) -> None:
        """Malformed quoting should fall through safely."""
        _setup_config(tmp_path, {
            "bashAliases": {},
            "commandMappings": {},
            "resolvedCanonicals": ["python"],
        })

        code, out, _ = _run_hook(
            "bash-gate.sh",
            {"tool_input": {"command": "echo 'unclosed"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out


class TestWebFetchDomains:
    def test_extra_domain_auto_approve(self, tmp_path: Path) -> None:
        """Domain from extraDomains should auto-approve."""
        _setup_config(tmp_path, {
            "webfetch": {"extraDomains": ["*.w3.org", "*.docs.rs"]},
        })

        code, out, _ = _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://docs.w3.org/some/page"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_wildcard_match(self, tmp_path: Path) -> None:
        """Wildcard domain should match subdomains."""
        _setup_config(tmp_path, {
            "webfetch": {"extraDomains": ["*.github.com"]},
        })

        code, out, _ = _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://api.github.com/repos"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert '"decision":"allow"' in out or '{"decision":"allow"}' in out

    def test_unknown_domain_passthrough(self, tmp_path: Path) -> None:
        """Unknown domain should exit silently."""
        _setup_config(tmp_path, {
            "webfetch": {"extraDomains": ["*.github.com"]},
        })

        code, out, _ = _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://evil.example.com/malware"}},
            {"HOME": str(tmp_path)},
        )
        assert code == 0
        assert "allow" not in out

    def test_audit_log_written(self, tmp_path: Path) -> None:
        """WebFetch hook should write to audit log."""
        _setup_config(tmp_path, {
            "webfetch": {"extraDomains": ["*.python.org"]},
        })

        _run_hook(
            "approve-webfetch-domains.sh",
            {"tool_input": {"url": "https://docs.python.org/3/"}},
            {"HOME": str(tmp_path)},
        )

        audit_log = tmp_path / ".claude" / "hooks" / "hook.jsonl"
        assert audit_log.exists()
        entry = json.loads(audit_log.read_text().strip().splitlines()[0])
        assert entry["tool"] == "WebFetch"
        assert entry["decision"] == "allow"
