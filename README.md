# Clean Line

Take the clean line. Permission hooks that reduce Claude Code prompt fatigue, with a CLI for managing composable community profiles.

## The Problem

Claude Code asks for permission on every tool call. If `python` is in your allow list but you run `python3.13`, you get prompted. If `npm test` is allowed but Claude runs `npx jest`, you get prompted. If Claude chains `python3 a.py && echo done`, you get prompted even though both are safe.

These hooks auto-approve safe, predictable operations so you can stay in flow.

## What It Does

- **Bash alias resolution**: `python3.13 script.py` auto-approved when `python` is in your allow list
- **&& and ; chain splitting**: `python3 a.py && echo done` splits into sub-commands and resolves each independently
- **Multi-word command mapping**: `npx jest --coverage` auto-approved when `npm test` is allowed
- **WebFetch domain matching**: Documentation sites auto-approved (docs.rs, cppreference.com, etc.)
- **File access control**: Read/Edit/Write/Glob/Grep auto-approved for safe directories, with a hardcoded deny list for sensitive paths (~/.ssh, ~/.aws, .env files, etc.)
- **Audit logging**: Every decision logged to `~/.claude/hooks/hook.jsonl`
- **Community profiles**: Import and share curated permission rule sets
- **Smart suggestions**: Analyze your usage and recommend config changes
- **Permission decay**: Detect and remove stale rules to maintain least privilege

## Quick Start

### 1. Install the Plugin

**Option A: Test during development**

```bash
# Launch Claude Code with the plugin loaded from a local directory
claude --plugin-dir /path/to/clean-line/plugins/clean-line
```

**Option B: Permanent installation via marketplace**

```bash
# Clone the repo
git clone https://github.com/asherliu/clean-line.git
cd clean-line
```

Then inside Claude Code:

```
/plugin marketplace add /path/to/clean-line
/plugin install clean-line@claude-code-hooks
```

The plugin's hooks are registered automatically via `hooks.json` -- no manual settings.json editing needed.

### 2. Run Setup (Generate Config)

```bash
# Install the CLI
cd clean-line
uv sync

# Run setup (scans your allow list, generates permission-config.json)
cleanline setup
```

Setup will:
1. Check Python 3.10+ is available
2. Scan your `~/.claude/settings.json` allow list
3. Cross-reference against known alias families (python3.x, cargo-clippy, etc.)
4. Generate `~/.claude/hooks/permission-config.json` with aliases, mappings, domains, file access paths, and `resolvedCanonicals`
5. Save your config to the lockfile for future mutations

Example output:

```
Scanning ~/.claude/settings.json...
  Found 47 commands in your allow list

Generating alias rules...
  12 alias rules from known variants

Generating domain rules...
  8 documentation domains

Proceed? [Y/n] y

  + wrote ~/.claude/hooks/permission-config.json
  + saved user_config to lockfile

Done! Your next Claude Code session will have fewer permission prompts.
Run 'cleanline status' after a few sessions to see the impact.
```

### 3. Done

Your next Claude Code session will have fewer permission prompts immediately. After a few sessions, run `cleanline status` or `cleanline suggest` to tune further.

## CLI Commands

### `setup` -- First-time onboarding

```bash
cleanline setup              # Interactive setup
cleanline setup --yes        # Skip confirmation (scripting)
cleanline setup --dry-run    # Preview without writing
cleanline setup --profile github:user/repo  # Also install a profile
```

### `status` -- See what's installed and how it's performing

```bash
cleanline status
```

Shows: installed profiles, audit summary (allow/passthrough counts), top auto-approved rules, top passthrough candidates.

### `suggest` -- Get config recommendations from your usage

```bash
cleanline suggest            # Show suggestions
cleanline suggest --apply    # Apply suggestions interactively
cleanline suggest --min-count 5  # Only suggest groups with 5+ occurrences
```

Analyzes your audit log to find patterns: versioned commands that should be aliased, domain groups that should be wildcarded. Suggestions are ranked by confidence (high/medium/low).

### `tighten` -- Remove stale permission rules (least privilege)

```bash
cleanline tighten              # Analyze and show stale rule candidates
cleanline tighten --apply      # Remove/suppress stale rules interactively
cleanline tighten --days 60    # Flag rules unused for 60+ days (default: 30)
cleanline tighten --apply --force  # Override the minimum data requirement
```

The complement to `suggest`. Analyzes your audit log to find rules that haven't triggered recently. User rules are removed directly; profile rules are suppressed via overrides.

### `init` -- Add a community profile

```bash
cleanline init github:user/rust-profile
cleanline init local:path/to/profile.json
```

### `dry-run` -- Preview a profile without installing

```bash
cleanline dry-run github:user/new-profile
```

### `update` / `remove` -- Manage profiles

```bash
cleanline update             # Update all profiles
cleanline remove rust-profile
```

## How It Works

### Architecture

```
Plugin (single-process)              CLI (management)
  bash-gate.sh (thin dispatcher)       cleanline setup / suggest / tighten
  resolve.py (all Bash logic)          writes to lockfile user_config
  resolve_webfetch.py (WebFetch)       generates permission-config.json
  resolve_fileops.py (file ops)
        |                                        |
        | reads ONE file                         | reads/writes
        v                                        v
  permission-config.json               profiles.lock.json
  hook.jsonl (audit log)               permission-config.json
```

Three hooks, each a single Python process. The shell dispatcher is a 5-line wrapper that forwards stdin to the Python entry point.

### Bash Hook Pipeline (resolve.py)

```
"python3.13 script.py && echo done"
        |
  [1] REJECT dangerous metacharacters (|, backticks, $(), >(), <()
        |
  [2] SPLIT on && and ; via shlex (respects quoting, cap 5 sub-commands)
        -> ["python3.13 script.py", "echo done"]
        |
  [3] For each sub-command:
        NORMALIZE binary (strip path, unwrap env/timeout)
        -> DIRECT CANONICAL check (in resolvedCanonicals?)
        -> ALIAS LOOKUP (python3.13 -> python, check resolvedCanonicals)
        -> COMMAND MAPPING (longest prefix match)
        |
  [4] ALL sub-commands resolve -> {"decision": "allow"}
      Any failure -> silent exit (defer to normal permissions)
```

### WebFetch Hook Pipeline (resolve_webfetch.py)

```
WebFetch "https://docs.github.com/..."
        |
  Parse hostname -> "docs.github.com"
  Check against webfetch.extraDomains
  Match: exact or wildcard ("*.github.com")
        |
  Match -> {"decision": "allow"}
  No match -> silent exit
```

### File Ops Hook Pipeline (resolve_fileops.py)

```
Read "~/.claude/settings.json"
        |
  Extract path from tool_input (file_path or path, default: cwd)
  Normalize: expand ~, resolve symlinks, make absolute
        |
  DENY check: hardcoded list (ssh, gnupg, aws, .env, etc.)
  DENY check: config denyPaths
        |
  ALLOW check: readPaths (Read/Glob/Grep) or writePaths (Edit/Write)
        |
  Match -> {"decision": "allow"}
  No match -> silent exit
```

**Hardcoded deny list** (cannot be overridden by config):
`~/.ssh/**`, `~/.gnupg/**`, `~/.aws/**`, `~/.netrc`, `~/.claude/credentials*`, `~/.password-store/**`, `~/.local/share/keyrings/**`, `~/.kube/**`, `~/.docker/**`, `**/.env`, `**/.env.*`, `**/.git/config`

Symlinks are resolved before matching -- a symlink pointing to `~/.ssh/id_rsa` is still denied.

### Key Design Principles

- **Fail-closed**: Hooks exit silently on any error. Only explicit matches produce auto-approval. You can never get *less* security than default Claude Code.
- **No chaining**: One level of alias indirection only. `mypy3 -> python3 -> python` will NOT chain.
- **resolvedCanonicals baked in**: Hooks don't read settings.json at runtime. The CLI pre-computes which commands are canonical and writes them into permission-config.json.

### Files on Disk

| File | Location | Purpose |
|------|----------|---------|
| `permission-config.json` | `~/.claude/hooks/` | Merged rules that hooks read (aliases, mappings, domains, fileAccess, resolvedCanonicals) |
| `profiles.lock.json` | `~/.claude/hooks/` | CLI's source of truth (user_config + profiles + overrides) |
| `hook.jsonl` | `~/.claude/hooks/` | Audit log (append-only JSONL) |

## Community Profiles

Profiles are JSON files that declare aliases, command mappings, and domain rules:

```json
{
  "name": "rust-dev",
  "version": "1.0.0",
  "description": "Rust development aliases and domains",
  "bashAliases": {
    "cargo-clippy": "cargo",
    "cargo-fmt": "cargo"
  },
  "commandMappings": {
    "cargo test": ["cargo nextest run"]
  },
  "webfetch": {
    "extraDomains": ["*.docs.rs", "*.crates.io"]
  }
}
```

Profiles can also declare file access paths:

```json
{
  "fileAccess": {
    "readPaths": ["~/.cargo/**", "~/.rustup/**"],
    "writePaths": ["/opt/rust-cache/**"]
  }
}
```

Multiple profiles merge: domains are unioned, aliases are merged (user config takes priority on conflict), mappings are unioned per canonical, readPaths are unioned. Profile `writePaths` require explicit user opt-in during `cleanline init`. Profile caps: 50 aliases, 30 mappings, 50 domains, 50 file paths per sub-key.

### Profile Overrides

Suppress individual profile rules without removing the entire profile:

```bash
cleanline tighten --apply  # Creates overrides for stale profile rules
```

Overrides persist across `cleanline update`. When a profile author removes a rule you've already suppressed, the override is auto-cleaned (convergence detection).

## Customization

### Adding Custom Aliases

Use `cleanline suggest --apply` to let the CLI recommend aliases based on your usage, or manually edit via the lockfile:

```bash
cleanline suggest --apply
```

### Adding Custom Domains

Same approach -- `suggest --apply` will recommend domain wildcards, or you can run setup again.

### Adding File Access Paths

The `suggest` command will recommend read paths based on your usage. You can also configure them through setup or profiles:

```json
{
  "fileAccess": {
    "readPaths": ["~/.claude/**", "~/.config/**"],
    "writePaths": ["/tmp/**"],
    "denyPaths": ["~/.ssh/**", "~/.gnupg/**", "~/.aws/**"]
  }
}
```

- **readPaths**: Auto-approve Read, Glob, Grep to these paths
- **writePaths**: Auto-approve Edit, Write to these paths
- **denyPaths**: Always deny (checked before allow). The hardcoded deny list always applies regardless of config.

### Adding Command Mappings

The key is the canonical command (must be in your allow list). The value is a list of equivalent commands:

```json
{
  "commandMappings": {
    "npm test": ["npx jest", "yarn test", "pnpm test"],
    "cargo test": ["cargo nextest run"]
  }
}
```

## Known Aliases

Setup automatically generates rules for these version families when the canonical command is in your allow list:

| Canonical | Auto-aliased variants |
|-----------|-----------------------|
| python | python3, python3.10-3.14 |
| pip | pip3 |
| pytest | py.test |
| cargo | cargo-clippy, cargo-fmt, cargo-watch |
| node | nodejs |
| npm | npx |
| ruby | ruby3.0-3.3 |
| gem | gem3 |
| java | java17, java21 |
| go | go1.21-1.23 |

## Requirements

- Claude Code CLI
- Python 3.10+
- `uv` (for CLI commands)

## License

MIT
