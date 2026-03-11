"""CLI entry point for Clean Line.

Usage: cleanline <command> [options]

Commands:
  setup              First-time onboarding
  init <source>      Add a profile (github:user/repo or local path)
  status             Show installed profiles and audit summary
  suggest            Propose config changes from audit data
  tighten            Identify and remove stale permission rules
  clean              Consolidate settings.json allow list
  update [name]      Re-fetch and update profiles
  remove <name>      Remove a profile
  dry-run <source>   Show what a profile would change without applying
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import profile_ops
from . import setup_cmd
from . import suggest as suggest_mod
from . import audit as audit_mod
from . import tighten as tighten_mod
from . import lockfile as lockfile_mod
from . import clean_cmd as clean_mod
from .tiers import DEFAULT_TIER, VALID_TIERS, get_tier_config


def _print_json(data: dict) -> None:
    """Pretty-print a result dict."""
    print(json.dumps(data, indent=2))


def _print_result(result: dict, label: str = "") -> int:
    """Print a result dict with errors/warnings, return exit code."""
    if label:
        print(f"\n{label}")
        print("=" * len(label))

    errors = result.get("errors", [])
    warnings = result.get("warnings", [])
    actions = result.get("actions", [])

    for action in actions:
        print(f"  + {action}")

    for warning in warnings:
        print(f"  ! {warning}")

    for error in errors:
        print(f"  x {error}")

    # Print remaining keys
    skip = {"errors", "warnings", "actions"}
    for key, val in result.items():
        if key in skip:
            continue
        if isinstance(val, list) and val:
            print(f"\n  {key}:")
            for item in val:
                if isinstance(item, dict):
                    print(f"    {json.dumps(item)}")
                else:
                    print(f"    - {item}")
        elif isinstance(val, dict) and val:
            print(f"\n  {key}:")
            for k, v in val.items():
                print(f"    {k}: {v}")

    return 1 if errors else 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Run the setup command."""
    config_dir = Path(args.config_dir) if args.config_dir else _default_hooks_dir()

    result = setup_cmd.run_setup(
        config_dir,
        tier=args.tier,
        profile_source=args.profile,
        dry_run=args.dry_run,
        auto_yes=args.yes,
    )

    # In non-interactive mode (dry-run), print result
    if args.dry_run:
        return _print_result(result, "Setup (dry run)")

    # In interactive mode, run_setup prints its own output
    return 1 if result.get("errors") else 0


def cmd_init(args: argparse.Namespace) -> int:
    """Run the init command."""
    result = profile_ops.init_profile(args.source)
    return _print_result(result, f"Init: {args.source}")


def cmd_status(args: argparse.Namespace) -> int:
    """Run the status command."""
    result = profile_ops.get_status()

    # Show tier
    lockfile_data = lockfile_mod.read_lockfile()
    tier = lockfile_mod.get_tier(lockfile_data)
    print(f"\nTrust Tier: {tier}")

    print("\nInstalled Profiles")
    print("==================")
    profiles = result.get("profiles", [])
    if not profiles:
        print("  (none)")
    for p in profiles:
        print(f"  {p['name']} v{p['version']}  source={p['source']}")

    print("\nAudit Summary")
    print("=============")
    summary = result.get("audit_summary", {})
    if not summary:
        print("  (no audit data)")
    else:
        total_allow = summary.get("allow", 0)
        total_passthrough = summary.get("passthrough", 0)
        total = total_allow + total_passthrough
        if total > 0:
            pct = 100 * total_allow // total
            print(f"  {total_allow} prompts saved out of {total} tool calls ({pct}% auto-approved)")
            if total_passthrough > 0:
                print(f"  {total_passthrough} still prompted -- run 'cleanline suggest' to reduce")
        else:
            for decision, count in summary.items():
                print(f"  {decision}: {count}")

    top_allow = result.get("top_auto_approved", [])
    if top_allow:
        print("\nTop Auto-Approved Rules")
        print("-----------------------")
        for rule, count in top_allow:
            print(f"  {rule}: {count}")

    top_pass = result.get("top_passthroughs", [])
    if top_pass:
        print("\nTop Passthroughs (candidates for suggest)")
        print("------------------------------------------")
        for rule, count in top_pass:
            print(f"  {rule}: {count}")

    # Allow list health
    settings_path = setup_cmd.find_settings_path()
    if settings_path:
        allow_list = setup_cmd.parse_allow_list(settings_path)
        if allow_list:
            config = _load_permission_config()
            analysis = clean_mod.analyze_allow_list(allow_list, config)
            redundant = analysis["redundant"]
            handled = analysis["handled"]
            if redundant or handled:
                print("\nAllow List Health")
                print("=================")
                if redundant:
                    print(f"  {len(redundant)} redundant entries found")
                if handled:
                    print(f"  {len(handled)} entries also handled by Clean Line aliases")
                print("  Run 'cleanline clean' to consolidate.")

    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Consolidate settings.json allow list."""
    settings_path = setup_cmd.find_settings_path()
    if settings_path is None:
        print("Cannot find ~/.claude/settings.json")
        return 1

    allow_list = setup_cmd.parse_allow_list(settings_path)
    if not allow_list:
        print("\nNothing to clean — allow list is empty")
        return 0

    config = _load_permission_config()
    analysis = clean_mod.analyze_allow_list(allow_list, config)

    redundant = analysis["redundant"]
    consolidations = analysis["consolidations"]
    handled = analysis["handled"]

    # Print analysis
    if redundant:
        print(f"\nRedundant entries ({len(redundant)}):")
        for r in redundant:
            print(f"  {r['entry']}  (covered by {r['covered_by']})")

    if consolidations:
        print(f"\nConsolidation opportunities ({len(consolidations)}):")
        for c in consolidations:
            print(f"  {c['proposed']}  (replaces {len(c['entries'])} entries)")
            for e in c["entries"]:
                print(f"    - {e}")

    if handled:
        print(f"\nAlso handled by Clean Line ({len(handled)}, informational):")
        for h in handled:
            print(f"  {h['entry']}  (alias: {h['alias']})")

    if not redundant and not consolidations:
        print("\nNothing to clean — allow list is already lean.")
        return 0

    if args.dry_run:
        return 0

    # Prompt
    if not args.yes:
        try:
            answer = input("\nApply changes? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0
        if answer not in ("", "y", "yes"):
            return 0

    result = clean_mod.apply_clean(settings_path, redundant, consolidations)
    for action in result["actions"]:
        print(f"  + {action}")

    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    """Run the suggest command."""
    events = audit_mod.read_audit_log()
    if not events:
        print("No audit data found. Run Claude Code with hooks enabled to generate data.")
        return 0

    # Read tier for default thresholds; explicit --min-count overrides
    lockfile_data = lockfile_mod.read_lockfile()
    tier = lockfile_mod.get_tier(lockfile_data)
    min_count = getattr(args, "min_count", None)
    suggestions = suggest_mod.generate_suggestions(events, min_count=min_count, tier=tier)

    # Headline stats
    summary = audit_mod.summarize_decisions(events)
    total_allow = summary.get("allow", 0)
    total_passthrough = summary.get("passthrough", 0)
    total = total_allow + total_passthrough
    if total > 0:
        print(f"\n{total_allow} prompts saved out of {total} tool calls "
              f"({100 * total_allow // total}% auto-approved)")

    cmd_groups = suggestions.get("command_groups", [])
    domain_groups = suggestions.get("domain_groups", [])
    path_groups = suggestions.get("file_path_groups", [])
    saveable = sum(g["total"] for g in cmd_groups) + sum(g["total"] for g in domain_groups) + sum(g["total"] for g in path_groups)

    if path_groups:
        print(f"\nFile read paths to add:")
        for group in path_groups:
            conf = group.get("confidence", "")
            conf_label = f", {conf} confidence" if conf else ""
            print(f"  {group['pattern']}:  ({group['total']} prompts saved{conf_label})")
            for p, count in group["paths"]:
                print(f"    {p}  ({count}x)")

    if cmd_groups:
        print(f"\nCommand aliases to add:")
        for group in cmd_groups:
            conf = group.get("confidence", "")
            conf_label = f", {conf} confidence" if conf else ""
            print(f"  {group['canonical']}:  ({group['total']} prompts saved{conf_label})")
            for variant, count in group["variants"]:
                print(f"    {variant} -> {group['canonical']}  ({count}x)")

    if domain_groups:
        print(f"\nDomain patterns to add:")
        for group in domain_groups:
            conf = group.get("confidence", "")
            conf_label = f", {conf} confidence" if conf else ""
            print(f"  {group['pattern']}:  ({group['total']} prompts saved{conf_label})")
            for sub, count in group["subdomains"]:
                print(f"    {sub}  ({count}x)")

    top_cmds = suggestions.get("top_commands", [])
    if top_cmds and not cmd_groups:
        print("\nTop passthrough commands:")
        for cmd, count in top_cmds:
            print(f"  {cmd}: {count}")

    top_doms = suggestions.get("top_domains", [])
    if top_doms and not domain_groups:
        print("\nTop passthrough domains:")
        for dom, count in top_doms:
            print(f"  {dom}: {count}")

    top_paths = suggestions.get("top_file_paths", [])
    if top_paths and not path_groups:
        print("\nTop passthrough file paths:")
        for p, count in top_paths:
            print(f"  {p}: {count}")

    if saveable > 0:
        print(f"\nApplying these suggestions would save {saveable} prompts.")
        print("Run 'cleanline suggest --apply' to apply.")

    if not any([cmd_groups, domain_groups, path_groups, top_cmds, top_doms, top_paths]):
        print("\n  No suggestions -- all passthroughs are low frequency.")

    # --apply: apply suggestions interactively
    if args.apply and any([cmd_groups, domain_groups, path_groups]):
        return _apply_suggestions(suggestions)

    return 0


def _apply_suggestions(suggestions: dict) -> int:
    """Apply suggested config changes interactively."""
    config_dir = _default_hooks_dir()
    config_path = config_dir / "permission-config.json"

    result = suggest_mod.apply_suggestions(suggestions, config_path)
    if result.get("cancelled"):
        print("\nCancelled.")
        return 0

    print()
    for action in result.get("actions", []):
        print(f"  + {action}")
    return 0


def cmd_tighten(args: argparse.Namespace) -> int:
    """Identify and remove stale permission rules."""
    events = audit_mod.read_audit_log()
    if not events:
        print("No audit data found. Run Claude Code with hooks enabled to generate data.")
        return 0

    config_dir = _default_hooks_dir()
    config_path = config_dir / "permission-config.json"
    config = _load_permission_config()

    lockfile_path = lockfile_mod.get_lockfile_path()
    lockfile_data = lockfile_mod.read_lockfile(lockfile_path)

    # Tier-aware staleness: --days overrides tier default when explicitly set
    tier = lockfile_mod.get_tier(lockfile_data)
    effective_days = args.days if args.days is not None else get_tier_config(tier)["tighten_days"]
    stale = tighten_mod.find_stale_rules(events, config, lockfile_data, effective_days)

    # Print analysis
    span = stale["audit_span_days"]
    print(f"\nAnalyzing {span} days of audit data ({len(events)} events)...")

    if stale["insufficient_data"]:
        print(f"\nWarning: only {span} days of data. Results may not be reliable.")

    # User-config candidates
    user = stale["user_stale"]
    user_count = sum(len(v) for v in user.values())
    if user_count:
        print("\nCandidates from your config (removable):")
        for entry in user["aliases"]:
            note = f"  ({entry['family_note']})" if entry.get("family_note") else ""
            last = entry["last_used"] or "never triggered"
            if entry["last_used"]:
                last = f"last used {entry['last_used'][:10]}"
            print(f"  {entry['key']} -> {entry['canonical']:<16} {last}{note}")
        for entry in user["domains"]:
            last = entry["last_used"] or "never triggered"
            if entry["last_used"]:
                last = f"last used {entry['last_used'][:10]}"
            print(f"  {entry['pattern']:<32} {last}")
        for entry in user["mappings"]:
            last = entry["last_used"] or "never triggered"
            if entry["last_used"]:
                last = f"last used {entry['last_used'][:10]}"
            print(f"  {entry['canonical']:<32} {last}")

    # Profile candidates
    profile = stale["profile_stale"]
    profile_count = sum(len(v) for v in profile.values())
    if profile_count:
        print("\nCandidates from profiles (suppressible):")
        for entry in profile["aliases"]:
            note = f"  ({entry['family_note']})" if entry.get("family_note") else ""
            last = entry["last_used"] or "never triggered"
            if entry["last_used"]:
                last = f"last used {entry['last_used'][:10]}"
            print(f"  {entry['key']} -> {entry['canonical']:<16} {last}  (from: {entry['profile']}){note}")
        for entry in profile["domains"]:
            last = entry["last_used"] or "never triggered"
            if entry["last_used"]:
                last = f"last used {entry['last_used'][:10]}"
            print(f"  {entry['pattern']:<32} {last}  (from: {entry['profile']})")
        for entry in profile["mappings"]:
            last = entry["last_used"] or "never triggered"
            if entry["last_used"]:
                last = f"last used {entry['last_used'][:10]}"
            print(f"  {entry['canonical']:<32} {last}  (from: {entry['profile']})")
        print("\n  Profile rules aren't deleted -- they're suppressed via override.")
        print("  Suppressed rules persist across profile updates.")

    counts = stale["active_counts"]
    print(f"\nActive rules: {counts['aliases']} aliases, "
          f"{counts['mappings']} mappings, {counts['domains']} domains")
    print(f"Candidates: {user_count} user, {profile_count} profile")

    if not user_count and not profile_count:
        print("\nNo stale rules found. Your config is lean.")
        return 0

    # --apply logic
    if args.apply:
        if stale["insufficient_data"] and not args.force:
            print(f"\nRefused: only {span} days of audit data (minimum 7).")
            print("Use 'cleanline tighten --apply --force' to override.")
            return 1

        try:
            answer = input("\nReview each candidate? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0

        if answer not in ("", "y", "yes"):
            print("Cancelled.")
            return 0

        if user_count:
            removals = tighten_mod.select_rules_to_remove(stale)
            user_result = tighten_mod.apply_tighten_user(removals, config_path)
            for action in user_result["actions"]:
                print(f"  + {action}")

        if profile_count:
            suppressions = tighten_mod.select_rules_to_remove(stale)
            profile_result = tighten_mod.apply_tighten_profile(suppressions, lockfile_path)
            for action in profile_result["actions"]:
                print(f"  + {action}")
    else:
        if user_count or profile_count:
            print("\nRun 'cleanline tighten --apply' to review each candidate.")

    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Run the update command."""
    result = profile_ops.update_profiles(name=args.name)
    return _print_result(result, "Update")


def cmd_remove(args: argparse.Namespace) -> int:
    """Run the remove command."""
    result = profile_ops.remove_profile(args.name)
    return _print_result(result, f"Remove: {args.name}")


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Run the dry-run command."""
    result = profile_ops.dry_run_profile(args.source)
    return _print_result(result, f"Dry Run: {args.source}")


def _default_hooks_dir() -> Path:
    """Default hooks directory (where permission-config.json lives)."""
    return Path.home() / ".claude" / "hooks"


def _load_permission_config() -> dict:
    """Load permission-config.json from default hooks directory."""
    config_path = _default_hooks_dir() / "permission-config.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cleanline",
        description="Take the clean line -- fewer permission prompts in Claude Code.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # setup
    p_setup = sub.add_parser("setup", help="First-time onboarding")
    p_setup.add_argument("--tier", choices=sorted(VALID_TIERS), default=DEFAULT_TIER,
                          help=f"Trust tier (default: {DEFAULT_TIER})")
    p_setup.add_argument("--profile", help="Also init a profile (github:user/repo or path)")
    p_setup.add_argument("--config-dir", help="Override config directory")
    p_setup.add_argument("--dry-run", action="store_true", help="Show what would be done")
    p_setup.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    # init
    p_init = sub.add_parser("init", help="Add a profile")
    p_init.add_argument("source", help="Profile source (github:user/repo or local path)")

    # status
    sub.add_parser("status", help="Show installed profiles and audit summary")

    # clean
    p_clean = sub.add_parser("clean", help="Consolidate settings.json allow list")
    p_clean.add_argument("--dry-run", action="store_true", help="Show analysis without applying")
    p_clean.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    # suggest
    p_suggest = sub.add_parser("suggest", help="Propose config changes from audit data")
    p_suggest.add_argument("--apply", action="store_true", help="Apply suggested changes interactively")
    p_suggest.add_argument("--min-count", type=int, default=None,
                           help="Minimum passthrough count to suggest (default: tier-based)")

    # tighten
    p_tighten = sub.add_parser("tighten",
                               help="Identify and remove stale permission rules (least privilege)")
    p_tighten.add_argument("--apply", action="store_true",
                           help="Remove/suppress stale rules interactively")
    p_tighten.add_argument("--days", type=int, default=None,
                           help="Flag rules unused for N days (default: tier-based)")
    p_tighten.add_argument("--force", action="store_true",
                           help="Allow --apply even with insufficient audit data")

    # update
    p_update = sub.add_parser("update", help="Re-fetch and update profiles")
    p_update.add_argument("name", nargs="?", help="Profile name (or all)")

    # remove
    p_remove = sub.add_parser("remove", help="Remove a profile")
    p_remove.add_argument("name", help="Profile name")

    # dry-run
    p_dry = sub.add_parser("dry-run", help="Show what a profile would change")
    p_dry.add_argument("source", help="Profile source (github:user/repo or local path)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "setup": cmd_setup,
        "init": cmd_init,
        "status": cmd_status,
        "clean": cmd_clean,
        "suggest": cmd_suggest,
        "tighten": cmd_tighten,
        "update": cmd_update,
        "remove": cmd_remove,
        "dry-run": cmd_dry_run,
    }

    handler = commands.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
