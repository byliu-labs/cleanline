# permission-hooks

Claude Code plugin that provides three permission hooks to reduce prompt fatigue.

## Hooks

### 1. WebFetch Domain Auto-Approval (`approve-webfetch-domains.sh`)

**Event:** `PreToolUse` on `WebFetch`

Auto-approves WebFetch requests to domains already in your sandbox allowlist (`~/.claude/settings.json` → `sandbox.network.allowedDomains`) plus extra documentation domains defined in `permission-config.json` and any community profile domains from `~/.claude/hooks/profiles.lock.json`.

### 2. Bash Alias Resolution + Command Mapping (`bash-gate.sh`)

**Event:** `PreToolUse` on `Bash`

Two layers of command normalization:

**Single-word aliases:** When Claude runs a versioned binary like `python3.13`, the hook resolves it to its canonical name (`python`) and checks if that canonical name is in your permission allow list. If `Bash(python *)` is allowed, then `python3.13 script.py` is auto-approved.

**Multi-word command mappings:** When Claude runs a command like `npx jest --coverage`, the hook checks `commandMappings` in `permission-config.json`. If `npx jest` maps to `npm test` and `Bash(npm test *)` is in your allow list, the command is auto-approved. Matching is token-exact — `npx jest` will NOT match `npx jester`.

Both layers also check `~/.claude/hooks/profiles.lock.json` (managed by the CLI) as a fallback if no match is found in the local config.

Compound commands (pipes, `&&`, `;`, etc.) are skipped — they go through normal permission flow.

### 3. E2E Commit Gate (`require-e2e-gate.sh` + `clear-e2e-gate.sh`)

**Event:** `PreToolUse` / `PostToolUse` on `Bash`

Blocks `git commit` unless a testing gate file (`/tmp/.claude-e2e-gate`) exists. Projects opt in by creating `.claude/e2e-required` in their root. After each successful commit, the gate file is deleted so the next commit requires fresh testing.

## Configuration

Edit `hooks/permission-config.json` to customize:

```json
{
  "webfetch": {
    "extraDomains": ["*.example.com"]
  },
  "bashAliases": {
    "python3.13": "python",
    "node20": "node"
  },
  "commandMappings": {
    "npm test": ["npx jest", "yarn test"]
  }
}
```

- **`webfetch.extraDomains`** — additional domains to auto-approve (supports `*.` wildcards)
- **`bashAliases`** — maps versioned/aliased binaries to their canonical names
- **`commandMappings`** — maps canonical commands to lists of equivalent multi-word commands

## Profile System

Community profiles extend the config with shared rule sets. The `cleanline` CLI manages profiles as composable layers:

```bash
uv run cleanline setup           # First-time setup
uv run cleanline init <source>   # Add a profile
uv run cleanline status          # Show installed profiles + audit stats
uv run cleanline suggest         # Propose config changes from usage data
```

Profiles merge into `~/.claude/hooks/profiles.lock.json`. Hooks read both the local config and the lock file.

## Audit Logging

All hook decisions are logged to `~/.claude/hooks/hook.jsonl` as JSONL:

```json
{"ts":"2025-03-09T12:00:00Z","tool":"Bash","input":"python3 script.py","decision":"allow","matched_rule":"alias:python3->python"}
```

The `suggest` command analyzes this data to propose config additions for frequently-prompted commands.

## Requirements

- `jq` (for JSON parsing in shell scripts)
- Python 3.10+ (for URL parsing and command normalization helpers)

## How It Works

All hooks follow the fail-closed principle: any error causes silent exit (code 0), falling through to Claude Code's normal permission flow. Only explicit matches produce `{"decision":"allow"}` output.
