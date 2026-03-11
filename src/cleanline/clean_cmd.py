"""Allow list consolidation: find redundant entries, propose wildcards.

Scans settings.json Bash(...) entries and identifies:
  - Redundant: specific entries covered by existing wildcards
  - Consolidations: groups of specific entries → narrowest wildcard
  - Clean Line handled: entries also covered by aliases (informational)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Captures everything inside Bash(...)
_BASH_ENTRY = re.compile(r"^Bash\((.+)\)$")


def _parse_bash_entry(entry: str) -> dict | None:
    """Parse a Bash(...) allow list entry.

    Returns {"content": str, "is_wildcard": bool, "prefix": str} or None.
    Wildcard: Bash(git *) → content="git *", is_wildcard=True, prefix="git"
    Specific: Bash(git log) → content="git log", is_wildcard=False, prefix="git log"
    """
    m = _BASH_ENTRY.match(entry)
    if not m:
        return None
    content = m.group(1)
    if content.endswith(" *"):
        return {"content": content, "is_wildcard": True, "prefix": content[:-2]}
    return {"content": content, "is_wildcard": False, "prefix": content}


def _word_lcp(strings: list[str]) -> str:
    """Longest common word-level prefix."""
    if not strings:
        return ""
    words_list = [s.split() for s in strings]
    prefix: list[str] = []
    for i, word in enumerate(words_list[0]):
        if all(len(w) > i and w[i] == word for w in words_list):
            prefix.append(word)
        else:
            break
    return " ".join(prefix)


def find_redundant_entries(allow_list: list[str]) -> list[dict]:
    """Entries covered by existing wildcards in the same list.

    e.g., Bash(git -C /foo log) redundant when Bash(git *) exists.
    Returns [{"entry": str, "covered_by": str}]
    """
    wildcards: list[tuple[str, str]] = []  # (prefix, original_entry)
    for entry in allow_list:
        parsed = _parse_bash_entry(entry)
        if parsed and parsed["is_wildcard"]:
            wildcards.append((parsed["prefix"], entry))

    redundant: list[dict] = []
    for entry in allow_list:
        parsed = _parse_bash_entry(entry)
        if not parsed or parsed["is_wildcard"]:
            continue
        content = parsed["content"]
        for wc_prefix, wc_entry in wildcards:
            if content == wc_prefix or content.startswith(wc_prefix + " "):
                redundant.append({"entry": entry, "covered_by": wc_entry})
                break

    return redundant


def find_cleanline_handled(allow_list: list[str], config: dict) -> list[dict]:
    """Entries also covered by Clean Line aliases. INFORMATIONAL ONLY.

    Detects Bash(cmd ...) where cmd is an alias key mapping to a canonical,
    and Bash(canonical *) already exists in the allow list.
    Returns [{"entry": str, "alias": str, "canonical_entry": str}]
    """
    aliases = config.get("bashAliases", {})
    if not aliases:
        return []

    # Build {cmd: entry_string} for wildcard entries
    wildcard_cmds: dict[str, str] = {}
    for entry in allow_list:
        parsed = _parse_bash_entry(entry)
        if parsed and parsed["is_wildcard"]:
            wildcard_cmds[parsed["prefix"]] = entry

    handled: list[dict] = []
    for entry in allow_list:
        parsed = _parse_bash_entry(entry)
        if not parsed:
            continue
        cmd = parsed["content"].split()[0]
        if cmd not in aliases:
            continue
        canonical = aliases[cmd]
        if canonical in wildcard_cmds:
            handled.append({
                "entry": entry,
                "alias": f"{cmd} -> {canonical}",
                "canonical_entry": wildcard_cmds[canonical],
            })

    return handled


def find_consolidations(
    allow_list: list[str],
    min_group: int = 3,
) -> list[dict]:
    """Groups of specific entries → narrowest wildcard.

    e.g., docker compose up/down/build → Bash(docker compose *)
    Returns [{"entries": list[str], "proposed": str, "saves": int}]
    """
    # Collect wildcard prefixes for skip-if-covered check
    wildcard_prefixes: set[str] = set()
    for entry in allow_list:
        parsed = _parse_bash_entry(entry)
        if parsed and parsed["is_wildcard"]:
            wildcard_prefixes.add(parsed["prefix"])

    # Group non-wildcard entries (not covered) by first word
    groups: dict[str, list[tuple[str, str]]] = {}
    for entry in allow_list:
        parsed = _parse_bash_entry(entry)
        if not parsed or parsed["is_wildcard"]:
            continue
        content = parsed["content"]
        covered = any(
            content == wp or content.startswith(wp + " ")
            for wp in wildcard_prefixes
        )
        if covered:
            continue
        first_word = content.split()[0]
        groups.setdefault(first_word, []).append((content, entry))

    consolidations: list[dict] = []
    for _binary, entries in sorted(groups.items()):
        if len(entries) < min_group:
            continue
        contents = [e[0] for e in entries]
        original_entries = [e[1] for e in entries]
        prefix = _word_lcp(contents)
        if not prefix:
            continue
        consolidations.append({
            "entries": original_entries,
            "proposed": f"Bash({prefix} *)",
            "saves": len(entries),
        })

    return consolidations


def analyze_allow_list(
    allow_list: list[str],
    config: dict,
) -> dict:
    """Analyze allow list for redundancies and consolidation opportunities.

    Pure analysis — no I/O, no side effects.
    Returns {"redundant": [...], "consolidations": [...], "handled": [...]}
    """
    return {
        "redundant": find_redundant_entries(allow_list),
        "consolidations": find_consolidations(allow_list),
        "handled": find_cleanline_handled(allow_list, config),
    }


def apply_clean(
    settings_path: Path,
    redundant: list[dict],
    consolidations: list[dict],
) -> dict:
    """Remove redundant entries and add wildcard consolidations.

    Reads settings.json, applies changes, writes atomically.
    Returns {"removed": int, "consolidated": int, "added": int, "actions": [...]}
    """
    with open(settings_path) as f:
        settings = json.load(f)

    allow_list = settings.get("permissions", {}).get("allow", [])

    to_remove: set[str] = {r["entry"] for r in redundant}
    for c in consolidations:
        to_remove.update(c["entries"])

    new_allow = [e for e in allow_list if e not in to_remove]
    for c in consolidations:
        new_allow.append(c["proposed"])

    settings.setdefault("permissions", {})["allow"] = new_allow

    # Atomic write: tmp file + rename
    tmp = settings_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    tmp.rename(settings_path)

    removed = len(redundant)
    consolidated = sum(len(c["entries"]) for c in consolidations)
    added = len(consolidations)

    actions: list[str] = []
    if removed:
        actions.append(f"Removed {removed} redundant entries")
    if consolidated:
        actions.append(
            f"Consolidated {consolidated} entries into {added} wildcards"
        )

    return {
        "removed": removed, "consolidated": consolidated,
        "added": added, "actions": actions,
    }
