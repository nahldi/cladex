from __future__ import annotations

import json
import hashlib
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


APP_NAME = "discord-codex-relay"
APP_AUTHOR = False
PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = Path(user_config_dir(APP_NAME, APP_AUTHOR))
DATA_ROOT = Path(user_data_dir(APP_NAME, APP_AUTHOR))
RELAY_CODEX_HOME_ROOT = DATA_ROOT / "codex-home"
PROFILES_DIR = CONFIG_ROOT / "profiles"
REGISTRY_PATH = CONFIG_ROOT / "workspaces.json"
RELAY_CODEX_CONFIG_HEADER = '# Managed by discord-codex-relay.\n[windows]\nsandbox = "elevated"\n'
DEFAULT_APP_SERVER_PORT_START = 18000
DEFAULT_APP_SERVER_PORT_RANGE = 40000
DEFAULT_LOG_TAIL_MAX_READ_BYTES = 1024 * 1024


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text[:64] or "workspace"


def workspace_root(path: Path) -> Path:
    path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        return path
    root = result.stdout.strip()
    return Path(root).resolve() if root else path


def token_fingerprint(token: str) -> str:
    return hashlib.sha1(token.encode("utf-8")).hexdigest()[:8]


def default_port_for_workspace(workspace: Path, *, token: str | None = None) -> int:
    seed = str(workspace) if token is None else f"{workspace}|{token_fingerprint(token)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return DEFAULT_APP_SERVER_PORT_START + (int(digest[:8], 16) % DEFAULT_APP_SERVER_PORT_RANGE)


def default_namespace_for_workspace(workspace: Path, *, token: str | None = None) -> str:
    slug = slugify(workspace.name)
    seed = str(workspace) if token is None else f"{workspace}|{token_fingerprint(token)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def state_dir_for_namespace(namespace: str) -> Path:
    text = str(namespace or "").strip()
    if not text or slugify(text) != text or "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError(f"Invalid state namespace: {namespace!r}")
    state_root = (DATA_ROOT / "state").resolve()
    target = (state_root / text).resolve()
    try:
        target.relative_to(state_root)
    except ValueError as exc:
        raise ValueError(f"Invalid state namespace: {namespace!r}") from exc
    return target


def relay_codex_home() -> Path:
    return RELAY_CODEX_HOME_ROOT


def _relay_codex_home_lock_path(relay_home: Path) -> Path:
    return relay_home / ".config.lock"


def _acquire_file_lock(path: Path) -> object:
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


def _release_file_lock(handle: object | None) -> None:
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


def _copy_file_if_changed(source: Path, destination: Path) -> None:
    if not source.exists() or not source.is_file():
        return
    source_bytes = source.read_bytes()
    if destination.exists():
        try:
            if destination.read_bytes() == source_bytes:
                return
        except OSError:
            pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=destination.parent, delete=False) as handle:
        handle.write(source_bytes)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    try:
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
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
    atomic_write_text(path, json.dumps(payload, indent=indent) + "\n")


def replace_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.parent / f".{destination.name}.tmp-{os.getpid()}"
    backup_destination = destination.parent / f".{destination.name}.bak-{os.getpid()}"
    if temp_destination.exists():
        shutil.rmtree(temp_destination)
    if backup_destination.exists():
        shutil.rmtree(backup_destination)
    shutil.copytree(
        source,
        temp_destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    moved_to_backup = False
    if destination.exists():
        os.replace(destination, backup_destination)
        moved_to_backup = True
    try:
        os.replace(temp_destination, destination)
    except OSError:
        # Restore the backup if the swap failed so we never leave the
        # destination missing.
        if moved_to_backup and backup_destination.exists() and not destination.exists():
            try:
                os.replace(backup_destination, destination)
            except OSError:
                pass
        if temp_destination.exists():
            try:
                shutil.rmtree(temp_destination)
            except OSError:
                pass
        raise
    if backup_destination.exists():
        shutil.rmtree(backup_destination)


def prune_directory_files(
    path: Path,
    *,
    older_than_seconds: float | None = None,
    max_files: int | None = None,
) -> int:
    if not path.exists():
        return 0
    now = time.time()
    files = [item for item in path.rglob("*") if item.is_file()]
    removed = 0
    if older_than_seconds is not None:
        for file_path in files:
            try:
                if now - file_path.stat().st_mtime > older_than_seconds:
                    file_path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        files = [item for item in path.rglob("*") if item.is_file()]
    if max_files is not None and len(files) > max_files:
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for file_path in files[max_files:]:
            try:
                file_path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
    return removed


def truncate_file_tail(path: Path, *, max_bytes: int, keep_bytes: int | None = None) -> None:
    if not path.exists():
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    retained = keep_bytes if keep_bytes is not None else max_bytes // 2
    retained = max(0, min(retained, size))
    try:
        with path.open("rb") as handle:
            if retained:
                handle.seek(-retained, os.SEEK_END)
                data = handle.read()
            else:
                data = b""
        atomic_write_text(
            path,
            "[log truncated]\n" + data.decode("utf-8", errors="replace"),
        )
    except OSError:
        return


def codex_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


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


def _upsert_trusted_project_block(config_text: str, workspace: Path) -> str:
    existing = _normalize_codex_config_project_headers(config_text)
    project_path = str(workspace.resolve())
    header_pattern = "|".join(re.escape(header) for header in _project_header_variants(project_path))
    block_pattern = re.compile(rf"(?ms)^(?:{header_pattern})\n(?:.*\n)*?(?=^\[|$)")
    block = f"[projects.{_toml_project_key(project_path)}]\ntrust_level = \"trusted\"\n"
    if block_pattern.search(existing):
        return block_pattern.sub(lambda _match: block + "\n", existing).rstrip() + "\n"
    updated = existing.rstrip()
    if updated:
        updated += "\n\n"
    updated += block
    return updated


def prepare_relay_codex_home(
    workspace: Path,
    *,
    source_home: Path | None = None,
    target_home: Path | None = None,
) -> Path:
    source_root = (source_home or (Path.home() / ".codex")).resolve()
    relay_home = (target_home or relay_codex_home()).resolve()
    relay_home.mkdir(parents=True, exist_ok=True)
    lock_handle = _acquire_file_lock(_relay_codex_home_lock_path(relay_home))
    try:
        for name in ("auth.json", "cap_sid"):
            _copy_file_if_changed(source_root / name, relay_home / name)

        config_path = relay_home / "config.toml"
        existing_raw = config_path.read_text(encoding="utf-8") if config_path.exists() else RELAY_CODEX_CONFIG_HEADER
        if not existing_raw.strip():
            existing_raw = RELAY_CODEX_CONFIG_HEADER
        if "Managed by discord-codex-relay" not in existing_raw:
            existing_raw = RELAY_CODEX_CONFIG_HEADER.rstrip() + "\n\n" + existing_raw.lstrip()
        updated = _upsert_trusted_project_block(existing_raw, workspace)
        if updated != (config_path.read_text(encoding="utf-8") if config_path.exists() else ""):
            atomic_write_text(config_path, updated)
    finally:
        _release_file_lock(lock_handle)
    return relay_home


_RELAY_CHILD_SECRET_KEYS = (
    "DISCORD_BOT_TOKEN",
    "CLADEX_REGISTER_DISCORD_BOT_TOKEN",
    "CLADEX_REMOTE_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_AUTH_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "BITBUCKET_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID",
    "DOCKER_AUTH_CONFIG",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "HUGGINGFACE_TOKEN",
    "HF_TOKEN",
    "SLACK_TOKEN",
    "SLACK_BOT_TOKEN",
    "DATABASE_URL",
    "DB_PASSWORD",
)


def _strip_relay_secrets(env: dict[str, str]) -> dict[str, str]:
    """Drop credential-like env vars from a child subprocess environment.

    The Codex CLI inherits the relay's process environment by default, which
    includes the Discord bot token and any cloud credentials the user has on
    PATH. The CLI-driven model can leak those into command output or shell
    commands it spawns. Strip the well-known credential names plus any key
    that looks like a secret based on its suffix.
    """
    sanitized: dict[str, str] = {}
    suffix_blocked = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD", "_PRIVATE_KEY", "_CREDENTIALS")
    for key, value in env.items():
        upper = key.upper()
        if upper in _RELAY_CHILD_SECRET_KEYS:
            continue
        if any(upper.endswith(suffix) for suffix in suffix_blocked):
            continue
        sanitized[key] = value
    return sanitized


# Positive allowlist for the Codex relay child env. The 2.5.6 Claude env
# fix flipped Claude from prefix-allowlist to explicit-allowlist + secret-
# suffix-deny; the Codex side kept a pure denylist, which only catches
# *_TOKEN/_KEY/_SECRET/_PASSWORD/_PRIVATE_KEY/_CREDENTIALS-shaped names
# and lets credential carriers like authenticated package index URLs,
# kubeconfig pointers, and SSH agent sockets through. This list mirrors
# the host-machine entries the Codex CLI legitimately needs to start.
_CODEX_RELAY_ALLOWED_NAMES = {
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "HOME",
    "LOCALAPPDATA",
    "APPDATA",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramData",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "WINDIR",
    "COMSPEC",
    "COMPUTERNAME",
    "USERNAME",
    "USERDOMAIN",
    "OS",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
    "SHELL",
    "USER",
    "LOGNAME",
    "DISPLAY",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "CODEX_HOME",
    "CODEX_DEBUG",
    "CODEX_LOG_LEVEL",
    "CODEX_DISABLE_TELEMETRY",
}
_CODEX_RELAY_ALLOWED_NAMES_UPPER = {name.upper() for name in _CODEX_RELAY_ALLOWED_NAMES}


def _codex_relay_env_allowlist(env: dict[str, str]) -> dict[str, str]:
    """Positive-allowlist the Codex child env the same way Claude does.

    The denylist version (`_strip_relay_secrets`) misses non-suffix
    credential carriers (authenticated package index URLs, agent sockets,
    kubeconfig-style pointers, future provider-specific env names). A
    positive allowlist restricts the child env to known-needed runtime
    keys so a future-named credential cannot leak silently."""
    allowed: dict[str, str] = {}
    suffix_blocked = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD", "_PRIVATE_KEY", "_CREDENTIALS")
    for key, value in env.items():
        upper = key.upper()
        if upper not in _CODEX_RELAY_ALLOWED_NAMES_UPPER:
            continue
        # Defense-in-depth: even if a future allowlist entry is added that
        # matches a secret-shaped key, drop it.
        if any(upper.endswith(suffix) for suffix in suffix_blocked):
            continue
        allowed[key] = value
    return allowed


def relay_codex_env(workspace: Path, base_env: dict[str, str] | None = None) -> dict[str, str]:
    source_env = dict((base_env or os.environ).items())
    env = _codex_relay_env_allowlist(source_env)
    configured_home = str(source_env.get("CODEX_HOME", "")).strip()
    if configured_home:
        relay_home = Path(configured_home).expanduser().resolve()
        env["CODEX_HOME"] = str(
            prepare_relay_codex_home(workspace, source_home=relay_home, target_home=relay_home)
        )
    else:
        env["CODEX_HOME"] = str(prepare_relay_codex_home(workspace))
    return env


def resolve_codex_bin() -> str:
    if os.name == "nt":
        codex_cmd = shutil.which("codex.cmd")
        if codex_cmd:
            shim_dir = Path(codex_cmd).resolve().parent
            candidates = sorted(
                (shim_dir / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai").glob(
                    "codex-win32-*/vendor/*/codex/codex.exe"
                )
            )
            if candidates:
                return str(candidates[0].resolve())
        codex_exe = shutil.which("codex.exe")
        if codex_exe:
            return codex_exe
    return shutil.which("codex") or "codex"


def codex_cli_version() -> str:
    codex_bin = resolve_codex_bin()
    if os.name == "nt" and not codex_bin.lower().endswith(".exe"):
        command = ["cmd", "/c", "codex.CMD", "--version"]
    else:
        command = [codex_bin, "--version"]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    text = (result.stdout or result.stderr or "").strip()
    return text or "unknown"


def listening_pids(port: int) -> list[int]:
    pids: set[int] = set()
    for conn in psutil.net_connections(kind="inet"):
        if conn.pid is None or not conn.laddr:
            continue
        if conn.status != psutil.CONN_LISTEN or conn.laddr.port != port:
            continue
        pids.add(conn.pid)
    return sorted(pids)


def pid_exists(pid: int | None) -> bool:
    return pid is not None and psutil.pid_exists(pid)


def terminate_process_tree(pid: int) -> bool:
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


def _log_tail_max_read_bytes() -> int:
    try:
        value = int(os.environ.get("CLADEX_LOG_TAIL_MAX_BYTES") or DEFAULT_LOG_TAIL_MAX_READ_BYTES)
    except (TypeError, ValueError):
        value = DEFAULT_LOG_TAIL_MAX_READ_BYTES
    return max(value, 1)


def tail_lines(path: Path, count: int) -> str:
    if count <= 0 or not path.exists():
        return ""
    max_read = _log_tail_max_read_bytes()
    chunk_size = min(64 * 1024, max_read)
    chunks: list[bytes] = []
    bytes_read = 0
    newline_count = 0
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            while position > 0 and bytes_read < max_read:
                to_read = min(chunk_size, position, max_read - bytes_read)
                position -= to_read
                handle.seek(position)
                chunk = handle.read(to_read)
                if not chunk:
                    break
                chunks.append(chunk)
                bytes_read += len(chunk)
                newline_count += chunk.count(b"\n")
                if newline_count > count:
                    break
    except OSError:
        return ""
    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines = text.splitlines(keepends=True)
    return "".join(lines[-count:]) if len(lines) > count else text


def follow_file(path: Path) -> int:
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
    for name in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    return None
