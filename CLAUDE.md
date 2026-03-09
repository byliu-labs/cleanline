# claude-code-hooks

Permission hooks + profile management CLI for Claude Code.

## Architecture

Two independent codepaths that share zero code:

```
Hooks (shell + Python helpers)          CLI (Python package)
  bash-gate.sh                            src/claude_hooks/cli.py
  approve-webfetch-domains.sh             src/claude_hooks/setup_cmd.py
  normalize-bash-cmd.py                   src/claude_hooks/profile_ops.py
  match-command-equiv.py                  src/claude_hooks/lockfile.py
  bash_utils.py                           src/claude_hooks/schema.py
                                          src/claude_hooks/fetch.py
                                          src/claude_hooks/conflicts.py
                                          src/claude_hooks/suggest.py
                                          src/claude_hooks/audit.py
        │                                        │
        │ reads                                   │ reads/writes
        ▼                                        ▼
  permission-config.json (user)         profiles.lock.json (managed)
  profiles.lock.json (profiles)         permission-config.json (setup)
  hook.jsonl (audit log, append)        hook.jsonl (audit log, read)
```

Hooks stay dumb — they read config and make allow/defer decisions. CLI manages everything.

## Key Design Decisions

- **Fail-closed**: All hooks exit 0 silently on error (defer to normal permissions). Only explicit matches produce `{"decision":"allow"}`.
- **No chaining**: Each resolution layer resolves against the allow list independently. One level of indirection only.
- **Backward compat**: `match-command-equiv.py` reads both `commandMappings` (new) and `commandEquivalences` (legacy).
- **Zero external deps**: CLI uses only Python stdlib. No pip dependencies.
- **Atomic writes**: Lock file writes use temp-file-then-rename pattern.

## Data Flow

```
User runs command
  → bash-gate.sh reads stdin JSON
  → Step 1: reject metacharacters (pipes, &&, etc.)
  → Step 2: normalize binary (python3.13 → python3.13)
  → Step 3: alias lookup in config, then lock file (python3.13 → python)
  → Step 4: command mapping in config, then lock file (npx jest → npm test)
  → Check canonical against settings.json allow list
  → Output {"decision":"allow"} or exit silently
  → Append to hook.jsonl audit log
```

## Development Commands

```bash
uv run pytest tests/ -v              # Run all tests
uv run claude-hooks --help           # CLI help
uv run claude-hooks setup --dry-run  # Preview setup without writing
uv run claude-hooks status           # View installed profiles
```

## When Making Changes

### Modifying hooks (bash/python)
- Test with `test_hooks_integration.py` — runs actual hooks with crafted stdin
- Audit logging must not block the hook (subshell + `|| true`)
- Always maintain fail-closed behavior

### Modifying CLI modules
- Each module has a corresponding `tests/test_<module>.py`
- Profile operations use `tmp_path` fixtures — never write to real `~/.claude/`
- Mock `lockfile.get_lockfile_path()` in integration tests

### Adding new config keys
- Add to both `permission-config.json` schema and `lockfile.merge_profiles()`
- Update `schema.validate_profile()` with caps
- Add conflict detection in `conflicts.py` if applicable
