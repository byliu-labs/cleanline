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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_utils import has_metacharacters, unwrap_command


def main() -> int:
    if len(sys.argv) != 2:
        return 1

    cmd = sys.argv[1]

    if has_metacharacters(cmd):
        return 1

    try:
        argv = shlex.split(cmd)
    except ValueError:
        return 1

    if not argv:
        return 1

    unwrapped = unwrap_command(argv)
    if not unwrapped:
        return 1

    binary = os.path.basename(unwrapped[0])
    if not binary:
        return 1

    print(binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
