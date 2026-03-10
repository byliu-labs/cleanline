"""Tests for the file operations permission hook (resolve_fileops.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the script as a module
import importlib.util

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "clean-line" / "scripts"

spec = importlib.util.spec_from_file_location("resolve_fileops", SCRIPTS_DIR / "resolve_fileops.py")
resolve_fileops = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(resolve_fileops)  # type: ignore[union-attr]


# ============================================================================
# TEST NORMALIZE_PATH
# ============================================================================


class TestNormalizePath:
    def test_tilde_expansion(self) -> None:
        result = resolve_fileops.normalize_path("~/foo")
        assert result is not None
        assert str(result).startswith("/")
        assert "~" not in str(result)
        assert str(result).endswith("/foo")

    def test_relative_path_resolution(self) -> None:
        result = resolve_fileops.normalize_path("foo/bar")
        assert result is not None
        assert result.is_absolute()

    def test_absolute_path_stays_absolute(self) -> None:
        result = resolve_fileops.normalize_path("/usr/local/bin")
        assert result is not None
        assert str(result) == "/usr/local/bin"

    def test_symlink_resolution(self, tmp_path: Path) -> None:
        """Symlinks should be resolved to their real target."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)

        result = resolve_fileops.normalize_path(str(link))
        assert result is not None
        assert result == real_file.resolve()

    def test_dotdot_resolved(self) -> None:
        result = resolve_fileops.normalize_path("/tmp/a/../b")
        assert result is not None
        # .. should be resolved
        assert ".." not in str(result)


# ============================================================================
# TEST EXTRACT_PATH
# ============================================================================


class TestExtractPath:
    def test_read_file_path(self) -> None:
        result = resolve_fileops.extract_path("Read", {"file_path": "/tmp/foo.txt"})
        assert result == "/tmp/foo.txt"

    def test_edit_file_path(self) -> None:
        result = resolve_fileops.extract_path("Edit", {"file_path": "/tmp/bar.py"})
        assert result == "/tmp/bar.py"

    def test_write_file_path(self) -> None:
        result = resolve_fileops.extract_path("Write", {"file_path": "/tmp/out.txt"})
        assert result == "/tmp/out.txt"

    def test_glob_with_path(self) -> None:
        result = resolve_fileops.extract_path("Glob", {"path": "/home/user/src", "pattern": "*.py"})
        assert result == "/home/user/src"

    def test_glob_missing_path_defaults_to_cwd(self) -> None:
        result = resolve_fileops.extract_path("Glob", {"pattern": "*.py"})
        assert result == os.getcwd()

    def test_grep_with_path(self) -> None:
        result = resolve_fileops.extract_path("Grep", {"path": "/var/log", "pattern": "error"})
        assert result == "/var/log"

    def test_grep_missing_path_defaults_to_cwd(self) -> None:
        result = resolve_fileops.extract_path("Grep", {"pattern": "TODO"})
        assert result == os.getcwd()

    def test_read_missing_file_path(self) -> None:
        result = resolve_fileops.extract_path("Read", {})
        assert result is None

    def test_unknown_tool(self) -> None:
        result = resolve_fileops.extract_path("Unknown", {"file_path": "/tmp/x"})
        assert result is None


# ============================================================================
# TEST MATCHES_PATTERN
# ============================================================================


class TestMatchesPattern:
    def test_exact_path_match(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.netrc")
        assert resolve_fileops.matches_pattern(path, "~/.netrc") is True

    def test_recursive_match(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.claude/settings.json")
        assert resolve_fileops.matches_pattern(path, "~/.claude/**") is True

    def test_recursive_match_deep(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.claude/hooks/permission-config.json")
        assert resolve_fileops.matches_pattern(path, "~/.claude/**") is True

    def test_recursive_match_dir_itself(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.claude")
        assert resolve_fileops.matches_pattern(path, "~/.claude/**") is True

    def test_no_match(self) -> None:
        path = Path("/usr/local/bin/python3")
        assert resolve_fileops.matches_pattern(path, "~/.claude/**") is False

    def test_wildcard_match(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.claude/credentials.bak")
        assert resolve_fileops.matches_pattern(path, "~/.claude/credentials*") is True

    def test_tmp_recursive(self) -> None:
        # On macOS, /tmp resolves to /private/tmp — use resolved path
        resolved_tmp = Path("/tmp").resolve()
        path = resolved_tmp / "foo" / "bar" / "baz.txt"
        assert resolve_fileops.matches_pattern(path, "/tmp/**") is True


# ============================================================================
# TEST .env PATTERN MATCHING
# ============================================================================


class TestEnvPatternMatching:
    def test_env_at_project_root_denied(self) -> None:
        path = Path("/project/.env")
        assert resolve_fileops.matches_pattern(path, "**/.env") is True

    def test_env_local_denied(self) -> None:
        path = Path("/project/.env.local")
        assert resolve_fileops.matches_pattern(path, "**/.env.*") is True

    def test_env_production_deep_denied(self) -> None:
        path = Path("/project/subdir/.env.production")
        assert resolve_fileops.matches_pattern(path, "**/.env.*") is True

    def test_env_sample_with_dash_allowed(self) -> None:
        """Dash after .env is NOT a dot -- .env-sample should NOT match **/.env.*"""
        path = Path("/project/.env-sample")
        assert resolve_fileops.matches_pattern(path, "**/.env.*") is False

    def test_env_without_dot_allowed(self) -> None:
        """'env' without leading dot should not match **/.env"""
        path = Path("/project/env")
        assert resolve_fileops.matches_pattern(path, "**/.env") is False

    def test_environment_file_not_denied(self) -> None:
        """.environment should NOT match **/.env.*"""
        path = Path("/project/.environment")
        assert resolve_fileops.matches_pattern(path, "**/.env.*") is False

    def test_env_exact_match_only(self) -> None:
        """.envrc should NOT match **/.env"""
        path = Path("/project/.envrc")
        assert resolve_fileops.matches_pattern(path, "**/.env") is False


# ============================================================================
# TEST IS_DENIED
# ============================================================================


class TestIsDenied:
    def test_ssh_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.ssh/id_rsa")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "ssh" in result

    def test_gnupg_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.gnupg/private-keys-v1.d/key")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "gnupg" in result

    def test_aws_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.aws/credentials")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "aws" in result

    def test_netrc_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.netrc")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "netrc" in result

    def test_credentials_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.claude/credentials.json")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "credentials" in result

    def test_password_store_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.password-store/email.gpg")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "password-store" in result

    def test_keyrings_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.local/share/keyrings/login.keyring")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "keyrings" in result

    def test_kube_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.kube/config")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "kube" in result

    def test_docker_denied(self) -> None:
        home = str(Path.home())
        path = Path(f"{home}/.docker/config.json")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert "docker" in result

    def test_env_file_denied(self) -> None:
        path = Path("/project/.env")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert ".env" in result

    def test_env_local_denied(self) -> None:
        path = Path("/project/.env.local")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None

    def test_git_config_denied(self) -> None:
        path = Path("/project/.git/config")
        result = resolve_fileops.is_denied(path, {})
        assert result is not None
        assert ".git/config" in result

    def test_config_deny_paths(self) -> None:
        path = Path("/opt/secrets/key.pem")
        config = {"fileAccess": {"denyPaths": ["/opt/secrets/**"]}}
        result = resolve_fileops.is_denied(path, config)
        assert result is not None
        assert "/opt/secrets/**" in result

    def test_non_denied_path(self) -> None:
        path = Path("/tmp/safe/file.txt")
        result = resolve_fileops.is_denied(path, {})
        assert result is None

    def test_safe_claude_file(self) -> None:
        """~/.claude/settings.json is not in the deny list."""
        home = str(Path.home())
        path = Path(f"{home}/.claude/settings.json")
        result = resolve_fileops.is_denied(path, {})
        assert result is None


# ============================================================================
# TEST SYMLINKS (HARDENED)
# ============================================================================


class TestSymlinks:
    def test_symlink_direct(self, tmp_path: Path) -> None:
        """Symlink to ~/.ssh/id_rsa should be denied after resolution."""
        home = str(Path.home())
        target = Path(f"{home}/.ssh/id_rsa")
        # Create a symlink pointing to the target (target needn't exist for the test)
        link = tmp_path / "link"
        link.symlink_to(target)

        # normalize_path follows symlinks
        resolved = resolve_fileops.normalize_path(str(link))
        if resolved is not None:
            result = resolve_fileops.is_denied(resolved, {})
            assert result is not None

    def test_symlink_parent(self, tmp_path: Path) -> None:
        """Symlink to ~/.ssh/ directory, then access via link/id_rsa -> denied."""
        home = str(Path.home())
        target_dir = Path(f"{home}/.ssh")
        link_dir = tmp_path / "safe"
        link_dir.symlink_to(target_dir)

        # Access link_dir/id_rsa -> resolves to ~/.ssh/id_rsa
        composed = link_dir / "id_rsa"
        resolved = resolve_fileops.normalize_path(str(composed))
        if resolved is not None:
            result = resolve_fileops.is_denied(resolved, {})
            assert result is not None

    def test_symlink_relative_escape(self, tmp_path: Path) -> None:
        """Path with .. that escapes to sensitive dir should be caught."""
        home = str(Path.home())
        # Construct a path that resolves to ~/.ssh/key via ..
        # tmp_path/../../../Users/<user>/.ssh/key  (or similar)
        tricky = f"{home}/a/b/../../.ssh/key"
        resolved = resolve_fileops.normalize_path(tricky)
        if resolved is not None:
            result = resolve_fileops.is_denied(resolved, {})
            assert result is not None

    def test_symlink_to_safe_dir(self, tmp_path: Path) -> None:
        """Symlink to a safe directory should be allowed."""
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)

        resolved = resolve_fileops.normalize_path(str(link))
        assert resolved is not None
        result = resolve_fileops.is_denied(resolved, {})
        assert result is None


# ============================================================================
# TEST CHECK_ACCESS
# ============================================================================


class TestCheckAccess:
    def _config(
        self,
        read_paths: list[str] | None = None,
        write_paths: list[str] | None = None,
        deny_paths: list[str] | None = None,
    ) -> dict:
        return {
            "fileAccess": {
                "readPaths": read_paths or [],
                "writePaths": write_paths or [],
                "denyPaths": deny_paths or [],
            }
        }

    def test_read_allowed(self) -> None:
        config = self._config(read_paths=["~/.claude/**"])
        home = str(Path.home())
        allowed, rule = resolve_fileops.check_access(
            "Read", {"file_path": f"{home}/.claude/settings.json"}, config
        )
        assert allowed is True
        assert rule == "read:~/.claude/**"

    def test_write_allowed(self) -> None:
        config = self._config(write_paths=["/tmp/**"])
        allowed, rule = resolve_fileops.check_access(
            "Write", {"file_path": "/tmp/output.txt"}, config
        )
        assert allowed is True
        assert rule == "write:/tmp/**"

    def test_edit_uses_write_paths(self) -> None:
        config = self._config(write_paths=["/tmp/**"])
        allowed, rule = resolve_fileops.check_access(
            "Edit", {"file_path": "/tmp/foo.py"}, config
        )
        assert allowed is True
        assert rule.startswith("write:")

    def test_deny_overrides_allow(self) -> None:
        home = str(Path.home())
        config = self._config(
            read_paths=["~/.ssh/**"],  # Allow reading SSH
            deny_paths=["~/.ssh/**"],  # But deny overrides
        )
        allowed, rule = resolve_fileops.check_access(
            "Read", {"file_path": f"{home}/.ssh/id_rsa"}, config
        )
        assert allowed is False
        assert rule.startswith("deny:")

    def test_hardcoded_deny_overrides_config_allow(self) -> None:
        """Hardcoded deny cannot be overridden by config readPaths."""
        home = str(Path.home())
        config = self._config(read_paths=["~/.ssh/**"])
        allowed, rule = resolve_fileops.check_access(
            "Read", {"file_path": f"{home}/.ssh/id_rsa"}, config
        )
        assert allowed is False
        assert rule.startswith("deny:")

    def test_write_to_read_only_path_passthrough(self) -> None:
        """Writing to a path only in readPaths should not be allowed."""
        config = self._config(read_paths=["~/.claude/**"])
        home = str(Path.home())
        allowed, rule = resolve_fileops.check_access(
            "Write", {"file_path": f"{home}/.claude/settings.json"}, config
        )
        assert allowed is False
        assert rule == "no_match"

    def test_no_file_access_config_passthrough(self) -> None:
        allowed, rule = resolve_fileops.check_access(
            "Read", {"file_path": "/tmp/foo"}, {}
        )
        assert allowed is False
        assert rule == "no_match"

    def test_glob_with_missing_path_defaults_to_cwd(self) -> None:
        config = self._config(read_paths=[f"{os.getcwd()}/**"])
        allowed, rule = resolve_fileops.check_access(
            "Glob", {"pattern": "*.py"}, config
        )
        # CWD should match the allowed readPath
        assert allowed is True
        assert rule.startswith("read:")

    def test_grep_allowed(self) -> None:
        config = self._config(read_paths=["~/.config/**"])
        home = str(Path.home())
        allowed, rule = resolve_fileops.check_access(
            "Grep", {"path": f"{home}/.config/git", "pattern": "email"}, config
        )
        assert allowed is True
        assert rule == "read:~/.config/**"

    def test_env_file_denied_even_in_allowed_dir(self) -> None:
        """A .env file should be denied even if the parent dir is allowed."""
        config = self._config(read_paths=["/project/**"])
        allowed, rule = resolve_fileops.check_access(
            "Read", {"file_path": "/project/.env"}, config
        )
        assert allowed is False
        assert "deny:" in rule


# ============================================================================
# TEST LOG_DECISION
# ============================================================================


class TestLogDecision:
    def test_jsonl_written(self, tmp_path: Path) -> None:
        resolve_fileops.log_decision(
            tmp_path, "Read", "/tmp/foo.txt", "allow", "read:/tmp/**"
        )
        audit_log = tmp_path / "hook.jsonl"
        assert audit_log.exists()
        entry = json.loads(audit_log.read_text().strip())
        assert entry["tool"] == "Read"
        assert entry["decision"] == "allow"
        assert entry["matched_rule"] == "read:/tmp/**"

    def test_jsonl_append(self, tmp_path: Path) -> None:
        resolve_fileops.log_decision(tmp_path, "Read", "/a", "allow", "read:/a")
        resolve_fileops.log_decision(tmp_path, "Write", "/b", "allow", "write:/b")
        audit_log = tmp_path / "hook.jsonl"
        lines = audit_log.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_rule_prefix_format(self, tmp_path: Path) -> None:
        resolve_fileops.log_decision(
            tmp_path, "Read", "/tmp/x", "allow", "read:~/.claude/**"
        )
        resolve_fileops.log_decision(
            tmp_path, "Write", "/tmp/y", "allow", "write:/tmp/**"
        )
        resolve_fileops.log_decision(
            tmp_path, "Read", "/ssh/key", "passthrough", "deny:~/.ssh/**"
        )
        audit_log = tmp_path / "hook.jsonl"
        lines = audit_log.read_text().strip().splitlines()
        entries = [json.loads(line) for line in lines]
        assert entries[0]["matched_rule"].startswith("read:")
        assert entries[1]["matched_rule"].startswith("write:")
        assert entries[2]["matched_rule"].startswith("deny:")
