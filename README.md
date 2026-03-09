# claude-code-hooks

A Claude Code plugin marketplace providing permission hooks that reduce prompt fatigue.

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

## Customization

After installation, edit the plugin's `hooks/permission-config.json` to add your own domains and aliases.

## Requirements

- Claude Code CLI
- `jq`
- Python 3.10+

## License

MIT
