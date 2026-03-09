#!/usr/bin/env python3
"""Match a command against multi-word equivalences.

Usage: match-command-equiv.py <command> <config-file>

Reads commandEquivalences from config, checks if the command matches any
alias, and prints the canonical form if so.

Example config:
  {
    "commandEquivalences": {
      "npm test": ["npx jest", "yarn test", "pnpm test"]
    }
  }

Running: match-command-equiv.py "npx jest --coverage" config.json
Output:  npm test

Exit 0 + printed canonical = match found.
Exit 1 = no match (or error). Fail closed.
"""
import json
import os
import shlex
import sys

METACHARACTERS = {"&&", "||", ";", "|", "`", "$(", ">(", "<(", "{", "}"}


def has_metacharacters(cmd: str) -> bool:
    for meta in METACHARACTERS:
        if meta in cmd:
            return True
    return False


def unwrap_command(argv: list[str]) -> list[str]:
    """Strip env/timeout wrappers to get the real command tokens."""
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


def tokens_prefix_match(cmd_tokens: list[str], alias_tokens: list[str]) -> bool:
    """Check if cmd_tokens starts with alias_tokens (exact token match)."""
    if len(cmd_tokens) < len(alias_tokens):
        return False
    return cmd_tokens[:len(alias_tokens)] == alias_tokens


def main() -> int:
    if len(sys.argv) != 3:
        return 1

    cmd = sys.argv[1]
    config_path = sys.argv[2]

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

    # Normalize basenames for matching
    unwrapped[0] = os.path.basename(unwrapped[0])

    try:
        with open(config_path) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 1

    equivalences = config.get("commandEquivalences", {})
    if not equivalences:
        return 1

    # Build inverted lookup: {alias_tokens_tuple: canonical}
    # Sort longest-first for greedy matching
    lookup: list[tuple[list[str], str]] = []
    for canonical, aliases in equivalences.items():
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            try:
                alias_tokens = shlex.split(alias)
            except ValueError:
                continue
            if alias_tokens:
                lookup.append((alias_tokens, canonical))

    lookup.sort(key=lambda x: len(x[0]), reverse=True)

    for alias_tokens, canonical in lookup:
        if tokens_prefix_match(unwrapped, alias_tokens):
            print(canonical)
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
