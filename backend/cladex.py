from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import psutil
from platformdirs import user_config_dir, user_data_dir

from agent_guardrails import assert_workspace_allowed, workspace_protection_violation
import claude_relay
import relayctl
import review_swarm
import fix_orchestrator


CLAUDE_APP_NAME = "discord-claude-relay"
CLAUDE_CONFIG_ROOT = Path(user_config_dir(CLAUDE_APP_NAME, False))
CLAUDE_DATA_ROOT = Path(user_data_dir(CLAUDE_APP_NAME, False))
CLAUDE_REGISTRY_PATH = CLAUDE_CONFIG_ROOT / "workspaces.json"
CLADEX_APP_NAME = "cladex"
CLADEX_CONFIG_ROOT = Path(user_config_dir(CLADEX_APP_NAME, False))
CLADEX_PROJECTS_PATH = CLADEX_CONFIG_ROOT / "projects.json"
GUI_CHILD_ENV = "CLADEX_GUI_CHILD"
OPERATOR_POLL_INTERVAL_SECONDS = 0.25
OPERATOR_TIMEOUT_SECONDS = 120


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


def _load_cladex_projects() -> dict[str, Any]:
    payload = _load_json_file(CLADEX_PROJECTS_PATH, default={"projects": []})
    projects = payload.get("projects", [])
    if projects:
        return payload
    legacy = relayctl._load_registry().get("projects", [])
    if not legacy:
        return payload
    migrated = {
        "projects": [
            _project_record(
                str(project.get("name", "")).strip(),
                [
                    _member_ref(profile)
                    for profile_name in project.get("profiles", [])
                    for profile in _filter_profiles(name=str(profile_name), relay_type=None)
                ],
            )
            for project in legacy
            if str(project.get("name", "")).strip()
        ]
    }
    migrated["projects"] = [project for project in migrated["projects"] if project.get("members")]
    if migrated["projects"]:
        _save_cladex_projects(migrated)
        return migrated
    return payload


def _save_cladex_projects(payload: dict[str, Any]) -> None:
    CLADEX_CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    relayctl.atomic_write_text(CLADEX_PROJECTS_PATH, json.dumps(payload, indent=2) + "\n")


def _save_claude_registry(registry: dict[str, Any]) -> None:
    CLAUDE_CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    relayctl.atomic_write_text(CLAUDE_REGISTRY_PATH, json.dumps(registry, indent=2) + "\n")


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


def _workspace_label(workspace: str) -> str:
    text = str(workspace or "").strip()
    if not text:
        return "Workspace"
    return Path(text).name or text


def _humanize_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text.replace("_", " ").replace("-", " ").strip()
    parts = [part for part in candidate.split() if part]
    if not parts:
        return text
    return " ".join(part.capitalize() for part in parts[:4])


def _looks_technical_label(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    if re.fullmatch(r"[a-z0-9]+-[0-9a-f]{6,}", text):
        return True
    return text in {"codexcmd", "claudecmd", "relay", "bot"}


def _display_name(profile: dict[str, Any]) -> str:
    bot_name = str(profile.get("_bot_name") or profile.get("bot_name") or "").strip()
    if bot_name and not _looks_technical_label(bot_name):
        return bot_name if re.search(r"[A-Z\s]", bot_name) else _humanize_name(bot_name)
    workspace_name = _workspace_label(str(profile.get("workspace", "")))
    if workspace_name and workspace_name.lower() not in {"workspace", "repo"}:
        return _humanize_name(workspace_name)
    return _humanize_name(str(profile.get("name", ""))) or "Relay"


def _channel_label(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    return f"Channel {text}"


def _profile_lookup_key(profile: dict[str, Any]) -> tuple[str, str]:
    return (
        str(profile.get("_relay_type", "")).strip().lower(),
        str(profile.get("name", "")).strip().lower(),
    )


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
    active_worktree = ""
    active_channel = ""
    model = ""
    effort = ""
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
        active_worktree = str(status_payload.get("active_worktree", "")).strip()
        active_channel = str(status_payload.get("active_channel", "")).strip()
        model = str(status_payload.get("model", "")).strip()
        effort = str(status_payload.get("effort", "")).strip()
        if raw_status == "working":
            state = "working"
        elif raw_status in {"error", "stopped"}:
            state = "idle"
        elif raw_status:
            state = "idle"
        ready = running and raw_status not in {"error", "stopped", "", "starting"}
    else:
        # No status.json yet: the worker has been spawned but has not reached
        # its ready signal (Discord login + CLI handshake). Treat as
        # not-ready so polling consumers don't claim the profile is up.
        ready = False
    return {
        "running": running,
        "ready": ready,
        "pid": pid,
        "log_path": log_path,
        "state_dir": state_dir,
        "state": state,
        "status_message": status_message,
        "session_id": session_id,
        "active_worktree": active_worktree,
        "active_channel": active_channel,
        "model": model,
        "effort": effort,
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
                "_codex_home": env.get("CODEX_HOME", ""),
                "_trigger_mode": env.get("BOT_TRIGGER_MODE", "mention_or_dm"),
                "_log_path": str(runtime["log_path"]),
                "_bot_name": env.get("RELAY_BOT_NAME", profile.get("bot_name", "")),
                "_allow_dms": env.get("ALLOW_DMS", "false").strip().lower() in {"1", "true", "yes"},
                "_state_namespace": profile.get("state_namespace", ""),
                "_effort": env.get("CODEX_REASONING_EFFORT_DEFAULT", "high"),
                "_allowed_user_ids": env.get("ALLOWED_USER_IDS", ""),
                "_allowed_bot_ids": env.get("ALLOWED_BOT_IDS", ""),
                "_allowed_channel_ids": env.get("ALLOWED_CHANNEL_IDS", ""),
                "_allowed_channel_author_ids": env.get("ALLOWED_CHANNEL_AUTHOR_IDS", ""),
                "_channel_no_mention_author_ids": env.get("CHANNEL_NO_MENTION_AUTHOR_IDS", ""),
                "_channel_history_limit": env.get("CHANNEL_HISTORY_LIMIT", "20"),
                "_startup_dm_user_ids": env.get("STARTUP_DM_USER_IDS", ""),
                "_startup_dm_text": env.get("STARTUP_DM_TEXT", ""),
                "_startup_channel_text": env.get("STARTUP_CHANNEL_TEXT", ""),
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
                "_model": runtime.get("model") or env.get("CLAUDE_MODEL", ""),
                "_claude_config_dir": env.get("CLAUDE_CONFIG_DIR", ""),
                "_trigger_mode": env.get("BOT_TRIGGER_MODE", "mention_or_dm"),
                "_log_path": str(runtime["log_path"]),
                "_state": runtime.get("state", "idle"),
                "_status_message": runtime.get("status_message", ""),
                "_session_id": runtime.get("session_id", ""),
                "_active_worktree": runtime.get("active_worktree", ""),
                "_active_channel": runtime.get("active_channel", ""),
                "_effort": runtime.get("effort", ""),
                "_bot_name": env.get("RELAY_BOT_NAME", profile.get("bot_name", "")),
                "_allow_dms": env.get("ALLOW_DMS", "false").strip().lower() in {"1", "true", "yes"},
                "_state_namespace": profile.get("state_namespace", ""),
                "_operator_ids": env.get("OPERATOR_IDS", ""),
                "_allowed_user_ids": env.get("ALLOWED_USER_IDS", ""),
                "_allowed_bot_ids": env.get("ALLOWED_BOT_IDS", ""),
                "_allowed_channel_ids": env.get("ALLOWED_CHANNEL_IDS", ""),
                "_channel_history_limit": env.get("CHANNEL_HISTORY_LIMIT", "20"),
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
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
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


def _backend_script_path(name: str) -> str:
    return str(Path(__file__).parent / name)


def _ensure_claude_background_runtime() -> None:
    """Ensure the Claude background runtime Python environment exists."""
    runtime_python = relayctl.install_plugin.runtime_python_path()
    if not runtime_python.exists():
        source = relayctl.install_plugin._install_source()
        relayctl.install_plugin._ensure_runtime(source=source)


def start_profile(profile: dict[str, Any], *, start_reason: str = "operator-start") -> None:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    if relay_type == "codex":
        workspace = Path(str(profile.get("workspace", "") or Path.cwd()))
        env_file = Path(str(profile.get("env_file", "")).strip()) if profile.get("env_file") else None
        env: dict[str, str] = {}
        if env_file is not None and env_file.is_file():
            env = relayctl._normalized_profile_env(relayctl._load_env_file(env_file))
        assert_workspace_allowed(workspace, env=env)
        relayctl._run_profile(profile)
        return
    if relay_type != "claude":
        raise RuntimeError(f"Unknown relay type: {relay_type}")
    _ensure_claude_background_runtime()

    # Load env from env_file
    env_file = profile.get("env_file", "")
    if not env_file or not Path(env_file).exists():
        raise RuntimeError(f"Config file not found: {env_file}")

    env = _load_claude_env(profile)
    workspace = str(profile.get("workspace", "") or Path.cwd())
    assert_workspace_allowed(Path(workspace), env=env)
    state_namespace = str(profile.get("state_namespace", "")).strip()

    # Create state directory
    state_dir = _claude_state_dir(profile)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Set up environment for bot
    run_env = os.environ.copy()
    run_env.update(env)
    run_env["CLAUDE_WORKDIR"] = workspace
    run_env["STATE_NAMESPACE"] = state_namespace
    run_env["CLADEX_START_REASON"] = start_reason

    # Log file
    log_file = state_dir / "relay.log"
    log_handle = log_file.open("a")

    # Launch bot.py in background
    popen_kwargs: dict[str, Any] = {
        "cwd": workspace,
        "env": run_env,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    bot_script = _backend_script_path("claude_bot.py")
    process = subprocess.Popen(
        [relayctl._background_python_windowless_executable(), bot_script],
        **popen_kwargs,
    )
    log_handle.close()

    # Write PID file
    pid_file = state_dir / "relay.pid"
    pid_file.write_text(str(process.pid))


def stop_profile(profile: dict[str, Any]) -> None:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    if relay_type == "codex":
        relayctl._stop_profile(profile)
        return
    if relay_type != "claude":
        raise RuntimeError(f"Unknown relay type: {relay_type}")

    # Terminate process using PID file
    runtime = _claude_profile_runtime_state(profile)
    pid = runtime.get("pid")
    if pid:
        relayctl.terminate_process_tree(pid)

    # Clean up PID file
    pid_file = _claude_state_dir(profile) / "relay.pid"
    pid_file.unlink(missing_ok=True)


def restart_profile(profile: dict[str, Any]) -> None:
    stop_profile(profile)
    start_profile(profile, start_reason="operator-restart")


def _filter_profiles(name: str | None = None, relay_type: str | None = None) -> list[dict[str, Any]]:
    profiles = get_all_profiles()
    if relay_type:
        profiles = [item for item in profiles if item.get("_relay_type") == relay_type]
    if name:
        target = name.strip().lower()
        profiles = [
            item
            for item in profiles
            if target
            in {
                str(item.get("name", "")).lower(),
                str(item.get("_bot_name", "")).strip().lower(),
                _display_name(item).lower(),
            }
        ]
    return profiles


def _profile_json_record(profile: dict[str, Any]) -> dict[str, Any]:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    attach_channel = (
        profile.get("attach_channel_id")
        or profile.get("discord_channel")
        or profile.get("channel")
        or ""
    )
    display_name = _display_name(profile)
    return {
        "id": profile.get("name", ""),
        "name": profile.get("name", ""),
        "displayName": display_name,
        "technicalName": profile.get("name", ""),
        "workspaceLabel": _workspace_label(profile.get("workspace", "")),
        "type": "Claude" if relay_type == "claude" else "Codex",
        "relayType": relay_type,
        "workspace": profile.get("workspace", ""),
        "status": "Running" if profile.get("_running") else "Stopped",
        "running": bool(profile.get("_running")),
        "ready": bool(profile.get("_ready")),
        "provider": profile.get("_provider", ""),
        "model": profile.get("_model", ""),
        "codexHome": profile.get("_codex_home", "") if relay_type == "codex" else "",
        "claudeConfigDir": profile.get("_claude_config_dir", "") if relay_type == "claude" else "",
        "triggerMode": profile.get("_trigger_mode", ""),
        "botName": profile.get("_bot_name", ""),
        "allowDms": bool(profile.get("_allow_dms", False)),
        "stateNamespace": profile.get("_state_namespace", profile.get("state_namespace", "")),
        "effort": profile.get("_effort", ""),
        "discordChannel": attach_channel,
        "channelLabel": _channel_label(profile.get("_active_channel") or attach_channel),
        "state": profile.get("_state", "working" if profile.get("_running") else "idle"),
        "statusText": profile.get("_status_message", ""),
        "sessionId": profile.get("_session_id", ""),
        "activeWorktree": profile.get("_active_worktree", ""),
        "activeChannel": profile.get("_active_channel", ""),
        "logPath": profile.get("_log_path", ""),
        "operatorIds": profile.get("_operator_ids", ""),
        "allowedUserIds": profile.get("_allowed_user_ids", ""),
        "allowedBotIds": profile.get("_allowed_bot_ids", ""),
        "allowedChannelIds": profile.get("_allowed_channel_ids", ""),
        "allowedChannelAuthorIds": profile.get("_allowed_channel_author_ids", ""),
        "channelNoMentionAuthorIds": profile.get("_channel_no_mention_author_ids", ""),
        "channelHistoryLimit": str(profile.get("_channel_history_limit", "") or ""),
        "startupDmUserIds": profile.get("_startup_dm_user_ids", ""),
        "startupDmText": profile.get("_startup_dm_text", ""),
        "startupChannelText": profile.get("_startup_channel_text", ""),
    }


def _profile_state_dir(profile: dict[str, Any]) -> Path:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    namespace = str(profile.get("state_namespace") or profile.get("_state_namespace") or "").strip()
    if not namespace:
        raise RuntimeError(f"Profile `{profile.get('name', '')}` is missing a state namespace.")
    if relay_type == "codex":
        return relayctl.state_dir_for_namespace(namespace)
    if relay_type == "claude":
        return claude_relay.state_dir_for_namespace(namespace)
    raise RuntimeError(f"Unknown relay type: {relay_type}")


def _operator_dir(profile: dict[str, Any]) -> Path:
    return _profile_state_dir(profile) / "operator"


def _operator_requests_dir(profile: dict[str, Any]) -> Path:
    return _operator_dir(profile) / "requests"


def _operator_responses_dir(profile: dict[str, Any]) -> Path:
    return _operator_dir(profile) / "responses"


def _operator_history_path(profile: dict[str, Any]) -> Path:
    return _operator_dir(profile) / "history.json"


def _read_operator_history(profile: dict[str, Any]) -> list[dict[str, Any]]:
    path = _operator_history_path(profile)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    messages = payload.get("messages") if isinstance(payload, dict) else None
    return messages if isinstance(messages, list) else []


def _chat_with_profile(
    profile: dict[str, Any],
    *,
    message: str,
    channel_id: str | None = None,
    sender_name: str = "Operator",
    sender_id: str = "0",
    timeout_seconds: int = OPERATOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    requests_dir = _operator_requests_dir(profile)
    responses_dir = _operator_responses_dir(profile)
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    request_path = requests_dir / f"{request_id}.json"
    response_path = responses_dir / f"{request_id}.json"
    payload = {
        "id": request_id,
        "message": message.strip(),
        "channelId": str(channel_id or "").strip(),
        "senderName": sender_name.strip() or "Operator",
        "senderId": str(sender_id or "0").strip() or "0",
        "createdAt": time.time(),
    }
    relayctl.atomic_write_text(request_path, json.dumps(payload, indent=2))
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if response_path.exists():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            finally:
                response_path.unlink(missing_ok=True)
            if not isinstance(response, dict):
                raise RuntimeError("Operator bridge returned invalid data.")
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "Operator bridge failed.")))
            return response
        time.sleep(OPERATOR_POLL_INTERVAL_SECONDS)
    request_path.unlink(missing_ok=True)
    raise RuntimeError("Timed out waiting for the running relay to answer the local operator message.")


def _update_profile_registry(registry: dict[str, Any], *, name: str, changes: dict[str, Any]) -> None:
    for item in registry.get("profiles", []):
        if item.get("name") == name:
            item.update(changes)
            return


def _update_codex_profile(
    profile: dict[str, Any],
    *,
    workspace: str | None = None,
    discord_bot_token: str | None = None,
    bot_name: str | None = None,
    model: str | None = None,
    codex_home: str | None = None,
    trigger_mode: str | None = None,
    allow_dms: bool | None = None,
    allowed_user_ids: str | None = None,
    allowed_bot_ids: str | None = None,
    allowed_channel_id: str | None = None,
    allowed_channel_author_ids: str | None = None,
    channel_no_mention_author_ids: str | None = None,
    channel_history_limit: str | None = None,
    startup_dm_user_ids: str | None = None,
    startup_dm_text: str | None = None,
    startup_channel_text: str | None = None,
) -> None:
    env_path = Path(str(profile.get("env_file", "")).strip())
    env = relayctl._normalized_profile_env(relayctl._load_env_file(env_path))
    if workspace is not None and workspace.strip():
        env["CODEX_WORKDIR"] = str(Path(workspace.strip()).expanduser().resolve())
    if discord_bot_token is not None and discord_bot_token.strip():
        env["DISCORD_BOT_TOKEN"] = discord_bot_token.strip()
    if bot_name is not None:
        env["RELAY_BOT_NAME"] = bot_name.strip()
    if model is not None:
        normalized_model = model.strip()
        env["RELAY_MODEL"] = normalized_model
        env["CODEX_MODEL"] = env["RELAY_MODEL"]
    if codex_home is not None:
        env["CODEX_HOME"] = codex_home.strip()
    if trigger_mode is not None:
        env["BOT_TRIGGER_MODE"] = trigger_mode.strip() or env.get("BOT_TRIGGER_MODE", "mention_or_dm")
    if allow_dms is not None:
        env["ALLOW_DMS"] = "true" if allow_dms else "false"
    if allowed_user_ids is not None:
        env["ALLOWED_USER_IDS"] = relayctl._parse_csv_ids(allowed_user_ids)
    if allowed_bot_ids is not None:
        env["ALLOWED_BOT_IDS"] = relayctl._parse_csv_ids(allowed_bot_ids)
    if allowed_channel_id is not None:
        channel_ids = relayctl._parse_csv_ids(allowed_channel_id)
        env["ALLOWED_CHANNEL_IDS"] = channel_ids
        env["RELAY_ATTACH_CHANNEL_ID"] = channel_ids.split(",", 1)[0] if channel_ids else ""
    if allowed_channel_author_ids is not None:
        env["ALLOWED_CHANNEL_AUTHOR_IDS"] = relayctl._parse_csv_ids(allowed_channel_author_ids)
    if channel_no_mention_author_ids is not None:
        env["CHANNEL_NO_MENTION_AUTHOR_IDS"] = relayctl._parse_csv_ids(channel_no_mention_author_ids)
    if channel_history_limit is not None:
        env["CHANNEL_HISTORY_LIMIT"] = str(channel_history_limit).strip() or env.get("CHANNEL_HISTORY_LIMIT", "20")
    if startup_dm_user_ids is not None:
        env["STARTUP_DM_USER_IDS"] = relayctl._parse_csv_ids(startup_dm_user_ids)
    if startup_dm_text is not None:
        env["STARTUP_DM_TEXT"] = startup_dm_text.strip()
    if startup_channel_text is not None:
        env["STARTUP_CHANNEL_TEXT"] = startup_channel_text.strip()
    env = relayctl._normalized_profile_env(env)
    assert_workspace_allowed(Path(env["CODEX_WORKDIR"]), env=env)
    new_profile = relayctl._profile_from_env(env)
    relayctl._replace_profile_registration(profile, new_profile)


def _update_claude_profile(
    profile: dict[str, Any],
    *,
    workspace: str | None = None,
    discord_bot_token: str | None = None,
    bot_name: str | None = None,
    model: str | None = None,
    claude_config_dir: str | None = None,
    trigger_mode: str | None = None,
    allow_dms: bool | None = None,
    allowed_user_ids: str | None = None,
    allowed_bot_ids: str | None = None,
    allowed_channel_id: str | None = None,
    operator_ids: str | None = None,
    channel_history_limit: str | None = None,
) -> None:
    env_path = Path(str(profile.get("env_file", "")).strip())
    env = claude_relay._load_env_file(env_path)
    if workspace is not None and workspace.strip():
        env["CLAUDE_WORKDIR"] = str(Path(workspace.strip()).expanduser().resolve())
    if discord_bot_token is not None and discord_bot_token.strip():
        env["DISCORD_BOT_TOKEN"] = discord_bot_token.strip()
    if bot_name is not None:
        env["RELAY_BOT_NAME"] = bot_name.strip()
    if model is not None:
        env["CLAUDE_MODEL"] = model.strip()
    if claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = claude_config_dir.strip()
    if trigger_mode is not None:
        env["BOT_TRIGGER_MODE"] = trigger_mode.strip() or env.get("BOT_TRIGGER_MODE", "mention_or_dm")
    if allow_dms is not None:
        env["ALLOW_DMS"] = "true" if allow_dms else "false"
    if operator_ids is not None:
        env["OPERATOR_IDS"] = claude_relay._parse_csv_ids(operator_ids)
    if allowed_user_ids is not None:
        env["ALLOWED_USER_IDS"] = claude_relay._parse_csv_ids(allowed_user_ids)
    if allowed_bot_ids is not None:
        env["ALLOWED_BOT_IDS"] = claude_relay._parse_csv_ids(allowed_bot_ids)
    if allowed_channel_id is not None:
        env["ALLOWED_CHANNEL_IDS"] = claude_relay._parse_csv_ids(allowed_channel_id)
    if channel_history_limit is not None:
        env["CHANNEL_HISTORY_LIMIT"] = str(channel_history_limit).strip() or env.get("CHANNEL_HISTORY_LIMIT", "20")
    env["ALLOW_DMS"] = "true" if env.get("ALLOW_DMS", "false").lower() in {"1", "true", "yes"} else "false"
    env["BOT_TRIGGER_MODE"] = env.get("BOT_TRIGGER_MODE", "mention_or_dm") or "mention_or_dm"
    env["OPERATOR_IDS"] = claude_relay._parse_csv_ids(env.get("OPERATOR_IDS", ""))
    env["ALLOWED_USER_IDS"] = claude_relay._parse_csv_ids(env.get("ALLOWED_USER_IDS", ""))
    env["ALLOWED_BOT_IDS"] = claude_relay._parse_csv_ids(env.get("ALLOWED_BOT_IDS", ""))
    env["ALLOWED_CHANNEL_IDS"] = claude_relay._parse_csv_ids(env.get("ALLOWED_CHANNEL_IDS", ""))
    env["CLAUDE_MODEL"] = (env.get("CLAUDE_MODEL", "") or "").strip()
    assert_workspace_allowed(Path(env["CLAUDE_WORKDIR"]), env=env)
    new_profile = claude_relay._profile_from_env(env)
    registry = _load_claude_registry()
    previous_name = str(profile.get("name", "")).strip()
    previous_env = str(profile.get("env_file", "")).strip().lower()
    registry["profiles"] = [
        item
        for item in registry.get("profiles", [])
        if str(item.get("name", "")).strip() != previous_name
        and str(item.get("env_file", "")).strip().lower() != previous_env
    ]
    registry.setdefault("profiles", []).append(new_profile)
    registry["profiles"].sort(key=lambda item: str(item.get("name", "")).lower())
    _save_claude_registry(registry)
    previous_env_path = Path(str(profile.get("env_file", "")).strip()) if profile.get("env_file") else None
    new_env_path = Path(str(new_profile.get("env_file", "")).strip()) if new_profile.get("env_file") else None
    if previous_env_path and previous_env_path != new_env_path:
        previous_env_path.unlink(missing_ok=True)


def update_profile(
    profile: dict[str, Any],
    *,
    workspace: str | None = None,
    discord_bot_token: str | None = None,
    bot_name: str | None = None,
    model: str | None = None,
    codex_home: str | None = None,
    claude_config_dir: str | None = None,
    trigger_mode: str | None = None,
    allow_dms: bool | None = None,
    allowed_user_ids: str | None = None,
    allowed_bot_ids: str | None = None,
    allowed_channel_id: str | None = None,
    allowed_channel_author_ids: str | None = None,
    channel_no_mention_author_ids: str | None = None,
    operator_ids: str | None = None,
    channel_history_limit: str | None = None,
    startup_dm_user_ids: str | None = None,
    startup_dm_text: str | None = None,
    startup_channel_text: str | None = None,
) -> None:
    relay_type = str(profile.get("_relay_type", "")).strip().lower()
    if relay_type == "codex":
        _update_codex_profile(
            profile,
            workspace=workspace,
            discord_bot_token=discord_bot_token,
            bot_name=bot_name,
            model=model,
            codex_home=codex_home,
            trigger_mode=trigger_mode,
            allow_dms=allow_dms,
            allowed_user_ids=allowed_user_ids,
            allowed_bot_ids=allowed_bot_ids,
            allowed_channel_id=allowed_channel_id,
            allowed_channel_author_ids=allowed_channel_author_ids,
            channel_no_mention_author_ids=channel_no_mention_author_ids,
            channel_history_limit=channel_history_limit,
            startup_dm_user_ids=startup_dm_user_ids,
            startup_dm_text=startup_dm_text,
            startup_channel_text=startup_channel_text,
        )
        return
    if relay_type == "claude":
        _update_claude_profile(
            profile,
            workspace=workspace,
            discord_bot_token=discord_bot_token,
            bot_name=bot_name,
            model=model,
            claude_config_dir=claude_config_dir,
            trigger_mode=trigger_mode,
            allow_dms=allow_dms,
            allowed_user_ids=allowed_user_ids,
            allowed_bot_ids=allowed_bot_ids,
            allowed_channel_id=allowed_channel_id,
            operator_ids=operator_ids,
            channel_history_limit=channel_history_limit,
        )
        return
    raise RuntimeError(f"Unknown relay type: {relay_type}")


def stop_all_profiles(*, relay_type: str | None = None) -> list[dict[str, Any]]:
    profiles = _filter_profiles(relay_type=relay_type)
    for profile in profiles:
        stop_profile(profile)
    return profiles


def _project_record(name: str, members: list[dict[str, str]]) -> dict[str, Any]:
    return {"name": name, "members": members}


def _member_ref(profile: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(profile.get("name", "")),
        "relayType": str(profile.get("_relay_type", "")),
    }


def _resolve_project_members(project: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    all_profiles = {_profile_lookup_key(profile): profile for profile in get_all_profiles()}
    resolved: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for member in project.get("members", []):
        relay_type = str(member.get("relayType", "")).strip().lower()
        name = str(member.get("name", "")).strip()
        profile = all_profiles.get((relay_type, name.lower()))
        if profile is None:
            missing.append({"name": name, "relayType": relay_type})
        else:
            resolved.append(profile)
    return resolved, missing


def _project_json_record(project: dict[str, Any]) -> dict[str, Any]:
    resolved, missing = _resolve_project_members(project)
    return {
        "name": str(project.get("name", "")),
        "memberCount": len(project.get("members", [])),
        "members": [
            {
                "id": profile.get("name", ""),
                "displayName": _display_name(profile),
                "relayType": profile.get("_relay_type", ""),
                "workspace": profile.get("workspace", ""),
            }
            for profile in resolved
        ],
        "missingMembers": missing,
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


def cmd_stop_all(args: argparse.Namespace) -> int:
    profiles = stop_all_profiles(relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"stopped": [profile.get("name", "") for profile in profiles]}))
        return 0
    print(f"Stopped {len(profiles)} relay(s).")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    record = _profile_json_record(profiles[0])
    if getattr(args, "json", False):
        print(json.dumps(record))
        return 0
    print(json.dumps(record, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    allow_dms: bool | None = None
    if getattr(args, "allow_dms", False):
        allow_dms = True
    elif getattr(args, "deny_dms", False):
        allow_dms = False
    discord_bot_token = getattr(args, "discord_bot_token", None)
    if discord_bot_token is None or not str(discord_bot_token).strip():
        env_var = (getattr(args, "discord_bot_token_env", None) or "").strip()
        if env_var:
            env_value = os.environ.get(env_var, "").strip()
            if env_value:
                discord_bot_token = env_value
                # Drop the value once consumed so it never flows on to children.
                os.environ.pop(env_var, None)
        else:
            fallback = os.environ.get("CLADEX_REGISTER_DISCORD_BOT_TOKEN", "").strip()
            if fallback:
                discord_bot_token = fallback
                os.environ.pop("CLADEX_REGISTER_DISCORD_BOT_TOKEN", None)
    update_profile(
        profiles[0],
        workspace=getattr(args, "workspace", None),
        discord_bot_token=discord_bot_token,
        bot_name=getattr(args, "bot_name", None),
        model=getattr(args, "model", None),
        codex_home=getattr(args, "codex_home", None),
        claude_config_dir=getattr(args, "claude_config_dir", None),
        trigger_mode=getattr(args, "trigger_mode", None),
        allow_dms=allow_dms,
        operator_ids=getattr(args, "operator_ids", None),
        allowed_user_ids=getattr(args, "allowed_user_ids", None),
        allowed_bot_ids=getattr(args, "allowed_bot_ids", None),
        allowed_channel_id=getattr(args, "allowed_channel_id", None),
        allowed_channel_author_ids=getattr(args, "allowed_channel_author_ids", None),
        channel_no_mention_author_ids=getattr(args, "channel_no_mention_author_ids", None),
        channel_history_limit=getattr(args, "channel_history_limit", None),
        startup_dm_user_ids=getattr(args, "startup_dm_user_ids", None),
        startup_dm_text=getattr(args, "startup_dm_text", None),
        startup_channel_text=getattr(args, "startup_channel_text", None),
    )
    refreshed = _filter_profiles(name=args.name, relay_type=args.type)
    record = _profile_json_record(refreshed[0]) if refreshed else {}
    if getattr(args, "json", False):
        print(json.dumps(record))
        return 0
    print(f"Updated {profiles[0]['name']} [{profiles[0]['_relay_type']}].")
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


def _doctor_command(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception as exc:
        return {"ok": False, "command": command, "error": str(exc)}
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return {"ok": result.returncode == 0, "command": command, "returncode": result.returncode, "output": output}


def _doctor_version(name: str, command: list[str]) -> dict[str, Any]:
    result = _doctor_command(command)
    output = str(result.get("output", "")).splitlines()
    return {
        "name": name,
        "ok": bool(result.get("ok")),
        "version": output[0] if output else "",
        "detail": result.get("error") or result.get("output", ""),
    }


def _doctor_codex_app_server_schema() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cladex-codex-schema-") as temp_dir:
        out_dir = Path(temp_dir)
        result = _doctor_command(
            [relayctl.resolve_codex_bin(), "app-server", "generate-json-schema", "--out", str(out_dir)]
        )
        schemas = sorted(item.name for item in out_dir.glob("*.json"))
    ok = bool(result.get("ok")) and bool(schemas)
    detail = str(result.get("error") or result.get("output") or "").strip()
    if bool(result.get("ok")) and not schemas:
        detail = "Codex app-server schema command succeeded but wrote no JSON schema files."
    return {
        "name": "codex-app-server-schema",
        "ok": ok,
        "version": f"{len(schemas)} schema file(s)" if ok else "",
        "detail": detail,
        "schemas": schemas,
    }


def _doctor_windows_powershell_shim(name: str) -> dict[str, Any]:
    check_name = f"{name}-powershell-shim"
    if os.name != "nt":
        return {"name": check_name, "ok": True, "warning": False, "detail": "not applicable"}
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or ""
    if not powershell:
        return {"name": check_name, "ok": True, "warning": False, "detail": "PowerShell not found"}
    result = _doctor_command([powershell, "-NoProfile", "-Command", f"{name} --version"])
    output = str(result.get("output", "")).strip()
    ok = bool(result.get("ok"))
    detail = output or str(result.get("error") or "").strip()
    if not ok and "cannot be loaded because running scripts is disabled" in detail:
        detail = (
            f"PowerShell cannot invoke `{name}` because the npm .ps1 shim is blocked by execution policy. "
            f"CLADEX uses the resolved executable internally; users can run `{name}.cmd` or adjust PowerShell policy."
        )
    return {"name": check_name, "ok": ok, "warning": not ok, "detail": detail}


def _doctor_profiles() -> dict[str, Any]:
    profiles = get_all_profiles()
    ports: dict[str, list[str]] = {}
    unsafe_workspaces: list[dict[str, str]] = []
    account_homes: dict[str, dict[str, list[str]]] = {"codex": {}, "claude": {}}
    for profile in profiles:
        relay_type = str(profile.get("_relay_type", "")).lower()
        env: dict[str, str] = {}
        if relay_type == "codex":
            try:
                env = relayctl._normalized_profile_env(relayctl._load_env_file(Path(str(profile.get("env_file", "")))))
            except Exception:
                env = {}
            port = str(env.get("CODEX_APP_SERVER_PORT", "")).strip()
            if port:
                ports.setdefault(port, []).append(str(profile.get("name", "")))
            home = str(env.get("CODEX_HOME", "")).strip() or "(default Codex home)"
            account_homes["codex"].setdefault(home, []).append(str(profile.get("name", "")))
        elif relay_type == "claude":
            env = _load_claude_env(profile)
            home = str(env.get("CLAUDE_CONFIG_DIR", "")).strip() or "(default Claude config)"
            account_homes["claude"].setdefault(home, []).append(str(profile.get("name", "")))
        else:
            continue

        workspace = str(profile.get("workspace", "")).strip()
        if not workspace:
            continue
        violation = workspace_protection_violation(workspace, env=env)
        if violation:
            unsafe_workspaces.append(
                {
                    "name": str(profile.get("name", "")),
                    "relayType": relay_type,
                    "workspace": workspace,
                    "reason": violation,
                }
            )
    duplicate_ports = {port: names for port, names in ports.items() if len(names) > 1}
    shared_account_homes = {
        relay_type: {home: names for home, names in homes.items() if len(names) > 1}
        for relay_type, homes in account_homes.items()
    }
    return {
        "count": len(profiles),
        "codex": sum(1 for item in profiles if item.get("_relay_type") == "codex"),
        "claude": sum(1 for item in profiles if item.get("_relay_type") == "claude"),
        "running": [item.get("name", "") for item in profiles if item.get("_running")],
        "duplicateCodexPorts": duplicate_ports,
        "unsafeWorkspaces": unsafe_workspaces,
        "accountHomes": {relay_type: len(homes) for relay_type, homes in account_homes.items()},
        "sharedAccountHomes": shared_account_homes,
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = [
        _doctor_version("node", ["node", "--version"]),
        _doctor_version("npm", ["cmd", "/c", "npm", "--version"] if os.name == "nt" else ["npm", "--version"]),
        {
            "name": "python",
            "ok": True,
            "version": sys.version.split()[0],
            "detail": sys.executable,
        },
        _doctor_version("codex", [relayctl.resolve_codex_bin(), "--version"]),
        _doctor_version("claude", [claude_relay.claude_code_bin(), "--version"]),
        _doctor_codex_app_server_schema(),
    ]
    warnings = [
        _doctor_windows_powershell_shim("codex"),
        _doctor_windows_powershell_shim("claude"),
    ]
    profiles = _doctor_profiles()
    ok = (
        all(bool(item.get("ok")) for item in checks)
        and not profiles["duplicateCodexPorts"]
        and not profiles.get("unsafeWorkspaces")
    )
    payload = {
        "ok": ok,
        "repo": str(Path(__file__).resolve().parents[1]),
        "checks": checks,
        "warnings": warnings,
        "profiles": profiles,
        "ciCommands": [
            "npm ci",
            "npm audit",
            "npm run lint",
            "npm run build",
            "npm run api:smoke",
            "python -m pip install -e \"backend[dev]\" -c backend/constraints.txt",
            "python backend/relayctl.py privacy-audit --tracked-only .",
            "python -m pytest --tb=short -q",
            "python backend/cladex.py doctor --json",
            "npm run electron:build",
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"CLADEX doctor: {'ok' if ok else 'issues found'}")
        for check in checks:
            status = "ok" if check.get("ok") else "fail"
            version = check.get("version") or check.get("detail") or "-"
            print(f"- {check['name']}: {status} ({version})")
        for warning in warnings:
            if not warning.get("warning"):
                continue
            print(f"- warning {warning['name']}: {warning.get('detail') or '-'}")
        print(f"- profiles: {profiles['count']} total, {profiles['running']} running")
        if profiles["duplicateCodexPorts"]:
            print(f"- duplicate Codex ports: {profiles['duplicateCodexPorts']}")
        if profiles.get("unsafeWorkspaces"):
            print(f"- unsafe workspaces: {profiles['unsafeWorkspaces']}")
        if profiles.get("sharedAccountHomes"):
            shared = {kind: homes for kind, homes in profiles["sharedAccountHomes"].items() if homes}
            if shared:
                print(f"- shared account homes: {shared}")
    return 0 if ok else 1


def cmd_chat(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    payload = _chat_with_profile(
        profiles[0],
        message=str(args.message or "").strip(),
        channel_id=args.channel_id,
        sender_name=args.sender_name or "Operator",
        sender_id=args.sender_id or "0",
    )
    if getattr(args, "json", False):
        print(json.dumps(payload))
        return 0
    print(payload.get("reply", ""))
    return 0


def cmd_chat_history(args: argparse.Namespace) -> int:
    profiles = _filter_profiles(name=args.name, relay_type=args.type)
    if not profiles:
        print("No matching profiles found.")
        return 1
    payload = {"messages": _read_operator_history(profiles[0])}
    if getattr(args, "json", False):
        print(json.dumps(payload))
        return 0
    print(json.dumps(payload, indent=2))
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    payload = _load_cladex_projects()
    records = [_project_json_record(project) for project in payload.get("projects", [])]
    if getattr(args, "json", False):
        print(json.dumps(records))
        return 0
    if not records:
        print("No saved workgroups.")
        return 0
    for record in records:
        members = ", ".join(member["displayName"] for member in record["members"]) or "none"
        print(f"{record['name']}\t{record['memberCount']}\t{members}")
    return 0


def cmd_project_save(args: argparse.Namespace) -> int:
    name = str(args.name or "").strip()
    if not name:
        print("Provide a workgroup name.")
        return 1
    members: list[dict[str, str]] = []
    for raw_member in args.member:
        if ":" not in raw_member:
            raise SystemExit("Members must use relayType:name format, for example `codex:Tyson`.")
        relay_type, member_name = raw_member.split(":", 1)
        matches = _filter_profiles(name=member_name, relay_type=relay_type.strip().lower())
        if not matches:
            raise SystemExit(f"No matching profile found for `{raw_member}`.")
        members.append(_member_ref(matches[0]))
    if not members:
        raise SystemExit("Select at least one relay for the workgroup.")
    payload = _load_cladex_projects()
    projects = [project for project in payload.get("projects", []) if str(project.get("name", "")).strip().lower() != name.lower()]
    projects.append(_project_record(name, members))
    projects.sort(key=lambda item: str(item.get("name", "")).lower())
    payload["projects"] = projects
    _save_cladex_projects(payload)
    print(f"Saved workgroup `{name}` with {len(members)} relay(s).")
    return 0


def _project_by_name(name: str) -> dict[str, Any]:
    target = str(name or "").strip().lower()
    for project in _load_cladex_projects().get("projects", []):
        if str(project.get("name", "")).strip().lower() == target:
            return project
    raise SystemExit(f"No saved workgroup named `{name}`.")


def cmd_project_start(args: argparse.Namespace) -> int:
    project = _project_by_name(args.name)
    members, missing = _resolve_project_members(project)
    for profile in members:
        start_profile(profile)
    if getattr(args, "json", False):
        print(json.dumps({"started": [profile.get("name", "") for profile in members], "missing": missing}))
        return 0
    print(f"Started workgroup `{project['name']}`.")
    return 0


def cmd_project_stop(args: argparse.Namespace) -> int:
    project = _project_by_name(args.name)
    members, missing = _resolve_project_members(project)
    for profile in members:
        stop_profile(profile)
    if getattr(args, "json", False):
        print(json.dumps({"stopped": [profile.get("name", "") for profile in members], "missing": missing}))
        return 0
    print(f"Stopped workgroup `{project['name']}`.")
    return 0


def cmd_project_remove(args: argparse.Namespace) -> int:
    target = str(args.name or "").strip()
    payload = _load_cladex_projects()
    projects = payload.get("projects", [])
    remaining = [project for project in projects if str(project.get("name", "")).strip().lower() != target.lower()]
    if len(remaining) == len(projects):
        print(f"No saved workgroup named `{target}`.")
        return 1
    payload["projects"] = remaining
    _save_cladex_projects(payload)
    print(f"Removed workgroup `{target}`.")
    return 0


def cmd_review_list(args: argparse.Namespace) -> int:
    records = review_swarm.list_reviews()
    if getattr(args, "json", False):
        print(json.dumps(records))
        return 0
    if not records:
        print("No review jobs.")
        return 0
    for record in records:
        progress = record.get("progress", {})
        print(
            f"{record.get('id')}\t{record.get('status')}\t"
            f"running {progress.get('running', 0)}/{progress.get('total', 0)}\t"
            f"done {progress.get('done', 0)}/{progress.get('total', 0)}\t"
            f"{record.get('workspace')}"
        )
    return 0


def cmd_review_start(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.start_review(
            args.workspace,
            provider=args.provider,
            agents=args.agents,
            title=args.title or "",
            account_home=args.account_home or "",
            launch=not getattr(args, "no_background", False),
            preflight_only=getattr(args, "preflight_only", False),
            allow_self_review=getattr(args, "allow_cladex_self_review", False),
            backup_before_review=not getattr(args, "no_backup", False),
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Started review `{record['id']}`. Report: {record.get('reportPath')}")
    return 0


def cmd_backup_list(args: argparse.Namespace) -> int:
    records = review_swarm.list_backups()
    if getattr(args, "json", False):
        print(json.dumps(records))
        return 0
    if not records:
        print("No CLADEX source backups.")
        return 0
    for record in records:
        print(f"{record.get('id')}\t{record.get('createdAt')}\t{record.get('reason')}\t{record.get('workspace')}")
    return 0


def cmd_backup_create(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.create_source_backup(args.workspace, reason=args.reason or "manual")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Created backup `{record['id']}` for {record['workspace']}.")
    return 0


def cmd_backup_restore(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.restore_backup(args.id, confirm=args.confirm or "")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Restored backup `{record['id']}`. Pre-restore backup: {record.get('preRestoreBackupId')}")
    return 0


def cmd_review_run(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.run_review_job(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Review `{record['id']}` is {record.get('status')}.")
    return 0


def cmd_review_show(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.show_review(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(json.dumps(record, indent=2))
    return 0


def cmd_review_fix_plan(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.create_fix_plan(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Wrote fix plan: {record.get('fixPlanPath')}")
    return 0


def cmd_review_cancel(args: argparse.Namespace) -> int:
    try:
        record = review_swarm.cancel_review(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Cancellation requested for review `{record.get('id')}` (status: {record.get('status')}).")
    return 0


def cmd_fix_list(args: argparse.Namespace) -> int:
    records = fix_orchestrator.list_fix_runs()
    if getattr(args, "json", False):
        print(json.dumps(records))
        return 0
    if not records:
        print("No fix runs.")
        return 0
    for record in records:
        progress = record.get("progress", {})
        print(
            f"{record.get('id')}\t{record.get('status')}\t"
            f"running {progress.get('running', 0)}/{progress.get('total', 0)}\t"
            f"done {progress.get('done', 0)}/{progress.get('total', 0)}\t"
            f"{record.get('workspace')}"
        )
    return 0


def cmd_fix_start(args: argparse.Namespace) -> int:
    try:
        record = fix_orchestrator.start_fix_run(
            args.review,
            max_agents=args.max_agents,
            allow_self_fix=getattr(args, "allow_cladex_self_fix", False),
            launch=not getattr(args, "no_background", False),
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Started fix run `{record['id']}`. Report: {record.get('reportPath')}")
    return 0


def cmd_fix_run(args: argparse.Namespace) -> int:
    try:
        record = fix_orchestrator.run_fix_run(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Fix run `{record['id']}` is {record.get('status')}.")
    return 0


def cmd_fix_show(args: argparse.Namespace) -> int:
    try:
        record = fix_orchestrator.show_fix_run(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(json.dumps(record, indent=2))
    return 0


def cmd_fix_run_task(args: argparse.Namespace) -> int:
    try:
        record = fix_orchestrator.run_fix_task_once(args.id, args.task_id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Ran fix task `{args.task_id}` in `{record['id']}`.")
    return 0


def cmd_fix_cancel(args: argparse.Namespace) -> int:
    try:
        record = fix_orchestrator.cancel_fix_run(args.id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(record))
    else:
        print(f"Cancellation requested for fix run `{record.get('id')}` (status: {record.get('status')}).")
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

    show_parser = subparsers.add_parser("show", help="Show one profile.")
    show_parser.add_argument("name")
    show_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    show_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    show_parser.set_defaults(func=cmd_show)

    start_parser = subparsers.add_parser("start", help="Start a profile or all profiles of a type.")
    start_parser.add_argument("name", nargs="?")
    start_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    start_parser.set_defaults(func=cmd_start)

    stop_parser = subparsers.add_parser("stop", help="Stop a profile or all profiles of a type.")
    stop_parser.add_argument("name", nargs="?")
    stop_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    stop_parser.set_defaults(func=cmd_stop)

    stop_all_parser = subparsers.add_parser("stop-all", help="Stop every profile or every profile of a type.")
    stop_all_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    stop_all_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    stop_all_parser.set_defaults(func=cmd_stop_all)

    restart_parser = subparsers.add_parser("restart", help="Restart a profile or all profiles of a type.")
    restart_parser.add_argument("name", nargs="?")
    restart_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    restart_parser.set_defaults(func=cmd_restart)

    update_parser = subparsers.add_parser("update-profile", help="Update editable relay profile settings.")
    update_parser.add_argument("name")
    update_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    update_parser.add_argument("--workspace")
    update_parser.add_argument("--discord-bot-token")
    update_parser.add_argument(
        "--discord-bot-token-env",
        help=(
            "Read the new Discord bot token from this environment variable "
            "instead of passing it on the command line. The variable is "
            "consumed and unset after read."
        ),
    )
    update_parser.add_argument("--bot-name")
    update_parser.add_argument("--model")
    update_parser.add_argument("--codex-home")
    update_parser.add_argument("--claude-config-dir")
    update_parser.add_argument("--trigger-mode", choices=("all", "mention_or_dm", "dm_only"), default=None)
    update_parser.add_argument("--allow-dms", action="store_true", default=False)
    update_parser.add_argument("--deny-dms", action="store_true", default=False)
    update_parser.add_argument("--operator-ids")
    update_parser.add_argument("--allowed-user-ids")
    update_parser.add_argument("--allowed-bot-ids")
    update_parser.add_argument("--allowed-channel-id")
    update_parser.add_argument("--allowed-channel-author-ids")
    update_parser.add_argument("--channel-no-mention-author-ids")
    update_parser.add_argument("--channel-history-limit")
    update_parser.add_argument("--startup-dm-user-ids")
    update_parser.add_argument("--startup-dm-text")
    update_parser.add_argument("--startup-channel-text")
    update_parser.add_argument("--json", action="store_true", help="Output the updated profile as JSON")
    update_parser.set_defaults(func=cmd_update)

    logs_parser = subparsers.add_parser("logs", help="Show recent logs for one profile.")
    logs_parser.add_argument("name")
    logs_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    logs_parser.add_argument("--lines", type=int, default=80)
    logs_parser.add_argument("--json", action="store_true", help="Output logs as JSON")
    logs_parser.set_defaults(func=cmd_logs)

    doctor_parser = subparsers.add_parser("doctor", help="Check local prerequisites and profile collisions.")
    doctor_parser.add_argument("--json", action="store_true", help="Output as JSON for automation")
    doctor_parser.set_defaults(func=cmd_doctor)

    chat_parser = subparsers.add_parser("chat", help="Send a local operator message through a running relay.")
    chat_parser.add_argument("name")
    chat_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    chat_parser.add_argument("--message", required=True)
    chat_parser.add_argument("--channel-id", default=None)
    chat_parser.add_argument("--sender-name", default="Operator")
    chat_parser.add_argument("--sender-id", default="0")
    chat_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    chat_parser.set_defaults(func=cmd_chat)

    chat_history_parser = subparsers.add_parser("chat-history", help="Read local operator chat history for a relay.")
    chat_history_parser.add_argument("name")
    chat_history_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    chat_history_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    chat_history_parser.set_defaults(func=cmd_chat_history)

    remove_parser = subparsers.add_parser("remove", help="Remove a profile from the unified registry.")
    remove_parser.add_argument("name")
    remove_parser.add_argument("--type", choices=("codex", "claude"), default=None)
    remove_parser.set_defaults(func=cmd_remove)

    project_parser = subparsers.add_parser("project", help="Manage saved CLADEX workgroups.")
    project_subparsers = project_parser.add_subparsers(dest="project_command")

    project_list_parser = project_subparsers.add_parser("list", help="List saved workgroups.")
    project_list_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    project_list_parser.set_defaults(func=cmd_project_list)

    project_save_parser = project_subparsers.add_parser("save", help="Save or update a workgroup.")
    project_save_parser.add_argument("name")
    project_save_parser.add_argument("--member", action="append", default=[], help="Workgroup member as relayType:name")
    project_save_parser.set_defaults(func=cmd_project_save)

    project_start_parser = project_subparsers.add_parser("start", help="Start a workgroup.")
    project_start_parser.add_argument("name")
    project_start_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    project_start_parser.set_defaults(func=cmd_project_start)

    project_stop_parser = project_subparsers.add_parser("stop", help="Stop a workgroup.")
    project_stop_parser.add_argument("name")
    project_stop_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    project_stop_parser.set_defaults(func=cmd_project_stop)

    project_remove_parser = project_subparsers.add_parser("remove", help="Remove a workgroup.")
    project_remove_parser.add_argument("name")
    project_remove_parser.set_defaults(func=cmd_project_remove)

    review_parser = subparsers.add_parser("review", help="Run read-only project review swarms.")
    review_subparsers = review_parser.add_subparsers(dest="review_command")

    review_list_parser = review_subparsers.add_parser("list", help="List project review jobs.")
    review_list_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    review_list_parser.set_defaults(func=cmd_review_list)

    review_start_parser = review_subparsers.add_parser("start", help="Start a read-only project review job.")
    review_start_parser.add_argument("--workspace", required=True)
    review_start_parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    review_start_parser.add_argument("--agents", type=int, default=4)
    review_start_parser.add_argument("--account-home", default="")
    review_start_parser.add_argument("--title", default="")
    review_start_parser.add_argument("--allow-cladex-self-review", action="store_true", help="Explicitly allow reviewing the protected CLADEX repo after creating a source backup.")
    review_start_parser.add_argument("--no-backup", action="store_true", help="Skip the source snapshot for non-CLADEX review targets.")
    review_start_parser.add_argument("--preflight-only", action="store_true", help=argparse.SUPPRESS)
    review_start_parser.add_argument("--no-background", action="store_true", help=argparse.SUPPRESS)
    review_start_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    review_start_parser.set_defaults(func=cmd_review_start)

    review_run_parser = review_subparsers.add_parser("run", help=argparse.SUPPRESS)
    review_run_parser.add_argument("id")
    review_run_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    review_run_parser.set_defaults(func=cmd_review_run)

    review_show_parser = review_subparsers.add_parser("show", help="Show one project review job.")
    review_show_parser.add_argument("id")
    review_show_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    review_show_parser.set_defaults(func=cmd_review_show)

    review_fix_parser = review_subparsers.add_parser("fix-plan", help="Generate an ordered fix plan for a review job.")
    review_fix_parser.add_argument("id")
    review_fix_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    review_fix_parser.set_defaults(func=cmd_review_fix_plan)

    review_cancel_parser = review_subparsers.add_parser("cancel", help="Cancel a queued or running review job.")
    review_cancel_parser.add_argument("id")
    review_cancel_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    review_cancel_parser.set_defaults(func=cmd_review_cancel)

    backup_parser = subparsers.add_parser("backup", help="Create, list, and restore CLADEX source snapshots.")
    backup_subparsers = backup_parser.add_subparsers(dest="backup_command")

    backup_list_parser = backup_subparsers.add_parser("list", help="List source backups.")
    backup_list_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    backup_list_parser.set_defaults(func=cmd_backup_list)

    backup_create_parser = backup_subparsers.add_parser("create", help="Create a source backup snapshot.")
    backup_create_parser.add_argument("--workspace", required=True)
    backup_create_parser.add_argument("--reason", default="manual")
    backup_create_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    backup_create_parser.set_defaults(func=cmd_backup_create)

    backup_restore_parser = backup_subparsers.add_parser("restore", help="Restore a source backup snapshot.")
    backup_restore_parser.add_argument("id")
    backup_restore_parser.add_argument("--confirm", required=True, help="Must exactly match the backup id.")
    backup_restore_parser.add_argument("--json", action="store_true", help="Output as JSON for automation")
    backup_restore_parser.set_defaults(func=cmd_backup_restore)

    fix_parser = subparsers.add_parser("fix", help="Run guarded Fix Review workflows.")
    fix_subparsers = fix_parser.add_subparsers(dest="fix_command")

    fix_list_parser = fix_subparsers.add_parser("list", help="List fix runs.")
    fix_list_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    fix_list_parser.set_defaults(func=cmd_fix_list)

    fix_start_parser = fix_subparsers.add_parser("start", help="Start a guarded fix run from a completed review.")
    fix_start_parser.add_argument("--review", required=True, help="Review job id to fix.")
    fix_start_parser.add_argument("--max-agents", type=int, default=fix_orchestrator.DEFAULT_FIX_MAX_AGENTS)
    fix_start_parser.add_argument("--allow-cladex-self-fix", action="store_true", help="Explicitly allow write-capable Fix Review against the CLADEX repo after a completed self-review.")
    fix_start_parser.add_argument("--no-background", action="store_true", help=argparse.SUPPRESS)
    fix_start_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    fix_start_parser.set_defaults(func=cmd_fix_start)

    fix_run_parser = fix_subparsers.add_parser("run", help=argparse.SUPPRESS)
    fix_run_parser.add_argument("id")
    fix_run_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    fix_run_parser.set_defaults(func=cmd_fix_run)

    fix_show_parser = fix_subparsers.add_parser("show", help="Show one fix run.")
    fix_show_parser.add_argument("id")
    fix_show_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    fix_show_parser.set_defaults(func=cmd_fix_show)

    fix_run_task_parser = fix_subparsers.add_parser("run-task", help=argparse.SUPPRESS)
    fix_run_task_parser.add_argument("id")
    fix_run_task_parser.add_argument("task_id")
    fix_run_task_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    fix_run_task_parser.set_defaults(func=cmd_fix_run_task)

    fix_cancel_parser = fix_subparsers.add_parser("cancel", help="Cancel a queued or running fix run.")
    fix_cancel_parser.add_argument("id")
    fix_cancel_parser.add_argument("--json", action="store_true", help="Output as JSON for API")
    fix_cancel_parser.set_defaults(func=cmd_fix_cancel)

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
