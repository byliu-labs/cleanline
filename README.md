# Clean Line

Take the clean line. Permission hooks that reduce Claude Code prompt fatigue, with a CLI for managing composable community profiles.

## The Problem

Claude Code asks for permission on every tool call. If `python` is in your allow list but you run `python3.13`, you get prompted. If `npm test` is allowed but Claude runs `npx jest`, you get prompted. These hooks auto-approve safe, predictable operations so you can stay in flow.

## What It Does

- **Bash alias resolution**: `python3.13 script.py` auto-approved when `python` is in your allow list
- **Multi-word command mapping**: `npx jest --coverage` auto-approved when `npm test` is allowed
- **WebFetch domain matching**: Documentation sites auto-approved (docs.rs, cppreference.com, etc.)
- **Audit logging**: Every decision logged to `~/.claude/hooks/hook.jsonl` via JSON-safe Python helper
- **Community profiles**: Import and share curated permission rule sets
- **Smart suggestions**: Analyze your usage and recommend config changes

## Quick Start

```bash
# Clone the repo
git clone https://github.com/asherliu/clean-line.git
cd clean-line

# Install the CLI
uv sync

# Run setup (scans your allow list, installs hooks, generates config)
cleanline setup
```

Setup will:
1. Check prerequisites (jq, Python 3.10+, settings.json)
2. Scan your allow list and generate alias rules automatically
3. Copy hook scripts to `~/.claude/hooks/`
4. Register hooks in `~/.claude/settings.json`
5. Show you exactly what it will do and ask for confirmation

Example output:

```
Scanning ~/.claude/settings.json...
  Found 47 commands in your allow list

Generating alias rules...
  12 alias rules from known variants

Generating domain rules...
  8 documentation domains

Hook registration:
  + WebFetch auto-approval (approve-webfetch-domains.sh)
  + Bash alias resolution (bash-gate.sh)
  7 hook files -> ~/.claude/hooks/

Proceed? [Y/n] y

  + wrote ~/.claude/hooks/permission-config.json
  + copied 7 hook files to ~/.claude/hooks/
  + registered 2 hooks in settings.json

Done! Your next Claude Code session will have fewer permission prompts.
```

Your next Claude Code session will have fewer permission prompts immediately.

## CLI Commands

### `setup` -- First-time onboarding

```bash
cleanline setup              # Interactive setup
cleanline setup --yes        # Skip confirmation (scripting)
cleanline setup --dry-run    # Preview without writing
cleanline setup --uninstall  # Remove hooks and clean up
cleanline setup --profile github:user/repo  # Also install a profile
```

### `status` -- See what's installed and how it's performing

```bash
cleanline status
```

Shows: installed profiles, audit summary (allow/passthrough/deny counts), top auto-approved rules, top passthrough candidates, and hook health warnings.

### `suggest` -- Get config recommendations from your usage

```bash
cleanline suggest            # Show suggestions
cleanline suggest --apply    # Apply suggestions interactively
cleanline suggest --min-count 5  # Only suggest groups with 5+ occurrences
```

Analyzes your audit log to find patterns: versioned commands that should be aliased (via regex and curated `known_aliases.json`), domain groups that should be wildcarded. The `--apply` flag lets you accept changes interactively.

Suggestions are ranked by confidence:
- **high** (10+ occurrences): Strong evidence this rule is needed
- **medium** (5-9 occurrences): Reasonable evidence
- **low** (3-4 occurrences): Minimal evidence (default threshold)

Use `--min-count` to adjust the minimum evidence threshold.

### `tighten` -- Remove stale permission rules (least privilege)

```bash
cleanline tighten              # Analyze and show stale rule candidates
cleanline tighten --apply      # Remove/suppress stale rules interactively
cleanline tighten --days 60    # Flag rules unused for 60+ days (default: 30)
cleanline tighten --apply --force  # Override the minimum data requirement
```

The complement to `suggest`. Analyzes your audit log to find rules that haven't triggered recently. User-config rules are removed directly; profile rules are suppressed via overrides that persist across profile updates.

Includes a data sufficiency gate: `--apply` requires at least 7 days of audit data unless `--force` is passed. For stale aliases in known families, shows which siblings are still active (e.g., "python3.11, python3.12 are active").

### `init` -- Add a community profile

```bash
cleanline init github:user/rust-profile
cleanline init local:path/to/profile.json
```

Fetches, validates, checks for conflicts, and merges into the lock file.

### `dry-run` -- Preview a profile without installing

```bash
cleanline dry-run github:user/new-profile
```

Shows what rules would be added, which are ready vs inert (canonical not in your allow list), and any conflicts with existing profiles.

### `update` -- Re-fetch profiles

```bash
cleanline update             # Update all profiles
cleanline update rust-profile  # Update one profile
```

### `remove` -- Remove a profile

```bash
cleanline remove rust-profile
```

Shows impact: which aliases and domains will be removed.

## Security Principles

Clean Line's design follows established security engineering principles:

- **Fail-safe defaults** (Saltzer & Schroeder, 1975): Hooks exit silently on error, deferring to normal permissions. Only explicit matches produce auto-approval. You can never get *less* security than default Claude Code.

- **Attribute-Based Access Control (ABAC)**: Alias and mapping resolution resolves tool calls against a policy of attributes (binary names, command patterns, domain patterns) rather than simple identity checks.

- **Federated policy management**: Community profiles provide shared permission rule sets. Users can override individual profile rules without abandoning the entire profile -- like git's local commits on top of upstream.

- **Least privilege** via `cleanline tighten`: A bidirectional feedback loop. `suggest` adds rules based on observed need; `tighten` prunes stale rules based on observed disuse. Together they converge on the minimum necessary permission set.

- **Defense in depth**: No chaining (single level of indirection) prevents transitive privilege escalation. `mypy3 -> python3 -> python` will NOT chain -- each resolution resolves independently against the allow list.

## How It Works

### Hook Resolution Pipeline

When Claude Code invokes a Bash command, `bash-gate.sh` runs this pipeline:

```
"python3.13 script.py"
        |
  [1] METACHARACTER REJECTION
        Does it contain &&, ||, |, ;, $(), etc?
        Yes -> defer to normal permissions (fail-closed)
        |
  [2] NORMALIZE
        Extract binary name (handles env/timeout wrappers)
        -> "python3.13"
        |
  [3] ALIAS LOOKUP
        Check permission-config.json: python3.13 -> python
        Check profiles.lock.json:     python3.13 -> python
        Is "python" in settings.json allow list?
        Yes -> {"decision": "allow"}
        |
  [4] COMMAND MAPPING
        Check "npx jest --coverage" against mappings
        Match: (npx jest) -> "npm test"
        Is "npm test" in settings.json allow list?
        Yes -> {"decision": "allow"}
        |
  No match -> defer to normal permissions
```

For WebFetch, `approve-webfetch-domains.sh` extracts the hostname and checks it against sandbox allowed domains + extra domains from config + profile domains.

#### Wildcard Semantics

`*.example.com` matches `sub.example.com` and `deep.sub.example.com` but does NOT match `example.com` itself. This follows standard subdomain wildcard conventions.

**No chaining**: Each resolution layer resolves against the allow list independently. Only one level of alias indirection is allowed -- `mypy3 -> python3` will NOT chain through `python3 -> python`.

### Fail-Closed Design

Every hook exits silently on error, deferring to Claude Code's normal permission flow. Only an explicit `{"decision": "allow"}` bypasses the prompt. This means:
- If `jq` crashes: normal prompt
- If config is missing: normal prompt
- If the hook script has a bug: normal prompt

You can never get *less* security than default Claude Code.

### Audit Logging

All hook decisions are logged to `~/.claude/hooks/hook.jsonl` via `log_event.py`, a Python helper that uses `json.dumps()` for proper JSON escaping. This ensures commands with quotes, backslashes, and special characters produce valid JSONL entries.

### Files on Disk

After setup, `~/.claude/hooks/` contains:

| File | Purpose |
|------|---------|
| `bash-gate.sh` | Bash PreToolUse hook (alias + mapping resolution) |
| `approve-webfetch-domains.sh` | WebFetch PreToolUse hook (domain matching) |
| `normalize-bash-cmd.py` | Extract binary name from commands |
| `match-command-equiv.py` | Multi-word command mapping lookup |
| `parse-url-host.py` | Extract hostname from URLs |
| `bash_utils.py` | Shared utilities (metacharacter detection) |
| `log_event.py` | JSON-safe audit log writer |
| `permission-config.json` | Your auto-generated + customized rules |
| `profiles.lock.json` | Merged state from installed profiles |
| `hook.jsonl` | Audit log (append-only) |

### Settings.json Registration

Setup adds these entries to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "WebFetch",
        "hooks": [{"type": "command", "command": "~/.claude/hooks/approve-webfetch-domains.sh"}]
      },
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "~/.claude/hooks/bash-gate.sh"}]
      }
    ]
  }
}
```

Registration is idempotent -- running `setup` again won't duplicate entries.

## Community Profiles

Profiles are JSON files that declare aliases, command mappings, and domain rules. They let you share permission configurations with others. "Here's my clean line config."

### Profile Format

```json
{
  "name": "rust-dev",
  "version": "1.0.0",
  "description": "Rust development aliases and domains",
  "author": "username",
  "bashAliases": {
    "cargo-clippy": "cargo",
    "cargo-fmt": "cargo",
    "cargo-watch": "cargo"
  },
  "commandMappings": {
    "cargo test": ["cargo nextest run"]
  },
  "webfetch": {
    "extraDomains": ["*.docs.rs", "*.crates.io"]
  }
}
```

### How Profiles Merge

Multiple profiles merge into a single `profiles.lock.json` that hooks read:

- **Domains**: Set union (deduplicated)
- **Aliases**: Dict merge (last profile wins on conflict)
- **Command mappings**: Alias list union per canonical

Conflicts are detected at install time and reported as warnings.

### Profile Overrides

You can suppress individual profile rules without removing the entire profile. This is useful when a profile is mostly good but includes a few rules you don't need.

`cleanline tighten --apply` automatically creates overrides for stale profile rules. Overrides are stored in `profiles.lock.json` under `user_overrides` and persist across `cleanline update`.

When a profile author removes a rule you've already suppressed, the override is automatically cleaned up during `cleanline update` (convergence detection).

### Profile Caps

To prevent any single profile from dominating the config:

| Field | Max entries |
|-------|------------|
| bashAliases | 50 |
| commandMappings | 30 |
| webfetch.extraDomains | 50 |

### Publishing a Profile

Create a `profile.json` in a GitHub repo:

```bash
# Others install it with:
cleanline init github:yourname/your-repo
```

The CLI fetches from `https://raw.githubusercontent.com/yourname/your-repo/main/profile.json`.

## Customization

### Adding Custom Aliases

Edit `~/.claude/hooks/permission-config.json`:

```json
{
  "bashAliases": {
    "python3.13": "python",
    "mycompany-lint": "eslint"
  }
}
```

Or use `cleanline suggest --apply` to let the CLI recommend and apply aliases based on your usage.

### Adding Custom Domains

```json
{
  "webfetch": {
    "extraDomains": [
      "*.internal-docs.company.com",
      "*.confluence.company.com"
    ]
  }
}
```

Default documentation domains are loaded from `known_domains.json` (w3.org, rust-lang.org, docs.rs, cppreference.com, etc.) and can be extended via config or profiles.

### Adding Command Mappings

```json
{
  "commandMappings": {
    "npm test": ["npx jest", "yarn test", "pnpm test"],
    "cargo test": ["cargo nextest run"]
  }
}
```

The key is the canonical command (must be in your allow list). The value is a list of equivalent commands that should be auto-approved.

## Uninstalling

```bash
cleanline setup --uninstall
```

This removes hook entries from `settings.json` and deletes hook files from `~/.claude/hooks/`. Your `profiles.lock.json` is preserved (profile data is kept).

## Requirements

- Claude Code CLI
- `jq` (JSON processing in shell hooks)
- Python 3.10+
- `uv` (for CLI commands)

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

The `suggest` command also uses `known_aliases.json` to group non-versioned variants (e.g., cargo-clippy + cargo-fmt under cargo) alongside regex-based version detection.

## License

MIT
