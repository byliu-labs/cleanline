"""CLI entry point for Clean Line.

Usage: cleanline <command> [options]

Commands:
  setup              First-time onboarding
  init <source>      Add a profile (github:user/repo or local path)
  status             Show installed profiles and audit summary
  suggest            Propose config changes from audit data
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

    if args.uninstall:
        result = setup_cmd.run_uninstall(
            config_dir,
            auto_yes=args.yes,
        )
        return _print_result(result, "Uninstall")

    result = setup_cmd.run_setup(
        config_dir,
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

    # Hook health check
    health_warnings = result.get("hook_health", [])
    if health_warnings:
        print("\nHook Health Warnings")
        print("--------------------")
        for w in health_warnings:
            print(f"  ! {w}")

    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    """Run the suggest command."""
    events = audit_mod.read_audit_log()
    if not events:
        print("No audit data found. Run Claude Code with hooks enabled to generate data.")
        return 0

    suggestions = suggest_mod.generate_suggestions(events)

    print("\nSuggested Config Changes")
    print("========================")

    cmd_groups = suggestions.get("command_groups", [])
    if cmd_groups:
        print("\nVersion Groups (add to bashAliases):")
        for group in cmd_groups:
            print(f"  {group['canonical']}:")
            for variant, count in group["variants"]:
                print(f"    {variant}: {count} passthroughs")

    domain_groups = suggestions.get("domain_groups", [])
    if domain_groups:
        print("\nDomain Groups (add to webfetch.extraDomains):")
        for group in domain_groups:
            print(f"  {group['pattern']}:")
            for sub, count in group["subdomains"]:
                print(f"    {sub}: {count} passthroughs")

    top_cmds = suggestions.get("top_commands", [])
    if top_cmds and not cmd_groups:
        print("\nTop Passthrough Commands:")
        for cmd, count in top_cmds:
            print(f"  {cmd}: {count}")

    top_doms = suggestions.get("top_domains", [])
    if top_doms and not domain_groups:
        print("\nTop Passthrough Domains:")
        for dom, count in top_doms:
            print(f"  {dom}: {count}")

    if not any([cmd_groups, domain_groups, top_cmds, top_doms]):
        print("  No suggestions -- all passthroughs are low frequency.")

    # --apply: apply suggestions interactively
    if args.apply and any([cmd_groups, domain_groups]):
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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="cleanline",
        description="Take the clean line -- fewer permission prompts in Claude Code.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # setup
    p_setup = sub.add_parser("setup", help="First-time onboarding")
    p_setup.add_argument("--profile", help="Also init a profile (github:user/repo or path)")
    p_setup.add_argument("--config-dir", help="Override config directory")
    p_setup.add_argument("--dry-run", action="store_true", help="Show what would be done")
    p_setup.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_setup.add_argument("--uninstall", action="store_true", help="Remove hooks and clean up")

    # init
    p_init = sub.add_parser("init", help="Add a profile")
    p_init.add_argument("source", help="Profile source (github:user/repo or local path)")

    # status
    sub.add_parser("status", help="Show installed profiles and audit summary")

    # suggest
    p_suggest = sub.add_parser("suggest", help="Propose config changes from audit data")
    p_suggest.add_argument("--apply", action="store_true", help="Apply suggested changes interactively")

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
        "suggest": cmd_suggest,
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
