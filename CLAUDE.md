# Clean Line

Permission hooks + profile management CLI for Claude Code.
Reduces prompt fatigue by auto-approving safe, predictable tool calls.

## Architecture

Two independent codepaths that share zero code:

```
Hooks (shell + Python helpers)          CLI (Python package)
  bash-gate.sh                            src/cleanline/cli.py
  approve-webfetch-domains.sh             src/cleanline/setup_cmd.py
  normalize-bash-cmd.py                   src/cleanline/profile_ops.py
  match-command-equiv.py                  src/cleanline/lockfile.py
  parse-url-host.py                       src/cleanline/schema.py
  bash_utils.py                           src/cleanline/fetch.py
  log_event.py                            src/cleanline/conflicts.py
                                          src/cleanline/suggest.py
                                          src/cleanline/audit.py
        |                                        |
        | reads                                  | reads/writes
        v                                        v
  permission-config.json (user rules)   profiles.lock.json (managed)
  profiles.lock.json (profile rules)    permission-config.json (generated)
  hook.jsonl (audit log, append)        hook.jsonl (audit log, read)
```

Hooks stay dumb -- they read config and make allow/defer decisions. CLI manages everything.

## Key Design Decisions

- **Fail-closed**: All hooks exit 0 silently on error (defer to normal permissions). Only explicit matches produce `{"decision":"allow"}`.
- **No chaining**: Each resolution layer resolves against the allow list independently. One level of indirection only.
- **Backward compat**: `match-command-equiv.py` reads both `commandMappings` (new) and `commandEquivalences` (legacy).
- **Zero external deps**: CLI uses only Python stdlib. No pip dependencies.
- **Atomic writes**: All config/lock file writes use temp-file-then-rename pattern.
- **Idempotent setup**: Running `setup` multiple times is safe -- detects existing registrations, skips unchanged files.
- **JSON-safe audit logging**: `log_event.py` uses `json.dumps()` instead of shell `printf` to handle special characters in commands.
- **Data-driven config**: Default domains (`known_domains.json`) and alias mappings (`known_aliases.json`) are data files, not hardcoded.

## Data Flow

### Bash Hook Pipeline (bash-gate.sh)

```
User runs command
  -> bash-gate.sh reads stdin JSON {"tool_input": {"command": "..."}}
  -> Step 1: REJECT metacharacters (pipes, &&, ||, ;, $(), etc.)
  -> Step 2: NORMALIZE binary via normalize-bash-cmd.py
             (handles env/timeout wrappers, extracts binary name)
  -> Step 3: ALIAS LOOKUP in permission-config.json, then lock file
             (python3.13 -> python)
  -> Step 4: COMMAND MAPPING in permission-config.json, then lock file
             (npx jest -> npm test)
  -> Check canonical against settings.json allow list
  -> Output {"decision":"allow"} or exit silently
  -> Append to hook.jsonl via log_event.py (subshell, non-blocking)
```

### WebFetch Hook Pipeline (approve-webfetch-domains.sh)

```
WebFetch call
  -> Read stdin JSON {"tool_input": {"url": "..."}}
  -> Extract hostname via parse-url-host.py
  -> Build domain list: sandbox.network.allowedDomains
                       + webfetch.extraDomains (config)
                       + merged.webfetch.extraDomains (lock file)
  -> Match: exact ("github.com") or wildcard ("*.docs.rs")
  -> Output {"decision":"allow"} or exit silently
```

## Module Reference

### CLI Modules (src/cleanline/)

| Module | Responsibility |
|--------|---------------|
| `cli.py` | Argument parsing, command dispatch, output formatting |
| `setup_cmd.py` | First-time onboarding: prereq checks, config generation, hook file copying, settings.json registration, interactive flow, uninstall |
| `profile_ops.py` | Profile CRUD: init, status, update, remove, dry-run |
| `lockfile.py` | Lock file read/write, profile merging (domain union, alias merge, mapping merge) |
| `schema.py` | Profile validation with hard caps (50 aliases, 30 mappings, 50 domains) |
| `fetch.py` | Profile fetching from `github:user/repo` or `local:path` sources |
| `conflicts.py` | Conflict detection: alias conflicts (same key, different canonical), mapping conflicts (same alias, different canonical) |
| `suggest.py` | Audit log analysis: version grouping (regex + known_aliases.json), domain grouping, confidence labels, apply suggestions |
| `tighten.py` | Audit-based decay analysis: stale rule detection, family context, profile suppression via overrides |
| `audit.py` | Audit log reader: JSONL parsing, decision summaries, provenance enrichment, `parse_rule()` for structured rule parsing |
| `known_aliases.json` | Curated alias table: python -> [python3, python3.10-3.14], cargo -> [cargo-clippy, cargo-fmt, cargo-watch], etc. |
| `known_domains.json` | Default documentation domains: *.w3.org, *.rust-lang.org, *.docs.rs, etc. |

### Hook Scripts (plugins/permission-hooks/hooks/)

| Script | Called by | Input | Output |
|--------|-----------|-------|--------|
| `bash-gate.sh` | PreToolUse(Bash) | stdin JSON | `{"decision":"allow"}` or silent exit |
| `approve-webfetch-domains.sh` | PreToolUse(WebFetch) | stdin JSON | `{"decision":"allow"}` or silent exit |
| `normalize-bash-cmd.py` | bash-gate.sh | argv[1]: command string | stdout: binary name |
| `match-command-equiv.py` | bash-gate.sh | argv[1]: command, argv[2]: config path, argv[3]?: root key | stdout: canonical command |
| `parse-url-host.py` | approve-webfetch-domains.sh | argv[1]: URL | stdout: lowercase hostname |
| `bash_utils.py` | imported by normalize + match | - | metacharacter detection, command unwrapping |
| `log_event.py` | bash-gate.sh, approve-webfetch-domains.sh | argv[1-4]: tool, input, decision, rule | appends JSONL to hook.jsonl |

### Config Files

| File | Location | Written by | Read by |
|------|----------|-----------|---------|
| `permission-config.json` | `~/.claude/hooks/` | `setup` command | Both hooks |
| `profiles.lock.json` | `~/.claude/hooks/` | `init/update/remove` commands | Both hooks (merged section only) |
| `hook.jsonl` | `~/.claude/hooks/` | log_event.py (via both hooks) | `status/suggest` commands |
| `settings.json` | `~/.claude/` | `setup --[un]install` (hook registration) | Hooks (allow list check) |

## Setup Flow

`setup` is the first command a user runs. It orchestrates:

1. **Prerequisites** -- Check jq, python3 >= 3.10, settings.json exist
2. **Scan allow list** -- Parse `Bash(python *)` entries from settings.json
3. **Generate config** -- Cross-reference canonicals against known_aliases.json
4. **Interactive summary** -- Show what will happen, prompt `[Y/n]`
5. **Copy hooks** -- 7 files to `~/.claude/hooks/` (MD5 skip if unchanged)
6. **Register hooks** -- Add PreToolUse entries to settings.json (backup first)
7. **Write config** -- permission-config.json with aliases + default domains from known_domains.json

Flags: `--yes` (skip confirmation), `--dry-run` (preview only), `--uninstall` (reverse all)

Hook identification: by command path (`~/.claude/hooks/bash-gate.sh`). No custom fields in settings.json.

Hook source discovery: `find_hook_source_dir()` walks up from the package to find `plugins/permission-hooks/hooks/`. Only works from repo checkout / editable install.

## Profile System

```
Profile A: python aliases          \
Profile B: rust commands            }-> Merge into "merged" section
User Config: custom domains        /
                                    |
                                    v
Lock File (profiles.lock.json):
  profiles: []            <- immutable source records
  merged: {}              <- what hooks actually read
```

Merge rules:
- **Domains**: Set union, order preserved, deduplicated
- **Aliases**: Dict merge, last profile wins on key conflict
- **Mappings**: Per-canonical alias list union

Conflicts are detected at install time but are warnings, not blockers.

### User Overrides

Users can suppress individual profile rules without removing the entire profile.
The mental model is git: profile = upstream, overrides = local commits.

```
Lock File (profiles.lock.json):
  profiles: []              <- immutable source records
  merged: {}                <- what hooks read (post-override)
  user_overrides:
    removed_rules: []       <- suppressed profile rules
```

`rebuild_merged()` applies overrides automatically after merging profiles.
`cleanline update` detects convergence: if an author removes a rule the user
already suppressed, the override is auto-cleaned (redundancy detection).

## Development Commands

```bash
uv run pytest tests/ -v              # Run all tests (160 tests)
cleanline --help                     # CLI help
cleanline setup --dry-run            # Preview setup without writing
cleanline setup --yes                # Full install, no prompts
cleanline setup --uninstall          # Remove hooks
cleanline status                     # View profiles + hook health
cleanline suggest --apply            # Apply suggestions interactively
cleanline tighten                    # Analyze stale rules
cleanline tighten --apply            # Remove/suppress stale rules
```

## When Making Changes

### Modifying hooks (bash/python)
- Test with `test_hooks_integration.py` -- runs actual hooks with crafted stdin
- Audit logging goes through `log_event.py` -- uses `json.dumps()` for proper escaping
- Always maintain fail-closed behavior
- `$HOOK_DIR` is resolved at runtime -- all helpers must be in the same directory

### Modifying CLI modules
- Each module has a corresponding `tests/test_<module>.py`
- Profile operations use `tmp_path` fixtures -- never write to real `~/.claude/`
- Mock `find_settings_path()` in tests that would touch real settings.json
- Mock `lockfile.get_lockfile_path()` in profile operation tests

### Modifying setup_cmd.py
- `run_setup()` has an `interactive` flag -- set to `False` in tests
- `register_hooks()` is idempotent but creates a backup -- tests should use tmp_path
- Tests that call `run_setup` without mocking `find_settings_path` will try to modify real settings

### Adding new config keys
- Add to `permission-config.json` default template
- Add to `lockfile.merge_profiles()` merge logic
- Update `schema.validate_profile()` with caps and validation
- Add conflict detection in `conflicts.py` if the key allows conflicts
- Update both hooks to read the new key

### Adding new hook scripts
- Add filename to `HOOK_FILES` list in `setup_cmd.py`
- Add registration entry to `HOOK_ENTRIES` in `setup_cmd.py`
- Add `_is_our_hook()` detection pattern if the new hook has a unique path
- Add integration test in `test_hooks_integration.py`

### Adding new data files
- Place in `src/cleanline/` alongside `known_aliases.json` and `known_domains.json`
- Load via `pkg_files("cleanline").joinpath("filename.json")`
- Add a `_load_*()` helper function

## Test Coverage

| Module | Test File | Key tests |
|--------|-----------|-----------|
| setup_cmd | test_setup.py | Prereqs, copy (with MD5 skip), register (idempotent), unregister, health check, full flow, idempotent second run |
| lockfile | test_lockfile.py | Read/write roundtrip, merge strategies, add/remove profiles, override CRUD, apply_overrides, redundancy detection |
| schema | test_schema.py | Validation caps, warn thresholds, type checking |
| audit | test_audit.py | JSONL parsing, summarize, top rules, provenance enrichment, parse_rule (all 5 types + rsplit + unknown) |
| fetch | test_fetch.py | GitHub URL parsing, local fetch, error handling |
| conflicts | test_conflicts.py | Alias conflicts, mapping conflicts, no-conflict dedup |
| suggest | test_suggest.py | Version grouping (regex + known_aliases), domain grouping, confidence labels, sorting, min_count, apply suggestions (add/cancel/dedup) |
| tighten | test_tighten.py | Usage map, stale detection (user + profile), timestamp logic, insufficient data, family context, apply user/profile, CLI gate (--force) |
| profile_ops | test_profile_ops.py | Init, status, update, remove, dry-run, update reconciliation (redundant/valid overrides) |
| bash_utils | test_bash_utils.py | normalize-bash-cmd.py and match-command-equiv.py subprocess tests |
| hooks | test_hooks_integration.py | Full hook execution, audit log JSON escaping, no-chaining transitive aliases |
