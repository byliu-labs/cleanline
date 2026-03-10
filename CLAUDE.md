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
  scripts/approve-fileops.sh             src/cleanline/schema.py
  scripts/resolve_fileops.py             src/cleanline/suggest.py
  scripts/default-config.json            src/cleanline/tighten.py
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
- **Trust tiers**: Three tiers (cautious/balanced/flow) control what `cleanline setup` generates. Tiers are metadata, not enforcement — they set starting points for domains, file paths, command mappings, and suggest/tighten thresholds. Defined in `tiers.py` as pure Python constants.
- **Data-driven config**: Default domains (`known_domains.json`) and alias mappings (`known_aliases.json`) are data files, not hardcoded. Tier-specific domain additions in `known_domains_balanced.json` and `known_domains_flow.json`.
- **Hardcoded deny list**: File access hook has 13 deny patterns (ssh, gnupg, aws, .env, etc.) in a Python constant that cannot be overridden by config.
- **Symlink resolution**: File paths are resolved via `Path.resolve()` before deny matching, preventing symlink-based bypass.
- **Read/write separation**: Read operations (Read, Glob, Grep) check `readPaths`; write operations (Edit, Write) check `writePaths`. Reading a path does not grant write access.
- **Profile writePaths opt-in**: Profile `readPaths` merge automatically; `writePaths` require explicit user acceptance (stored in `pendingWritePaths` until accepted).

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

### File Ops Hook Pipeline (resolve_fileops.py)

```
Read/Edit/Write/Glob/Grep call
  → approve-fileops.sh reads stdin, forwards to resolve_fileops.py
  → Extract path: file_path (Read/Edit/Write) or path (Glob/Grep, default: cwd)
  → Normalize: expand ~, resolve symlinks via Path.resolve(), make absolute
  → DENY check: hardcoded deny list first (ssh, gnupg, aws, .env, etc.)
  → DENY check: config denyPaths second
  → ALLOW check: readPaths (Read/Glob/Grep) or writePaths (Edit/Write)
  → Output {"decision":"allow"} or exit silently
  → Append to hook.jsonl with read:/write:/deny: prefixed rules
```

Hardcoded deny (not config-overridable):
`~/.ssh/**`, `~/.gnupg/**`, `~/.aws/**`, `~/.netrc`, `~/.claude/credentials*`,
`~/.password-store/**`, `~/.local/share/keyrings/**`, `~/.kube/**`, `~/.docker/**`,
`**/.env`, `**/.env.*`, `**/.git/config`

Pattern matching:
- `~/.claude/**` → recursive match with symlink-resolved prefix
- `**/.env` → filename-component match at any depth
- `**/.git/config` → multi-component suffix match
- Exact paths resolved through symlinks before comparison

### Config Generation (CLI → Hooks)

```
user_config (lockfile)          profile rules (lockfile merged)
       \                              /
        → lockfile.write_permission_config() →
                  permission-config.json
                  (cleanlineTier + bashAliases + commandMappings
                   + webfetch.extraDomains + fileAccess + resolvedCanonicals)
```

User aliases take priority over profile aliases on key conflict.

## Module Reference

### CLI Modules (src/cleanline/)

| Module | Responsibility |
|--------|---------------|
| `tiers.py` | Trust tier definitions: `VALID_TIERS`, `TIER_ORDER`, `TIER_DEFAULTS` (domains, paths, mappings, suggest/tighten thresholds). Pure constants, no I/O |
| `cli.py` | Argument parsing, command dispatch, output formatting. Reads tier from lockfile for suggest/tighten defaults |
| `setup_cmd.py` | First-time onboarding: scan settings.json allow list, generate tier-parameterized permission-config.json with resolvedCanonicals, save user_config (incl. tier) to lockfile |
| `profile_ops.py` | Profile CRUD: init, status, update, remove, dry-run. Regenerates permission-config.json after mutations |
| `lockfile.py` | Lock file read/write, profile merging, user_config storage, `write_permission_config()` for generating the merged output |
| `schema.py` | Profile validation with hard caps (50 aliases, 30 mappings, 50 domains, 50 file paths) |
| `fetch.py` | Profile fetching from `github:user/repo` or `local:path` sources |
| `conflicts.py` | Conflict detection: alias conflicts (same key, different canonical), mapping conflicts |
| `suggest.py` | Audit log analysis: version grouping (regex + known_aliases.json), domain grouping, file path grouping, tier-aware confidence labels and min_count thresholds, apply to lockfile user_config |
| `tighten.py` | Audit-based decay analysis: stale rule detection (aliases, mappings, domains, file paths), family context, tier-aware staleness windows (14/30/60 days), remove from lockfile user_config or suppress via overrides |
| `audit.py` | Audit log reader: JSONL parsing, decision summaries, provenance enrichment, log rotation. Parses `read:`, `write:`, `deny:` rule prefixes |
| `known_aliases.json` | Curated alias table: python → [python3, python3.10-3.14], cargo → [cargo-clippy, cargo-fmt, cargo-watch], etc. |
| `known_domains.json` | Cautious-tier documentation domains: *.w3.org, *.rust-lang.org, *.docs.rs, etc. |
| `known_domains_balanced.json` | Additional balanced-tier domains: *.stackoverflow.com, *.npmjs.com, *.mozilla.org, etc. |
| `known_domains_flow.json` | Additional flow-tier domains: *.medium.com, *.dev.to, *.arxiv.org |
| `known_file_paths.json` | Legacy file access paths (superseded by tier table in `tiers.py` for setup) |

### Plugin Scripts (plugins/clean-line/scripts/)

| Script | Purpose |
|--------|---------|
| `bash-gate.sh` | Thin 5-line dispatcher: reads stdin, forwards to resolve.py |
| `resolve.py` | Single Python entry point for Bash hooks. All resolution logic in one process |
| `approve-webfetch-domains.sh` | Thin dispatcher: reads stdin, forwards to resolve_webfetch.py |
| `resolve_webfetch.py` | Single Python entry point for WebFetch hooks |
| `approve-fileops.sh` | Thin dispatcher: reads stdin, forwards to resolve_fileops.py |
| `resolve_fileops.py` | Single Python entry point for file ops hooks (Read/Edit/Write/Glob/Grep). Hardcoded deny list + configurable allow paths |
| `default-config.json` | Static defaults (common aliases, domains, file paths). Copied on first run |

### Config Files

| File | Location | Written by | Read by |
|------|----------|-----------|---------|
| `permission-config.json` | `~/.claude/hooks/` | CLI (`setup`, `write_permission_config`) | All three hooks |
| `profiles.lock.json` | `~/.claude/hooks/` | CLI (all mutation commands) | CLI only |
| `hook.jsonl` | `~/.claude/hooks/` | resolve.py / resolve_webfetch.py / resolve_fileops.py | CLI (`status/suggest/tighten`) |

## Setup Flow

`setup` generates the permission-config.json. Plugin installation is separate (`/plugin install`).

1. **Prerequisites** — Check python3 >= 3.10
2. **Tier selection** — `--tier cautious|balanced|flow` (default: balanced). Determines domains, file paths, command mappings, and suggest/tighten thresholds
3. **Scan allow list** — Parse `Bash(python *)` and `Read(path)`/`Edit(path)` entries from `~/.claude/settings.json`
4. **Generate config** — Cross-reference canonicals against known_aliases.json, load tier-appropriate domains (cumulative: flow includes balanced + cautious), include resolvedCanonicals + fileAccess from tier table
5. **Interactive summary** — Show what will happen, prompt `[Y/n]`
6. **Write config** — permission-config.json (with `cleanlineTier` field) to `~/.claude/hooks/`
7. **Save user_config** — Write to lockfile (with `tier` field) for future mutations by suggest/tighten

Flags: `--tier <name>`, `--yes` (skip confirmation), `--dry-run` (preview only)

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
  user_config: {}         ← user's aliases, domains, mappings, tier
  user_overrides: {}      ← suppressed profile rules
                                    |
                                    v (write_permission_config)
  permission-config.json  ← what hooks actually read
```

Merge rules:
- **Domains**: Set union, deduplicated
- **Aliases**: Dict merge. User aliases override profile aliases on conflict
- **Mappings**: Per-canonical alias list union
- **File access readPaths**: Set union across profiles + user_config
- **File access writePaths**: User-only (profile writePaths require explicit opt-in, stored in `pendingWritePaths`)
- **resolvedCanonicals**: Computed from settings.json at config generation time

### User Overrides

Users can suppress individual profile rules without removing the entire profile.

`rebuild_merged()` applies overrides automatically after merging profiles.
`cleanline update` detects convergence: if an author removes a rule the user
already suppressed, the override is auto-cleaned (redundancy detection).

## Development Commands

```bash
uv run python -m pytest tests/ -v   # Run all tests (330+ tests)
cleanline --help                     # CLI help
cleanline setup --dry-run            # Preview setup without writing
cleanline setup --tier flow --yes    # Setup with flow tier, no prompts
cleanline setup --yes                # Full setup, balanced (default), no prompts
cleanline status                     # View profiles + tier + audit summary
cleanline suggest --apply            # Apply suggestions (tier-aware thresholds)
cleanline tighten                    # Analyze stale rules (tier-aware staleness)
cleanline tighten --apply            # Remove/suppress stale rules
```

## When Making Changes

### Modifying hooks (resolve.py / resolve_webfetch.py / resolve_fileops.py)
- Test with `test_hooks_integration.py` — runs actual shell dispatchers with crafted stdin
- Test internals with `test_resolve.py` / `test_resolve_fileops.py` — unit tests for all resolution functions
- Always maintain fail-closed behavior (silent exit on any error)
- All helpers live in the same script — no subprocess calls within resolve scripts
- For file ops: never bypass the hardcoded deny list; always resolve symlinks before matching

### Modifying CLI modules
- Each module has a corresponding `tests/test_<module>.py`
- Profile operations use `tmp_path` fixtures — never write to real `~/.claude/`
- Mock `find_settings_path()` in tests that would touch real settings.json
- Mock `lockfile.get_lockfile_path()` in tests that write to lockfile
- After any lockfile mutation, call `write_permission_config()` to regenerate

### Modifying setup_cmd.py
- `run_setup()` has an `interactive` flag — set to `False` in tests
- Tests that call `run_setup` must mock both `find_settings_path` and `get_lockfile_path`

### Modifying tiers (tiers.py)
- `tiers.py` is pure constants — no I/O, no imports beyond `__future__`
- Adding a new tier: add to `VALID_TIERS`, `TIER_ORDER`, and `TIER_DEFAULTS`
- Changing thresholds: update `TIER_DEFAULTS` entries, run `test_tiers.py` (invariant tests check ordering)
- Adding new tier-varying parameters: add to all three tier dicts, update consumers (`setup_cmd.py`, `suggest.py`, `cli.py`)
- Domain files are cumulative: `_load_domains_for_tier("flow")` loads cautious + balanced + flow domains

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
| resolve_fileops.py | test_resolve_fileops.py | Path normalization, extraction, pattern matching, .env recursive denial, symlink resolution, hardcoded deny, check_access, audit logging |
| hooks integration | test_hooks_integration.py | Full hook execution via shell dispatchers, alias/mapping/chain/pipe/env/path tests, audit log escaping, first-run, shlex errors, file ops (read/write/deny/symlink) |
| tiers | test_tiers.py | Tier definitions, ordering invariants, threshold relationships across tiers, get_tier_config, validate_tier |
| setup_cmd | test_setup.py | Canonicals extraction, alias generation, file path extraction, config with resolvedCanonicals + fileAccess, full flow, user_config to lockfile, tier parameterization (cautious/balanced/flow config generation, domain cumulation) |
| lockfile | test_lockfile.py | Read/write roundtrip, merge strategies, add/remove profiles, overrides, user_config, write_permission_config, fileAccess merging, get_tier helper |
| schema | test_schema.py | Validation caps, warn thresholds, type checking, fileAccess validation, meta.recommendedTier validation |
| audit | test_audit.py | JSONL parsing, summarize, top rules, provenance enrichment, parse_rule (alias/mapping/domain/read/write/deny) |
| fetch | test_fetch.py | GitHub URL parsing, local fetch, error handling |
| conflicts | test_conflicts.py | Alias conflicts, mapping conflicts, no-conflict dedup |
| suggest | test_suggest.py | Version grouping, domain grouping, file path grouping, tier-aware confidence labels and min_count, sorting, apply to lockfile user_config (aliases + domains + read paths) |
| tighten | test_tighten.py | Usage map (aliases/mappings/domains/read/write paths), stale detection, family context, apply user/profile via lockfile, file path removal, CLI gate (--force) |
| profile_ops | test_profile_ops.py | Init, status, update, remove, dry-run, override reconciliation, tier compatibility warnings |
