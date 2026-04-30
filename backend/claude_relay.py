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
import contextlib
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psutil

from agent_guardrails import assert_workspace_allowed
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
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_MODEL",
    "CLAUDE_PERMISSION_MODE",
    "CLADEX_ALLOW_CLADEX_WORKSPACE",
    "STATE_NAMESPACE",
    "ALLOW_DMS",
    "BOT_TRIGGER_MODE",
    "OPERATOR_IDS",
    "ALLOWED_USER_IDS",
    "ALLOWED_BOT_IDS",
    "ALLOWED_CHANNEL_IDS",
    "CHANNEL_HISTORY_LIMIT",
]


def _load_env_file(path: Path) -> dict[str, str]:
    """Load .env file, transparently resolving secret-ref values via the
    OS-native secret store (Windows DPAPI / fs0600 elsewhere)."""
    import secret_store

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
    return secret_store.materialize_env_secrets(env)


def _load_env_file_raw(path: Path) -> dict[str, str]:
    """Load .env values without resolving secret refs."""
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


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _sanitize_env_value(key: str, value: str) -> str:
    text = "" if value is None else str(value)
    if "\x00" in text or "\r" in text or "\n" in text:
        raise ValueError(
            f"Profile env value for {key!r} contains forbidden control characters; "
            "newline/carriage-return injection could create new settings on reload."
        )
    return text


def _write_env_file(path: Path, env: dict[str, str]) -> None:
    """Write .env file with ordered keys; route sensitive values through
    the OS-native secret store and persist `secret-ref:scheme:id`
    placeholders instead of plaintext tokens."""
    import secret_store

    safe_env: dict[str, str] = {}
    for key, value in env.items():
        if not _ENV_KEY_RE.match(str(key)):
            raise ValueError(f"Refusing to persist profile env key with unsupported characters: {key!r}")
        safe_env[str(key)] = _sanitize_env_value(str(key), value)
    profile_hint = path.stem
    existing_env = _load_env_file_raw(path) if path.exists() else {}
    safe_env, stale_secret_refs = secret_store.prepare_sensitive_env_for_write(
        safe_env,
        profile_hint=profile_hint,
        existing_env=existing_env,
    )
    ordered_keys = [key for key in ENV_KEY_ORDER if key in safe_env]
    ordered_keys.extend(sorted(key for key in safe_env if key not in ordered_keys))
    lines = [f"{key}={safe_env[key]}" for key in ordered_keys]
    atomic_write_text(path, "\n".join(lines) + "\n")
    for reference in stale_secret_refs:
        secret_store.delete_secret(reference)
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


@contextlib.contextmanager
def _registry_lock():
    """Cross-process file lock around the Claude registry load+mutate+save
    transaction. Without this two concurrent profile registrations (e.g. a
    desktop API call racing a CLI register) can read the same registry
    snapshot, each append a new profile, and have one of them lose the
    other's append on save."""
    from relay_common import _acquire_file_lock, _release_file_lock

    lock_path = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".lock")
    handle = _acquire_file_lock(lock_path)
    try:
        yield
    finally:
        _release_file_lock(handle)


def _parse_csv_ids(value: str) -> str:
    """Parse comma-separated IDs."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    valid = [part for part in parts if part.isdigit()]
    return ",".join(valid)


def _require_workspace_allowed(workspace: Path, env: dict[str, str] | None = None) -> None:
    try:
        assert_workspace_allowed(workspace, env=env)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _env_flag_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _require_profile_access_invariant(env: dict[str, str]) -> None:
    allow_dms = _env_flag_enabled(env.get("ALLOW_DMS"))
    channel_ids = _parse_csv_ids(env.get("ALLOWED_CHANNEL_IDS", ""))
    user_ids = _parse_csv_ids(env.get("ALLOWED_USER_IDS", ""))
    operator_ids = _parse_csv_ids(env.get("OPERATOR_IDS", ""))
    approved = _parse_csv_ids(",".join(part for part in (user_ids, operator_ids) if part))
    if allow_dms and not approved:
        raise SystemExit("ALLOW_DMS=true requires at least one approved numeric user or operator id.")
    if not channel_ids and not approved:
        raise SystemExit("Claude profiles require at least one numeric allowed channel id or approved numeric sender id.")


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
    claude_config_dir = str(normalized.get("CLAUDE_CONFIG_DIR", "")).strip()
    if claude_config_dir:
        normalized["CLAUDE_CONFIG_DIR"] = str(Path(claude_config_dir).expanduser().resolve())
    else:
        normalized.pop("CLAUDE_CONFIG_DIR", None)
    normalized["STATE_NAMESPACE"] = namespace
    normalized["ALLOW_DMS"] = "true" if normalized.get("ALLOW_DMS", "false").lower() in {"1", "true", "yes"} else "false"
    normalized["BOT_TRIGGER_MODE"] = normalized.get("BOT_TRIGGER_MODE", "mention_or_dm") or "mention_or_dm"
    normalized["CHANNEL_HISTORY_LIMIT"] = str(normalized.get("CHANNEL_HISTORY_LIMIT", "20") or "20")
    normalized["OPERATOR_IDS"] = _parse_csv_ids(normalized.get("OPERATOR_IDS", ""))
    normalized["ALLOWED_USER_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_USER_IDS", ""))
    normalized["ALLOWED_BOT_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_BOT_IDS", ""))
    normalized["ALLOWED_CHANNEL_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_CHANNEL_IDS", ""))
    normalized["CLAUDE_MODEL"] = (normalized.get("CLAUDE_MODEL", "") or "").strip()
    normalized["CLAUDE_PERMISSION_MODE"] = (normalized.get("CLAUDE_PERMISSION_MODE", "default") or "default").strip()
    _require_profile_access_invariant(normalized)

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
    namespace = str(profile.get("state_namespace", "")).strip() or None
    try:
        pid = int(pid_file.read_text().strip())
        return pid_exists(pid) and _pid_matches_claude_relay(pid, namespace=namespace)
    except Exception:
        return False


def _pid_matches_claude_relay(pid: int, *, namespace: str | None = None) -> bool:
    """Check whether `pid` is one of OUR Claude bot processes.

    The historical liveness check matched any process with `claude_bot.py`,
    `claude_relay.py`, or `claude-discord` in its command line. F0002 audit
    finding: a stale or reused `relay.pid` from a different profile (or a
    user's own `python -m claude_relay`) would pass that check, and
    `cmd_stop` would terminate the wrong process. The fix is to ALSO
    require an environment match: if a `state_namespace` is provided and
    the process exposes a different `STATE_NAMESPACE` env var, refuse.
    """
    try:
        process = psutil.Process(pid)
        command_line = " ".join(process.cmdline()).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False
    if not (
        "claude_bot.py" in command_line
        or "claude_relay.py" in command_line
        or "claude-discord" in command_line
    ):
        return False
    if not namespace:
        # Legacy callers without namespace context — fall back to the
        # cmdline-only check so existing helpers don't break.
        return True
    try:
        proc_env = process.environ()
        proc_namespace = str(proc_env.get("STATE_NAMESPACE", "")).strip()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        # Env unreadable on Windows for cross-user / elevated processes.
        # Be conservative: do NOT match — refuse to act on a pid we
        # cannot positively identify as ours.
        return False
    return proc_namespace == namespace


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
    _require_workspace_allowed(workspace)
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
        raise SystemExit(
            "A valid Discord user ID is required so the relay only responds to a known operator. "
            "Re-run setup and provide the numeric Discord user ID."
        )

    # DMs allowed? (Default to off; opening DMs requires deliberate opt-in.)
    allow_dms_input = input("\nAllow DMs? (y/n, default: n): ").strip().lower()
    allow_dms = allow_dms_input == "y"

    # Channel ID (optional, but required if DMs are off)
    print("\nEnter a channel ID to restrict the bot to.")
    print("(Required unless you enabled DMs above.)")
    channel_id = input("Channel ID: ").strip()
    if channel_id and not channel_id.isdigit():
        raise SystemExit("Invalid channel ID; must be a numeric Discord channel id.")

    if not channel_id and not allow_dms:
        raise SystemExit(
            "Refusing to register a Claude relay with no allowed channels and DMs disabled. "
            "Provide a channel ID or enable DMs (with a known operator)."
        )
    if not channel_id and allow_dms and not operator_id:
        raise SystemExit(
            "DMs require at least one operator/user ID so direct messages are scoped to a known operator."
        )

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
    with _registry_lock():
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
    if args.workspace:
        workspace = workspace_root(Path(args.workspace).resolve())
    else:
        workspace = workspace_root(Path.cwd())
    allow_cladex_workspace = bool(getattr(args, "allow_cladex_workspace", False))
    if not allow_cladex_workspace:
        _require_workspace_allowed(workspace)

    discord_bot_token = (args.discord_bot_token or "").strip()
    if not discord_bot_token:
        env_token = os.environ.get("CLADEX_REGISTER_DISCORD_BOT_TOKEN", "").strip()
        if env_token:
            discord_bot_token = env_token
            os.environ.pop("CLADEX_REGISTER_DISCORD_BOT_TOKEN", None)
    if not discord_bot_token:
        print(
            "[ERR] Discord bot token is required. Pass --discord-bot-token or set "
            "CLADEX_REGISTER_DISCORD_BOT_TOKEN in the environment.",
            file=sys.stderr,
        )
        return 2
    args.discord_bot_token = discord_bot_token

    def _reject_non_numeric_csv(value: str | None, flag_name: str) -> None:
        if not value:
            return
        for raw in str(value).split(","):
            text = raw.strip()
            if text and not text.isdigit():
                print(
                    f"[ERR] {flag_name} expects numeric Discord IDs; received {text!r}. "
                    "Non-numeric IDs are silently dropped during normalization, which "
                    "can leave the profile with an empty allowlist.",
                    file=sys.stderr,
                )
                raise SystemExit(2)

    _reject_non_numeric_csv(args.allowed_channel_id, "--allowed-channel-id")
    _reject_non_numeric_csv(args.allowed_user_ids, "--allowed-user-ids")
    _reject_non_numeric_csv(args.operator_ids, "--operator-ids")
    _reject_non_numeric_csv(getattr(args, "allowed_bot_ids", None), "--allowed-bot-ids")

    allowed_channel_ids = _parse_csv_ids(args.allowed_channel_id or "")
    allowed_user_ids = _parse_csv_ids(args.allowed_user_ids or args.operator_ids or "")
    allow_dms = bool(args.allow_dms)
    if not allowed_channel_ids and not allowed_user_ids:
        print(
            "[ERR] Refusing to register a Claude relay with empty allowlists. "
            "Provide at least one --allowed-channel-id or --allowed-user-ids/--operator-ids "
            "so the bot only responds to authorized senders.",
            file=sys.stderr,
        )
        return 2
    if allow_dms and not allowed_user_ids:
        print(
            "[ERR] --allow-dms requires --allowed-user-ids or --operator-ids so direct messages "
            "are scoped to a known operator. Add an allowlist or drop --allow-dms.",
            file=sys.stderr,
        )
        return 2

    env = {
        "DISCORD_BOT_TOKEN": args.discord_bot_token,
        "RELAY_BOT_NAME": args.bot_name or "",
        "CLAUDE_WORKDIR": str(workspace),
        "CLAUDE_CONFIG_DIR": args.claude_config_dir or "",
        "OPERATOR_IDS": _parse_csv_ids(args.operator_ids or ""),
        "ALLOWED_USER_IDS": allowed_user_ids,
        "ALLOWED_BOT_IDS": _parse_csv_ids(getattr(args, "allowed_bot_ids", "") or ""),
        "ALLOW_DMS": "true" if allow_dms else "false",
        "BOT_TRIGGER_MODE": args.trigger_mode or "mention_or_dm",
        "ALLOWED_CHANNEL_IDS": allowed_channel_ids,
        "CHANNEL_HISTORY_LIMIT": str(args.channel_history_limit or 20),
        "CLAUDE_MODEL": (args.model or "").strip(),
        "CLAUDE_PERMISSION_MODE": "default",
    }
    if allow_cladex_workspace:
        env["CLADEX_ALLOW_CLADEX_WORKSPACE"] = "true"

    profile = _profile_from_env(env)

    with _registry_lock():
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

    namespace = str(profile.get("state_namespace", "")).strip() or None
    try:
        pid = int(pid_file.read_text().strip())
        if not _pid_matches_claude_relay(pid, namespace=namespace):
            print(f"Removed stale relay PID file (PID {pid} is not this profile's Claude relay).")
            pid_file.unlink(missing_ok=True)
            return 0
        if terminate_process_tree(pid):
            print(f"[OK] Stopped relay (PID {pid})")
            pid_file.unlink(missing_ok=True)
        else:
            print("Relay was not running.")
            pid_file.unlink(missing_ok=True)
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
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
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
    _require_profile_access_invariant(env)
    _require_workspace_allowed(workspace, env)

    # Refuse to launch a duplicate Claude bot for this profile. Without this
    # guard a second `cmd_run` (e.g. operator double-click, supervisor
    # restart racing a slow shutdown, accidental re-launch from the desktop
    # app) overwrites the same `relay.pid` file and leaves the original
    # process unstoppable via the PID file. _is_profile_running checks for
    # an existing live PID + matching command line.
    if _is_profile_running(profile):
        print(
            f"Claude relay for `{profile.get('name', '?')}` is already running; "
            f"refusing to start a duplicate. Stop the existing relay first."
        )
        return 0

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
    bot_module = Path(__file__).parent / "claude_bot.py"

    process: subprocess.Popen | None = None
    try:
        with open(log_file, "a") as log:
            process = subprocess.Popen(
                [sys.executable, str(bot_module)],
                env=run_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(workspace),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            process.wait()
            return process.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping relay...")
        if process is not None and process.poll() is None:
            terminate_process_tree(process.pid)

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
    reg_parser.add_argument("--workspace", help="Workspace path (defaults to cwd)")
    reg_parser.add_argument(
        "--discord-bot-token",
        default=None,
        help=(
            "Discord bot token for this relay profile. Prefer setting "
            "CLADEX_REGISTER_DISCORD_BOT_TOKEN in the environment so the "
            "token is not visible in process command lines."
        ),
    )
    reg_parser.add_argument("--bot-name")
    reg_parser.add_argument("--operator-ids")
    reg_parser.add_argument("--allowed-user-ids")
    reg_parser.add_argument("--allowed-bot-ids")
    reg_parser.add_argument("--allow-dms", action="store_true")
    reg_parser.add_argument("--trigger-mode", default="mention_or_dm")
    reg_parser.add_argument("--allowed-channel-id")
    reg_parser.add_argument("--channel-history-limit", type=int, default=20)
    reg_parser.add_argument("--model", default="")
    reg_parser.add_argument("--claude-config-dir", default="", help="Optional CLAUDE_CONFIG_DIR for this relay profile/account")
    reg_parser.add_argument(
        "--allow-cladex-workspace",
        action="store_true",
        help="Allow this profile to use the CLADEX runtime repository as its workspace; only use for deliberate CLADEX development.",
    )

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
