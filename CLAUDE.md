# Clean Line

Permission hooks + profile management CLI for Claude Code.
Reduces prompt fatigue by auto-approving safe, predictable tool calls.

## Architecture

Two independent codepaths — hooks (fast, single-process) and CLI (management):

```
Plugin (bash dispatcher + Python)      CLI (Python package)
  scripts/bash-gate.sh (thin)            src/cleanline/cli.py
  scripts/resolve.py (single entry)      src/cleanline/setup_cmd.py
  scripts/approve-webfetch-domains.sh    src/cleanline/profile_ops.py
  scripts/resolve_webfetch.py            src/cleanline/lockfile.py
  scripts/default-config.json            src/cleanline/schema.py
                                         src/cleanline/suggest.py
                                         src/cleanline/tighten.py
                                         src/cleanline/audit.py
        |                                        |
        | reads (ONE file)                       | reads/writes
        v                                        v
  permission-config.json               profiles.lock.json (user_config + profiles)
  hook.jsonl (audit log, append)        permission-config.json (generated output)
                                        hook.jsonl (audit log, read)
```

Hooks read **only** `permission-config.json` (not the lockfile). CLI generates `permission-config.json` by merging user_config + profile rules from the lockfile.

## Key Design Decisions

- **Fail-closed**: All hooks exit silently on error (defer to normal permissions). Only explicit matches produce `{"decision":"allow"}`.
- **Single Python entry point**: bash-gate.sh → resolve.py (one interpreter startup). No multi-subprocess pipeline.
- **No chaining**: One level of alias indirection only. If an alias resolves to a value that is itself an alias, the second lookup is skipped.
- **&& and ; splitting**: Commands chained with `&&` or `;` are split (via shlex, respects quoting) and each sub-command resolved independently. Pipes, backticks, `$(`, `>(`, `<(` are rejected.
- **resolvedCanonicals baked into config**: Hooks don't read settings.json at runtime. CLI pre-computes canonical commands and writes them into permission-config.json.
- **Plugin-only deployment**: Hooks are registered via plugin install (`hooks.json`). CLI only generates `permission-config.json`, no file copying or settings.json registration.
- **Zero external deps**: CLI uses only Python stdlib. No pip dependencies.
- **Atomic writes**: All config/lock file writes use temp-file-then-rename pattern.
- **Data-driven config**: Default domains (`known_domains.json`) and alias mappings (`known_aliases.json`) are data files, not hardcoded.

## Data Flow

### Bash Hook Pipeline (resolve.py)

```
User runs command
  → bash-gate.sh reads stdin, forwards to resolve.py
  → resolve.py loads permission-config.json (first-run: copy defaults + scan settings.json)
  → REJECT dangerous metacharacters (|, backticks, $(), >(), <()
  → SPLIT on && and ; via shlex tokenizer (cap 5 sub-commands)
  → For each sub-command:
      1. NORMALIZE binary (shlex, strip path, unwrap env/timeout)
      2. DIRECT CANONICAL check (binary in resolvedCanonicals?)
      3. ALIAS LOOKUP in bashAliases (python3.13 → python)
      4. COMMAND MAPPING longest-prefix match (npx jest → npm test)
      5. Check resolved canonical against resolvedCanonicals
  → ALL sub-commands must resolve → {"decision":"allow"}
  → Any failure → silent exit (passthrough)
  → Append to hook.jsonl (audit log)
```

### WebFetch Hook Pipeline (resolve_webfetch.py)

```
WebFetch call
  → approve-webfetch-domains.sh reads stdin, forwards to resolve_webfetch.py
  → Parse hostname via urllib.parse (reject IPs, fail closed)
  → Check against webfetch.extraDomains in permission-config.json
  → Match: exact ("github.com") or wildcard ("*.docs.rs")
  → Output {"decision":"allow"} or exit silently
  → Append to hook.jsonl
```

### Config Generation (CLI → Hooks)

```
user_config (lockfile)          profile rules (lockfile merged)
       \                              /
        → lockfile.write_permission_config() →
                  permission-config.json
                  (bashAliases + commandMappings + webfetch.extraDomains
                   + resolvedCanonicals)
```

User aliases take priority over profile aliases on key conflict.

## Module Reference

### CLI Modules (src/cleanline/)

| Module | Responsibility |
|--------|---------------|
| `cli.py` | Argument parsing, command dispatch, output formatting |
| `setup_cmd.py` | First-time onboarding: scan settings.json allow list, generate permission-config.json with resolvedCanonicals, save user_config to lockfile |
| `profile_ops.py` | Profile CRUD: init, status, update, remove, dry-run. Regenerates permission-config.json after mutations |
| `lockfile.py` | Lock file read/write, profile merging, user_config storage, `write_permission_config()` for generating the merged output |
| `schema.py` | Profile validation with hard caps (50 aliases, 30 mappings, 50 domains) |
| `fetch.py` | Profile fetching from `github:user/repo` or `local:path` sources |
| `conflicts.py` | Conflict detection: alias conflicts (same key, different canonical), mapping conflicts |
| `suggest.py` | Audit log analysis: version grouping (regex + known_aliases.json), domain grouping, confidence labels, apply to lockfile user_config |
| `tighten.py` | Audit-based decay analysis: stale rule detection, family context, remove from lockfile user_config or suppress via overrides |
| `audit.py` | Audit log reader: JSONL parsing, decision summaries, provenance enrichment, log rotation |
| `known_aliases.json` | Curated alias table: python → [python3, python3.10-3.14], cargo → [cargo-clippy, cargo-fmt, cargo-watch], etc. |
| `known_domains.json` | Default documentation domains: *.w3.org, *.rust-lang.org, *.docs.rs, etc. |

### Plugin Scripts (plugins/clean-line/scripts/)

| Script | Purpose |
|--------|---------|
| `bash-gate.sh` | Thin 5-line dispatcher: reads stdin, forwards to resolve.py |
| `resolve.py` | Single Python entry point for Bash hooks. All resolution logic in one process |
| `approve-webfetch-domains.sh` | Thin dispatcher: reads stdin, forwards to resolve_webfetch.py |
| `resolve_webfetch.py` | Single Python entry point for WebFetch hooks |
| `default-config.json` | Static defaults (common aliases, domains). Copied on first run |

### Config Files

| File | Location | Written by | Read by |
|------|----------|-----------|---------|
| `permission-config.json` | `~/.claude/hooks/` | CLI (`setup`, `write_permission_config`) | Both hooks |
| `profiles.lock.json` | `~/.claude/hooks/` | CLI (all mutation commands) | CLI only |
| `hook.jsonl` | `~/.claude/hooks/` | resolve.py / resolve_webfetch.py | CLI (`status/suggest/tighten`) |

## Setup Flow

`setup` generates the permission-config.json. Plugin installation is separate (`/plugin install`).

1. **Prerequisites** — Check python3 >= 3.10
2. **Scan allow list** — Parse `Bash(python *)` entries from `~/.claude/settings.json`
3. **Generate config** — Cross-reference canonicals against known_aliases.json, include resolvedCanonicals
4. **Interactive summary** — Show what will happen, prompt `[Y/n]`
5. **Write config** — permission-config.json to `~/.claude/hooks/`
6. **Save user_config** — Write to lockfile for future mutations by suggest/tighten

Flags: `--yes` (skip confirmation), `--dry-run` (preview only)

## Profile System

```
Profile A: python aliases          \
Profile B: rust commands            }→ Merge into "merged" section
User Config: custom aliases/domains /
                                    |
                                    v
Lock File (profiles.lock.json):
  profiles: []            ← immutable source records
  merged: {}              ← merged profile rules
  user_config: {}         ← user's aliases, domains, mappings
  user_overrides: {}      ← suppressed profile rules
                                    |
                                    v (write_permission_config)
  permission-config.json  ← what hooks actually read
```

Merge rules:
- **Domains**: Set union, deduplicated
- **Aliases**: Dict merge. User aliases override profile aliases on conflict
- **Mappings**: Per-canonical alias list union
- **resolvedCanonicals**: Computed from settings.json at config generation time

### User Overrides

Users can suppress individual profile rules without removing the entire profile.

`rebuild_merged()` applies overrides automatically after merging profiles.
`cleanline update` detects convergence: if an author removes a rule the user
already suppressed, the override is auto-cleaned (redundancy detection).

## Development Commands

```bash
uv run python -m pytest tests/ -v   # Run all tests (200+ tests)
cleanline --help                     # CLI help
cleanline setup --dry-run            # Preview setup without writing
cleanline setup --yes                # Full setup, no prompts
cleanline status                     # View profiles + audit summary
cleanline suggest --apply            # Apply suggestions interactively
cleanline tighten                    # Analyze stale rules
cleanline tighten --apply            # Remove/suppress stale rules
```

## When Making Changes

### Modifying hooks (resolve.py / resolve_webfetch.py)
- Test with `test_hooks_integration.py` — runs actual shell dispatchers with crafted stdin
- Test internals with `test_resolve.py` — unit tests for all resolution functions
- Always maintain fail-closed behavior (silent exit on any error)
- All helpers live in the same script — no subprocess calls within resolve.py

### Modifying CLI modules
- Each module has a corresponding `tests/test_<module>.py`
- Profile operations use `tmp_path` fixtures — never write to real `~/.claude/`
- Mock `find_settings_path()` in tests that would touch real settings.json
- Mock `lockfile.get_lockfile_path()` in tests that write to lockfile
- After any lockfile mutation, call `write_permission_config()` to regenerate

### Modifying setup_cmd.py
- `run_setup()` has an `interactive` flag — set to `False` in tests
- Tests that call `run_setup` must mock both `find_settings_path` and `get_lockfile_path`

### Adding new config keys
- Add to `default-config.json` (plugin defaults)
- Add to `lockfile.write_permission_config()` merge logic
- Add to `lockfile.merge_profiles()` if profiles can contribute
- Update `schema.validate_profile()` with caps and validation
- Update resolve.py / resolve_webfetch.py to read the new key

### Adding new data files
- Place in `src/cleanline/` alongside `known_aliases.json` and `known_domains.json`
- Load via `pkg_files("cleanline").joinpath("filename.json")`
- Add a `_load_*()` helper function

## Test Coverage

| Module | Test File | Key tests |
|--------|-----------|-----------|
| resolve.py | test_resolve.py | Metacharacter detection, chain splitting, binary normalization, alias/mapping/direct-canonical resolution, no-chaining invariant, audit logging, first-run config, hostname parsing, domain matching |
| hooks integration | test_hooks_integration.py | Full hook execution via shell dispatchers, alias/mapping/chain/pipe/env/path tests, audit log escaping, first-run, shlex errors |
| setup_cmd | test_setup.py | Canonicals extraction, alias generation, config with resolvedCanonicals, full flow, user_config to lockfile |
| lockfile | test_lockfile.py | Read/write roundtrip, merge strategies, add/remove profiles, overrides, user_config, write_permission_config |
| schema | test_schema.py | Validation caps, warn thresholds, type checking |
| audit | test_audit.py | JSONL parsing, summarize, top rules, provenance enrichment, parse_rule |
| fetch | test_fetch.py | GitHub URL parsing, local fetch, error handling |
| conflicts | test_conflicts.py | Alias conflicts, mapping conflicts, no-conflict dedup |
| suggest | test_suggest.py | Version grouping, domain grouping, confidence labels, sorting, min_count, apply to lockfile user_config |
| tighten | test_tighten.py | Usage map, stale detection, family context, apply user/profile via lockfile, CLI gate (--force) |
| profile_ops | test_profile_ops.py | Init, status, update, remove, dry-run, override reconciliation |
