#!/usr/bin/env python3
"""
Discord Claude Relay - CLI Entry Point

Usage:
    claude-discord              # Start relay for current workspace (interactive setup if needed)
    claude-discord setup        # Install plugin and configure
    claude-discord register     # Register workspace with bot token
    claude-discord gui          # Open canonical CLADEX manager
    claude-discord status       # Show relay status
    claude-discord logs         # Show logs
    claude-discord stop         # Stop relay
    claude-discord list         # List profiles
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psutil

from claude_common import (
    CONFIG_ROOT,
    DATA_ROOT,
    PROFILES_DIR,
    REGISTRY_PATH,
    atomic_write_json,
    atomic_write_text,
    claude_code_bin,
    claude_code_version,
    default_namespace_for_workspace,
    pid_exists,
    slugify,
    state_dir_for_namespace,
    tail_lines,
    terminate_process_tree,
    token_fingerprint,
    workspace_root,
    follow_file,
)

PACKAGE_NAME = "discord-codex-relay"

ENV_KEY_ORDER = [
    "DISCORD_BOT_TOKEN",
    "RELAY_BOT_NAME",
    "CLAUDE_WORKDIR",
    "STATE_NAMESPACE",
    "ALLOW_DMS",
    "BOT_TRIGGER_MODE",
    "OPERATOR_IDS",
    "ALLOWED_USER_IDS",
    "ALLOWED_CHANNEL_IDS",
    "CHANNEL_HISTORY_LIMIT",
]


def _load_env_file(path: Path) -> dict[str, str]:
    """Load .env file."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _write_env_file(path: Path, env: dict[str, str]) -> None:
    """Write .env file with ordered keys."""
    ordered_keys = [key for key in ENV_KEY_ORDER if key in env]
    ordered_keys.extend(sorted(key for key in env if key not in ordered_keys))
    lines = [f"{key}={env[key]}" for key in ordered_keys]
    atomic_write_text(path, "\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_registry() -> dict:
    """Load workspace registry."""
    if not REGISTRY_PATH.exists():
        return {"profiles": [], "projects": []}
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": [], "projects": []}
    registry.setdefault("profiles", [])
    registry.setdefault("projects", [])
    return registry


def _save_registry(registry: dict) -> None:
    """Save workspace registry."""
    atomic_write_json(REGISTRY_PATH, registry)


def _parse_csv_ids(value: str) -> str:
    """Parse comma-separated IDs."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    valid = [part for part in parts if part.isdigit()]
    return ",".join(valid)


def _profile_from_env(env: dict[str, str]) -> dict:
    """Create profile dict from env."""
    workspace = Path(env["CLAUDE_WORKDIR"]).resolve()
    namespace = env.get("STATE_NAMESPACE") or default_namespace_for_workspace(workspace, token=env.get("DISCORD_BOT_TOKEN"))
    fingerprint = token_fingerprint(env["DISCORD_BOT_TOKEN"])
    name = slugify(f"{namespace}-{fingerprint[:4]}")
    digest = token_fingerprint(str(workspace))[:10]
    profile_env_path = PROFILES_DIR / f"{name}-{digest}.env"

    normalized = dict(env)
    normalized["CLAUDE_WORKDIR"] = str(workspace)
    normalized["STATE_NAMESPACE"] = namespace
    normalized["ALLOW_DMS"] = "true" if normalized.get("ALLOW_DMS", "false").lower() in {"1", "true", "yes"} else "false"
    normalized["BOT_TRIGGER_MODE"] = normalized.get("BOT_TRIGGER_MODE", "mention_or_dm") or "mention_or_dm"
    normalized["CHANNEL_HISTORY_LIMIT"] = str(normalized.get("CHANNEL_HISTORY_LIMIT", "20") or "20")
    normalized["OPERATOR_IDS"] = _parse_csv_ids(normalized.get("OPERATOR_IDS", ""))
    normalized["ALLOWED_USER_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_USER_IDS", ""))
    normalized["ALLOWED_CHANNEL_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_CHANNEL_IDS", ""))
    normalized["CLAUDE_MODEL"] = (normalized.get("CLAUDE_MODEL", "") or "").strip()

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    _write_env_file(profile_env_path, normalized)

    return {
        "name": name,
        "workspace": str(workspace),
        "env_file": str(profile_env_path),
        "state_namespace": namespace,
        "token_fingerprint": fingerprint,
        "bot_name": normalized.get("RELAY_BOT_NAME", ""),
        "backend": "claude-code",
    }


def _get_profile_for_workspace(workspace: Path) -> dict | None:
    """Find profile for workspace."""
    registry = _load_registry()
    workspace_str = str(workspace.resolve())
    for profile in registry.get("profiles", []):
        if profile.get("workspace") == workspace_str:
            return profile
    return None


def _is_profile_running(profile: dict) -> bool:
    """Check if profile relay is running."""
    state_dir = state_dir_for_namespace(profile.get("state_namespace", ""))
    pid_file = state_dir / "relay.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        return pid_exists(pid)
    except Exception:
        return False


def _package_version() -> str:
    """Get package version."""
    try:
        import importlib.metadata
        return importlib.metadata.version(PACKAGE_NAME)
    except Exception:
        return "dev"


# ============================================================================
# Interactive Setup
# ============================================================================

def interactive_setup(workspace: Path) -> dict:
    """Interactive setup wizard for new workspace."""
    print("\n=== Discord Claude Relay Setup ===\n")

    # Bot token
    print("Enter your Discord bot token.")
    print("(Create one at https://discord.com/developers/applications)")
    token = getpass.getpass("Bot token: ").strip()
    if not token:
        raise SystemExit("Bot token is required.")

    # Bot name (optional)
    bot_name = input("Bot name (optional, press Enter to skip): ").strip()

    # Operator ID
    print("\nEnter your Discord user ID (you'll have operator access).")
    print("(Enable Developer Mode in Discord, right-click yourself, Copy ID)")
    operator_id = input("Your Discord user ID: ").strip()
    if not operator_id.isdigit():
        print("Warning: Invalid user ID, proceeding without operator.")
        operator_id = ""

    # DMs allowed?
    allow_dms = input("\nAllow DMs? (y/n, default: y): ").strip().lower()
    allow_dms = allow_dms != "n"

    # Channel ID (optional)
    print("\nEnter a channel ID to restrict the bot to (optional).")
    channel_id = input("Channel ID (press Enter to allow all): ").strip()
    if channel_id and not channel_id.isdigit():
        print("Warning: Invalid channel ID, ignoring.")
        channel_id = ""

    env = {
        "DISCORD_BOT_TOKEN": token,
        "RELAY_BOT_NAME": bot_name,
        "CLAUDE_WORKDIR": str(workspace),
        "OPERATOR_IDS": operator_id,
        "ALLOWED_USER_IDS": operator_id,
        "ALLOW_DMS": "true" if allow_dms else "false",
        "BOT_TRIGGER_MODE": "mention_or_dm",
        "ALLOWED_CHANNEL_IDS": channel_id,
        "CHANNEL_HISTORY_LIMIT": "20",
    }

    profile = _profile_from_env(env)

    # Save to registry
    registry = _load_registry()
    profiles = registry.setdefault("profiles", [])
    profiles[:] = [p for p in profiles if p.get("workspace") != str(workspace)]
    profiles.append(profile)
    _save_registry(registry)

    print(f"\n[OK] Profile saved: {profile['name']}")
    print(f"[OK] Config: {profile['env_file']}")

    return profile


# ============================================================================
# Commands
# ============================================================================

def cmd_setup(args: argparse.Namespace) -> int:
    """Setup command - install plugin and dependencies."""
    print("=== Discord Claude Relay Setup ===\n")

    # Check Claude Code
    claude_bin = claude_code_bin()
    version = claude_code_version()
    print(f"Claude Code: {claude_bin}")
    print(f"Version: {version}")

    if "unknown" in version.lower():
        print("\nWarning: Could not detect Claude Code CLI.")
        print("Make sure Claude Code is installed and 'claude' is in PATH.")

    # Create directories
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nConfig: {CONFIG_ROOT}")
    print(f"Data: {DATA_ROOT}")

    print("\n[OK] Setup complete!")
    print("\nNext steps:")
    print("  1. Run 'claude-discord' in your workspace to register a bot")
    print("  2. Or run 'cladex gui' to open the canonical desktop manager")

    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """Register workspace with bot token."""
    workspace = workspace_root(Path.cwd())

    env = {
        "DISCORD_BOT_TOKEN": args.discord_bot_token,
        "RELAY_BOT_NAME": args.bot_name or "",
        "CLAUDE_WORKDIR": str(workspace),
        "OPERATOR_IDS": _parse_csv_ids(args.operator_ids or ""),
        "ALLOWED_USER_IDS": _parse_csv_ids(args.allowed_user_ids or args.operator_ids or ""),
        "ALLOW_DMS": "true" if args.allow_dms else "false",
        "BOT_TRIGGER_MODE": args.trigger_mode or "mention_or_dm",
        "ALLOWED_CHANNEL_IDS": _parse_csv_ids(args.allowed_channel_id or ""),
        "CHANNEL_HISTORY_LIMIT": str(args.channel_history_limit or 20),
        "CLAUDE_MODEL": (args.model or "").strip(),
    }

    profile = _profile_from_env(env)

    registry = _load_registry()
    profiles = registry.setdefault("profiles", [])
    profiles[:] = [p for p in profiles if p.get("workspace") != str(workspace)]
    profiles.append(profile)
    _save_registry(registry)

    print(f"[OK] Registered: {profile['name']}")
    print(f"[OK] Workspace: {workspace}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show status of relay for current workspace."""
    workspace = workspace_root(Path.cwd())
    profile = _get_profile_for_workspace(workspace)

    if not profile:
        print(f"No relay configured for: {workspace}")
        print("Run 'claude-discord' to set up.")
        return 1

    running = _is_profile_running(profile)

    print(f"Workspace: {workspace}")
    print(f"Profile: {profile['name']}")
    print(f"Bot: {profile.get('bot_name') or '(unnamed)'}")
    print(f"Status: {'RUNNING' if running else 'STOPPED'}")

    state_dir = state_dir_for_namespace(profile.get("state_namespace", ""))
    log_file = state_dir / "relay.log"
    if log_file.exists():
        print(f"\nRecent logs ({log_file}):")
        print(tail_lines(log_file, 10))

    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """Show logs for current workspace relay."""
    workspace = workspace_root(Path.cwd())
    profile = _get_profile_for_workspace(workspace)

    if not profile:
        print(f"No relay configured for: {workspace}")
        return 1

    state_dir = state_dir_for_namespace(profile.get("state_namespace", ""))
    log_file = state_dir / "relay.log"

    if not log_file.exists():
        print("No logs found.")
        return 1

    if args.follow:
        return follow_file(log_file)
    else:
        print(tail_lines(log_file, args.lines or 50))
        return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop relay for current workspace."""
    workspace = workspace_root(Path.cwd())
    profile = _get_profile_for_workspace(workspace)

    if not profile:
        print(f"No relay configured for: {workspace}")
        return 1

    state_dir = state_dir_for_namespace(profile.get("state_namespace", ""))
    pid_file = state_dir / "relay.pid"

    if not pid_file.exists():
        print("Relay not running.")
        return 0

    try:
        pid = int(pid_file.read_text().strip())
        if terminate_process_tree(pid):
            print(f"[OK] Stopped relay (PID {pid})")
            pid_file.unlink(missing_ok=True)
        else:
            print("Relay was not running.")
    except Exception as e:
        print(f"Failed to stop relay: {e}")
        return 1

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all registered profiles."""
    registry = _load_registry()
    profiles = registry.get("profiles", [])

    if not profiles:
        print("No profiles registered.")
        print("Run 'claude-discord' in a workspace to set up.")
        return 0

    print(f"{'Name':<30} {'Status':<10} {'Workspace'}")
    print("-" * 80)

    for profile in profiles:
        name = profile.get("name", "unknown")
        workspace = profile.get("workspace", "?")
        running = _is_profile_running(profile)
        status = "RUNNING" if running else "stopped"
        print(f"{name:<30} {status:<10} {workspace}")

    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Open the canonical CLADEX desktop manager."""
    cladex_bin = shutil.which("cladex")
    if not cladex_bin:
        print("cladex is not installed. Install or update the CLADEX package first.")
        return 1

    kwargs = {"close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
        kwargs["stdin"] = subprocess.DEVNULL

    subprocess.Popen([cladex_bin, "gui"], **kwargs)
    print("Opened canonical CLADEX manager.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run relay (internal command)."""
    workspace = workspace_root(Path.cwd())
    profile = _get_profile_for_workspace(workspace)

    if not profile:
        # Interactive setup
        profile = interactive_setup(workspace)

    env_file = Path(profile["env_file"])
    if not env_file.exists():
        print(f"Config not found: {env_file}")
        return 1

    env = _load_env_file(env_file)

    # Create state directory
    state_dir = state_dir_for_namespace(profile.get("state_namespace", ""))
    state_dir.mkdir(parents=True, exist_ok=True)

    # Log file
    log_file = state_dir / "relay.log"

    print(f"Starting relay for: {workspace}")
    print(f"Profile: {profile['name']}")
    print(f"Logs: {log_file}")

    # Set environment for bot
    run_env = os.environ.copy()
    run_env.update(env)
    run_env["CLAUDE_WORKDIR"] = str(workspace)
    run_env["STATE_NAMESPACE"] = profile.get("state_namespace", "")

    # Run the Python bot
    bot_module = Path(__file__).parent / "bot.py"

    try:
        with open(log_file, "a") as log:
            process = subprocess.Popen(
                [sys.executable, str(bot_module)],
                env=run_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(workspace),
            )
            process.wait()
    except KeyboardInterrupt:
        print("\nStopping relay...")

    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Show version information."""
    print(f"discord-claude-relay {_package_version()}")
    print(f"Claude Code: {claude_code_version()}")
    return 0


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="claude-discord",
        description="Discord relay for Claude Code",
    )
    subparsers = parser.add_subparsers(dest="command")

    # setup
    subparsers.add_parser("setup", help="Install and configure")

    # register
    reg_parser = subparsers.add_parser("register", help="Register workspace")
    reg_parser.add_argument("--discord-bot-token", required=True)
    reg_parser.add_argument("--bot-name")
    reg_parser.add_argument("--operator-ids")
    reg_parser.add_argument("--allowed-user-ids")
    reg_parser.add_argument("--allow-dms", action="store_true")
    reg_parser.add_argument("--trigger-mode", default="mention_or_dm")
    reg_parser.add_argument("--allowed-channel-id")
    reg_parser.add_argument("--channel-history-limit", type=int, default=20)
    reg_parser.add_argument("--model", default="")

    # status
    subparsers.add_parser("status", help="Show status")

    # logs
    logs_parser = subparsers.add_parser("logs", help="Show logs")
    logs_parser.add_argument("-f", "--follow", action="store_true")
    logs_parser.add_argument("-n", "--lines", type=int, default=50)

    # stop
    subparsers.add_parser("stop", help="Stop relay")

    # list
    subparsers.add_parser("list", help="List profiles")

    # gui
    subparsers.add_parser("gui", help="Open GUI manager")

    # run (internal)
    run_parser = subparsers.add_parser("run", help="Run relay")
    run_parser.add_argument("--foreground", action="store_true")

    # version
    subparsers.add_parser("version", help="Show version")

    args = parser.parse_args()

    commands = {
        "setup": cmd_setup,
        "register": cmd_register,
        "status": cmd_status,
        "logs": cmd_logs,
        "stop": cmd_stop,
        "list": cmd_list,
        "gui": cmd_gui,
        "run": cmd_run,
        "version": cmd_version,
    }

    if args.command in commands:
        return commands[args.command](args)

    # Default: run relay for current workspace
    return cmd_run(argparse.Namespace(foreground=False))


if __name__ == "__main__":
    sys.exit(main())
