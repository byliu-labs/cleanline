# permission-hooks

Claude Code plugin that provides three permission hooks to reduce prompt fatigue:

## Hooks

### 1. WebFetch Domain Auto-Approval (`approve-webfetch-domains.sh`)

**Event:** `PreToolUse` on `WebFetch`

Auto-approves WebFetch requests to domains already in your sandbox allowlist (`~/.claude/settings.json` → `sandbox.network.allowedDomains`) plus extra documentation domains defined in `permission-config.json`.

### 2. Bash Alias Resolution (`bash-gate.sh`)

**Event:** `PreToolUse` on `Bash`

When Claude runs a versioned binary like `python3.13`, this hook resolves it to its canonical name (`python`) and checks if that canonical name is in your permission allow list. If `Bash(python *)` is allowed, then `python3.13 script.py` is auto-approved.

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
  }
}
```

- **`webfetch.extraDomains`** — additional domains to auto-approve (supports `*.` wildcards)
- **`bashAliases`** — maps versioned/aliased binaries to their canonical names

## Requirements

- `jq` (for JSON parsing in shell scripts)
- Python 3.10+ (for URL parsing and command normalization helpers)

## How It Works

All hooks follow the fail-closed principle: any error causes silent exit (code 0), falling through to Claude Code's normal permission flow. Only explicit matches produce `{"decision":"allow"}` output.
