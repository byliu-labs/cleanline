#!/usr/bin/env python3
"""Normalize a bash command to its base binary name.

Reads a command string from argv[1].
Prints the normalized binary name to stdout.
Exits non-zero on any error (fail closed).

Handles two wrappers:
  - env: skips flags (-i, -u, --) and VAR=val assignments
  - timeout: skips flags and the numeric duration arg
"""
import os
import shlex
import sys

# Metacharacters that signal compound/piped commands.
# If ANY of these appear raw in the command, we refuse to parse.
METACHARACTERS = {"&&", "||", ";", "|", "`", "$(", ">(", "<(", "{", "}"}


def has_metacharacters(cmd: str) -> bool:
    for meta in METACHARACTERS:
        if meta in cmd:
            return True
    return False


def skip_env(argv: list[str]) -> list[str]:
    """Skip past 'env' and its flags/assignments to find the real command."""
    i = 1  # skip 'env' itself
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return argv[i + 1:]
        if arg.startswith("-"):
            i += 1
            continue
        if "=" in arg:
            i += 1
            continue
        return argv[i:]
    return []


def skip_timeout(argv: list[str]) -> list[str]:
    """Skip past 'timeout' and its flags/duration to find the real command."""
    i = 1  # skip 'timeout' itself
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


def main() -> int:
    if len(sys.argv) != 2:
        return 1

    cmd = sys.argv[1]

    # Belt-and-suspenders metacharacter check
    if has_metacharacters(cmd):
        return 1

    try:
        argv = shlex.split(cmd)
    except ValueError:
        return 1

    if not argv:
        return 1

    # Get basename (strips /usr/bin/ etc.)
    binary = os.path.basename(argv[0])

    # Handle known wrappers
    if binary == "env" and len(argv) > 1:
        rest = skip_env(argv)
        if not rest:
            return 1
        binary = os.path.basename(rest[0])

    elif binary == "timeout" and len(argv) > 1:
        rest = skip_timeout(argv)
        if not rest:
            return 1
        binary = os.path.basename(rest[0])

    if not binary:
        return 1

    print(binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
