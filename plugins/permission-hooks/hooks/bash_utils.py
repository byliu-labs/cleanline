"""Shared utilities for bash command parsing.

Extracted from normalize-bash-cmd.py and match-command-equiv.py to eliminate
duplication. Both scripts import from here via sys.path manipulation.
"""
import os

METACHARACTERS = {"&&", "||", ";", "|", "`", "$(", ">(", "<(", "{", "}"}


def has_metacharacters(cmd: str) -> bool:
    """Return True if cmd contains any shell metacharacter."""
    for meta in METACHARACTERS:
        if meta in cmd:
            return True
    return False


def unwrap_command(argv: list[str]) -> list[str]:
    """Strip env/timeout wrappers to get the real command tokens.

    Handles:
      - env: skips flags (-i, -u, --) and VAR=val assignments
      - timeout: skips flags and the numeric duration arg
    """
    if not argv:
        return []

    binary = os.path.basename(argv[0])

    if binary == "env" and len(argv) > 1:
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                return argv[i + 1:]
            if arg.startswith("-") or "=" in arg:
                i += 1
                continue
            return argv[i:]
        return []

    if binary == "timeout" and len(argv) > 1:
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                return argv[i + 1:]
            if arg.startswith("-"):
                i += 1
                continue
            # First non-flag arg is the duration — skip it
            return argv[i + 1:]
        return []

    return argv
