# claude-code-hooks

A Claude Code plugin marketplace providing permission hooks that reduce prompt fatigue, with a CLI for managing composable community profiles.

## Available Plugins

| Plugin | Description |
|--------|-------------|
| [permission-hooks](plugins/permission-hooks/) | WebFetch domain auto-approval, Bash alias resolution, E2E commit gating |

## Installation

```bash
# Add the marketplace
claude plugin marketplace add asherliu/claude-code-hooks

# Install the plugin
claude plugin install permission-hooks@asherliu/claude-code-hooks
```

## What This Solves

Claude Code asks for permission on every tool call. These hooks auto-approve safe, predictable operations:

- **WebFetch** to documentation domains you already trust (sandbox allowlist + extras)
- **Bash** commands using versioned binaries (`python3.13`) when the canonical name (`python`) is already permitted
- **git commit** blocked unless E2E tests were run (opt-in per project)

## Profile System

Community profiles let you share and import curated permission rules. The CLI manages profiles as composable layers that merge into a single lock file read by the hooks.

```bash
# First-time setup (scans your allow list, generates baseline config)
uv run claude-hooks setup

# Add a community profile
uv run claude-hooks init github:user/rust-profile

# See what's installed and how it's performing
uv run claude-hooks status

# Get suggestions from your audit data
uv run claude-hooks suggest

# Preview a profile without installing
uv run claude-hooks dry-run github:user/new-profile

# Update profiles to latest versions
uv run claude-hooks update

# Remove a profile
uv run claude-hooks remove rust-profile
```

### How Profiles Work

```
permission-config.json (your rules)     profiles.lock.json (managed by CLI)
        │                                        │
        │ read by hooks                          │ read by hooks
        ▼                                        ▼
   bash-gate.sh ──── checks settings.json allow list ──── auto-approve or defer
   approve-webfetch-domains.sh ─────────────────────────── auto-approve or defer
```

Hooks read from both config files. Profiles contribute aliases, command mappings, and domain rules that merge with your own. Conflicts are detected at install time.

## Customization

Edit `hooks/permission-config.json` to add your own domains and aliases, or use `claude-hooks setup` to generate a baseline from your existing allow list.

## Requirements

- Claude Code CLI
- `jq`
- Python 3.10+
- `uv` (for CLI commands)

## License

MIT
