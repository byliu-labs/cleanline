"""Tests for resolve.py (Bash hook) and resolve_webfetch.py (WebFetch hook) internals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add the scripts directory to the path so we can import resolve modules
SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "clean-line" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import resolve
import resolve_webfetch


# ============================================================================
# METACHARACTER DETECTION
# ============================================================================


class TestDangerousMetacharacters:
    def test_pipe_rejected(self) -> None:
        assert resolve.has_dangerous_metacharacters("ls | head") is True

    def test_backtick_rejected(self) -> None:
        assert resolve.has_dangerous_metacharacters("echo `whoami`") is True

    def test_dollar_paren_rejected(self) -> None:
        assert resolve.has_dangerous_metacharacters("echo $(whoami)") is True

    def test_process_sub_rejected(self) -> None:
        assert resolve.has_dangerous_metacharacters("diff <(cmd1) >(cmd2)") is True

    def test_double_amp_not_dangerous(self) -> None:
        # && is handled by chain splitting, not dangerous metacharacters
        assert resolve.has_dangerous_metacharacters("python a.py && echo done") is False

    def test_semicolon_not_dangerous(self) -> None:
        # ; is handled by chain splitting, not dangerous metacharacters
        assert resolve.has_dangerous_metacharacters("python a.py; echo done") is False

    def test_clean_command(self) -> None:
        assert resolve.has_dangerous_metacharacters("python3 script.py --arg foo") is False


# ============================================================================
# CHAIN SPLITTING
# ============================================================================


class TestSplitChain:
    def test_simple_command(self) -> None:
        result = resolve.split_chain("python3 script.py")
        assert result == ["python3 script.py"]

    def test_double_amp_split(self) -> None:
        result = resolve.split_chain("python3 a.py && echo done")
        assert result == ["python3 a.py", "echo done"]

    def test_semicolon_split(self) -> None:
        result = resolve.split_chain("python3 a.py ; echo done")
        assert result == ["python3 a.py", "echo done"]

    def test_mixed_chain(self) -> None:
        result = resolve.split_chain("python3 a.py && echo done ; ls")
        assert result == ["python3 a.py", "echo done", "ls"]

    def test_quoted_double_amp_stays_intact(self) -> None:
        result = resolve.split_chain('echo "hello && world"')
        assert result is not None
        assert len(result) == 1
        assert result[0] == "echo hello && world"

    def test_pipe_rejected(self) -> None:
        assert resolve.split_chain("ls | head") is None

    def test_backtick_rejected(self) -> None:
        assert resolve.split_chain("echo `whoami`") is None

    def test_dollar_paren_rejected(self) -> None:
        assert resolve.split_chain("echo $(whoami)") is None

    def test_too_many_subcommands(self) -> None:
        cmd = " && ".join([f"echo {i}" for i in range(10)])
        assert resolve.split_chain(cmd) is None

    def test_max_subcommands_ok(self) -> None:
        cmd = " && ".join([f"echo {i}" for i in range(5)])
        result = resolve.split_chain(cmd)
        assert result is not None
        assert len(result) == 5

    def test_shlex_error_returns_none(self) -> None:
        # Unmatched quote
        assert resolve.split_chain("echo 'unclosed") is None

    def test_empty_returns_none(self) -> None:
        assert resolve.split_chain("") is None


# ============================================================================
# COMMAND NORMALIZATION
# ============================================================================


class TestNormalizeBinary:
    def test_simple_command(self) -> None:
        assert resolve.normalize_binary("python3 script.py") == "python3"

    def test_env_wrapper(self) -> None:
        assert resolve.normalize_binary("env -i python3.13 script.py") == "python3.13"

    def test_timeout_wrapper(self) -> None:
        assert resolve.normalize_binary("timeout 30 cargo build") == "cargo"

    def test_full_path(self) -> None:
        assert resolve.normalize_binary("/usr/bin/python3 test.py") == "python3"

    def test_env_with_var_assignment(self) -> None:
        assert resolve.normalize_binary("env PYTHONPATH=/tmp python3 test.py") == "python3"

    def test_empty_returns_none(self) -> None:
        assert resolve.normalize_binary("") is None

    def test_shlex_error_returns_none(self) -> None:
        assert resolve.normalize_binary("echo 'unclosed") is None


# ============================================================================
# ALIAS LOOKUP
# ============================================================================


class TestResolveAlias:
    def test_match(self) -> None:
        config = {"bashAliases": {"python3": "python", "pip3": "pip"}}
        assert resolve.resolve_alias("python3", config) == "python"

    def test_no_match(self) -> None:
        config = {"bashAliases": {"python3": "python"}}
        assert resolve.resolve_alias("unknown", config) is None

    def test_empty_config(self) -> None:
        assert resolve.resolve_alias("python3", {}) is None


# ============================================================================
# COMMAND MAPPING
# ============================================================================


class TestResolveMapping:
    def test_match(self) -> None:
        config = {"commandMappings": {"npm test": ["npx jest", "yarn test"]}}
        assert resolve.resolve_mapping("npx jest --coverage", config) == "npm test"

    def test_no_match(self) -> None:
        config = {"commandMappings": {"npm test": ["npx jest"]}}
        assert resolve.resolve_mapping("cargo build", config) is None

    def test_empty_mappings(self) -> None:
        assert resolve.resolve_mapping("anything", {}) is None

    def test_longest_prefix_wins(self) -> None:
        config = {"commandMappings": {
            "npm": ["npx"],
            "npm test": ["npx jest"],
        }}
        assert resolve.resolve_mapping("npx jest --coverage", config) == "npm test"

    def test_legacy_key_name(self) -> None:
        config = {"commandEquivalences": {"pip install": ["pip3 install"]}}
        assert resolve.resolve_mapping("pip3 install requests", config) == "pip install"


# ============================================================================
# RESOLVE SINGLE
# ============================================================================


class TestResolveSingle:
    def test_direct_canonical(self) -> None:
        config = {"bashAliases": {}}
        allowed, rule = resolve.resolve_single("python script.py", config, ["python"])
        assert allowed is True
        assert rule == "direct:python"

    def test_alias_resolution(self) -> None:
        config = {"bashAliases": {"python3.13": "python"}}
        allowed, rule = resolve.resolve_single("python3.13 test.py", config, ["python"])
        assert allowed is True
        assert "alias:python3.13->python" == rule

    def test_mapping_resolution(self) -> None:
        config = {"commandMappings": {"npm test": ["npx jest"]}}
        allowed, rule = resolve.resolve_single("npx jest --coverage", config, ["npm"])
        assert allowed is True
        assert rule == "mapping:npm test"

    def test_no_match(self) -> None:
        config = {"bashAliases": {}, "commandMappings": {}}
        allowed, rule = resolve.resolve_single("unknown-cmd arg", config, ["python"])
        assert allowed is False
        assert rule == "no_match"

    def test_alias_not_in_canonicals(self) -> None:
        config = {"bashAliases": {"cargo-nightly": "cargo"}}
        allowed, rule = resolve.resolve_single("cargo-nightly build", config, ["python"])
        assert allowed is False
        assert rule == "no_match"

    def test_env_wrapper_resolved(self) -> None:
        config = {"bashAliases": {"python3.13": "python"}}
        allowed, rule = resolve.resolve_single(
            "env PYTHONPATH=/tmp python3.13 script.py", config, ["python"]
        )
        assert allowed is True
        assert "alias:python3.13->python" == rule

    def test_full_path_resolved(self) -> None:
        config = {"bashAliases": {"python3": "python"}}
        allowed, rule = resolve.resolve_single(
            "/usr/bin/python3 script.py", config, ["python"]
        )
        assert allowed is True
        assert "alias:python3->python" == rule

    def test_no_chaining_transitive(self) -> None:
        """Alias resolution must NOT recurse through multiple levels."""
        config = {"bashAliases": {"mypy3": "python3", "python3": "python"}}
        # mypy3 -> python3 (alias lookup), but python3 is NOT in canonicals
        # Only "python" is in canonicals. If chaining existed, mypy3 -> python3 -> python
        # But we don't chain, so this should fail.
        allowed, rule = resolve.resolve_single("mypy3 script.py", config, ["python"])
        assert allowed is False


# ============================================================================
# AUDIT LOGGING
# ============================================================================


class TestLogDecision:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        resolve.log_decision(tmp_path, "Bash", "python3 test.py", "allow", "alias:python3->python")
        log_path = tmp_path / "hook.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["tool"] == "Bash"
        assert entry["decision"] == "allow"
        assert entry["matched_rule"] == "alias:python3->python"

    def test_appends_multiple(self, tmp_path: Path) -> None:
        resolve.log_decision(tmp_path, "Bash", "cmd1", "allow", "rule1")
        resolve.log_decision(tmp_path, "Bash", "cmd2", "passthrough", "no_match")
        log_path = tmp_path / "hook.jsonl"
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2


# ============================================================================
# ENSURE CONFIG (first-run logic)
# ============================================================================


class TestEnsureConfig:
    def test_config_exists_returns_path(self, tmp_path: Path) -> None:
        config_path = tmp_path / "permission-config.json"
        config_path.write_text('{"bashAliases": {}}')
        result = resolve.ensure_config(tmp_path, str(SCRIPTS_DIR))
        assert result == config_path

    def test_first_run_copies_defaults(self, tmp_path: Path) -> None:
        # Use real script dir which has default-config.json
        result = resolve.ensure_config(tmp_path, str(SCRIPTS_DIR))
        assert result.exists()
        config = json.loads(result.read_text())
        assert "bashAliases" in config
        assert "webfetch" in config


# ============================================================================
# WEBFETCH: HOSTNAME PARSING
# ============================================================================


class TestParseHostname:
    def test_normal_url(self) -> None:
        assert resolve_webfetch.parse_hostname("https://docs.python.org/3/") == "docs.python.org"

    def test_ip_rejected(self) -> None:
        assert resolve_webfetch.parse_hostname("https://192.168.1.1/path") is None

    def test_empty_host_rejected(self) -> None:
        assert resolve_webfetch.parse_hostname("file:///etc/passwd") is None

    def test_trailing_dot_stripped(self) -> None:
        assert resolve_webfetch.parse_hostname("https://example.com./path") == "example.com"


# ============================================================================
# WEBFETCH: DOMAIN MATCHING
# ============================================================================


class TestMatchesDomain:
    def test_exact_match(self) -> None:
        assert resolve_webfetch.matches_domain("github.com", "github.com") is True

    def test_wildcard_match(self) -> None:
        assert resolve_webfetch.matches_domain("docs.python.org", "*.python.org") is True

    def test_wildcard_no_match_root(self) -> None:
        assert resolve_webfetch.matches_domain("python.org", "*.python.org") is False

    def test_no_match(self) -> None:
        assert resolve_webfetch.matches_domain("evil.com", "*.python.org") is False
