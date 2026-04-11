#!/usr/bin/env python3
"""
Common utilities for the Claude relay inside the unified CLADEX package.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import psutil
from platformdirs import user_config_dir, user_data_dir

if os.name == "nt":
    import msvcrt
else:
    import fcntl


APP_NAME = "discord-claude-relay"
APP_AUTHOR = False
PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = Path(user_config_dir(APP_NAME, APP_AUTHOR))
DATA_ROOT = Path(user_data_dir(APP_NAME, APP_AUTHOR))
PROFILES_DIR = CONFIG_ROOT / "profiles"
REGISTRY_PATH = CONFIG_ROOT / "workspaces.json"


def slugify(value: str) -> str:
    """Convert string to URL-safe slug."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text[:64] or "workspace"


def workspace_root(path: Path) -> Path:
    """Find git root or return path as-is."""
    path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return path
    root = result.stdout.strip()
    return Path(root).resolve() if root else path


def token_fingerprint(token: str) -> str:
    """Generate short hash of bot token for identification."""
    return hashlib.sha1(token.encode("utf-8")).hexdigest()[:8]


def default_port_for_workspace(workspace: Path, *, token: str | None = None) -> int:
    """Generate deterministic port number for workspace."""
    seed = str(workspace) if token is None else f"{workspace}|{token_fingerprint(token)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return 9700 + (int(digest[:6], 16) % 1000)  # Different range from Codex


def default_namespace_for_workspace(workspace: Path, *, token: str | None = None) -> str:
    """Generate namespace identifier for workspace."""
    slug = slugify(workspace.name)
    seed = str(workspace) if token is None else f"{workspace}|{token_fingerprint(token)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def state_dir_for_namespace(namespace: str) -> Path:
    """Get state directory for a namespace."""
    return DATA_ROOT / "state" / namespace


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically write text to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding=encoding, dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    try:
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: object, *, indent: int = 2) -> None:
    """Atomically write JSON to file."""
    atomic_write_text(path, json.dumps(payload, indent=indent) + "\n")


def claude_code_bin() -> str:
    """Find Claude Code CLI executable."""
    # Try common names
    for name in ("claude", "claude.cmd", "claude.exe"):
        path = shutil.which(name)
        if path:
            return path
    return "claude"


def claude_code_version() -> str:
    """Get Claude Code CLI version."""
    try:
        result = subprocess.run(
            [claude_code_bin(), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return (result.stdout or result.stderr or "").strip() or "unknown"
    except Exception:
        return "unknown"


def listening_pids(port: int) -> list[int]:
    """Find PIDs listening on a port."""
    pids: set[int] = set()
    for conn in psutil.net_connections(kind="inet"):
        if conn.pid is None or not conn.laddr:
            continue
        if conn.status != psutil.CONN_LISTEN or conn.laddr.port != port:
            continue
        pids.add(conn.pid)
    return sorted(pids)


def pid_exists(pid: int | None) -> bool:
    """Check if PID exists."""
    return pid is not None and psutil.pid_exists(pid)


def terminate_process_tree(pid: int) -> bool:
    """Terminate process and all children."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return False

    try:
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        processes = [root]

    seen: set[int] = set()
    unique: list[psutil.Process] = []
    for proc in processes:
        if proc.pid in seen:
            continue
        seen.add(proc.pid)
        unique.append(proc)

    stopped = False
    for proc in reversed(unique):
        try:
            proc.terminate()
            stopped = True
        except psutil.Error:
            continue

    _, alive = psutil.wait_procs(unique, timeout=3)
    for proc in alive:
        try:
            proc.kill()
            stopped = True
        except psutil.Error:
            continue
    psutil.wait_procs(alive, timeout=2)
    return stopped


def tail_lines(path: Path, count: int) -> str:
    """Read last N lines of file."""
    if count <= 0 or not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return "".join(lines[-count:])


def follow_file(path: Path) -> int:
    """Follow file like tail -f."""
    if not path.exists():
        return 1
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        try:
            while True:
                line = handle.readline()
                if line:
                    print(line, end="")
                    continue
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0


def best_windows_shell() -> str | None:
    """Find best shell on Windows."""
    for name in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    return None


# File locking for config
def acquire_file_lock(path: Path) -> object:
    """Acquire exclusive file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle
    except Exception:
        handle.close()
        raise


def release_file_lock(handle: object | None) -> None:
    """Release file lock."""
    if handle is None:
        return
    try:
        if os.name == "nt":
            try:
                handle.seek(0)
            except OSError:
                pass
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
