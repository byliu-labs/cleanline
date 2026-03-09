"""Tests for shared bash utilities."""
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "plugins" / "permission-hooks" / "hooks"


def _run_normalize(cmd: str) -> tuple[int, str]:
    """Run normalize-bash-cmd.py and return (exit_code, stdout)."""
    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "normalize-bash-cmd.py"), cmd],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout.strip()


def _run_match(cmd: str, config_path: str, root_path: str = "") -> tuple[int, str]:
    """Run match-command-equiv.py and return (exit_code, stdout)."""
    args = [sys.executable, str(HOOKS_DIR / "match-command-equiv.py"), cmd, config_path]
    if root_path:
        args.append(root_path)
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


class TestNormalize:
    def test_simple_command(self) -> None:
        code, out = _run_normalize("python3 script.py")
        assert code == 0
        assert out == "python3"

    def test_env_wrapper(self) -> None:
        code, out = _run_normalize("env -i python3.13 script.py")
        assert code == 0
        assert out == "python3.13"

    def test_timeout_wrapper(self) -> None:
        code, out = _run_normalize("timeout 30 cargo build")
        assert code == 0
        assert out == "cargo"

    def test_full_path(self) -> None:
        code, out = _run_normalize("/usr/bin/python3 test.py")
        assert code == 0
        assert out == "python3"

    def test_metacharacter_rejected(self) -> None:
        code, _ = _run_normalize("python3 && rm -rf /")
        assert code == 1

    def test_pipe_rejected(self) -> None:
        code, _ = _run_normalize("ls | head")
        assert code == 1

    def test_empty_command(self) -> None:
        code, _ = _run_normalize("")
        assert code == 1


class TestMatch:
    def test_match_against_config(self, tmp_config: Path) -> None:
        code, out = _run_match("npx jest --coverage", str(tmp_config))
        assert code == 0
        assert out == "npm test"

    def test_no_match(self, tmp_config: Path) -> None:
        code, _ = _run_match("unknown-cmd arg", str(tmp_config))
        assert code == 1

    def test_metacharacter_rejected(self, tmp_config: Path) -> None:
        code, _ = _run_match("npx jest && rm /", str(tmp_config))
        assert code == 1

    def test_root_path_argument(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "lock.json"
        import json
        lockfile.write_text(json.dumps({
            "merged": {"commandMappings": {"cargo build": ["cargo-nightly build"]}}
        }))
        code, out = _run_match("cargo-nightly build --release", str(lockfile), "merged")
        assert code == 0
        assert out == "cargo build"

    def test_backward_compat_old_key(self, tmp_path: Path) -> None:
        config = tmp_path / "old-config.json"
        import json
        config.write_text(json.dumps({
            "commandEquivalences": {"pip install": ["pip3 install"]}
        }))
        code, out = _run_match("pip3 install requests", str(config))
        assert code == 0
        assert out == "pip install"
