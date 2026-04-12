#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.metadata
import install_plugin
import json
import os
import re
import shutil
import socket
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil
from relay_common import (
    CONFIG_ROOT,
    DATA_ROOT,
    PROFILES_DIR,
    REGISTRY_PATH,
    atomic_write_json,
    atomic_write_text,
    codex_config_path,
    default_namespace_for_workspace,
    default_port_for_workspace,
    follow_file,
    listening_pids,
    pid_exists,
    prepare_relay_codex_home,
    relay_codex_env,
    resolve_codex_bin,
    slugify,
    state_dir_for_namespace,
    tail_lines,
    terminate_process_tree,
    token_fingerprint,
    truncate_file_tail,
)

if os.name == "nt":
    import msvcrt
else:
    import fcntl


ENV_KEY_ORDER = [
    "DISCORD_BOT_TOKEN",
    "RELAY_BOT_NAME",
    "RELAY_MODEL",
    "CODEX_WORKDIR",
    "CODEX_MODEL",
    "CODEX_FULL_ACCESS",
    "CODEX_READ_ONLY",
    "CODEX_APP_SERVER_TRANSPORT",
    "CODEX_APP_SERVER_PORT",
    "STATE_NAMESPACE",
    "ALLOW_DMS",
    "BOT_TRIGGER_MODE",
    "ALLOWED_USER_IDS",
    "ALLOWED_BOT_IDS",
    "ALLOWED_CHANNEL_AUTHOR_IDS",
    "CHANNEL_NO_MENTION_AUTHOR_IDS",
    "STARTUP_DM_USER_IDS",
    "STARTUP_DM_TEXT",
    "STARTUP_CHANNEL_TEXT",
    "ALLOWED_CHANNEL_IDS",
    "CHANNEL_HISTORY_LIMIT",
    "OPEN_VISIBLE_TERMINAL",
    "RELAY_ATTACH_CHANNEL_ID",
]

STALE_PROFILE_KEYS = {
    "RELAY_PROVIDER",
}


PACKAGE_NAME = "discord-codex-relay"
GUI_CHILD_ENV = "CODEX_DISCORD_GUI_CHILD"
DEFAULT_CODEX_MODEL = "gpt-5.4"
SUPERVISOR_FAILURE_WINDOW_SECONDS = 10 * 60
SUPERVISOR_BACKOFF_INITIAL_SECONDS = 2.0
SUPERVISOR_BACKOFF_MAX_SECONDS = 5 * 60
SUPERVISOR_STABLE_RUN_SECONDS = 5 * 60
PRIVACY_IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".mypy_cache",
}
PRIVACY_ALLOWED_DOTENV_FILES = {".env.example"}
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
WINDOWS_USER_PATH_RE = re.compile(r"(?i)\b([A-Z]:\\Users\\)([^\\]+)")
EXTRAS_CATALOG = [
    {
        "name": "playwright",
        "category": "browser",
        "free": "local/free",
        "summary": "Real browser automation for search, login flows, scraping, and UI verification.",
    },
    {
        "name": "playwright-interactive",
        "category": "browser",
        "free": "local/free",
        "summary": "Persistent browser control for long debugging sessions and live app inspection.",
    },
    {
        "name": "screenshot",
        "category": "vision",
        "free": "local/free",
        "summary": "OS-level screenshots when browser capture is not enough.",
    },
    {
        "name": "frontend-skill",
        "category": "frontend",
        "free": "local/free",
        "summary": "Frontend build/debug/design helper patterns for web app work.",
    },
    {
        "name": "doc",
        "category": "documents",
        "free": "local/free",
        "summary": "Create and inspect DOCX files with layout-aware validation.",
    },
    {
        "name": "pdf",
        "category": "documents",
        "free": "local/free",
        "summary": "Read, generate, and visually inspect PDFs.",
    },
    {
        "name": "imagegen",
        "category": "media",
        "free": "provider-backed",
        "summary": "Generate or edit raster images when visual assets are actually needed.",
    },
    {
        "name": "speech",
        "category": "media",
        "free": "provider-backed",
        "summary": "Text-to-speech style audio generation.",
    },
    {
        "name": "transcribe",
        "category": "media",
        "free": "provider-backed",
        "summary": "Speech-to-text transcription workflows.",
    },
    {
        "name": "sora",
        "category": "media",
        "free": "provider-backed",
        "summary": "Video generation/edit workflows when available.",
    },
    {
        "name": "openai-docs",
        "category": "research",
        "free": "local/free",
        "summary": "Official OpenAI docs lookup for current models and API behavior.",
    },
    {
        "name": "gh-address-comments",
        "category": "git",
        "free": "local/free",
        "summary": "Handle GitHub PR and issue comments from the terminal when gh auth is available.",
    },
    {
        "name": "gh-fix-ci",
        "category": "git",
        "free": "local/free",
        "summary": "Inspect failing GitHub checks and logs, then drive CI fixes from the repo.",
    },
    {
        "name": "security-best-practices",
        "category": "security",
        "free": "local/free",
        "summary": "Language-aware secure-by-default review guidance for Python and JS/TS work.",
    },
    {
        "name": "security-threat-model",
        "category": "security",
        "free": "local/free",
        "summary": "Repo-grounded threat modeling for trust boundaries, abuse paths, and mitigations.",
    },
    {
        "name": "security-ownership-map",
        "category": "security",
        "free": "local/free",
        "summary": "Find ownership gaps and sensitive-code bus-factor risks from git history.",
    },
    {
        "name": "jupyter-notebook",
        "category": "analysis",
        "free": "local/free",
        "summary": "Notebook-first analysis workflows for data-heavy or iterative inspection tasks.",
    },
    {
        "name": "spreadsheet",
        "category": "analysis",
        "free": "local/free",
        "summary": "Spreadsheet generation and manipulation when the task is naturally tabular.",
    },
    {
        "name": "cloudflare-deploy",
        "category": "deploy",
        "free": "service account",
        "summary": "Deploy apps and infra to Cloudflare Workers/Pages.",
    },
    {
        "name": "vercel-deploy",
        "category": "deploy",
        "free": "service account",
        "summary": "Deploy and manage Vercel-hosted apps.",
    },
    {
        "name": "netlify-deploy",
        "category": "deploy",
        "free": "service account",
        "summary": "Deploy and manage Netlify-hosted apps.",
    },
    {
        "name": "render-deploy",
        "category": "deploy",
        "free": "service account",
        "summary": "Deploy and manage Render-hosted services.",
    },
    {
        "name": "chatgpt-apps",
        "category": "apps",
        "free": "local/free",
        "summary": "Build and maintain ChatGPT Apps / connectors.",
    },
]


def _bot_label(profile: dict) -> str:
    bot_name = str(profile.get("bot_name", "")).strip()
    if bot_name:
        return bot_name
    fingerprint = str(profile.get("token_fingerprint", "")).strip()
    if fingerprint:
        return f"bot-{fingerprint[:8]}"
    return "unnamed-bot"


def _sanitize_auth_status_text(text: str) -> str:
    redacted = EMAIL_RE.sub("[redacted-email]", text or "")
    return redacted.strip()


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _write_env_file(path: Path, env: dict[str, str]) -> None:
    ordered_keys = [key for key in ENV_KEY_ORDER if key in env]
    ordered_keys.extend(sorted(key for key in env if key not in ordered_keys))
    lines = [f"{key}={env[key]}" for key in ordered_keys]
    atomic_write_text(path, "\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_registry() -> dict:
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
    atomic_write_json(REGISTRY_PATH, registry)


def _gui_python_executable() -> str:
    executable = Path(sys.executable)
    if os.name == "nt" and executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(executable)


def _launch_gui_detached() -> int:
    env = os.environ.copy()
    env[GUI_CHILD_ENV] = "1"
    command = [_gui_python_executable(), _backend_script_path("relayctl.py"), "gui"]
    kwargs: dict[str, object] = {"env": env, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
        kwargs["stdin"] = subprocess.DEVNULL
    subprocess.Popen(command, **kwargs)
    print("Opened relay manager GUI.")
    return 0


def _is_relay_source_tree(path: Path) -> bool:
    return (
        (path / "pyproject.toml").exists()
        and (path / "relayctl.py").exists()
        and (path / ".codex-plugin" / "plugin.json").exists()
    )


def _default_self_update_source() -> str | None:
    override = os.environ.get("CODEX_DISCORD_UPDATE_SOURCE", "").strip()
    if override:
        return override

    candidates: list[Path] = []
    for start in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        if start in candidates:
            continue
        candidates.append(start)
        candidates.extend(parent for parent in start.parents if parent not in candidates)

    for candidate in candidates:
        if _is_relay_source_tree(candidate):
            return str(candidate)
    return None


def _resolved_self_update_target(explicit_source: str | None) -> str:
    if explicit_source:
        return explicit_source.strip()
    try:
        install_source = str(install_plugin._install_source()).strip()
    except Exception:
        install_source = ""
    if install_source:
        try:
            candidate = Path(install_source).expanduser().resolve()
        except Exception:
            candidate = None
        if candidate is not None and _is_relay_source_tree(candidate):
            return str(candidate)
    return PACKAGE_NAME


def _run_gui_self_update() -> list[str]:
    update_source = _default_self_update_source()
    cmd_self_update(
        argparse.Namespace(
            source=update_source,
            force_reinstall=True,
            no_restart=False,
        )
    )
    source_label = update_source or PACKAGE_NAME
    return [
        f"Updated relay from `{source_label}`.",
        f"discord-codex-relay version: {_package_version()}",
    ]


def _runtime_venv_root() -> Path:
    executable = Path(sys.executable).resolve()
    if os.name == "nt" and executable.parent.name.lower() == "scripts":
        return executable.parent.parent
    return executable.parent.parent


def _can_use_external_windows_update(update_target: str) -> bool:
    if os.name != "nt":
        return False
    try:
        target_path = Path(update_target).expanduser().resolve()
    except Exception:
        return False
    if not _is_relay_source_tree(target_path):
        return False
    runtime_root = (DATA_ROOT / "runtime").resolve()
    return _runtime_venv_root() == runtime_root


def _launch_external_windows_update_background(
    update_target: str,
    *,
    restarted_profiles: list[dict],
) -> None:
    base_python = Path(_background_python_windowless_executable()).resolve()
    if not base_python.exists():
        raise SystemExit(
            "Windows self-update requires a working Python interpreter, but it was not found at "
            f"{base_python}."
        )
    source_root = Path(update_target).expanduser().resolve()
    if not _is_relay_source_tree(source_root):
        raise SystemExit(f"Local update source is not a relay source tree: {source_root}")
    helper_code = """
import json
import os
import subprocess
import sys
import time
from pathlib import Path

parent_pid = int(sys.argv[1])
source_root = Path(sys.argv[2])
profiles = json.loads(sys.argv[3])

for _ in range(600):
    try:
        os.kill(parent_pid, 0)
    except OSError:
        break
    time.sleep(0.25)

sys.path.insert(0, str(source_root))
import install_plugin

install_plugin.main(source=str(source_root))
runtime_python = str(install_plugin.runtime_python_path())
creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
for profile in profiles:
    env_file = str(profile.get("env_file", "")).strip()
    if not env_file:
        continue
    subprocess.Popen(
        [runtime_python, "-m", "relayctl", "serve", "--env-file", env_file],
        creationflags=creationflags,
        close_fds=True,
    )
"""
    helper_path = Path(tempfile.gettempdir()) / f"codex-discord-update-{os.getpid()}.py"
    atomic_write_text(helper_path, helper_code)
    command = [
        str(base_python),
        str(helper_path),
        str(os.getpid()),
        str(source_root),
        json.dumps(restarted_profiles),
    ]
    kwargs: dict[str, object] = {
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(command, **kwargs)


def _upsert_profile(registry: dict, profile: dict) -> None:
    profiles = registry.setdefault("profiles", [])
    profiles[:] = [
        item
        for item in profiles
        if not (
            item.get("workspace") == profile["workspace"]
            and item.get("token_fingerprint") == profile.get("token_fingerprint")
        )
    ]
    profiles.append(profile)
    profiles.sort(key=lambda item: item.get("name", ""))


def _normalize_project_name(name: str) -> str:
    normalized = slugify(name)
    if not normalized:
        raise SystemExit("Project name cannot be blank.")
    return normalized


def _upsert_project(registry: dict, project: dict) -> None:
    projects = registry.setdefault("projects", [])
    projects[:] = [item for item in projects if item.get("name") != project["name"]]
    projects.append(project)
    projects.sort(key=lambda item: item.get("name", ""))


def _parse_csv_ids(value: str) -> str:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    valid = [part for part in parts if part.isdigit()]
    return ",".join(valid)


def _normalized_profile_env(env: dict[str, str]) -> dict[str, str]:
    workspace = Path(env["CODEX_WORKDIR"]).resolve()
    normalized = {
        key: value
        for key, value in dict(env).items()
        if key not in STALE_PROFILE_KEYS
    }
    token = normalized.get("DISCORD_BOT_TOKEN", "")
    normalized["CODEX_WORKDIR"] = str(workspace)
    normalized["RELAY_BOT_NAME"] = normalized.get("RELAY_BOT_NAME", "").strip()
    default_model = DEFAULT_CODEX_MODEL
    normalized["RELAY_MODEL"] = (normalized.get("RELAY_MODEL") or normalized.get("CODEX_MODEL") or default_model).strip()
    normalized["CODEX_MODEL"] = normalized["RELAY_MODEL"]
    normalized["CODEX_FULL_ACCESS"] = "true"
    normalized["CODEX_READ_ONLY"] = "false"
    transport = (normalized.get("CODEX_APP_SERVER_TRANSPORT", "stdio") or "stdio").strip().lower()
    normalized["CODEX_APP_SERVER_TRANSPORT"] = transport if transport in {"stdio", "websocket"} else "stdio"
    normalized["OPEN_VISIBLE_TERMINAL"] = "true" if normalized.get("OPEN_VISIBLE_TERMINAL", "false").lower() in {"1", "true", "yes", "on"} else "false"
    if normalized["CODEX_APP_SERVER_TRANSPORT"] != "websocket":
        normalized["OPEN_VISIBLE_TERMINAL"] = "false"
    normalized["CHANNEL_HISTORY_LIMIT"] = str(normalized.get("CHANNEL_HISTORY_LIMIT", "20") or "20")
    normalized["BOT_TRIGGER_MODE"] = normalized.get("BOT_TRIGGER_MODE", "mention_or_dm") or "mention_or_dm"
    normalized["ALLOW_DMS"] = "true" if normalized.get("ALLOW_DMS", "false").lower() in {"1", "true", "yes", "on"} else "false"
    normalized["STATE_NAMESPACE"] = normalized.get("STATE_NAMESPACE") or default_namespace_for_workspace(workspace, token=token)
    normalized["CODEX_APP_SERVER_PORT"] = str(
        normalized.get("CODEX_APP_SERVER_PORT") or default_port_for_workspace(workspace, token=token)
    )
    normalized["STARTUP_DM_TEXT"] = normalized.get("STARTUP_DM_TEXT") or "Discord relay online. DM me here to chat with Codex."
    normalized["ALLOWED_USER_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_USER_IDS", ""))
    normalized["ALLOWED_BOT_IDS"] = _parse_csv_ids(normalized.get("ALLOWED_BOT_IDS", ""))
    allowed_dm_ids = [part for part in normalized["ALLOWED_USER_IDS"].split(",") if part]
    allowed_channel_author_ids = {
        part.strip()
        for part in normalized.get("ALLOWED_CHANNEL_AUTHOR_IDS", "").split(",")
        if part.strip().isdigit()
    }
    channel_no_mention_author_ids = {
        part.strip()
        for part in normalized.get("CHANNEL_NO_MENTION_AUTHOR_IDS", "").split(",")
        if part.strip().isdigit()
    }
    allowed_channel_author_ids.update(allowed_dm_ids)
    allowed_channel_author_ids.update(channel_no_mention_author_ids)
    normalized["ALLOWED_CHANNEL_AUTHOR_IDS"] = ",".join(sorted(allowed_channel_author_ids, key=int))
    normalized["CHANNEL_NO_MENTION_AUTHOR_IDS"] = ",".join(sorted(channel_no_mention_author_ids, key=int))
    allowed_channels = [part.strip() for part in normalized.get("ALLOWED_CHANNEL_IDS", "").split(",") if part.strip().isdigit()]
    normalized["ALLOWED_CHANNEL_IDS"] = ",".join(allowed_channels)
    startup_dm_ids = normalized.get("STARTUP_DM_USER_IDS", normalized["ALLOWED_USER_IDS"])
    normalized["STARTUP_DM_USER_IDS"] = _parse_csv_ids(startup_dm_ids)
    attach_channel = str(normalized.get("RELAY_ATTACH_CHANNEL_ID", "")).strip()
    if not attach_channel.isdigit() or (allowed_channels and attach_channel not in allowed_channels):
        normalized["RELAY_ATTACH_CHANNEL_ID"] = allowed_channels[0] if allowed_channels else ""
    else:
        normalized["RELAY_ATTACH_CHANNEL_ID"] = attach_channel
    return normalized


def _profile_from_env(env: dict[str, str]) -> dict:
    normalized = _normalized_profile_env(env)
    workspace = normalized["CODEX_WORKDIR"]
    namespace = normalized["STATE_NAMESPACE"]
    fingerprint = token_fingerprint(normalized["DISCORD_BOT_TOKEN"])
    name = slugify(f"{namespace}-{fingerprint[:4]}")
    digest = token_fingerprint(workspace)[:10]
    profile_env_path = PROFILES_DIR / f"{name}-{digest}.env"
    _write_env_file(profile_env_path, normalized)
    return {
        "name": name,
        "workspace": workspace,
        "env_file": str(profile_env_path),
        "attach_channel_id": normalized.get("RELAY_ATTACH_CHANNEL_ID", ""),
        "state_namespace": namespace,
        "token_fingerprint": fingerprint,
        "bot_name": normalized.get("RELAY_BOT_NAME", ""),
    }


def _refresh_profile_metadata(profile: dict) -> dict:
    env_file = profile.get("env_file", "")
    if not env_file:
        return profile
    path = Path(env_file)
    if not path.exists():
        return profile
    env = _load_env_file(path)
    normalized = _normalized_profile_env(env)
    refreshed = dict(profile)
    refreshed["workspace"] = normalized["CODEX_WORKDIR"]
    refreshed["attach_channel_id"] = normalized.get("RELAY_ATTACH_CHANNEL_ID", "")
    refreshed["state_namespace"] = normalized.get("STATE_NAMESPACE", "")
    refreshed["token_fingerprint"] = token_fingerprint(normalized.get("DISCORD_BOT_TOKEN", ""))
    refreshed["bot_name"] = normalized.get("RELAY_BOT_NAME", "")
    return refreshed


def _matching_profiles_for_workspace(workspace: Path) -> list[dict]:
    registry = _load_registry()
    target = str(workspace.resolve())
    matches = []
    for profile in registry.get("profiles", []):
        refreshed = _refresh_profile_metadata(profile)
        root = str(Path(refreshed["workspace"]).resolve())
        if target == root or target.startswith(root + os.sep):
            matches.append((len(root), refreshed))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in matches]


def _all_registered_profiles() -> list[dict]:
    profiles: list[dict] = []
    seen: set[str] = set()
    for profile in _load_registry().get("profiles", []):
        refreshed = _refresh_profile_metadata(profile)
        env_file = str(refreshed.get("env_file", "")).strip()
        if not env_file:
            continue
        key = env_file.lower()
        if key in seen:
            continue
        seen.add(key)
        profiles.append(refreshed)
    profiles.sort(key=lambda item: item.get("name", ""))
    return profiles


def _profile_by_name(name: str) -> dict | None:
    target = name.strip()
    if not target:
        return None
    for profile in _all_registered_profiles():
        if profile.get("name") == target:
            return profile
    return None


def _profiles_under_workspace_root(workspace_root: Path) -> list[dict]:
    root = workspace_root.resolve()
    matches: list[dict] = []
    for profile in _all_registered_profiles():
        profile_workspace = Path(profile["workspace"]).resolve()
        if profile_workspace == root or str(profile_workspace).startswith(str(root) + os.sep):
            matches.append(profile)
    matches.sort(key=lambda item: (Path(item["workspace"]).resolve() == root, item.get("name", "")))
    return matches


def _project_by_name(name: str) -> dict:
    target = _normalize_project_name(name)
    for project in _load_registry().get("projects", []):
        if project.get("name") == target:
            return project
    raise SystemExit(f"No saved relay project named `{target}`.")


def _profiles_for_project(project: dict) -> list[dict]:
    profiles: list[dict] = []
    missing: list[str] = []
    seen: set[str] = set()
    for profile_name in project.get("profiles", []):
        profile = _profile_by_name(str(profile_name))
        if profile is None:
            missing.append(str(profile_name))
            continue
        key = profile.get("name", "")
        if key in seen:
            continue
        seen.add(key)
        profiles.append(profile)
    if missing:
        raise SystemExit(
            f"Project `{project.get('name', 'unknown')}` references missing profiles: {', '.join(missing)}"
        )
    if not profiles:
        raise SystemExit(f"Project `{project.get('name', 'unknown')}` has no registered relay profiles.")
    profiles.sort(key=lambda item: item.get("name", ""))
    return profiles


def _shared_runtime_running_profiles() -> list[dict]:
    running: list[dict] = []
    for profile in _all_registered_profiles():
        try:
            if _profile_runtime_state(profile)["running"]:
                running.append(profile)
        except Exception:
            continue
    return running


def _restart_profiles(profiles: list[dict]) -> None:
    for profile in profiles:
        try:
            _run_profile(profile)
        except Exception as exc:
            print(f"warning: failed to restart profile {profile.get('name', 'unknown')}: {exc}")


def _toml_project_key(project_path: str) -> str:
    escaped = project_path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _decode_project_header_key(key: str) -> str:
    if len(key) >= 2 and key[0] == "'" and key[-1] == "'":
        return key[1:-1]
    if len(key) >= 2 and key[0] == '"' and key[-1] == '"':
        inner = key[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return key


def _normalize_codex_config_project_headers(config_text: str) -> str:
    pattern = re.compile(r"(?m)^\[projects\.(?P<key>'.*?'|\".*?\")\]$")
    return pattern.sub(
        lambda match: f"[projects.{_toml_project_key(_decode_project_header_key(match.group('key')))}]",
        config_text,
    )


def _project_header_variants(project_path: str) -> list[str]:
    escaped = project_path.replace("\\", "\\\\").replace('"', '\\"')
    variants = [
        f"[projects.{_toml_project_key(project_path)}]",
        f"[projects.'{project_path}']",
        f'[projects."{project_path}"]',
        f'[projects."{escaped}"]',
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        if variant in seen:
            continue
        seen.add(variant)
        unique.append(variant)
    return unique


def _ensure_codex_project_trusted(workspace: Path) -> None:
    config_path = codex_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing_raw = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    existing = _normalize_codex_config_project_headers(existing_raw)
    project_path = str(workspace.resolve())
    header_pattern = "|".join(re.escape(header) for header in _project_header_variants(project_path))
    block_pattern = re.compile(rf"(?ms)^(?:{header_pattern})\n(?:.*\n)*?(?=^\[|$)")
    block = f"[projects.{_toml_project_key(project_path)}]\ntrust_level = \"trusted\"\n"
    if block_pattern.search(existing):
        updated = block_pattern.sub(lambda _match: block + "\n", existing).rstrip() + "\n"
    else:
        updated = existing.rstrip()
        if updated:
            updated += "\n\n"
        updated += block
    if updated != existing_raw:
        atomic_write_text(config_path, updated)


def _prompt(prompt: str, default: str | None = None, *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        if secret:
            value = getpass.getpass(f"{prompt}{suffix}: ").strip()
        else:
            value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default


def _prompt_bool(prompt: str, default: bool = False) -> bool:
    default_text = "y" if default else "n"
    while True:
        value = input(f"{prompt} [y/n] [{default_text}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False


def _interactive_register(workspace: Path) -> dict:
    print(f"Configuring Discord relay for workspace:\n{workspace}")
    token = _prompt("Discord bot token", secret=True)
    bot_name = _prompt("Discord bot name (optional)", default="")
    allow_dms = _prompt_bool("Allow DMs to this bot", default=False)
    dm_user_ids = _parse_csv_ids(_prompt("Allowed DM user IDs (comma-separated, optional)", default=""))
    main_channel_id = _parse_csv_ids(_prompt("Main Discord channel ID (optional, leave blank for DM-only)", default=""))
    while not allow_dms and not main_channel_id:
        main_channel_id = _parse_csv_ids(_prompt("Main Discord channel ID is required when DMs are disabled", default=""))
    extra_trigger_ids = ""
    if main_channel_id:
        extra_trigger_ids = _parse_csv_ids(_prompt("Extra user/bot IDs allowed in that channel (optional)", default=""))
    trigger_mode = "mention_or_dm"
    env = {
        "DISCORD_BOT_TOKEN": token,
        "RELAY_BOT_NAME": bot_name,
        "CODEX_WORKDIR": str(workspace),
        "CODEX_MODEL": DEFAULT_CODEX_MODEL,
        "CODEX_FULL_ACCESS": "true",
        "CODEX_READ_ONLY": "false",
        "CODEX_APP_SERVER_TRANSPORT": "stdio",
        "CODEX_APP_SERVER_PORT": str(default_port_for_workspace(workspace, token=token)),
        "STATE_NAMESPACE": default_namespace_for_workspace(workspace, token=token),
        "ALLOW_DMS": "true" if allow_dms else "false",
        "BOT_TRIGGER_MODE": trigger_mode,
        "ALLOWED_USER_IDS": dm_user_ids,
        "ALLOWED_CHANNEL_AUTHOR_IDS": extra_trigger_ids,
        "CHANNEL_NO_MENTION_AUTHOR_IDS": "",
        "STARTUP_DM_USER_IDS": dm_user_ids if allow_dms else "",
        "STARTUP_DM_TEXT": "Discord relay online. DM me here to chat with Codex.",
        "ALLOWED_CHANNEL_IDS": main_channel_id,
        "CHANNEL_HISTORY_LIMIT": "20",
        "OPEN_VISIBLE_TERMINAL": "false",
        "RELAY_ATTACH_CHANNEL_ID": main_channel_id.split(",")[0] if main_channel_id else "",
    }
    return _profile_from_env(env)


def _register_profile(profile: dict) -> None:
    registry = _load_registry()
    _upsert_profile(registry, profile)
    _save_registry(registry)


def _replace_profile_registration(previous_profile: dict, new_profile: dict) -> None:
    registry = _load_registry()
    previous_name = str(previous_profile.get("name", "")).strip()
    previous_env = str(previous_profile.get("env_file", "")).strip().lower()
    registry["profiles"] = [
        item
        for item in registry.get("profiles", [])
        if str(item.get("name", "")).strip() != previous_name
        and str(item.get("env_file", "")).strip().lower() != previous_env
    ]
    _upsert_profile(registry, new_profile)
    new_name = str(new_profile.get("name", "")).strip()
    for project in registry.get("projects", []):
        updated_members: list[str] = []
        seen: set[str] = set()
        for member in project.get("profiles", []):
            value = new_name if str(member).strip() == previous_name else str(member).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            updated_members.append(value)
        project["profiles"] = updated_members
    _save_registry(registry)

    previous_env_path = Path(str(previous_profile.get("env_file", "")).strip()) if previous_profile.get("env_file") else None
    new_env_path = Path(str(new_profile.get("env_file", "")).strip()) if new_profile.get("env_file") else None
    if previous_env_path and previous_env_path != new_env_path:
        previous_env_path.unlink(missing_ok=True)


def _remove_profile_registration(profile: dict) -> None:
    registry = _load_registry()
    profile_name = str(profile.get("name", "")).strip()
    env_file = str(profile.get("env_file", "")).strip().lower()
    registry["profiles"] = [
        item
        for item in registry.get("profiles", [])
        if str(item.get("name", "")).strip() != profile_name
        and str(item.get("env_file", "")).strip().lower() != env_file
    ]
    remaining_projects: list[dict] = []
    for project in registry.get("projects", []):
        members = [str(member).strip() for member in project.get("profiles", []) if str(member).strip() != profile_name]
        if not members:
            continue
        remaining_projects.append({"name": project.get("name", ""), "profiles": members})
    registry["projects"] = remaining_projects
    _save_registry(registry)
    if profile.get("env_file"):
        Path(str(profile["env_file"])).unlink(missing_ok=True)


def _select_profile_for_workspace(workspace: Path) -> dict:
    matches = _matching_profiles_for_workspace(workspace)
    if not matches:
        raise SystemExit("No relay profile registered for this workspace.")
    target = str(workspace.resolve())
    exact_matches = [profile for profile in matches if str(Path(profile["workspace"]).resolve()) == target]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        matches = exact_matches
    if len(matches) == 1:
        return matches[0]
    print("Multiple Discord relay profiles exist for this workspace:")
    for index, item in enumerate(matches, start=1):
        channel_id = str(item.get("attach_channel_id", "")).strip() or "no-channel"
        print(f"{index}. {_bot_label(item)} [{str(item.get('token_fingerprint', ''))[:8]}] channel={channel_id}")
    choice = _prompt("Select profile number", default="1")
    selected_index = int(choice) if choice.isdigit() else 1
    selected_index = max(1, min(len(matches), selected_index))
    return matches[selected_index - 1]


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        pid_text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(pid_text) if pid_text.isdigit() else None


def _read_pid_collection_file(path: Path) -> list[int]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    if isinstance(payload, dict):
        values = list(payload.values())
    elif isinstance(payload, list):
        values = payload
    else:
        values = [payload]
    pids: list[int] = []
    for value in values:
        try:
            pid = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if pid > 0:
            pids.append(pid)
    return sorted(set(pids))


def _supervisor_pid_path(state_dir: Path) -> Path:
    return state_dir / ".supervisor.pid"


def _app_server_pid_path(state_dir: Path) -> Path:
    return state_dir / ".app-server.pid"


def _supervisor_lock_path(state_dir: Path) -> Path:
    return state_dir / ".supervisor.lock"


def _launch_lock_path(state_dir: Path) -> Path:
    return state_dir / ".launch.lock"


def _acquire_pid_lock(path: Path) -> object:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        os.fsync(handle.fileno())
        return handle
    except Exception:
        handle.close()
        raise


def _release_pid_lock(handle: object | None) -> None:
    if handle is None:
        return
    try:
        if os.name == "nt":
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _package_version() -> str:
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".codex"


def _skill_installer_script(name: str) -> Path:
    return _codex_home() / "skills" / ".system" / "skill-installer" / "scripts" / name


def _require_skill_installer_script(name: str) -> Path:
    script = _skill_installer_script(name)
    if not script.exists():
        raise SystemExit(
            f"Codex skill installer helper is missing: {script}\n"
            "Install or repair Codex before using `codex-discord skill ...`."
        )
    return script


def _privacy_personal_markers() -> list[str]:
    markers: list[str] = []
    home = Path.home()
    markers.extend(
        [
            str(home).strip(),
            home.name.strip(),
            os.environ.get("USER", "").strip(),
            os.environ.get("USERNAME", "").strip(),
            os.environ.get("USERPROFILE", "").strip(),
        ]
    )
    unique: list[str] = []
    seen: set[str] = set()
    for marker in markers:
        if len(marker) < 3:
            continue
        lowered = marker.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(marker)
    return unique


def _privacy_sensitive_env_keys(path: Path) -> list[str]:
    try:
        env = _load_env_file(path)
    except Exception:
        return []
    flagged: list[str] = []
    for key in sorted(env):
        lowered = key.lower()
        if any(term in lowered for term in ("token", "secret", "password", "passwd", "api_key", "apikey", "private_key")):
            flagged.append(key)
    return flagged


def _privacy_audit(root: Path) -> list[str]:
    findings: list[str] = []
    markers = _privacy_personal_markers()
    for current_root, dir_names, file_names in os.walk(root, topdown=True):
        dir_names[:] = [name for name in dir_names if name not in PRIVACY_IGNORED_DIR_NAMES]
        current_root_path = Path(current_root)
        for file_name in sorted(file_names):
            path = current_root_path / file_name
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            relative_text = str(relative)
            lowered_name = file_name.lower()
            if lowered_name.startswith(".env") and file_name not in PRIVACY_ALLOWED_DOTENV_FILES:
                findings.append(f"repo-local secret file: {relative_text}")
                sensitive_keys = _privacy_sensitive_env_keys(path)
                if sensitive_keys:
                    findings.append(f"secret-like env keys in {relative_text}: {', '.join(sensitive_keys)}")
                continue
            if lowered_name.endswith((".log", ".jsonl")):
                findings.append(f"runtime artifact in repo tree: {relative_text}")
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for marker in markers:
                if marker and marker.lower() in text.lower():
                    findings.append(f"personal marker found in {relative_text}: {marker}")
                    break
            if re.search(r"/mnt/c/users/[a-z0-9._ -]{2,}/", text, flags=re.IGNORECASE) or re.search(r"c:\\users\\[a-z0-9._ -]{2,}\\", text, flags=re.IGNORECASE):
                findings.append(f"user-specific path literal found in {relative_text}")
    return findings


def _state_dir_for_profile(profile: dict, env: dict | None = None) -> Path:
    if env is None:
        env = _normalized_profile_env(_load_env_file(Path(profile["env_file"])))
    return state_dir_for_namespace(env["STATE_NAMESPACE"])


def _ready_marker_path(state_dir: Path) -> Path:
    return state_dir / ".ready"


def _auth_failure_marker_path(state_dir: Path) -> Path:
    return state_dir / ".auth_failed"


def _startup_notice_marker_path(state_dir: Path) -> Path:
    return state_dir / ".startup_notice"


def _discovered_relay_process_pids(profile: dict, env: dict[str, str]) -> tuple[list[int], list[int]]:
    target_env_file = str(Path(profile["env_file"]).resolve()).lower()
    target_workspace = str(Path(env["CODEX_WORKDIR"]).resolve()).lower()
    relayctl_path = str(Path(__file__).resolve()).lower()
    bot_path = str(Path(__file__).resolve().with_name("bot.py")).lower()
    supervisor_pids: set[int] = set()
    relay_pids: set[int] = set()

    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline_items = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_items).lower()
        except (psutil.Error, OSError):
            continue
        if not cmdline:
            continue
        is_relayctl_serve = (
            (relayctl_path in cmdline and " serve " in f" {cmdline} ")
            or (" -m relayctl " in f" {cmdline} " and " serve " in f" {cmdline} ")
        )
        if is_relayctl_serve and target_env_file in cmdline:
            supervisor_pids.add(proc.info["pid"])
            continue
        is_bot_worker = bot_path in cmdline or " -m bot " in f" {cmdline} "
        if not is_bot_worker:
            continue
        try:
            cwd = str(proc.cwd()).lower()
        except (psutil.Error, OSError):
            cwd = ""
        if cwd == target_workspace:
            relay_pids.add(proc.info["pid"])

    return sorted(supervisor_pids), sorted(relay_pids)


def _all_relay_process_pids() -> tuple[list[int], list[int]]:
    relayctl_path = str(Path(__file__).resolve()).lower()
    bot_path = str(Path(__file__).resolve().with_name("bot.py")).lower()
    supervisor_pids: set[int] = set()
    relay_pids: set[int] = set()

    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline_items = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_items).lower()
        except (psutil.Error, OSError):
            continue
        if not cmdline:
            continue
        if (relayctl_path in cmdline and " serve " in f" {cmdline} ") or (
            " -m relayctl " in f" {cmdline} " and " serve " in f" {cmdline} "
        ):
            supervisor_pids.add(proc.info["pid"])
            continue
        if bot_path in cmdline or " -m bot " in f" {cmdline} ":
            relay_pids.add(proc.info["pid"])

    return _dedupe_nested_launcher_pids(sorted(supervisor_pids)), _dedupe_nested_launcher_pids(sorted(relay_pids))


def _discovered_codex_app_server_pids(workspace: Path | None = None) -> list[int]:
    target_workspace = str(workspace.resolve()).lower() if workspace is not None else None
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline_items = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_items).lower()
        except (psutil.Error, OSError):
            continue
        if not cmdline or " app-server" not in f" {cmdline} ":
            continue
        if "codex.exe" not in cmdline and "codex.cmd" not in cmdline and "codex.js" not in cmdline:
            continue
        if target_workspace is not None:
            try:
                cwd = str(proc.cwd()).lower()
            except (psutil.Error, OSError):
                continue
            if cwd != target_workspace:
                continue
        pids.add(proc.info["pid"])
    return sorted(pids)


def _dedupe_nested_launcher_pids(pids: list[int]) -> list[int]:
    unique = sorted(set(pid for pid in pids if pid))
    keep = set(unique)
    for pid in unique:
        try:
            children = psutil.Process(pid).children(recursive=False)
        except psutil.Error:
            continue
        if any(child.pid in keep for child in children):
            keep.discard(pid)
    return sorted(keep)


def _process_executable(pid: int) -> str:
    try:
        return str(Path(psutil.Process(pid).exe()).resolve()).lower()
    except (psutil.Error, OSError):
        return ""


def _preferred_process_pids(pids: list[int]) -> tuple[list[int], list[int]]:
    unique = _dedupe_nested_launcher_pids(pids)
    if not unique:
        return [], []
    preferred_executable = str(Path(_background_python_executable()).resolve()).lower()
    preferred = [pid for pid in unique if _process_executable(pid) == preferred_executable]
    if preferred:
        return preferred, [pid for pid in unique if pid not in preferred]
    return unique, []


def _profile_runtime_state(profile: dict) -> dict:
    env = _normalized_profile_env(_load_env_file(Path(profile["env_file"])))
    state_dir = _state_dir_for_profile(profile, env)
    lock_path = state_dir / ".instance.lock"
    supervisor_pid_path = _supervisor_pid_path(state_dir)
    app_server_pid_path = _app_server_pid_path(state_dir)
    log_path = state_dir / "logs" / "relay.log"
    app_server_log_path = state_dir / "logs" / "app-server.log"
    session_dir = state_dir / "sessions"
    launch_lock_path = _launch_lock_path(state_dir)
    supervisor_lock_path = _supervisor_lock_path(state_dir)
    supervisor_pid = _read_pid_file(supervisor_pid_path)
    relay_pid = _read_pid_file(lock_path)
    app_server_file_pids = _read_pid_collection_file(app_server_pid_path)
    ready_marker_path = _ready_marker_path(state_dir)
    auth_failure_marker_path = _auth_failure_marker_path(state_dir)
    startup_notice_marker_path = _startup_notice_marker_path(state_dir)
    supervisor_alive = pid_exists(supervisor_pid)
    relay_alive = pid_exists(relay_pid)
    discovered_supervisor_pids, discovered_relay_pids = _discovered_relay_process_pids(profile, env)
    port = int(env.get("CODEX_APP_SERVER_PORT", "0") or "0")
    transport = env.get("CODEX_APP_SERVER_TRANSPORT", "stdio")
    app_server_pids = listening_pids(port) if transport == "websocket" and port > 0 else []
    lock_held = False
    app_server_live_file_pids = [pid for pid in app_server_file_pids if pid_exists(pid)]
    discovered_app_server_pids = _discovered_codex_app_server_pids(Path(env["CODEX_WORKDIR"]))

    if supervisor_alive and supervisor_pid is not None:
        discovered_supervisor_pids.append(supervisor_pid)
    if relay_alive and relay_pid is not None:
        discovered_relay_pids.append(relay_pid)

    if lock_path.exists() and not relay_alive and not discovered_relay_pids:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            lock_held = True
    elif lock_path.exists():
        lock_held = True
    if not lock_held:
        relay_pid = None
    if supervisor_pid_path.exists() and not supervisor_alive:
        try:
            supervisor_pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        supervisor_pid = None
    if app_server_pid_path.exists() and not app_server_live_file_pids:
        try:
            app_server_pid_path.unlink(missing_ok=True)
        except OSError:
            pass
    app_server_pids = sorted(set(app_server_pids + app_server_live_file_pids + discovered_app_server_pids))
    if ready_marker_path.exists() and not relay_alive and not discovered_relay_pids and not lock_held:
        try:
            ready_marker_path.unlink(missing_ok=True)
        except OSError:
            pass
    if not relay_alive and not discovered_relay_pids and not supervisor_alive and not discovered_supervisor_pids:
        for stale_lock_path in (launch_lock_path, supervisor_lock_path):
            if not stale_lock_path.exists():
                continue
            try:
                stale_lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    supervisor_pids_all = sorted(set(discovered_supervisor_pids))
    relay_pids_all = sorted(set(discovered_relay_pids))
    supervisor_pids, extra_supervisor_pids = _preferred_process_pids(supervisor_pids_all)
    relay_pids, extra_relay_pids = _preferred_process_pids(relay_pids_all)
    supervisor_pid = supervisor_pids[0] if supervisor_pids else supervisor_pid
    relay_pid = relay_pids[0] if relay_pids else relay_pid
    relay_running = bool(relay_pids) or lock_held
    ready = ready_marker_path.exists() and relay_running
    auth_failed = auth_failure_marker_path.exists()
    session_threads: dict[str, str] = {}
    if session_dir.exists():
        for session_file in sorted(session_dir.glob("*.json")):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            session_id = str(data.get("thread_id") or data.get("session_id") or "").strip()
            if session_id:
                session_threads[session_file.stem] = session_id
    return {
        "env": env,
        "state_dir": state_dir,
        "lock_path": lock_path,
        "supervisor_pid_path": supervisor_pid_path,
        "app_server_pid_path": app_server_pid_path,
        "log_path": log_path,
        "app_server_log_path": app_server_log_path,
        "session_dir": session_dir,
        "supervisor_pid": supervisor_pid,
        "relay_pid": relay_pid,
        "supervisor_pids_all": supervisor_pids_all,
        "relay_pids_all": relay_pids_all,
        "supervisor_pids": supervisor_pids,
        "relay_pids": relay_pids,
        "extra_supervisor_pids": extra_supervisor_pids,
        "extra_relay_pids": extra_relay_pids,
        "supervisor_alive": bool(supervisor_pids),
        "relay_alive": relay_running,
        "relay_lock_held": lock_held,
        "app_server_pids": app_server_pids,
        "running": relay_running or bool(app_server_pids) or bool(supervisor_pids),
        "ready": ready,
        "auth_failed": auth_failed,
        "provider": "codex",
        "ready_marker_path": ready_marker_path,
        "auth_failure_marker_path": auth_failure_marker_path,
        "startup_notice_marker_path": startup_notice_marker_path,
        "session_threads": session_threads,
    }


def _quarantine_stale_session_bindings(state_dir: Path, workspace: Path | None = None) -> int:
    session_dir = state_dir / "sessions"
    if not session_dir.exists():
        return 0
    if workspace is not None:
        codex_root = prepare_relay_codex_home(workspace) / "sessions"
    else:
        codex_root = prepare_relay_codex_home(Path.cwd()) / "sessions"
    moved = 0
    for session_file in session_dir.glob("*.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("session_id", "")).strip():
            continue
        thread_id = str(data.get("thread_id", "")).strip()
        if not thread_id:
            continue
        session_match = next(codex_root.rglob(f"*{thread_id}*.jsonl"), None) if codex_root.exists() else None
        if session_match is not None:
            continue
        bad_dir = state_dir / "bad-sessions"
        bad_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        target = bad_dir / f"{session_file.stem}.{timestamp}.json"
        session_file.replace(target)
        moved += 1
    return moved


def _wait_for_ready(
    pid: int,
    *,
    transport: str,
    port: int,
    ready_marker_path: Path,
    auth_failure_marker_path: Path,
    log_path: Path,
    timeout_seconds: float = 30.0,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_exists(pid):
            recent_log = tail_lines(log_path, 40)
            raise SystemExit(
                "Relay exited during startup.\n"
                + (f"Recent log output:\n{recent_log}" if recent_log else "No log output was captured.")
            )
        if auth_failure_marker_path.exists():
            recent_log = tail_lines(log_path, 40)
            raise SystemExit(
                "Relay startup failed because native Codex authentication is not healthy.\n"
                + (f"Recent log output:\n{recent_log}" if recent_log else "No log output was captured.")
            )
        if ready_marker_path.exists():
            return
        if transport == "websocket":
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    return
            except OSError:
                time.sleep(1)
                continue
        time.sleep(1)
    recent_log = tail_lines(log_path, 40)
    if transport == "websocket":
        raise SystemExit(
            f"Timed out waiting for relay readiness and Codex app-server on port {port}.\n"
            + (f"Recent log output:\n{recent_log}" if recent_log else "No log output was captured.")
        )
    raise SystemExit(
        "Timed out waiting for relay readiness.\n"
        + (f"Recent log output:\n{recent_log}" if recent_log else "No log output was captured.")
    )


def _wait_for_inflight_launch(profile: dict, timeout_seconds: float = 45.0) -> int:
    deadline = time.time() + timeout_seconds
    last_state: dict | None = None
    while time.time() < deadline:
        state = _profile_runtime_state(profile)
        last_state = state
        if state["ready"]:
            return 0
        if state["auth_failed"]:
            recent_log = tail_lines(state["log_path"], 40)
            raise SystemExit(
                "Relay startup failed because native Codex authentication is not healthy.\n"
                + (f"Recent log output:\n{recent_log}" if recent_log else "No log output was captured.")
            )
        time.sleep(0.5)
    if last_state is None:
        raise SystemExit("Another relay launch is in progress and could not be observed.")
    recent_log = tail_lines(last_state["log_path"], 40)
    raise SystemExit(
        "Another relay launch is in progress but never became ready.\n"
        + (f"Recent log output:\n{recent_log}" if recent_log else "No log output was captured.")
    )


def _wait_for_process_exit(pid: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not pid_exists(pid):
            return
        time.sleep(0.25)


def _sleep_until(seconds: float, stop_requested: list[bool]) -> bool:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        if stop_requested[0]:
            return False
        time.sleep(min(0.5, deadline - time.time()))
    return not stop_requested[0]


def _background_python_executable() -> str:
    runtime_python = install_plugin.runtime_python_path()
    if runtime_python.exists():
        return str(runtime_python)
    return sys.executable


def _background_python_windowless_executable() -> str:
    if os.name != "nt":
        return _background_python_executable()
    runtime_pythonw = install_plugin.runtime_python_path().with_name("pythonw.exe")
    if runtime_pythonw.exists():
        return str(runtime_pythonw)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return _background_python_executable()


def _backend_script_path(name: str) -> str:
    return str(Path(__file__).resolve().with_name(name))


def _codex_login_status(workspace: Path) -> tuple[bool, str]:
    provider_name = "codex"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    codex_bin = resolve_codex_bin()
    if not codex_bin:
        return False, "Codex CLI is not installed."
    result = subprocess.run(
        [codex_bin, "login", "status"],
        capture_output=True,
        text=True,
        check=False,
        env=relay_codex_env(workspace, os.environ.copy()),
        creationflags=creationflags,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0 and "logged in" in output.lower(), output


def _launch_bot_worker(env_file: Path, workspace: Path, *, log_path: Path | None = None) -> subprocess.Popen:
    env_data = _load_env_file(env_file)
    env_data.setdefault("CODEX_WORKDIR", str(workspace))
    env = _normalized_profile_env(env_data)
    child_env = relay_codex_env(workspace, os.environ.copy())
    child_env["ENV_FILE"] = str(env_file)
    child_env["PYTHONUNBUFFERED"] = "1"
    popen_kwargs: dict[str, object] = {
        "cwd": str(workspace),
        "env": child_env,
        "close_fds": True,
    }
    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab")
        popen_kwargs["stdout"] = log_handle
        popen_kwargs["stderr"] = subprocess.STDOUT
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(
            [
                _background_python_windowless_executable(),
                "-u",
                _backend_script_path("bot.py"),
            ],
            **popen_kwargs,
        )
    finally:
        if log_handle is not None:
            log_handle.close()
    return process


def _run_profile_foreground(profile: dict) -> int:
    env = _normalized_profile_env(_load_env_file(Path(profile["env_file"])))
    state_dir = state_dir_for_namespace(env["STATE_NAMESPACE"])
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    _quarantine_stale_session_bindings(state_dir, Path(profile["workspace"]))
    launch_lock = None
    try:
        try:
            launch_lock = _acquire_pid_lock(_launch_lock_path(state_dir))
        except OSError:
            return _wait_for_inflight_launch(profile)

        runtime = _profile_runtime_state(profile)
        if runtime["running"]:
            return 0

        logged_in, login_status = _codex_login_status(Path(profile["workspace"]))
        if not logged_in:
            raise SystemExit(
                "Native Codex CLI is not logged in for this terminal environment.\n"
                + f"`codex login status` -> {login_status or 'not logged in'}"
            )

        truncate_file_tail(runtime["log_path"], max_bytes=5 * 1024 * 1024, keep_bytes=1024 * 1024)
        truncate_file_tail(runtime["app_server_log_path"], max_bytes=5 * 1024 * 1024, keep_bytes=1024 * 1024)
        runtime["auth_failure_marker_path"].unlink(missing_ok=True)
        runtime["ready_marker_path"].unlink(missing_ok=True)

        child_env = relay_codex_env(Path(profile["workspace"]), os.environ.copy())
        child_env["ENV_FILE"] = profile["env_file"]
        child_env["PYTHONUNBUFFERED"] = "1"
        print(f"Running relay for `{profile['name']}` in the current terminal.")
        print(f"workspace: {profile['workspace']}")
        print(f"log: {runtime['log_path']}")
        return subprocess.run(
            [
                _background_python_executable(),
                "-u",
                str(Path(__file__).resolve().with_name("bot.py")),
            ],
            cwd=profile["workspace"],
            env=child_env,
            check=False,
        ).returncode
    finally:
        _release_pid_lock(launch_lock)


def _run_profile(profile: dict) -> int:
    env = _normalized_profile_env(_load_env_file(Path(profile["env_file"])))
    state_dir = state_dir_for_namespace(env["STATE_NAMESPACE"])
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    _quarantine_stale_session_bindings(state_dir, Path(profile["workspace"]))
    launch_lock = None
    try:
        try:
            launch_lock = _acquire_pid_lock(_launch_lock_path(state_dir))
        except OSError:
            return _wait_for_inflight_launch(profile)

        runtime = _profile_runtime_state(profile)
        if runtime["running"]:
            return 0

        launch_env = relay_codex_env(Path(profile["workspace"]), os.environ.copy())
        launch_env["ENV_FILE"] = profile["env_file"]
        launch_env["PYTHONUNBUFFERED"] = "1"
        logged_in, login_status = _codex_login_status(Path(profile["workspace"]))
        if not logged_in:
            raise SystemExit(
                "Native Codex CLI is not logged in for this terminal environment.\n"
                + f"`codex login status` -> {login_status or 'not logged in'}"
            )

        log_path = runtime["log_path"]
        truncate_file_tail(log_path, max_bytes=5 * 1024 * 1024, keep_bytes=1024 * 1024)
        truncate_file_tail(runtime["app_server_log_path"], max_bytes=5 * 1024 * 1024, keep_bytes=1024 * 1024)
        runtime["auth_failure_marker_path"].unlink(missing_ok=True)
        runtime["ready_marker_path"].unlink(missing_ok=True)
        log_handle = log_path.open("ab")
        popen_kwargs = {
            "cwd": profile["workspace"],
            "env": launch_env,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(
            [
                _background_python_windowless_executable(),
                _backend_script_path("relayctl.py"),
                "serve",
                "--env-file",
                profile["env_file"],
            ],
            **popen_kwargs,
        )
        log_handle.close()
        _wait_for_ready(
            process.pid,
            transport=env.get("CODEX_APP_SERVER_TRANSPORT", "stdio"),
            port=int(env["CODEX_APP_SERVER_PORT"]),
            ready_marker_path=runtime["ready_marker_path"],
            auth_failure_marker_path=runtime["auth_failure_marker_path"],
            log_path=log_path,
        )
        return 0
    finally:
        _release_pid_lock(launch_lock)


def _workspace_is_trusted(workspace: Path, *, config_path: Path | None = None) -> bool:
    if config_path is None:
        config_path = codex_config_path()
    if not config_path.exists():
        return False
    config_text = config_path.read_text(encoding="utf-8")
    project_path = str(workspace.resolve())
    header_pattern = "|".join(re.escape(header) for header in _project_header_variants(project_path))
    pattern = re.compile(rf"(?ms)^(?:{header_pattern})\n(.*?)(?=^\[|\Z)")
    match = pattern.search(config_text)
    return bool(match and 'trust_level = "trusted"' in match.group(1))


def _print_profile_status(profile: dict) -> None:
    state = _profile_runtime_state(profile)
    env = state["env"]
    print(f"profile: {profile['name']}")
    print(f"bot: {_bot_label(profile)}")
    print("provider: codex")
    print(f"model: {env.get('CODEX_MODEL') or DEFAULT_CODEX_MODEL}")
    print(
        "reasoning effort: "
        f"quick={env.get('CODEX_REASONING_EFFORT_QUICK', 'medium')} "
        f"default={env.get('CODEX_REASONING_EFFORT_DEFAULT', 'high')} "
        f"xhigh={'enabled' if env.get('CODEX_REASONING_EFFORT_ALLOW_XHIGH', 'false').strip().lower() in {'1', 'true', 'yes', 'on'} else 'disabled'}"
    )
    print(f"workspace: {profile['workspace']}")
    print(f"running: {'yes' if state['running'] else 'no'}")
    print(f"ready: {'yes' if state['ready'] else 'no'}")
    print(f"auth failed: {'yes' if state['auth_failed'] else 'no'}")
    print(f"transport: {env.get('CODEX_APP_SERVER_TRANSPORT', 'stdio')}")
    print(f"port: {env['CODEX_APP_SERVER_PORT']}")
    print(f"main channel: {env.get('RELAY_ATTACH_CHANNEL_ID', '') or '-'}")
    print(f"allow dms: {env.get('ALLOW_DMS', 'false')}")
    print(f"trigger mode: {env.get('BOT_TRIGGER_MODE', 'mention_or_dm')}")
    print(f"channel no-mention author ids: {env.get('CHANNEL_NO_MENTION_AUTHOR_IDS', '') or '-'}")
    print(f"supervisor pids: {', '.join(str(pid) for pid in state['supervisor_pids']) or '-'}")
    print(f"relay pids: {', '.join(str(pid) for pid in state['relay_pids']) or ('locked' if state['relay_lock_held'] else '-')}")
    if state["extra_supervisor_pids"]:
        print(f"extra supervisor pids: {', '.join(str(pid) for pid in state['extra_supervisor_pids'])}")
    if state["extra_relay_pids"]:
        print(f"extra relay pids: {', '.join(str(pid) for pid in state['extra_relay_pids'])}")
    print(f"app-server pids: {', '.join(str(pid) for pid in state['app_server_pids']) or '-'}")
    print(f"log: {state['log_path']}")
    print(f"app-server log: {state['app_server_log_path']}")
    print(f"state dir: {state['state_dir']}")
    print(f"config dir: {CONFIG_ROOT}")
    print(f"env file: {profile.get('env_file', '-')}")
    if state["session_threads"]:
        print("sessions:")
        for key, thread_id in state["session_threads"].items():
            print(f"  {key}: {thread_id}")
    else:
        print("sessions: -")


def _stop_profile(profile: dict) -> int:
    state = _profile_runtime_state(profile)
    candidate_pids: set[int] = set(state["app_server_pids"])
    candidate_pids.update(state["supervisor_pids_all"])
    candidate_pids.update(state["relay_pids_all"])

    stopped = False
    for pid in sorted(candidate_pids):
        stopped = terminate_process_tree(pid) or stopped
        _wait_for_process_exit(pid, timeout_seconds=5.0)

    if state["lock_path"].exists():
        lock_pid = _read_pid_file(state["lock_path"])
        if lock_pid is None or not pid_exists(lock_pid):
            state["lock_path"].unlink(missing_ok=True)
    if state["supervisor_pid_path"].exists():
        supervisor_pid = _read_pid_file(state["supervisor_pid_path"])
        if supervisor_pid is None or not pid_exists(supervisor_pid):
            state["supervisor_pid_path"].unlink(missing_ok=True)
    if state["app_server_pid_path"].exists():
        app_server_pids = _read_pid_collection_file(state["app_server_pid_path"])
        if not any(pid_exists(pid) for pid in app_server_pids):
            state["app_server_pid_path"].unlink(missing_ok=True)
    state["ready_marker_path"].unlink(missing_ok=True)
    state["auth_failure_marker_path"].unlink(missing_ok=True)

    return 0


def _cleanup_state_dir_artifacts(state_dir: Path) -> None:
    for path in (
        state_dir / ".instance.lock",
        _supervisor_pid_path(state_dir),
        _app_server_pid_path(state_dir),
        _launch_lock_path(state_dir),
        _supervisor_lock_path(state_dir),
        _ready_marker_path(state_dir),
        _auth_failure_marker_path(state_dir),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _state_dir_candidate_pids(state_dir: Path) -> list[int]:
    pids = _read_pid_collection_file(_app_server_pid_path(state_dir))
    for path in (state_dir / ".instance.lock", _supervisor_pid_path(state_dir)):
        pid = _read_pid_file(path)
        if pid is not None:
            pids.append(pid)
    return sorted(set(pid for pid in pids if pid_exists(pid)))


def _restart_profile(profile: dict) -> int:
    _stop_profile(profile)
    return _run_profile(profile)


def _profile_for_current_workspace() -> dict:
    return _select_profile_for_workspace(Path.cwd().resolve())


def cmd_run(_args: argparse.Namespace) -> int:
    workspace = Path.cwd().resolve()
    matches = _matching_profiles_for_workspace(workspace)
    if not matches:
        profile = _interactive_register(workspace)
        _register_profile(profile)
        print(f"Registered relay profile `{profile['name']}` for {profile['workspace']}")
    else:
        profile = _select_profile_for_workspace(workspace)
    background = not bool(getattr(_args, "foreground", False))
    return _run_profile(profile) if background else _run_profile_foreground(profile)


def cmd_list(_args: argparse.Namespace) -> int:
    registry = _load_registry()
    for profile in registry.get("profiles", []):
        print(f"{profile['name']}\t{_bot_label(profile)}\t{profile.get('attach_channel_id', '')}\t{profile['workspace']}")
    return 0


def cmd_project_list(_args: argparse.Namespace) -> int:
    registry = _load_registry()
    for project in registry.get("projects", []):
        members = ",".join(project.get("profiles", []))
        print(f"{project['name']}\t{len(project.get('profiles', []))}\t{members}")
    return 0


def cmd_project_save(args: argparse.Namespace) -> int:
    project_name = _normalize_project_name(args.name)
    selected: list[dict] = []
    if args.profile_names:
        for name in args.profile_names:
            profile = _profile_by_name(name)
            if profile is None:
                raise SystemExit(f"No registered relay profile named `{name}`.")
            selected.append(profile)
    elif args.workspace_root:
        selected = _profiles_under_workspace_root(Path(args.workspace_root))
    else:
        raise SystemExit("Provide at least one --profile or a --workspace-root.")

    if not selected:
        raise SystemExit(f"No registered relay profiles matched project `{project_name}`.")

    project = {
        "name": project_name,
        "profiles": [profile["name"] for profile in selected],
    }
    registry = _load_registry()
    _upsert_project(registry, project)
    _save_registry(registry)
    print(f"Saved relay project `{project_name}` with {len(project['profiles'])} profile(s).")
    for profile in selected:
        print(f"- {profile['name']} [{_bot_label(profile)}] {profile['workspace']}")
    return 0


def cmd_project_show(args: argparse.Namespace) -> int:
    project = _project_by_name(args.name)
    profiles = _profiles_for_project(project)
    print(f"project: {project['name']}")
    for profile in profiles:
        print(f"- {profile['name']}\t{_bot_label(profile)}\t{profile['workspace']}")
    return 0


def cmd_project_start(args: argparse.Namespace) -> int:
    project = _project_by_name(args.name)
    profiles = _profiles_for_project(project)
    for profile in profiles:
        _run_profile(profile)
    print(f"Started relay project `{project['name']}` with {len(profiles)} profile(s).")
    return 0


def cmd_project_stop(args: argparse.Namespace) -> int:
    project = _project_by_name(args.name)
    profiles = _profiles_for_project(project)
    for profile in profiles:
        _stop_profile(profile)
    print(f"Stopped relay project `{project['name']}`.")
    return 0


def cmd_project_status(args: argparse.Namespace) -> int:
    project = _project_by_name(args.name)
    profiles = _profiles_for_project(project)
    print(f"project: {project['name']}")
    for profile in profiles:
        state = _profile_runtime_state(profile)
        print(
            f"{profile['name']}\t{_bot_label(profile)}\t"
            f"running={'yes' if state['running'] else 'no'}\t"
            f"ready={'yes' if state['ready'] else 'no'}\t"
            f"workspace={profile['workspace']}"
        )
    return 0


def cmd_project_remove(args: argparse.Namespace) -> int:
    project_name = _normalize_project_name(args.name)
    registry = _load_registry()
    projects = registry.get("projects", [])
    remaining = [item for item in projects if item.get("name") != project_name]
    if len(remaining) == len(projects):
        raise SystemExit(f"No saved relay project named `{project_name}`.")
    registry["projects"] = remaining
    _save_registry(registry)
    print(f"Removed relay project `{project_name}`.")
    return 0


def _gui_profile_rows() -> list[dict]:
    rows: list[dict] = []
    for profile in _all_registered_profiles():
        state = _profile_runtime_state(profile)
        rows.append(
            {
                "name": profile["name"],
                "bot": _bot_label(profile),
                "running": "yes" if state["running"] else "no",
                "ready": "yes" if state["ready"] else "no",
                "channel": profile.get("attach_channel_id", "") or "-",
                "workspace": profile["workspace"],
                "profile": profile,
            }
        )
    return rows


def _gui_project_rows() -> list[dict]:
    rows: list[dict] = []
    for project in _load_registry().get("projects", []):
        members = [str(member).strip() for member in project.get("profiles", []) if str(member).strip()]
        rows.append(
            {
                "name": str(project.get("name", "")).strip(),
                "count": len(members),
                "members": ", ".join(members),
                "project": {"name": str(project.get("name", "")).strip(), "profiles": members},
            }
        )
    rows.sort(key=lambda item: item["name"])
    return rows


def _clean_live_text(text: str) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", text.replace("\r", ""))
    cleaned = cleaned.replace("\x00", "")
    cleaned = EMAIL_RE.sub("<redacted-email>", cleaned)
    cleaned = WINDOWS_USER_PATH_RE.sub(r"\1<user>", cleaned)
    return cleaned.strip()


def _short_live_text(text: str, limit: int = 110) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _compact_live_path(value: str, *, limit: int = 68) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    parts = re.split(r"[\\/]+", text)
    if len(parts) >= 3:
        return ".../" + "/".join(parts[-3:])
    return "..." + text[-(limit - 3) :]


def _last_path_token(text: str) -> str:
    token = text.strip().strip("'\"")
    try:
        return Path(token).name or token
    except Exception:
        return token


def _describe_command(command: str) -> str:
    text = command.strip()
    lowered = text.lower()
    if lowered.startswith("cmd /c "):
        text = text[7:].strip()
        lowered = text.lower()
    if lowered.startswith("get-content "):
        return f"Reading {_last_path_token(text.split(None, 1)[1])}"
    if lowered.startswith("type "):
        return f"Reading {_last_path_token(text.split(None, 1)[1])}"
    if lowered.startswith("rg ") or lowered.startswith("rg.exe "):
        return "Searching code"
    if lowered.startswith("findstr "):
        return "Searching text"
    if lowered.startswith("git diff"):
        return "Reviewing git diff"
    if lowered.startswith("git status"):
        return "Checking git status"
    if lowered.startswith("git show"):
        return "Inspecting git history"
    if lowered.startswith("pytest ") or " -m pytest" in lowered:
        return "Running tests"
    if "python -m compileall" in lowered:
        return "Checking Python compile health"
    if "apply_patch" in lowered:
        return "Editing files"
    if lowered.startswith("dir") or lowered.startswith("ls ") or lowered.startswith("get-childitem"):
        return "Listing files"
    return "Ran " + _short_live_text(text)


def _summarize_relay_log_text(text: str) -> list[str]:
    events: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_live_text(raw_line)
        lowered = line.lower()
        if not line:
            continue
        if "supervisor starting relay worker" in lowered:
            events.append("Started relay worker")
        elif lowered == "native codex login: ok":
            events.append("Codex login healthy")
        elif lowered == "logged in using chatgpt":
            events.append("Using ChatGPT login")
        elif lowered == "codex startup healthcheck: ok":
            events.append("Codex healthcheck passed")
        elif "discord relay connected as" in lowered:
            events.append(_short_live_text(line.replace("Discord relay connected as", "Discord connected as").strip()))
        elif "relay worker exited with code" in lowered:
            events.append(_short_live_text("Worker restarted: " + line.split("Relay worker exited with code", 1)[1].strip()))
        elif "relay error for" in lowered:
            events.append(_short_live_text("Relay error: " + line.split(":", 1)[-1].strip()))
    deduped: list[str] = []
    for event in events:
        if deduped and deduped[-1] == event:
            continue
        deduped.append(event)
    return deduped[-8:]


def _summarize_app_log_text(text: str) -> list[str]:
    session_markers = [match.start() for match in re.finditer(r"^=== app-server\[", text, flags=re.MULTILINE)]
    if session_markers:
        text = text[session_markers[-1] :]
    events: list[str] = []
    last_command_summary: str | None = None
    for raw_line in text.splitlines():
        line = _clean_live_text(raw_line)
        lowered = line.lower()
        if not line:
            continue
        if line.startswith("OBSERVE "):
            events.append(line.removeprefix("OBSERVE ").strip())
            continue
        if lowered.startswith("name  : ") or lowered.startswith("value : "):
            continue
        if line.startswith("=== app-server["):
            events.append("Codex session active")
            continue
        if line.startswith("+ "):
            command_text = line[2:].strip()
            command_lower = command_text.lower()
            if (
                command_text.startswith("~")
                or command_lower.startswith("categoryinfo")
                or command_lower.startswith("fullyqualifiederrorid")
            ):
                continue
            last_command_summary = _describe_command(command_text)
            events.append(last_command_summary)
            continue
        if lowered.startswith("at line:") or lowered.startswith("+ ~") or lowered.startswith("categoryinfo") or lowered.startswith("fullyqualifiederrorid"):
            continue
        if lowered == "output:" or lowered.startswith("wall time:"):
            continue
        if "cannot find path" in lowered:
            match = re.search(r"Cannot find path '([^']+)'", line)
            if match:
                missing = Path(match.group(1)).name
                if last_command_summary:
                    events.append(f"{last_command_summary}, but {missing} is missing")
                else:
                    events.append(f"Missing file: {missing}")
            else:
                events.append(_short_live_text("Missing path: " + line))
            continue
        if lowered.startswith("rg:") and "os error" in lowered:
            if last_command_summary:
                events.append(f"{last_command_summary}, but the search path failed")
            else:
                events.append(_short_live_text("Search error: " + line[3:].strip()))
            continue
        if "error" in lowered and "codex_core::tools::router" in lowered:
            if last_command_summary:
                events.append(f"{last_command_summary} failed")
            else:
                events.append("Tool command failed")
            continue
        if "exit code:" in lowered and "wall time:" in lowered:
            events.append(_short_live_text(line))
            continue
    deduped: list[str] = []
    for event in events:
        if deduped and deduped[-1] == event:
            continue
        deduped.append(event)
    return deduped[-10:]


def _profile_live_view_lines(profile: dict) -> list[str]:
    state = _profile_runtime_state(profile)
    lines = [
        f"{profile['name']} [{_bot_label(profile)}]",
        f"Status: running={'yes' if state['running'] else 'no'} | ready={'yes' if state['ready'] else 'no'} | auth_failed={'yes' if state['auth_failed'] else 'no'}",
        f"Workspace: {_compact_live_path(profile['workspace'])}",
    ]

    relay_events = _summarize_relay_log_text(tail_lines(state["log_path"], 80))
    app_events = _summarize_app_log_text(tail_lines(state["app_server_log_path"], 300))
    recent_events = relay_events + app_events
    if not recent_events:
        if state["running"]:
            recent_events = ["Working. No recent simplified events yet."]
        else:
            recent_events = ["Not running."]
    ignored_status = {
        "Codex session active",
        "Codex login healthy",
        "Using ChatGPT login",
        "Codex healthcheck passed",
        "Started relay worker",
        "Tool command failed",
        "working: Working on the current Discord turn.",
    }
    current_activity = "Not running." if not state["running"] else next(
        (
            event
            for event in reversed(recent_events)
            if event not in ignored_status
            and not event.startswith("Discord connected as")
            and not event.startswith("Relay error:")
            and not event.startswith("Worker restarted:")
        ),
        "Connected and working in the active Codex session.",
    )

    lines.append("")
    lines.append(f"Current activity: {current_activity}")
    lines.append("")
    lines.append("Live view")
    for event in recent_events[-12:]:
        lines.append(f"- {event}")
    return lines


def _profile_raw_terminal_lines(profile: dict, *, app_lines: int = 140, relay_lines: int = 24) -> list[str]:
    state = _profile_runtime_state(profile)
    lines = [
        f"{profile['name']} [{_bot_label(profile)}]",
        f"Status: running={'yes' if state['running'] else 'no'} | ready={'yes' if state['ready'] else 'no'} | auth_failed={'yes' if state['auth_failed'] else 'no'}",
        f"Workspace: {profile['workspace']}",
        "",
        "[codex terminal]",
    ]
    app_log = tail_lines(state["app_server_log_path"], app_lines).rstrip()
    if app_log:
        lines.extend(app_log.splitlines())
    else:
        lines.append("(no app-server output yet)")
    relay_log = tail_lines(state["log_path"], relay_lines).rstrip()
    if relay_log:
        lines.extend(["", "[relay supervisor]"])
        lines.extend(relay_log.splitlines())
    return lines


def _default_live_view_lines() -> list[str]:
    running_profiles = [profile for profile in _all_registered_profiles() if _profile_runtime_state(profile)["running"]]
    lines = ["Select a profile or project to inspect live activity."]
    if running_profiles:
        lines.append("")
        lines.append("Running now")
        for profile in running_profiles:
            state = _profile_runtime_state(profile)
            lines.append(
                f"- {_bot_label(profile)}: ready={'yes' if state['ready'] else 'no'} | workspace={_compact_live_path(profile['workspace'])}"
            )
    return lines


def cmd_gui(_args: argparse.Namespace) -> int:
    if os.environ.get(GUI_CHILD_ENV, "").strip() != "1":
        return _launch_gui_detached()

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        raise SystemExit(f"Native GUI is unavailable in this Python runtime: {exc}")

    root = tk.Tk()
    root.title("CLADEX Relay Manager")
    root.geometry("1420x880")
    root.minsize(1220, 720)

    colors = {
        "bg": "#efeae1",
        "surface": "#fbf8f2",
        "panel": "#fffdfa",
        "panel_alt": "#f4efe6",
        "border": "#d5cdc1",
        "text": "#1f2933",
        "muted": "#6b7280",
        "accent": "#0f766e",
        "accent_dark": "#115e59",
        "accent_soft": "#dff4f1",
        "console_bg": "#111827",
        "console_text": "#e5edf7",
    }
    root.configure(bg=colors["bg"])

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", font=("Segoe UI", 10))
    style.configure("Root.TFrame", background=colors["bg"])
    style.configure("Panel.TFrame", background=colors["surface"])
    style.configure("Sidebar.TLabelframe", background=colors["panel"], bordercolor=colors["border"], relief="solid")
    style.configure("Sidebar.TLabelframe.Label", background=colors["panel"], foreground=colors["text"], font=("Segoe UI Semibold", 10))
    style.configure("Treeview", rowheight=28, background=colors["panel"], fieldbackground=colors["panel"], foreground=colors["text"], bordercolor=colors["border"])
    style.configure("Treeview.Heading", background=colors["panel_alt"], foreground=colors["text"], font=("Segoe UI Semibold", 10), relief="flat", padding=(10, 8))
    style.map("Treeview", background=[("selected", colors["accent_soft"])], foreground=[("selected", colors["text"])])
    style.configure("Header.TLabel", background=colors["bg"], foreground=colors["text"], font=("Segoe UI Semibold", 18))
    style.configure("SubHeader.TLabel", background=colors["bg"], foreground=colors["muted"], font=("Segoe UI", 10))
    style.configure("Section.TLabel", background=colors["panel"], foreground=colors["text"], font=("Segoe UI Semibold", 10))
    style.configure("Detail.TLabel", background=colors["panel"], foreground=colors["muted"], font=("Segoe UI", 9))
    style.configure("MetricValue.TLabel", background=colors["panel"], foreground=colors["text"], font=("Segoe UI Semibold", 18))
    style.configure("Primary.TButton", background=colors["accent"], foreground="white", padding=(14, 8), borderwidth=0)
    style.map("Primary.TButton", background=[("active", colors["accent_dark"]), ("pressed", colors["accent_dark"])])
    style.configure("Secondary.TButton", background=colors["panel"], foreground=colors["text"], padding=(12, 8), bordercolor=colors["border"])
    style.map("Secondary.TButton", background=[("active", colors["panel_alt"]), ("pressed", colors["panel_alt"])])
    style.configure("SidebarPrimary.TButton", background=colors["accent"], foreground="white", padding=(10, 6), borderwidth=0, font=("Segoe UI Semibold", 9))
    style.map("SidebarPrimary.TButton", background=[("active", colors["accent_dark"]), ("pressed", colors["accent_dark"])])
    style.configure("SidebarSecondary.TButton", background=colors["panel"], foreground=colors["text"], padding=(8, 6), bordercolor=colors["border"], font=("Segoe UI", 9))
    style.map("SidebarSecondary.TButton", background=[("active", colors["panel_alt"]), ("pressed", colors["panel_alt"])])
    style.configure("Status.TLabel", background=colors["panel_alt"], foreground=colors["text"], padding=(10, 8))

    current_workspace = tk.StringVar(value=str(Path.cwd().resolve()))
    status_var = tk.StringVar(value="Ready.")
    project_name_var = tk.StringVar(value="")
    summary_vars = {
        "profiles": tk.StringVar(value="0"),
        "running": tk.StringVar(value="0"),
        "ready": tk.StringVar(value="0"),
        "projects": tk.StringVar(value="0"),
    }
    profile_detail_var = tk.StringVar(value="Select a profile to inspect and act on it here.")
    project_detail_var = tk.StringVar(value="Select a project to inspect and act on it here.")

    body = ttk.Frame(root, style="Root.TFrame", padding=18)
    body.pack(fill="both", expand=True)

    header = ttk.Frame(body, style="Root.TFrame")
    header.pack(fill="x", pady=(0, 10))
    ttk.Label(header, text="Relay Manager", style="Header.TLabel").pack(side="left")
    ttk.Label(header, text="Start, stop, group, and edit relay profiles without terminal babysitting.", style="SubHeader.TLabel").pack(side="left", padx=(14, 0), pady=(6, 0))
    ttk.Label(header, textvariable=current_workspace, style="SubHeader.TLabel").pack(side="right", pady=(6, 0))

    toolbar = ttk.Frame(body, style="Root.TFrame")
    toolbar.pack(fill="x", pady=(0, 12))

    metrics = ttk.Frame(body, style="Root.TFrame")
    metrics.pack(fill="x", pady=(0, 12))

    notebook = ttk.Notebook(body)
    notebook.pack(fill="both", expand=True)

    profiles_tab = ttk.Frame(notebook, padding=10, style="Panel.TFrame")
    projects_tab = ttk.Frame(notebook, padding=10, style="Panel.TFrame")
    notebook.add(profiles_tab, text="Profiles")
    notebook.add(projects_tab, text="Projects")

    output_frame = ttk.LabelFrame(body, text="Observer", padding=8, style="Sidebar.TLabelframe")
    output_frame.pack(fill="both", expand=False, pady=(10, 0))
    output_notebook = ttk.Notebook(output_frame)
    output_notebook.pack(fill="both", expand=True)

    live_tab = ttk.Frame(output_notebook, style="Panel.TFrame")
    activity_tab = ttk.Frame(output_notebook, style="Panel.TFrame")
    output_notebook.add(live_tab, text="Live View")
    output_notebook.add(activity_tab, text="Activity")

    live_text = tk.Text(
        live_tab,
        height=8,
        wrap="word",
        bg=colors["console_bg"],
        fg=colors["console_text"],
        insertbackground=colors["console_text"],
        relief="flat",
        borderwidth=0,
        padx=12,
        pady=10,
        font=("Cascadia Mono", 10),
    )
    live_scroll = ttk.Scrollbar(live_tab, orient="vertical", command=live_text.yview)
    live_text.configure(yscrollcommand=live_scroll.set)
    live_text.pack(side="left", fill="both", expand=True)
    live_scroll.pack(side="right", fill="y")
    live_text.insert("end", "Select a profile or project to inspect live activity.\n")
    live_text.configure(state="disabled")

    output_text = tk.Text(
        activity_tab,
        height=6,
        wrap="word",
        bg=colors["console_bg"],
        fg=colors["console_text"],
        insertbackground=colors["console_text"],
        relief="flat",
        borderwidth=0,
        padx=12,
        pady=10,
        font=("Cascadia Mono", 10),
    )
    output_scroll = ttk.Scrollbar(activity_tab, orient="vertical", command=output_text.yview)
    output_text.configure(yscrollcommand=output_scroll.set)
    output_text.pack(side="left", fill="both", expand=True)
    output_scroll.pack(side="right", fill="y")
    output_text.insert("end", "Relay manager ready.\n")
    output_text.configure(state="disabled")

    status_bar = ttk.Label(body, textvariable=status_var, anchor="w", style="Status.TLabel")
    status_bar.pack(fill="x", pady=(8, 0))

    profile_tree = ttk.Treeview(
        profiles_tab,
        columns=("name", "bot", "running", "ready", "channel", "workspace"),
        show="headings",
        selectmode="extended",
    )
    for column, label, width in [
        ("name", "Profile", 210),
        ("bot", "Bot", 140),
        ("running", "Running", 80),
        ("ready", "Ready", 80),
        ("channel", "Channel", 140),
        ("workspace", "Workspace", 420),
    ]:
        profile_tree.heading(column, text=label)
        profile_tree.column(column, width=width, anchor="w")
    profile_tree.column("running", anchor="center")
    profile_tree.column("ready", anchor="center")
    profile_scroll = ttk.Scrollbar(profiles_tab, orient="vertical", command=profile_tree.yview)
    profile_tree.configure(yscrollcommand=profile_scroll.set)
    profile_tree.pack(side="left", fill="both", expand=True)
    profile_scroll.pack(side="left", fill="y", padx=(6, 0))

    profile_button_col = ttk.LabelFrame(profiles_tab, text="Profile Actions", padding=8, style="Sidebar.TLabelframe", width=340)
    profile_button_col.pack(side="left", fill="y", padx=(14, 0))
    profile_button_col.pack_propagate(False)

    project_tree = ttk.Treeview(
        projects_tab,
        columns=("name", "count", "members"),
        show="headings",
        selectmode="browse",
    )
    project_tree.heading("name", text="Project")
    project_tree.heading("count", text="Profiles")
    project_tree.heading("members", text="Members")
    project_tree.column("name", width=220, anchor="w")
    project_tree.column("count", width=90, anchor="center")
    project_tree.column("members", width=540, anchor="w")
    project_scroll = ttk.Scrollbar(projects_tab, orient="vertical", command=project_tree.yview)
    project_tree.configure(yscrollcommand=project_scroll.set)
    project_tree.pack(side="left", fill="both", expand=True)
    project_scroll.pack(side="left", fill="y", padx=(6, 0))

    project_button_col = ttk.LabelFrame(projects_tab, text="Project Actions", padding=8, style="Sidebar.TLabelframe", width=340)
    project_button_col.pack(side="left", fill="y", padx=(14, 0))
    project_button_col.pack_propagate(False)

    profile_records: dict[str, dict] = {}
    project_records: dict[str, dict] = {}

    def _metric_card(parent, title: str, value_var: tk.StringVar) -> None:
        card = ttk.LabelFrame(parent, text=title, padding=10, style="Sidebar.TLabelframe")
        card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Label(card, textvariable=value_var, style="MetricValue.TLabel").pack(anchor="w")

    def _compact_path(value: str, *, limit: int = 44) -> str:
        if len(value) <= limit:
            return value
        keep = max(8, (limit - 3) // 2)
        return value[:keep] + "..." + value[-keep:]

    def _build_scrollable_sidebar(parent: ttk.LabelFrame) -> ttk.Frame:
        shell = ttk.Frame(parent, style="Panel.TFrame")
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            shell,
            bg=colors["panel"],
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas, style="Panel.TFrame")
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _sync_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _resize_body(_event) -> None:
            canvas.itemconfigure(window_id, width=_event.width)

        def _on_wheel(event) -> str:
            delta = event.delta
            if delta == 0 and getattr(event, "num", None) in {4, 5}:
                delta = 120 if event.num == 4 else -120
            if delta:
                canvas.yview_scroll(int(-delta / 120), "units")
            return "break"

        body.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _resize_body)
        for widget in (canvas, body):
            widget.bind("<MouseWheel>", _on_wheel)
            widget.bind("<Button-4>", _on_wheel)
            widget.bind("<Button-5>", _on_wheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return body

    _metric_card(metrics, "Profiles", summary_vars["profiles"])
    _metric_card(metrics, "Running", summary_vars["running"])
    _metric_card(metrics, "Ready", summary_vars["ready"])
    last_metric = ttk.LabelFrame(metrics, text="Projects", padding=10, style="Sidebar.TLabelframe")
    last_metric.pack(side="left", fill="x", expand=True)
    ttk.Label(last_metric, textvariable=summary_vars["projects"], style="MetricValue.TLabel").pack(anchor="w")

    profile_button_body = _build_scrollable_sidebar(profile_button_col)
    project_button_body = _build_scrollable_sidebar(project_button_col)

    live_render_cache = {"value": ""}
    live_refresh_job = {"id": None}

    def _set_console_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text.rstrip() + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def log(message: str) -> None:
        output_text.configure(state="normal")
        output_text.insert("end", message.rstrip() + "\n")
        output_text.see("end")
        output_text.configure(state="disabled")
        status_var.set(message.rstrip())

    def selected_profiles() -> list[dict]:
        return [profile_records[item] for item in profile_tree.selection() if item in profile_records]

    def selected_project() -> dict | None:
        current = project_tree.selection()
        if not current:
            return None
        return project_records.get(current[0])

    def run_action(label: str, callback) -> None:
        status_var.set(f"{label}...")

        def _worker() -> None:
            try:
                result = callback()
                lines = result if isinstance(result, list) else [str(result)] if result else [f"{label} complete."]
                root.after(0, lambda: [log(line) for line in lines if line])
            except Exception as exc:
                root.after(0, lambda: messagebox.showerror("Relay Manager", str(exc)))
                root.after(0, lambda: status_var.set(f"{label} failed."))
            finally:
                root.after(0, refresh_all)

        threading.Thread(target=_worker, daemon=True).start()

    def refresh_profiles() -> None:
        profile_records.clear()
        for item in profile_tree.get_children():
            profile_tree.delete(item)
        for row in _gui_profile_rows():
            item_id = row["name"]
            profile_records[item_id] = row["profile"]
            profile_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(row["name"], row["bot"], row["running"], row["ready"], row["channel"], row["workspace"]),
            )

    def refresh_projects() -> None:
        project_records.clear()
        for item in project_tree.get_children():
            project_tree.delete(item)
        for row in _gui_project_rows():
            item_id = row["name"]
            project_records[item_id] = row["project"]
            project_tree.insert("", "end", iid=item_id, values=(row["name"], row["count"], row["members"]))

    def update_summary() -> None:
        profiles = list(profile_records.values())
        running = 0
        ready = 0
        for profile in profiles:
            state = _profile_runtime_state(profile)
            running += 1 if state["running"] else 0
            ready += 1 if state["ready"] else 0
        summary_vars["profiles"].set(str(len(profiles)))
        summary_vars["running"].set(str(running))
        summary_vars["ready"].set(str(ready))
        summary_vars["projects"].set(str(len(project_records)))

    def update_profile_detail(*_args) -> None:
        selected = selected_profiles()
        if not selected:
            profile_detail_var.set("Select a profile to inspect and act on it here.")
            return
        if len(selected) == 1:
            profile = selected[0]
            state = _profile_runtime_state(profile)
            profile_detail_var.set(
                "\n".join(
                    [
                        f"Profile: {profile['name']}",
                        f"Bot: {_bot_label(profile)} | Running: {'yes' if state['running'] else 'no'} | Ready: {'yes' if state['ready'] else 'no'}",
                        f"Channel: {profile.get('attach_channel_id', '') or '-'}",
                        f"Workspace: {_compact_path(profile['workspace'])}",
                    ]
                )
            )
            return
        profile_detail_var.set(
            "\n".join(
                [
                    f"{len(selected)} profiles selected",
                    ", ".join(item["name"] for item in selected[:4]),
                ]
            )
        )

    def update_project_detail(*_args) -> None:
        project = selected_project()
        if project is None:
            project_detail_var.set("Select a project to inspect and act on it here.")
            return
        members = [f"- {member}" for member in project.get("profiles", [])]
        project_detail_var.set(
            "\n".join(
                [
                    f"Project: {project['name']}",
                    f"Profiles: {len(project.get('profiles', []))}",
                    ", ".join(project.get("profiles", [])[:4]) or "-",
                ]
            )
        )

    def _schedule_live_view_refresh(delay_ms: int = 0) -> None:
        if live_refresh_job["id"] is not None:
            root.after_cancel(live_refresh_job["id"])
        live_refresh_job["id"] = root.after(delay_ms, refresh_live_view)

    def refresh_all() -> None:
        refresh_profiles()
        refresh_projects()
        update_summary()
        update_profile_detail()
        update_project_detail()
        _schedule_live_view_refresh()

    def profile_status_lines(profile: dict) -> list[str]:
        state = _profile_runtime_state(profile)
        return [
            f"{profile['name']} [{_bot_label(profile)}]",
            f"  running={'yes' if state['running'] else 'no'} ready={'yes' if state['ready'] else 'no'}",
            f"  workspace={profile['workspace']}",
        ]

    def edit_profile_dialog(profile: dict | None = None) -> None:
        editing = profile is not None
        existing_env = _normalized_profile_env(_load_env_file(Path(profile["env_file"]))) if editing else {}
        dialog = tk.Toplevel(root)
        dialog.title("Edit Relay Profile" if editing else "Add Relay Profile")
        dialog.transient(root)
        dialog.grab_set()
        dialog.geometry("760x520")
        dialog.minsize(720, 460)

        vars_map = {
            "workspace": tk.StringVar(value=existing_env.get("CODEX_WORKDIR", str(Path.cwd().resolve()))),
            "token": tk.StringVar(value=existing_env.get("DISCORD_BOT_TOKEN", "")),
            "bot_name": tk.StringVar(value=existing_env.get("RELAY_BOT_NAME", "")),
            "channels": tk.StringVar(value=existing_env.get("ALLOWED_CHANNEL_IDS", "")),
            "users": tk.StringVar(value=existing_env.get("ALLOWED_USER_IDS", "")),
            "authors": tk.StringVar(value=existing_env.get("ALLOWED_CHANNEL_AUTHOR_IDS", "")),
            "no_mention": tk.StringVar(value=existing_env.get("CHANNEL_NO_MENTION_AUTHOR_IDS", "")),
            "model": tk.StringVar(value=existing_env.get("CODEX_MODEL", "")),
            "history_limit": tk.StringVar(value=existing_env.get("CHANNEL_HISTORY_LIMIT", "20")),
            "trigger_mode": tk.StringVar(value=existing_env.get("BOT_TRIGGER_MODE", "mention_or_dm")),
            "allow_dms": tk.BooleanVar(value=existing_env.get("ALLOW_DMS", "false").lower() in {"1", "true", "yes", "on"}),
            "open_terminal": tk.BooleanVar(value=existing_env.get("OPEN_VISIBLE_TERMINAL", "false").lower() in {"1", "true", "yes", "on"}),
        }

        shell = ttk.Frame(dialog, padding=14, style="Panel.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        ttk.Label(
            shell,
            text="Basics first. Advanced relay controls are available on their own tab.",
            style="SubHeader.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        notebook = ttk.Notebook(shell)
        notebook.grid(row=1, column=0, sticky="nsew")

        basics_tab = ttk.Frame(notebook, padding=14, style="Panel.TFrame")
        access_tab = ttk.Frame(notebook, padding=14, style="Panel.TFrame")
        advanced_tab = ttk.Frame(notebook, padding=14, style="Panel.TFrame")
        notebook.add(basics_tab, text="Basics")
        notebook.add(access_tab, text="Access")
        notebook.add(advanced_tab, text="Advanced")

        basic_rows = [
            ("Workspace", "workspace"),
            ("Discord Bot Token", "token"),
            ("Bot Name", "bot_name"),
            ("Main Channel ID(s)", "channels"),
        ]
        for index, (label_text, key) in enumerate(basic_rows):
            ttk.Label(basics_tab, text=label_text).grid(row=index, column=0, sticky="w", pady=6)
            entry = ttk.Entry(basics_tab, textvariable=vars_map[key], width=72, show="*" if key == "token" else "")
            entry.grid(row=index, column=1, sticky="ew", pady=6)
            if key == "workspace":
                ttk.Button(
                    basics_tab,
                    text="Browse",
                    style="Secondary.TButton",
                    command=lambda var=vars_map["workspace"]: var.set(filedialog.askdirectory(initialdir=var.get() or str(Path.cwd())) or var.get()),
                ).grid(row=index, column=2, sticky="ew", padx=(8, 0))
        ttk.Label(
            basics_tab,
            text="Use one or more channel IDs separated by commas if this bot should answer in multiple places.",
            style="Detail.TLabel",
        ).grid(row=len(basic_rows), column=1, sticky="w", pady=(0, 8))
        basics_tab.columnconfigure(1, weight=1)

        ttk.Label(access_tab, text="Allowed DM User IDs").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(access_tab, textvariable=vars_map["users"], width=72).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(access_tab, text="Trigger Mode").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(
            access_tab,
            textvariable=vars_map["trigger_mode"],
            values=("mention_or_dm", "all", "dm_only"),
            state="readonly",
            width=24,
        ).grid(row=1, column=1, sticky="w", pady=6)
        access_checks = ttk.Frame(access_tab, style="Panel.TFrame")
        access_checks.grid(row=2, column=1, sticky="w", pady=(8, 6))
        ttk.Checkbutton(access_checks, text="Allow DMs", variable=vars_map["allow_dms"]).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(access_checks, text="Open Visible Terminal", variable=vars_map["open_terminal"]).pack(side="left")
        ttk.Label(
            access_tab,
            text="Default use: set the channel, your DM user ID, and leave the rest alone.",
            style="Detail.TLabel",
        ).grid(row=3, column=1, sticky="w", pady=(4, 0))
        access_tab.columnconfigure(1, weight=1)

        advanced_rows = [
            ("Allowed Channel Author IDs", "authors"),
            ("No-Mention Author IDs", "no_mention"),
            ("Codex Model", "model"),
            ("Channel History Limit", "history_limit"),
        ]
        for index, (label_text, key) in enumerate(advanced_rows):
            ttk.Label(advanced_tab, text=label_text).grid(row=index, column=0, sticky="w", pady=6)
            ttk.Entry(advanced_tab, textvariable=vars_map[key], width=72).grid(row=index, column=1, sticky="ew", pady=6)
        ttk.Label(
            advanced_tab,
            text="Advanced is for relay operators who need custom routing or Codex runtime overrides.",
            style="Detail.TLabel",
        ).grid(row=len(advanced_rows), column=1, sticky="w", pady=(4, 0))
        advanced_tab.columnconfigure(1, weight=1)

        buttons = ttk.Frame(shell, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="e", pady=(16, 0))

        def _save() -> None:
            workspace = Path(vars_map["workspace"].get().strip() or Path.cwd()).resolve()
            token = vars_map["token"].get().strip()
            if not token:
                messagebox.showerror("Relay Manager", "Discord bot token is required.")
                return
            channels = _parse_csv_ids(vars_map["channels"].get())
            if not vars_map["allow_dms"].get() and not channels:
                messagebox.showerror("Relay Manager", "At least one allowed channel id is required unless DMs are enabled.")
                return
            env = {
                "DISCORD_BOT_TOKEN": token,
                "RELAY_BOT_NAME": vars_map["bot_name"].get().strip(),
                "CODEX_WORKDIR": str(workspace),
                "CODEX_MODEL": vars_map["model"].get().strip(),
                "CODEX_FULL_ACCESS": "true",
                "CODEX_READ_ONLY": "false",
                "CODEX_APP_SERVER_TRANSPORT": "stdio",
                "CODEX_APP_SERVER_PORT": str(default_port_for_workspace(workspace, token=token)),
                "STATE_NAMESPACE": existing_env.get("STATE_NAMESPACE") if editing else default_namespace_for_workspace(workspace, token=token),
                "ALLOW_DMS": "true" if vars_map["allow_dms"].get() else "false",
                "BOT_TRIGGER_MODE": vars_map["trigger_mode"].get().strip() or "mention_or_dm",
                "ALLOWED_USER_IDS": _parse_csv_ids(vars_map["users"].get()),
                "ALLOWED_CHANNEL_AUTHOR_IDS": _parse_csv_ids(vars_map["authors"].get()),
                "CHANNEL_NO_MENTION_AUTHOR_IDS": _parse_csv_ids(vars_map["no_mention"].get()),
                "STARTUP_DM_USER_IDS": _parse_csv_ids(vars_map["users"].get()),
                "STARTUP_DM_TEXT": existing_env.get("STARTUP_DM_TEXT", "Discord relay online. DM me here to chat with Codex."),
                "ALLOWED_CHANNEL_IDS": channels,
                "CHANNEL_HISTORY_LIMIT": vars_map["history_limit"].get().strip() or "20",
                "OPEN_VISIBLE_TERMINAL": "true" if vars_map["open_terminal"].get() else "false",
                "RELAY_ATTACH_CHANNEL_ID": channels.split(",")[0] if channels else "",
            }
            new_profile = _profile_from_env(env)
            if editing:
                _replace_profile_registration(profile, new_profile)
                log(f"Updated profile `{profile['name']}`.")
            else:
                _register_profile(new_profile)
                log(f"Added profile `{new_profile['name']}`.")
            dialog.destroy()
            refresh_all()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=_save).pack(side="right", padx=(0, 8))

    def save_project_from_selection() -> None:
        selected = selected_profiles()
        name = _normalize_project_name(project_name_var.get())
        if not selected:
            messagebox.showerror("Relay Manager", "Select one or more profiles first.")
            return
        if not name:
            messagebox.showerror("Relay Manager", "Enter a project name first.")
            return
        registry = _load_registry()
        _upsert_project(registry, {"name": name, "profiles": [profile["name"] for profile in selected]})
        _save_registry(registry)
        log(f"Saved project `{name}` with {len(selected)} profile(s).")
        refresh_projects()

    def edit_project_dialog(project: dict | None = None, *, suggested_profiles: list[str] | None = None) -> None:
        editing = project is not None
        registry = _load_registry()
        all_profiles = [item["name"] for item in registry.get("profiles", [])]
        selected_names = list(project.get("profiles", [])) if project is not None else list(suggested_profiles or [])

        dialog = tk.Toplevel(root)
        dialog.title("Edit Project" if editing else "Create Project")
        dialog.transient(root)
        dialog.grab_set()
        dialog.geometry("540x420")
        dialog.minsize(500, 360)

        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        project_name = tk.StringVar(value=project["name"] if editing else project_name_var.get().strip())
        ttk.Label(frame, text="Project Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=project_name).grid(row=1, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(frame, text="Project Members", style="Section.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(
            frame,
            text="Select the bots you want included in this project.",
            style="Detail.TLabel",
        ).grid(row=3, column=0, sticky="w", pady=(2, 8))

        picker = ttk.Frame(frame, style="Panel.TFrame")
        picker.grid(row=4, column=0, sticky="nsew")
        picker.columnconfigure(0, weight=1)
        picker.rowconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        member_list = tk.Listbox(picker, selectmode="multiple", exportselection=False, height=12)
        member_list.grid(row=0, column=0, sticky="nsew")
        member_scroll = ttk.Scrollbar(picker, orient="vertical", command=member_list.yview)
        member_scroll.grid(row=0, column=1, sticky="ns")
        member_list.configure(yscrollcommand=member_scroll.set)

        for name in all_profiles:
            member_list.insert("end", name)
        for index, name in enumerate(all_profiles):
            if name in selected_names:
                member_list.selection_set(index)

        picker_actions = ttk.Frame(frame, style="Panel.TFrame")
        picker_actions.grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Button(picker_actions, text="Select All", style="Secondary.TButton", command=lambda: member_list.selection_set(0, "end")).pack(side="left", padx=(0, 8))
        ttk.Button(picker_actions, text="Clear", style="Secondary.TButton", command=lambda: member_list.selection_clear(0, "end")).pack(side="left")

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.grid(row=6, column=0, sticky="e", pady=(16, 0))

        def _save_project_dialog() -> None:
            name = _normalize_project_name(project_name.get())
            if not name:
                messagebox.showerror("Relay Manager", "Project name is required.")
                return
            members = [all_profiles[index] for index in member_list.curselection()]
            if not members:
                messagebox.showerror("Relay Manager", "Select at least one profile.")
                return
            updated_registry = _load_registry()
            _upsert_project(updated_registry, {"name": name, "profiles": members})
            _save_registry(updated_registry)
            project_name_var.set(name)
            dialog.destroy()
            log(f"{'Updated' if editing else 'Saved'} project `{name}` with {len(members)} profile(s).")
            refresh_projects()

        ttk.Button(buttons, text="Cancel", style="Secondary.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", style="Primary.TButton", command=_save_project_dialog).pack(side="right", padx=(0, 8))

    def remove_selected_profile() -> None:
        selected = selected_profiles()
        if len(selected) != 1:
            messagebox.showerror("Relay Manager", "Select exactly one profile to remove.")
            return
        profile = selected[0]
        if not messagebox.askyesno("Relay Manager", f"Remove profile `{profile['name']}`?"):
            return

        def _remove() -> list[str]:
            _stop_profile(profile)
            _remove_profile_registration(profile)
            return [f"Removed profile `{profile['name']}`."]

        run_action("Removing profile", _remove)

    def remove_selected_project() -> None:
        project = selected_project()
        if project is None:
            messagebox.showerror("Relay Manager", "Select a project first.")
            return
        if not messagebox.askyesno("Relay Manager", f"Remove project `{project['name']}`?"):
            return
        registry = _load_registry()
        registry["projects"] = [item for item in registry.get("projects", []) if item.get("name") != project["name"]]
        _save_registry(registry)
        log(f"Removed project `{project['name']}`.")
        refresh_projects()

    def edit_selected_project() -> None:
        project = selected_project()
        if project is None:
            messagebox.showerror("Relay Manager", "Select a project first.")
            return
        edit_project_dialog(project)

    def _toolbar_start_selected() -> None:
        run_action("Starting profiles", lambda: [_run_profile(profile) or f"Started `{profile['name']}`." for profile in selected_profiles()])

    def _toolbar_stop_selected() -> None:
        run_action("Stopping profiles", lambda: [_stop_profile(profile) or f"Stopped `{profile['name']}`." for profile in selected_profiles()])

    def _toolbar_restart_selected() -> None:
        run_action("Restarting profiles", lambda: [_restart_profile(profile) or f"Restarted `{profile['name']}`." for profile in selected_profiles()])

    def refresh_live_view() -> None:
        live_refresh_job["id"] = None
        selected_tab = notebook.select()
        lines: list[str]
        if selected_tab == str(projects_tab):
            project = selected_project()
            if project is not None:
                lines = [f"Project {project['name']}", ""]
                try:
                    project_profiles = _profiles_for_project(project)
                except SystemExit as exc:
                    project_profiles = []
                    lines.append(str(exc))
                for index, profile in enumerate(project_profiles):
                    if index:
                        lines.extend(["", "-" * 52, ""])
                    lines.extend(_profile_raw_terminal_lines(profile))
            else:
                lines = _default_live_view_lines()
        else:
            profiles = selected_profiles()
            if profiles:
                lines = []
                for index, profile in enumerate(profiles):
                    if index:
                        lines.extend(["", "-" * 52, ""])
                    lines.extend(_profile_raw_terminal_lines(profile))
            else:
                lines = _default_live_view_lines()
        rendered = "\n".join(lines)
        if rendered != live_render_cache["value"]:
            live_render_cache["value"] = rendered
            _set_console_text(live_text, rendered)
        _schedule_live_view_refresh(1500)

    ttk.Button(toolbar, text="Refresh", style="Secondary.TButton", command=refresh_all).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Stop All", style="Secondary.TButton", command=lambda: run_action("Stopping all relays", lambda: [cmd_stop_all(argparse.Namespace()) or "Stopped all relay instances."])).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Self Update", style="Secondary.TButton", command=lambda: run_action("Updating relay", _run_gui_self_update)).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Start Selected", style="Primary.TButton", command=_toolbar_start_selected).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Stop Selected", style="Secondary.TButton", command=_toolbar_stop_selected).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Restart Selected", style="Secondary.TButton", command=_toolbar_restart_selected).pack(side="left", padx=(0, 8))
    ttk.Button(toolbar, text="Add Profile", style="Secondary.TButton", command=lambda: edit_profile_dialog(None)).pack(side="left")

    ttk.Label(profile_button_body, text="Selection", style="Section.TLabel").pack(anchor="w")
    ttk.Label(profile_button_body, textvariable=profile_detail_var, style="Detail.TLabel", justify="left", wraplength=290).pack(fill="x", pady=(4, 6))
    ttk.Separator(profile_button_body, orient="horizontal").pack(fill="x", pady=(0, 6))
    ttk.Label(profile_button_body, text="Quick Actions", style="Section.TLabel").pack(anchor="w")

    profile_action_grid = ttk.Frame(profile_button_body, style="Panel.TFrame")
    profile_action_grid.pack(fill="x", pady=(4, 6))
    for column in range(2):
        profile_action_grid.columnconfigure(column, weight=1)

    controls = [
        ("Start", _toolbar_start_selected, "SidebarPrimary.TButton"),
        ("Stop", _toolbar_stop_selected, "SidebarSecondary.TButton"),
        ("Restart", _toolbar_restart_selected, "SidebarSecondary.TButton"),
        ("Status", lambda: [log(line) for profile in selected_profiles() for line in profile_status_lines(profile)], "SidebarSecondary.TButton"),
        ("Edit", lambda: edit_profile_dialog(selected_profiles()[0]) if len(selected_profiles()) == 1 else messagebox.showerror("Relay Manager", "Select exactly one profile to edit."), "SidebarSecondary.TButton"),
        ("Remove", remove_selected_profile, "SidebarSecondary.TButton"),
        ("Add", lambda: edit_profile_dialog(None), "SidebarSecondary.TButton"),
        ("Refresh", refresh_all, "SidebarSecondary.TButton"),
    ]
    for index, (label, command, button_style) in enumerate(controls):
        ttk.Button(profile_action_grid, text=label, style=button_style, command=command).grid(
            row=index // 2,
            column=index % 2,
            sticky="ew",
            padx=(0, 8) if index % 2 == 0 else (0, 0),
            pady=2,
        )

    ttk.Separator(profile_button_body, orient="horizontal").pack(fill="x", pady=(2, 6))
    ttk.Label(profile_button_body, text="Projects", style="Section.TLabel").pack(anchor="w")
    project_inline = ttk.Frame(profile_button_body, style="Panel.TFrame")
    project_inline.pack(fill="x", pady=(4, 0))
    project_inline.columnconfigure(0, weight=1)
    ttk.Entry(project_inline, textvariable=project_name_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(project_inline, text="Save", style="SidebarSecondary.TButton", command=save_project_from_selection).grid(row=0, column=1, sticky="ew")
    ttk.Button(profile_button_body, text="Open Project Editor", style="SidebarSecondary.TButton", command=lambda: edit_project_dialog(None, suggested_profiles=[profile["name"] for profile in selected_profiles()])).pack(fill="x", pady=(6, 0))

    ttk.Label(project_button_body, text="Selection", style="Section.TLabel").pack(anchor="w")
    ttk.Label(project_button_body, textvariable=project_detail_var, style="Detail.TLabel", justify="left", wraplength=290).pack(fill="x", pady=(4, 6))
    ttk.Separator(project_button_body, orient="horizontal").pack(fill="x", pady=(0, 6))
    ttk.Label(project_button_body, text="Project Actions", style="Section.TLabel").pack(anchor="w")

    project_action_grid = ttk.Frame(project_button_body, style="Panel.TFrame")
    project_action_grid.pack(fill="x", pady=(4, 0))
    for column in range(2):
        project_action_grid.columnconfigure(column, weight=1)

    project_controls = [
        ("Start", lambda: run_action("Starting project", lambda: [(_run_profile(profile), f"Started `{profile['name']}`.")[-1] for profile in _profiles_for_project(selected_project())] if selected_project() else []), "SidebarPrimary.TButton"),
        ("Stop", lambda: run_action("Stopping project", lambda: [(_stop_profile(profile), f"Stopped `{profile['name']}`.")[-1] for profile in _profiles_for_project(selected_project())] if selected_project() else []), "SidebarSecondary.TButton"),
        ("Edit", edit_selected_project, "SidebarSecondary.TButton"),
        ("Status", lambda: [log(line) for project in [selected_project()] if project for profile in _profiles_for_project(project) for line in profile_status_lines(profile)], "SidebarSecondary.TButton"),
        ("Remove", remove_selected_project, "SidebarSecondary.TButton"),
        ("Refresh", refresh_projects, "SidebarSecondary.TButton"),
    ]
    for index, (label, command, button_style) in enumerate(project_controls):
        ttk.Button(project_action_grid, text=label, style=button_style, command=command).grid(
            row=index // 2,
            column=index % 2,
            sticky="ew",
            padx=(0, 8) if index % 2 == 0 else (0, 0),
            pady=2,
        )

    profile_tree.bind("<<TreeviewSelect>>", update_profile_detail)
    project_tree.bind("<<TreeviewSelect>>", update_project_detail)
    profile_tree.bind("<<TreeviewSelect>>", lambda _event: _schedule_live_view_refresh(), add="+")
    project_tree.bind("<<TreeviewSelect>>", lambda _event: _schedule_live_view_refresh(), add="+")
    notebook.bind("<<NotebookTabChanged>>", lambda _event: _schedule_live_view_refresh(), add="+")

    refresh_all()
    _schedule_live_view_refresh()
    root.mainloop()
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace or Path.cwd()).resolve()
    if not args.allow_dms and not args.allowed_channel_ids:
        raise SystemExit("At least one --allowed-channel-id is required unless --allow-dms is enabled.")
    inferred_trigger_mode = args.trigger_mode or "mention_or_dm"
    env = {
        "DISCORD_BOT_TOKEN": args.discord_bot_token,
        "RELAY_BOT_NAME": args.bot_name or "",
        "RELAY_MODEL": args.model or args.codex_model or "",
        "CODEX_WORKDIR": str(workspace),
        "CODEX_MODEL": args.model or args.codex_model or "",
        "CODEX_FULL_ACCESS": "true",
        "CODEX_READ_ONLY": "false",
        "CODEX_APP_SERVER_TRANSPORT": args.app_server_transport or "stdio",
        "CODEX_APP_SERVER_PORT": str(args.app_server_port or default_port_for_workspace(workspace, token=args.discord_bot_token)),
        "STATE_NAMESPACE": args.state_namespace or default_namespace_for_workspace(workspace, token=args.discord_bot_token),
        "ALLOW_DMS": "true" if args.allow_dms else "false",
        "BOT_TRIGGER_MODE": inferred_trigger_mode,
        "ALLOWED_USER_IDS": ",".join(args.allowed_user_ids),
        "ALLOWED_BOT_IDS": _parse_csv_ids(getattr(args, "allowed_bot_ids", "") or ""),
        "ALLOWED_CHANNEL_AUTHOR_IDS": ",".join(args.allowed_channel_author_ids),
        "CHANNEL_NO_MENTION_AUTHOR_IDS": ",".join(args.channel_no_mention_author_ids),
        "STARTUP_DM_USER_IDS": ",".join(args.allowed_user_ids),
        "STARTUP_DM_TEXT": args.startup_dm_text,
        "ALLOWED_CHANNEL_IDS": ",".join(args.allowed_channel_ids),
        "CHANNEL_HISTORY_LIMIT": str(args.channel_history_limit),
        "OPEN_VISIBLE_TERMINAL": "true" if args.open_visible_terminal else "false",
        "RELAY_ATTACH_CHANNEL_ID": args.attach_channel_id or (args.allowed_channel_ids[0] if args.allowed_channel_ids else ""),
    }
    profile = _profile_from_env(env)
    _register_profile(profile)
    print(f"Registered {profile['name']} for {profile['workspace']}")
    print("Run `codex-discord` from this workspace to launch it.")
    return 0


def cmd_show(_args: argparse.Namespace) -> int:
    workspace = Path.cwd().resolve()
    matches = _matching_profiles_for_workspace(workspace)
    if not matches:
        print("No profile registered for this workspace.", file=sys.stderr)
        return 1
    for index, profile in enumerate(matches, start=1):
        print(f"[{index}] {profile['name']}")
        print(f"  bot: {_bot_label(profile)}")
        print(f"  workspace: {profile['workspace']}")
        print(f"  env file: {profile.get('env_file', '-')}")
        print(f"  main channel: {profile.get('attach_channel_id', '') or '-'}")
        print(f"  state namespace: {profile.get('state_namespace', '-')}")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    profile = _select_profile_for_workspace(Path.cwd().resolve())
    return _stop_profile(profile)


def cmd_stop_all(_args: argparse.Namespace) -> int:
    found = False
    for profile in _all_registered_profiles():
        found = True
        _stop_profile(profile)

    supervisor_pids, relay_pids = _all_relay_process_pids()
    orphan_pids = sorted(set(supervisor_pids + relay_pids))
    for pid in orphan_pids:
        terminate_process_tree(pid)
        _wait_for_process_exit(pid, timeout_seconds=5.0)
        found = True
    for pid in _discovered_codex_app_server_pids():
        terminate_process_tree(pid)
        _wait_for_process_exit(pid, timeout_seconds=5.0)
        found = True

    state_root = DATA_ROOT / "state"
    if state_root.exists():
        for state_dir in state_root.iterdir():
            if state_dir.is_dir():
                for pid in _state_dir_candidate_pids(state_dir):
                    terminate_process_tree(pid)
                    _wait_for_process_exit(pid, timeout_seconds=5.0)
                    found = True
                _cleanup_state_dir_artifacts(state_dir)

    if found:
        print("Stopped all relay instances.")
    else:
        print("No relay instances found.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    profile = _select_profile_for_workspace(Path.cwd().resolve())
    _print_profile_status(profile)
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    profile = _select_profile_for_workspace(Path.cwd().resolve())
    log_path = _profile_runtime_state(profile)["log_path"]
    if not log_path.exists():
        print(f"No relay log found for `{profile['name']}` at {log_path}.")
        return 1
    if args.follow:
        return follow_file(log_path)
    print(tail_lines(log_path, args.lines), end="")
    return 0


def cmd_reset(_args: argparse.Namespace) -> int:
    profile = _select_profile_for_workspace(Path.cwd().resolve())
    _stop_profile(profile)
    session_dir = _profile_runtime_state(profile)["session_dir"]
    removed = 0
    if session_dir.exists():
        for session_file in session_dir.glob("*.json"):
            session_file.unlink(missing_ok=True)
            removed += 1
    print(f"Cleared {removed} saved relay session file(s) for `{profile['name']}`.")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    workspace = Path.cwd().resolve()
    try:
        profile = _select_profile_for_workspace(workspace)
    except SystemExit as exc:
        print(str(exc))
        return 1

    state = _profile_runtime_state(profile)
    env = state["env"]
    relay_env = relay_codex_env(Path(profile["workspace"]), os.environ.copy())
    relay_config_path = Path(relay_env["CODEX_HOME"]) / "config.toml"
    runtime_bin = resolve_codex_bin()
    print(f"workspace: {workspace}")
    print(f"profile: {profile['name']}")
    print(f"bot: {_bot_label(profile)}")
    print("provider: codex")
    print(f"config dir: {CONFIG_ROOT}")
    print(f"data dir: {state['state_dir'].parent}")
    print(f"relay runtime home: {relay_env.get('CODEX_HOME') or '-'}")
    print(f"codex binary: {runtime_bin or 'missing'}")
    if runtime_bin:
        version = subprocess.run(
            [runtime_bin, "--version"],
            capture_output=True,
            text=True,
            check=False,
            env=relay_env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        version_text = (version.stdout or version.stderr).strip()
        print(f"codex version: {version_text or 'unknown'}")
        logged_in, login_text = _codex_login_status(Path(profile["workspace"]))
        print(f"codex login: {'ok' if logged_in else 'missing'}")
        if login_text:
            print(_sanitize_auth_status_text(login_text))
    print(f"workspace trusted: {'yes' if _workspace_is_trusted(Path(profile['workspace']), config_path=relay_config_path) else 'no'}")
    print(f"relay running: {'yes' if state['running'] else 'no'}")
    print(f"app-server transport: {env.get('CODEX_APP_SERVER_TRANSPORT', 'stdio')}")
    print(f"app-server port: {env['CODEX_APP_SERVER_PORT']}")
    print(f"trigger mode: {env.get('BOT_TRIGGER_MODE', 'mention_or_dm')}")
    print(f"session count: {len(state['session_threads'])}")
    print(f"log path: {state['log_path']}")
    plugin_root = install_plugin.plugin_install_root()
    plugin_manifest = plugin_root / ".codex-plugin" / "plugin.json"
    marketplace_file = install_plugin.marketplace_path()
    print(f"plugin installed: {'yes' if plugin_manifest.exists() else 'no'}")
    print(f"plugin complete: {'yes' if install_plugin.installed_plugin_is_complete(plugin_root) else 'no'}")
    print(f"plugin root: {plugin_root}")
    print(f"marketplace entry: {'yes' if install_plugin.marketplace_has_plugin() else 'no'}")
    print(f"marketplace path: {marketplace_file}")
    if env.get("BOT_TRIGGER_MODE") == "all" and env.get("ALLOWED_CHANNEL_IDS"):
        print("warning: trigger mode `all` requires Discord message-content access for reliable channel operation.")
    return 0


def cmd_setup(_args: argparse.Namespace) -> int:
    running_profiles = _shared_runtime_running_profiles()
    if running_profiles:
        for profile in running_profiles:
            _stop_profile(profile)
    try:
        result = install_plugin.main()
    except Exception:
        if running_profiles:
            _restart_profiles(running_profiles)
        raise
    if running_profiles:
        _restart_profiles(running_profiles)
    return result


def cmd_privacy_audit(args: argparse.Namespace) -> int:
    root = Path(args.path or Path.cwd()).resolve()
    findings = _privacy_audit(root)
    if not findings:
        print(f"No privacy findings under {root}")
        return 0
    print(f"Privacy findings under {root}:")
    for finding in findings:
        print(f"- {finding}")
    return 1


def cmd_version(_args: argparse.Namespace) -> int:
    print(_package_version())
    return 0


def cmd_restart(_args: argparse.Namespace) -> int:
    profile = _profile_for_current_workspace()
    return _restart_profile(profile)


def cmd_self_update(args: argparse.Namespace) -> int:
    update_target = _resolved_self_update_target(args.source)
    try:
        profile = _profile_for_current_workspace()
    except SystemExit:
        profile = None

    running_profiles = _shared_runtime_running_profiles()
    restarted_profiles = running_profiles if not args.no_restart else []
    if running_profiles:
        for running_profile in running_profiles:
            _stop_profile(running_profile)

    update_env = os.environ.copy()
    update_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    if _can_use_external_windows_update(update_target):
        print("Starting Windows self-update in the background.")
        _launch_external_windows_update_background(update_target, restarted_profiles=restarted_profiles)
        print("The relay runtime will refresh after this process exits.")
        return 0
    else:
        command = [sys.executable, "-m", "pip", "install", "--upgrade"]
        if args.force_reinstall:
            command.append("--force-reinstall")
        command.append(update_target)

        print("Running update command:")
        print(" ".join(command))
        result = subprocess.run(command, check=False, env=update_env)
        if result.returncode != 0:
            if restarted_profiles:
                _restart_profiles(restarted_profiles)
            raise SystemExit(result.returncode)

        try:
            install_plugin.main(source=(update_target if update_target != PACKAGE_NAME else None))
        except Exception:
            if restarted_profiles:
                _restart_profiles(restarted_profiles)
            raise

    if restarted_profiles:
        _restart_profiles(restarted_profiles)

    print(f"discord-codex-relay version: {_package_version()}")
    return 0


def cmd_discord_smoke(args: argparse.Namespace) -> int:
    try:
        import discord
    except Exception as exc:
        raise SystemExit(f"discord.py is unavailable in this runtime: {exc}")

    profile = _profile_for_current_workspace()
    env = _normalized_profile_env(_load_env_file(Path(profile["env_file"])))
    token = (os.environ.get("DISCORD_BOT_TOKEN", "").strip() or env.get("DISCORD_BOT_TOKEN", "").strip())
    if not token:
        raise SystemExit("The current relay profile does not have a Discord bot token configured.")

    channel_id_text = str(args.channel_id or env.get("RELAY_ATTACH_CHANNEL_ID") or "").strip()
    if not channel_id_text.isdigit():
        raise SystemExit("A Discord channel id is required. Configure a main channel or pass --channel-id.")
    channel_id = int(channel_id_text)
    post_message = not args.no_post

    class _DiscordSmokeClient(discord.Client):
        def __init__(self) -> None:
            intents = discord.Intents.default()
            intents.guilds = True
            intents.messages = True
            intents.message_content = True
            super().__init__(intents=intents)
            self.result: dict[str, object] = {
                "ready": False,
                "channel_lookup": False,
                "history_fetch": False,
                "send": False,
                "edit": False,
                "delete": False,
                "gateway_latency_seconds": None,
                "channel_name": None,
                "guild_name": None,
                "bot_user_id": None,
            }
            self.done = asyncio.Event()
            self.error: Exception | None = None

        async def on_ready(self) -> None:
            try:
                self.result["ready"] = True
                self.result["gateway_latency_seconds"] = float(self.latency)
                self.result["bot_user_id"] = getattr(self.user, "id", None)
                channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
                self.result["channel_lookup"] = True
                self.result["channel_name"] = getattr(channel, "name", None)
                guild = getattr(channel, "guild", None)
                if guild is not None:
                    self.result["guild_name"] = getattr(guild, "name", None)

                async for _item in channel.history(limit=1):
                    self.result["history_fetch"] = True
                    break
                if self.result["history_fetch"] is False:
                    self.result["history_fetch"] = True

                if post_message:
                    sent = await channel.send(
                        f"discord-codex-relay smoke test {int(time.time())} (this message will self-delete)"
                    )
                    self.result["send"] = True
                    await sent.edit(content=sent.content + " [edited]")
                    self.result["edit"] = True
                    await sent.delete()
                    self.result["delete"] = True
            except Exception as exc:
                self.error = exc
            finally:
                self.done.set()

    async def _run_smoke() -> dict[str, object]:
        client = _DiscordSmokeClient()
        start_task = asyncio.create_task(client.start(token))
        try:
            await client.done.wait()
            await client.close()
            await start_task
        finally:
            if not start_task.done():
                start_task.cancel()
                try:
                    await start_task
                except asyncio.CancelledError:
                    pass
        if client.error is not None:
            raise client.error
        return client.result

    result = asyncio.run(_run_smoke())
    print("discord smoke OK")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    env_file = Path(args.env_file).resolve()
    env = _normalized_profile_env(_load_env_file(env_file))
    workspace = Path(env["CODEX_WORKDIR"]).resolve()
    state_dir = state_dir_for_namespace(env["STATE_NAMESPACE"])
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_path = _supervisor_pid_path(state_dir)
    supervisor_lock_path = _supervisor_lock_path(state_dir)
    auth_failure_marker_path = _auth_failure_marker_path(state_dir)
    supervisor_lock = None
    try:
        try:
            supervisor_lock = _acquire_pid_lock(supervisor_lock_path)
        except OSError:
            existing_pid = _read_pid_file(pid_path)
            if pid_exists(existing_pid):
                print(f"Relay supervisor is already running for {workspace} (pid {existing_pid}).")
                return 0
            raise SystemExit("Another relay supervisor is starting for this workspace.")
        atomic_write_text(pid_path, f"{os.getpid()}\n")

        stop_requested = [False]
        child_process: subprocess.Popen | None = None

        def _request_stop(_signum: int, _frame: object) -> None:
            stop_requested[0] = True
            if child_process is not None and child_process.poll() is None:
                terminate_process_tree(child_process.pid)

        for signame in ("SIGINT", "SIGTERM", "SIGBREAK"):
            signal_value = getattr(signal, signame, None)
            if signal_value is not None:
                signal.signal(signal_value, _request_stop)

        failure_timestamps: list[float] = []
        backoff_seconds = SUPERVISOR_BACKOFF_INITIAL_SECONDS
        exit_code = 0

        try:
            while not stop_requested[0]:
                truncate_file_tail(state_dir / "logs" / "relay.log", max_bytes=5 * 1024 * 1024, keep_bytes=1024 * 1024)
                truncate_file_tail(state_dir / "logs" / "app-server.log", max_bytes=5 * 1024 * 1024, keep_bytes=1024 * 1024)
                child_started_at = time.time()
                print(f"Supervisor starting relay worker for {workspace}")
                child_process = _launch_bot_worker(env_file, workspace, log_path=state_dir / "logs" / "relay.log")

                while not stop_requested[0]:
                    try:
                        exit_code = child_process.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        continue

                if stop_requested[0]:
                    if child_process.poll() is None:
                        terminate_process_tree(child_process.pid)
                        _wait_for_process_exit(child_process.pid)
                    break

                runtime_seconds = time.time() - child_started_at
                now = time.time()
                failure_timestamps = [stamp for stamp in failure_timestamps if now - stamp <= SUPERVISOR_FAILURE_WINDOW_SECONDS]

                if runtime_seconds >= SUPERVISOR_STABLE_RUN_SECONDS:
                    backoff_seconds = SUPERVISOR_BACKOFF_INITIAL_SECONDS
                    failure_timestamps.clear()
                else:
                    failure_timestamps.append(now)
                    if len(failure_timestamps) > 1:
                        backoff_seconds = min(backoff_seconds * 2, SUPERVISOR_BACKOFF_MAX_SECONDS)

                if auth_failure_marker_path.exists():
                    print("Relay worker exited after a native Codex authentication failure. Manual re-login is required before restart.")
                    exit_code = exit_code or 1
                    break

                print(
                    f"Relay worker exited with code {exit_code}; "
                    f"uptime={runtime_seconds:.1f}s; restart in {backoff_seconds:.1f}s"
                )
                if not _sleep_until(backoff_seconds, stop_requested):
                    break
        finally:
            if child_process is not None and child_process.poll() is None:
                terminate_process_tree(child_process.pid)
                _wait_for_process_exit(child_process.pid)
            current_pid = _read_pid_file(pid_path)
            if current_pid == os.getpid():
                pid_path.unlink(missing_ok=True)
        return 0
    finally:
        _release_pid_lock(supervisor_lock)


def cmd_skill_list(args: argparse.Namespace) -> int:
    script = _require_skill_installer_script("list-skills.py")
    command = [sys.executable, str(script)]
    if args.experimental:
        command.extend(["--path", "skills/.experimental"])
    if args.json:
        command.extend(["--format", "json"])
    return subprocess.run(command, check=False).returncode


def cmd_skill_install(args: argparse.Namespace) -> int:
    script = _require_skill_installer_script("install-skill-from-github.py")
    command = [sys.executable, str(script)]
    if args.url:
        command.extend(["--url", args.url])
    elif args.names:
        repo = args.repo or "openai/skills"
        base_path = "skills/.experimental" if args.experimental else "skills/.curated"
        command.extend(["--repo", repo])
        for name in args.names:
            command.extend(["--path", f"{base_path}/{name}"])
    else:
        if not args.repo or not args.paths:
            raise SystemExit("Provide either --url, one or more --name values, or --repo with one or more --path values.")
        command.extend(["--repo", args.repo])
        for path_value in args.paths:
            command.extend(["--path", path_value])

    if args.ref:
        command.extend(["--ref", args.ref])
    if args.dest:
        command.extend(["--dest", args.dest])
    if args.method:
        command.extend(["--method", args.method])

    result = subprocess.run(command, check=False)
    if result.returncode == 0:
        print("Restart Codex to pick up new skills.")
    return result.returncode


def _skill_listing(*, experimental: bool = False) -> dict[str, bool]:
    script = _require_skill_installer_script("list-skills.py")
    command = [sys.executable, str(script), "--format", "json"]
    if experimental:
        command.extend(["--path", "skills/.experimental"])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "Failed to query Codex skill listings.")
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit("Codex skill listing returned invalid JSON.") from exc
    return {
        str(item.get("name", "")).strip(): bool(item.get("installed"))
        for item in payload
        if str(item.get("name", "")).strip()
    }


def cmd_extras(args: argparse.Namespace) -> int:
    for name in args.disable_names:
        install_plugin.set_auto_skill_disabled(name, True)
    for name in args.enable_names:
        install_plugin.set_auto_skill_disabled(name, False)
    if args.install_auto:
        installed_now, failed_now = install_plugin.auto_install_enabled_skills()
        if installed_now:
            print("Installed: " + ", ".join(installed_now))
        if failed_now:
            print("Failed: " + ", ".join(failed_now))

    installed = _skill_listing(experimental=False)
    disabled_names = set(install_plugin.load_extras_preferences().get("disabled", []))
    auto_names = set(install_plugin.AUTO_INSTALL_SKILLS)

    rows = []
    for item in EXTRAS_CATALOG:
        rows.append(
            {
                "name": item["name"],
                "category": item["category"],
                "installed": installed.get(item["name"], False),
                "availability": item["free"],
                "auto": item["name"] in auto_names and item["name"] not in disabled_names,
                "summary": item["summary"],
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["category"]), []).append(row)

    print("Recommended relay extras")
    for category in sorted(grouped):
        print(f"\n[{category}]")
        for row in sorted(grouped[category], key=lambda item: str(item["name"])):
            status = "installed" if row["installed"] else "not installed"
            auto = "auto-on" if row["auto"] else "auto-off"
            print(f"- {row['name']}: {status} | {auto} | {row['availability']} | {row['summary']}")
    print("\nInstall with:")
    print("  codex-discord skill install --name <skill_name>")
    print("Auto-install controls:")
    print("  codex-discord extras --disable <skill_name>")
    print("  codex-discord extras --enable <skill_name>")
    print("  codex-discord extras --install-auto")
    print("Notes:")
    print("  - local/free = runs locally after the skill is installed")
    print("  - provider-backed = skill is installable, but actual generation/inference may require model/provider access")
    print("  - service account = skill is free to install, but deployment usually needs the target platform account/API access")
    return 0


def _rewrite_project_shortcut_args(argv: list[str]) -> list[str]:
    if argv and argv[0].startswith("pj-") and len(argv[0]) > 3:
        return ["project", "start", argv[0][3:], *argv[1:]]
    if argv[:1] == ["--gui"]:
        return ["gui", *argv[1:]]
    return argv


def _gui_supported() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def _should_offer_gui_prompt() -> bool:
    disabled = os.environ.get("CODEX_DISCORD_NO_GUI_PROMPT", "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    if not _gui_supported():
        return False
    stdin = getattr(sys, "stdin", None)
    stdout = getattr(sys, "stdout", None)
    if stdin is None or stdout is None:
        return False
    try:
        return bool(stdin.isatty() and stdout.isatty())
    except Exception:
        return False


def _prompt_for_optional_gui() -> bool:
    return _prompt_bool("Open relay manager GUI", default=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-discord", description="Workspace-local launcher for discord-codex-relay")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the relay for the current workspace")
    run_parser.add_argument("--background", action="store_true", default=False, help="Deprecated no-op; background launch is already the default")
    run_parser.add_argument("--foreground", action="store_true", default=False, help="Keep the relay in the current terminal instead of using the background supervisor")
    run_parser.set_defaults(func=cmd_run)

    list_parser = subparsers.add_parser("list", help="List registered workspace relay profiles")
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show the profile for the current workspace")
    show_parser.set_defaults(func=cmd_show)

    stop_parser = subparsers.add_parser("stop", help="Stop the relay for the current workspace")
    stop_parser.set_defaults(func=cmd_stop)

    stop_all_parser = subparsers.add_parser("stop-all", help="Stop every relay instance running on this machine")
    stop_all_parser.set_defaults(func=cmd_stop_all)

    status_parser = subparsers.add_parser("status", help="Show runtime status for the current workspace relay")
    status_parser.set_defaults(func=cmd_status)

    logs_parser = subparsers.add_parser("logs", help="Show relay logs for the current workspace")
    logs_parser.add_argument("-n", "--lines", type=int, default=80)
    logs_parser.add_argument("-f", "--follow", action="store_true", default=False)
    logs_parser.set_defaults(func=cmd_logs)

    reset_parser = subparsers.add_parser("reset", help="Stop the relay and clear saved session bindings for the current workspace")
    reset_parser.set_defaults(func=cmd_reset)

    doctor_parser = subparsers.add_parser("doctor", help="Check relay runtime prerequisites for the current workspace")
    doctor_parser.set_defaults(func=cmd_doctor)

    setup_parser = subparsers.add_parser("setup", help="Install the Codex plugin bundle for discord-codex-relay")
    setup_parser.set_defaults(func=cmd_setup)

    privacy_parser = subparsers.add_parser("privacy-audit", help="Scan a tree for repo-local secrets and personal path markers")
    privacy_parser.add_argument("path", nargs="?", default=str(Path.cwd()), help="Directory to scan; defaults to the current workspace")
    privacy_parser.set_defaults(func=cmd_privacy_audit)

    version_parser = subparsers.add_parser("version", help="Show the installed discord-codex-relay package version")
    version_parser.set_defaults(func=cmd_version)

    extras_parser = subparsers.add_parser("extras", help="List recommended optional relay skills and companion tools")
    extras_parser.add_argument("--json", action="store_true", default=False, help="Emit the extras catalog as JSON")
    extras_parser.add_argument("--disable", dest="disable_names", action="append", default=[], help="Disable auto-install for a recommended skill")
    extras_parser.add_argument("--enable", dest="enable_names", action="append", default=[], help="Re-enable auto-install for a recommended skill")
    extras_parser.add_argument("--install-auto", action="store_true", default=False, help="Install the currently enabled recommended skill pack now")
    extras_parser.set_defaults(func=cmd_extras)

    gui_parser = subparsers.add_parser("gui", help="Open the native relay manager GUI")
    gui_parser.set_defaults(func=cmd_gui)

    restart_parser = subparsers.add_parser("restart", help="Restart the relay for the current workspace")
    restart_parser.set_defaults(func=cmd_restart)

    update_parser = subparsers.add_parser("self-update", help="Upgrade discord-codex-relay and reinstall its plugin bundle")
    update_parser.add_argument("--source", help="Optional pip requirement, path, wheel, or URL to upgrade from")
    update_parser.add_argument("--force-reinstall", action="store_true", default=False, help="Force reinstall even if the target version is already installed")
    update_parser.add_argument("--no-restart", action="store_true", default=False, help="Do not restart the current workspace relay after updating")
    update_parser.set_defaults(func=cmd_self_update)

    update_alias_parser = subparsers.add_parser("update", help="Alias for `self-update`")
    update_alias_parser.add_argument("--source", help="Optional pip requirement, path, wheel, or URL to upgrade from")
    update_alias_parser.add_argument("--force-reinstall", action="store_true", default=False, help="Force reinstall even if the target version is already installed")
    update_alias_parser.add_argument("--no-restart", action="store_true", default=False, help="Do not restart the current workspace relay after updating")
    update_alias_parser.set_defaults(func=cmd_self_update)

    discord_smoke_parser = subparsers.add_parser("discord-smoke", help="Verify Discord login, channel access, and optional send/edit/delete for the current workspace profile")
    discord_smoke_parser.add_argument("--channel-id", type=int, help="Override the channel id to test; defaults to the profile's main channel")
    discord_smoke_parser.add_argument("--no-post", action="store_true", default=False, help="Skip send/edit/delete and only validate login, fetch, and history access")
    discord_smoke_parser.set_defaults(func=cmd_discord_smoke)

    register_parser = subparsers.add_parser("register", help="Register the current workspace as a relay profile")
    register_parser.add_argument("--workspace", default=str(Path.cwd()), help="Workspace root to bind this relay profile to")
    register_parser.add_argument("--discord-bot-token", required=True, help="Discord bot token for this relay profile")
    register_parser.add_argument("--bot-name", help="Optional bot identity name used for relay targeting context")
    register_parser.add_argument("--allowed-channel-id", dest="allowed_channel_ids", action="append", default=[], metavar="CHANNEL_ID", help="Main or additional channel IDs this relay may respond in")
    register_parser.add_argument("--allowed-user-id", dest="allowed_user_ids", action="append", default=[], metavar="USER_ID", help="DM user IDs allowed to talk to this relay")
    register_parser.add_argument("--allowed-bot-ids", default="", help="Comma-separated Discord bot IDs allowed for bot-to-bot chat")
    register_parser.add_argument("--allowed-channel-author-id", dest="allowed_channel_author_ids", action="append", default=[], metavar="ID", help="Optional allowlist for who may trigger the relay in allowed channels")
    register_parser.add_argument("--channel-no-mention-author-id", dest="channel_no_mention_author_ids", action="append", default=[], metavar="ID", help="Allowed channel author IDs that may trigger without mentioning the relay")
    register_parser.add_argument("--extra-trigger-id", dest="allowed_channel_author_ids", action="append", metavar="ID", help="Alias for --allowed-channel-author-id")
    register_parser.add_argument("--attach-channel-id", help=argparse.SUPPRESS)
    register_parser.add_argument("--state-namespace", help=argparse.SUPPRESS)
    register_parser.add_argument("--app-server-port", type=int, help=argparse.SUPPRESS)
    register_parser.add_argument("--app-server-transport", choices=["stdio", "websocket"], default=None, help="Codex app-server transport; defaults to `stdio` for production use")
    register_parser.add_argument("--model", default="", help="Optional model override for this relay profile")
    register_parser.add_argument("--codex-model", default="", help="Optional model override for this relay profile")
    register_parser.add_argument("--trigger-mode", choices=["all", "mention_or_dm", "dm_only"], default=None, help="How channel messages trigger the relay; defaults to `mention_or_dm` when a channel is configured")
    register_parser.add_argument("--channel-history-limit", type=int, default=20, help="Relevant messages included in a fresh channel bootstrap digest; use 0 for an unlimited backfill scan")
    register_parser.add_argument("--startup-dm-text", default="Discord relay online. DM me here to chat with Codex.", help="Optional startup DM text")
    register_parser.add_argument("--allow-dms", action="store_true", default=False, help="Allow approved users to talk to this relay in DMs")
    register_parser.add_argument("--open-visible-terminal", action="store_true", default=False, help="Best-effort visible `codex resume` terminal after bootstrap")
    register_parser.set_defaults(func=cmd_register)

    project_parser = subparsers.add_parser("project", help="Save and control named groups of relay profiles")
    project_subparsers = project_parser.add_subparsers(dest="project_command")

    project_list_parser = project_subparsers.add_parser("list", help="List saved relay projects")
    project_list_parser.set_defaults(func=cmd_project_list)

    project_save_parser = project_subparsers.add_parser("save", help="Save a named relay project from explicit profiles or a workspace root")
    project_save_parser.add_argument("name", help="Saved project name")
    project_save_parser.add_argument("--profile", dest="profile_names", action="append", default=[], help="Registered profile name to include; repeat as needed")
    project_save_parser.add_argument("--workspace-root", help="Include every registered profile rooted under this workspace")
    project_save_parser.set_defaults(func=cmd_project_save)

    project_show_parser = project_subparsers.add_parser("show", help="Show the profiles inside a saved relay project")
    project_show_parser.add_argument("name", help="Saved project name")
    project_show_parser.set_defaults(func=cmd_project_show)

    project_start_parser = project_subparsers.add_parser("start", help="Start every profile in a saved relay project")
    project_start_parser.add_argument("name", help="Saved project name")
    project_start_parser.set_defaults(func=cmd_project_start)

    project_stop_parser = project_subparsers.add_parser("stop", help="Stop every profile in a saved relay project")
    project_stop_parser.add_argument("name", help="Saved project name")
    project_stop_parser.set_defaults(func=cmd_project_stop)

    project_status_parser = project_subparsers.add_parser("status", help="Show runtime status for a saved relay project")
    project_status_parser.add_argument("name", help="Saved project name")
    project_status_parser.set_defaults(func=cmd_project_status)

    project_remove_parser = project_subparsers.add_parser("remove", help="Remove a saved relay project")
    project_remove_parser.add_argument("name", help="Saved project name")
    project_remove_parser.set_defaults(func=cmd_project_remove)

    skill_parser = subparsers.add_parser("skill", help="List or install Codex skills from the local Codex skill installer")
    skill_subparsers = skill_parser.add_subparsers(dest="skill_command")

    skill_list_parser = skill_subparsers.add_parser("list", help="List curated installable Codex skills")
    skill_list_parser.add_argument("--experimental", action="store_true", default=False, help="List experimental skills instead of curated skills")
    skill_list_parser.add_argument("--json", action="store_true", default=False, help="Emit the upstream JSON listing")
    skill_list_parser.set_defaults(func=cmd_skill_list)

    skill_install_parser = skill_subparsers.add_parser("install", help="Install one or more Codex skills")
    skill_install_parser.add_argument("--name", dest="names", action="append", default=[], help="Curated or experimental OpenAI skill name to install")
    skill_install_parser.add_argument("--experimental", action="store_true", default=False, help="Install named skills from the experimental OpenAI set")
    skill_install_parser.add_argument("--url", help="Full GitHub tree URL for a skill directory to install")
    skill_install_parser.add_argument("--repo", help="GitHub repo in owner/name form")
    skill_install_parser.add_argument("--path", dest="paths", action="append", default=[], help="Skill path inside --repo; repeat for multiple skills")
    skill_install_parser.add_argument("--ref", help="Git ref to install from")
    skill_install_parser.add_argument("--dest", help="Override destination directory for installed skills")
    skill_install_parser.add_argument("--method", choices=["auto", "download", "git"], help="Skill installer transport method")
    skill_install_parser.set_defaults(func=cmd_skill_install)

    return parser


def main() -> int:
    argv = _rewrite_project_shortcut_args(sys.argv[1:])
    if argv[:1] == ["serve"]:
        serve_parser = argparse.ArgumentParser(add_help=False)
        serve_parser.add_argument("command")
        serve_parser.add_argument("--env-file", required=True)
        return cmd_serve(serve_parser.parse_args(argv))

    if not argv and _should_offer_gui_prompt() and _prompt_for_optional_gui():
        return cmd_gui(argparse.Namespace(command="gui"))

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return cmd_run(args)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
