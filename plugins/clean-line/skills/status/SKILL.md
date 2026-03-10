# Clean Line Status

Show the current state of the Clean Line permission hooks system.

## Instructions

Read the permission config and audit log, then present a summary:

1. Read `~/.claude/hooks/permission-config.json` and show:
   - Number of bash aliases configured
   - Number of command mappings configured
   - Number of extra domains configured
   - Number of resolved canonicals

2. Read `~/.claude/hooks/hook.jsonl` (last 100 entries) and show:
   - Count of allow vs passthrough decisions
   - Top 5 auto-approved rules
   - Top 5 passthroughs (candidates for new rules)

3. If `~/.claude/hooks/profiles.lock.json` exists, show installed profiles.

Present the information in a clear, concise format.
