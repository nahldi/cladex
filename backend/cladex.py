from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil
from platformdirs import user_config_dir, user_data_dir

import relayctl


CLAUDE_APP_NAME = "discord-claude-relay"
CLAUDE_CONFIG_ROOT = Path(user_config_dir(CLAUDE_APP_NAME, False))
CLAUDE_DATA_ROOT = Path(user_data_dir(CLAUDE_APP_NAME, False))
CLAUDE_REGISTRY_PATH = CLAUDE_CONFIG_ROOT / "workspaces.json"
GUI_CHILD_ENV = "CLADEX_GUI_CHILD"


def _load_json_file(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    if not isinstance(payload, dict):
        return dict(default)
    payload.setdefault("profiles", [])
    payload.setdefault("projects", [])
    return payload


def _load_claude_registry() -> dict[str, Any]:
    return _load_json_file(CLAUDE_REGISTRY_PATH, default={"profiles": [], "projects": []})


def _load_claude_env(profile: dict[str, Any]) -> dict[str, str]:
    env_file = str(profile.get("env_file", "")).strip()
    if not env_file:
        return {}
    path = Path(env_file)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _claude_state_dir(profile: dict[str, Any]) -> Path:
    namespace = str(profile.get("state_namespace", "")).strip()
    return CLAUDE_DATA_ROOT / "state" / namespace


def _claude_profile_runtime_state(profile: dict[str, Any]) -> dict[str, Any]:
    state_dir = _claude_state_dir(profile)
    pid_file = state_dir / "relay.pid"
    log_path = state_dir / "relay.log"
    status_file = state_dir / "status.json"
    pid: int | None = None
    running = False
    ready = False
    state = "idle"
    status_message = ""
    session_id = ""
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
        running = pid is not None and psutil.pid_exists(pid)
    if status_file.exists():
        try:
            status_payload = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            status_payload = {}
        raw_status = str(status_payload.get("status", "")).strip().lower()
        status_message = str(status_payload.get("detail", "")).strip()
        session_id = str(status_payload.get("session_id", "")).strip()
        if raw_status == "working":
            state = "working"
        elif raw_status in {"error", "stopped"}:
            state = "idle"
        elif raw_status:
            state = "idle"
        ready = running and raw_status not in {"error", "stopped", ""}
    else:
        ready = running
    return {
        "running": running,
        "ready": ready,
        "pid": pid,
        "log_path": log_path,
        "state_dir": state_dir,
        "state": state,
        "status_message": status_message,
        "session_id": session_id,
    }


def _codex_profiles() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for profile in relayctl._all_registered_profiles():
        runtime = relayctl._profile_runtime_state(profile)
        env = relayctl._normalized_profile_env(relayctl._load_env_file(Path(profile["env_file"])))
        record = dict(profile)
        record.update(
            {
                "_relay_type": "codex",
                "_running": bool(runtime["running"]),
                "_ready": bool(runtime["ready"]),
                "_status": "running" if runtime["running"] else "stopped",
                "_provider": "codex-app-server" if not runtime.get("degraded") else "codex-cli-resume",
                "_model": env.get("CODEX_MODEL", relayctl.DEFAULT_CODEX_MODEL),
                "_trigger_mode": env.get("BOT_TRIGGER_MODE", "mention_or_dm"),
                "_log_path": str(runtime["log_path"]),
            }
        )
        records.append(record)
    return records


def _claude_profiles() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for profile in _load_claude_registry().get("profiles", []):
        runtime = _claude_profile_runtime_state(profile)
        env = _load_claude_env(profile)
        record = dict(profile)
        record.update(
            {
                "_relay_type": "claude",
                "_running": bool(runtime["running"]),
                "_ready": bool(runtime["ready"]),
                "_status": "running" if runtime["running"] else "stopped",
                "_provider": "claude-code",
                "_model": env.get("CLAUDE_MODEL", ""),
                "_trigger_mode": env.get("BOT_TRIGGER_MODE", "mention_or_dm"),
                "_log_path": str(runtime["log_path"]),
                "_state": runtime.get("state", "idle"),
                "_status_message": runtime.get("status_message", ""),
                "_session_id": runtime.get("session_id", ""),
            }
        )
        records.append(record)
    return records


def get_all_profiles() -> list[dict[str, Any]]:
    profiles = [*sorted(_codex_profiles(), key=lambda item: item.get("name", "")), *sorted(_claude_profiles(), key=lambda item: item.get("name", ""))]
    profiles.sort(key=lambda item: (item.get("_relay_type", ""), item.get("name", "")))
    return profiles


def _windowless_popen(command: list[str], *, cwd: str | Path | None = None) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _windowless_run(command: list[str], *, cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, **kwargs)


def _claude_discord_bin() -> str | None:
    for name in ("claude-discord", "claude-discord.cmd", "claude-discord.exe"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def start_profile(profile: dict[str, Any]) -> None:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    if relay_type == "codex":
        relayctl._run_profile(profile)
        return
    if relay_type != "claude":
        raise RuntimeError(f"Unknown relay type: {relay_type}")
    command = _claude_discord_bin()
    if not command:
        raise RuntimeError("`claude-discord` is not installed or not on PATH.")
    _windowless_popen([command, "run"], cwd=profile.get("workspace", ""))


def stop_profile(profile: dict[str, Any]) -> None:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    if relay_type == "codex":
        relayctl._stop_profile(profile)
        return
    if relay_type != "claude":
        raise RuntimeError(f"Unknown relay type: {relay_type}")
    command = _claude_discord_bin()
    if command:
        result = _windowless_run([command, "stop"], cwd=profile.get("workspace", ""))
        if result.returncode == 0:
            return
    runtime = _claude_profile_runtime_state(profile)
    pid = runtime.get("pid")
    if pid:
        relayctl.terminate_process_tree(pid)
    pid_file = _claude_state_dir(profile) / "relay.pid"
    pid_file.unlink(missing_ok=True)


def restart_profile(profile: dict[str, Any]) -> None:
    stop_profile(profile)
    start_profile(profile)


def _filter_profiles(name: str | None = None, relay_type: str | None = None) -> list[dict[str, Any]]:
    profiles = get_all_profiles()
    if relay_type:
        profiles = [item for item in profiles if item.get("_relay_type") == relay_type]
    if name:
        target = name.strip().lower()
        profiles = [item for item in profiles if str(item.get("name", "")).lower() == target]
    return profiles


def _profile_json_record(profile: dict[str, Any]) -> dict[str, Any]:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    attach_channel = (
        profile.get("attach_channel_id")
        or profile.get("discord_channel")
        or profile.get("channel")
        or ""
    )
    return {
        "id": profile.get("name", ""),
        "name": profile.get("name", ""),
        "type": "Claude" if relay_type == "claude" else "Codex",
        "relayType": relay_type,
        "workspace": profile.get("workspace", ""),
        "status": "Running" if profile.get("_running") else "Stopped",
        "running": bool(profile.get("_running")),
        "ready": bool(profile.get("_ready")),
        "provider": profile.get("_provider", ""),
        "model": profile.get("_model", ""),
        "triggerMode": profile.get("_trigger_mode", ""),
        "discordChannel": attach_channel,
        "state": profile.get("_state", "working" if profile.get("_running") else "idle"),
        "statusText": profile.get("_status_message", ""),
        "sessionId": profile.get("_session_id", ""),
        "logPath": profile.get("_log_path", ""),
    }


def _print_table(profiles: list[dict[str, Any]]) -> None:
    if not profiles:
        print("No profiles found.")
        return
    print(f"{'Type':<8} {'Profile':<26} {'Status':<9} {'Ready':<7} {'Model':<16} {'Workspace'}")
    print("-" * 120)
    for profile in profiles:
        print(
            f"{profile.get('_relay_type','?'):<8} "
            f"{str(profile.get('name',''))[:26]:<26} "
            f"{profile.get('_status','?'):<9} "
            f"{('yes' if profile.get('_ready') else 'no'):<7} "
            f"{str(profile.get('_model',''))[:16]:<16} "
            f"{profile.get('workspace','')}"
        )


def cmd_list(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(relay_type=args.type)
    if getattr(args, 'json', False):
        print(json.dumps([_profile_json_record(profile) for profile in profiles]))
        return 0
    _print_table(profiles)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if getattr(args, 'json', False):
        print(
            json.dumps(
                {
                    "running": [p.get("name", "") for p in profiles if p.get("_running")],
                    "profiles": [_profile_json_record(profile) for profile in profiles],
                }
            )
        )
        return 0
    if not profiles:
        print("No matching profiles found.")
        return 1
    for profile in profiles:
        print(f"{profile['name']} [{profile['_relay_type']}]")
        print(f"  running: {'yes' if profile.get('_running') else 'no'}")
        print(f"  ready: {'yes' if profile.get('_ready') else 'no'}")
        print(f"  provider: {profile.get('_provider', '-')}")
        print(f"  model: {profile.get('_model') or '-'}")
        print(f"  trigger: {profile.get('_trigger_mode') or '-'}")
        print(f"  workspace: {profile.get('workspace', '-')}")
        print(f"  log: {profile.get('_log_path', '-')}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    for profile in profiles:
        start_profile(profile)
        print(f"Started {profile['name']} [{profile['_relay_type']}].")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    for profile in profiles:
        stop_profile(profile)
        print(f"Stopped {profile['name']} [{profile['_relay_type']}].")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    for profile in profiles:
        restart_profile(profile)
        print(f"Restarted {profile['name']} [{profile['_relay_type']}].")
    return 0


def _remove_codex_profile(profile: dict[str, Any]) -> None:
    relayctl._stop_profile(profile)
    env_file = Path(str(profile.get("env_file", "")).strip())
    registry = relayctl._load_registry()
    registry["profiles"] = [item for item in registry.get("profiles", []) if item.get("name") != profile.get("name")]
    for project in registry.get("projects", []):
        members = [member for member in project.get("profiles", []) if member != profile.get("name")]
        project["profiles"] = members
    relayctl._save_registry(registry)
    if env_file.exists():
        env_file.unlink(missing_ok=True)


def _remove_claude_profile(profile: dict[str, Any]) -> None:
    stop_profile({**profile, "_relay_type": "claude"})
    env_file = Path(str(profile.get("env_file", "")).strip())
    registry = _load_claude_registry()
    registry["profiles"] = [item for item in registry.get("profiles", []) if item.get("name") != profile.get("name")]
    _claude_registry_path = CLAUDE_REGISTRY_PATH
    _claude_registry_path.parent.mkdir(parents=True, exist_ok=True)
    _claude_registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    if env_file.exists():
        env_file.unlink(missing_ok=True)


def cmd_remove(args: argparse.Namespace) -> int:
    if not args.name:
        print("Provide a profile name to remove.")
        return 1
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    profile = profiles[0]
    relay_type = profile.get("_relay_type")
    if relay_type == "codex":
        _remove_codex_profile(profile)
    elif relay_type == "claude":
        _remove_claude_profile(profile)
    else:
        raise RuntimeError(f"Unknown relay type: {relay_type}")
    print(f"Removed {profile['name']} [{relay_type}].")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    profile = profiles[0]
    log_path = Path(str(profile.get("_log_path", "")).strip())
    lines = max(int(getattr(args, "lines", 80) or 80), 1)
    if not log_path.exists():
        if getattr(args, "json", False):
            print(json.dumps({"logs": []}))
            return 0
        print(f"No log file found for {profile['name']}.")
        return 1
    text = relayctl.tail_lines(log_path, lines)
    if getattr(args, "json", False):
        print(json.dumps({"logs": [line for line in text.splitlines() if line.strip()]}))
        return 0
    print(text, end="")
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    if os.environ.get(GUI_CHILD_ENV, "").strip() != "1":
        env = os.environ.copy()
        env[GUI_CHILD_ENV] = "1"
        python_exe = sys.executable
        if os.name == "nt":
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            if pythonw.exists():
                python_exe = str(pythonw)
        command = [python_exe, "-m", "cladex", "gui"]
        _windowless_popen(command)
        print("Opened CLADEX manager GUI.")
        return 0

    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as exc:
        print(f"GUI unavailable: {exc}")
        return 1

    root = tk.Tk()
    root.title("CLADEX")
    root.geometry("1380x820")
    root.minsize(1120, 620)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    root.configure(bg="#f2ede3")
    style.configure(".", font=("Segoe UI", 10))
    style.configure("Header.TLabel", font=("Segoe UI Semibold", 18), background="#f2ede3")
    style.configure("Sub.TLabel", font=("Segoe UI", 10), background="#f2ede3", foreground="#68604f")
    style.configure("Treeview", rowheight=26)

    header = ttk.Frame(root)
    header.pack(fill="x", padx=18, pady=(18, 8))
    ttk.Label(header, text="CLADEX", style="Header.TLabel").pack(side="left")
    ttk.Label(
        header,
        text="Unified Claude + Codex relay manager in one repo.",
        style="Sub.TLabel",
    ).pack(side="left", padx=10)

    toolbar = ttk.Frame(root)
    toolbar.pack(fill="x", padx=18, pady=(0, 10))

    content = ttk.Frame(root)
    content.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    tree_frame = ttk.Frame(content)
    tree_frame.pack(side="left", fill="both", expand=True)
    side = ttk.LabelFrame(content, text="Actions", padding=10)
    side.pack(side="right", fill="y", padx=(12, 0))

    columns = ("type", "name", "running", "ready", "provider", "model", "workspace")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
    for key, label, width in (
        ("type", "Type", 80),
        ("name", "Profile", 230),
        ("running", "Running", 80),
        ("ready", "Ready", 70),
        ("provider", "Backend", 140),
        ("model", "Model", 130),
        ("workspace", "Workspace", 520),
    ):
        tree.heading(key, text=label)
        tree.column(key, width=width, anchor="w")
    tree.column("running", anchor="center")
    tree.column("ready", anchor="center")
    tree.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    scrollbar.pack(side="left", fill="y")
    tree.configure(yscrollcommand=scrollbar.set)

    activity = tk.Text(root, height=10, bg="#111827", fg="#dde7ff", font=("Cascadia Mono", 10), relief="flat")
    activity.pack(fill="x", padx=18, pady=(0, 18))
    activity.insert("end", "CLADEX ready.\n")
    activity.configure(state="disabled")

    records: dict[str, dict[str, Any]] = {}

    def log(text: str) -> None:
        activity.configure(state="normal")
        activity.insert("end", text.rstrip() + "\n")
        activity.see("end")
        activity.configure(state="disabled")

    def refresh() -> None:
        tree.delete(*tree.get_children())
        records.clear()
        for profile in get_all_profiles():
            item_id = tree.insert(
                "",
                "end",
                values=(
                    profile.get("_relay_type", ""),
                    profile.get("name", ""),
                    "yes" if profile.get("_running") else "no",
                    "yes" if profile.get("_ready") else "no",
                    profile.get("_provider", ""),
                    profile.get("_model", ""),
                    profile.get("workspace", ""),
                ),
            )
            records[item_id] = profile

    def _selected() -> list[dict[str, Any]]:
        return [records[item_id] for item_id in tree.selection() if item_id in records]

    def _run_action(action_name: str, fn) -> None:
        selected = _selected()
        if not selected:
            messagebox.showwarning("CLADEX", "Select at least one profile.")
            return
        for profile in selected:
            try:
                fn(profile)
                log(f"{action_name}: {profile.get('name')} [{profile.get('_relay_type')}]")
            except Exception as exc:
                log(f"{action_name} failed: {profile.get('name')} -> {exc}")
        refresh()

    ttk.Button(toolbar, text="Refresh", command=refresh).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Start Selected", command=lambda: _run_action("started", start_profile)).pack(side="left", padx=8)
    ttk.Button(toolbar, text="Stop Selected", command=lambda: _run_action("stopped", stop_profile)).pack(side="left", padx=8)
    ttk.Button(toolbar, text="Restart Selected", command=lambda: _run_action("restarted", restart_profile)).pack(side="left", padx=8)

    ttk.Button(side, text="Start", width=18, command=lambda: _run_action("started", start_profile)).pack(pady=4)
    ttk.Button(side, text="Stop", width=18, command=lambda: _run_action("stopped", stop_profile)).pack(pady=4)
    ttk.Button(side, text="Restart", width=18, command=lambda: _run_action("restarted", restart_profile)).pack(pady=4)
    ttk.Button(side, text="Refresh", width=18, command=refresh).pack(pady=12)

    refresh()
    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cladex", description="Unified manager for Codex and Claude Discord relays.")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List all profiles.")
    list_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    list_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    list_parser.set_defaults(func=cmd_list)

    status_parser = subparsers.add_parser("status", help="Show profile status.")
    status_parser.add_argument("name", nargs="?")
    status_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    status_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    status_parser.set_defaults(func=cmd_status)

    start_parser = subparsers.add_parser("start", help="Start a profile or all profiles of a type.")
    start_parser.add_argument("name", nargs="?")
    start_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    start_parser.set_defaults(func=cmd_start)

    stop_parser = subparsers.add_parser("stop", help="Stop a profile or all profiles of a type.")
    stop_parser.add_argument("name", nargs="?")
    stop_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    stop_parser.set_defaults(func=cmd_stop)

    restart_parser = subparsers.add_parser("restart", help="Restart a profile or all profiles of a type.")
    restart_parser.add_argument("name", nargs="?")
    restart_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    restart_parser.set_defaults(func=cmd_restart)

    logs_parser = subparsers.add_parser("logs", help="Show recent logs for one profile.")
    logs_parser.add_argument("name")
    logs_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    logs_parser.add_argument("--lines", type=int, default=80)
    logs_parser.add_argument("--json", action="store_true", help="Output logs as JSON")
    logs_parser.set_defaults(func=cmd_logs)

    remove_parser = subparsers.add_parser("remove", help="Remove a profile from the unified registry.")
    remove_parser.add_argument("name")
    remove_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    remove_parser.set_defaults(func=cmd_remove)

    gui_parser = subparsers.add_parser("gui", help="Open the CLADEX GUI.")
    gui_parser.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return cmd_gui(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
