"""Stdlib-only bootstrap entry point for the CLADEX backend runtime.

`server.cjs` invokes this on a clean packaged-user machine before the
managed venv exists. Other backend modules pull in third-party packages
(psutil, platformdirs) at import time, which crashes on a fresh machine
before pip can install them. Even after creating a venv, the same
problem repeats inside the venv until pip has actually run.

This script does the entire first-stage install with the standard
library only: create the venv, then call pip install via subprocess.
Subsequent runs are idempotent: the venv and package are already in
place, so pip becomes a no-op.

Operators can override the runtime root with CLADEX_RUNTIME_DATA_ROOT
and the install source with CLADEX_INSTALL_SOURCE.

# Stub-orphaned venv detection

A Python venv is a tiny stub `python.exe` / `pythonw.exe` plus a
`pyvenv.cfg` file that points at the base interpreter via `home = ...`.
When a user uninstalls the base interpreter (e.g. removes Python 3.10
because they installed 3.12), the venv stub still exists and passes a
`Path.exists()` check, but invoking it triggers the Windows Python
Launcher modal "No Python at <home>\\pythonw.exe". The dialog is
unblockable and stacks per call.

`_venv_is_healthy()` reads `pyvenv.cfg` and confirms the base
interpreter still exists on disk. If not, the bootstrap deletes the
broken venv (`shutil.rmtree`) and recreates it from the currently
running Python — which by definition is healthy because it is
executing this code.
"""

from __future__ import annotations

import os
import secrets as _stdlib_secrets
import shutil
import stat
import subprocess
import sys
import time
import venv
from pathlib import Path

PACKAGE_NAME = "discord-codex-relay"
DEFAULT_TIMEOUT_SECONDS = 900


def _stdlib_user_data_dir(app: str) -> Path:
    """Match `platformdirs.user_data_dir(app, appauthor=False)` with stdlib only."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / app
        return Path.home() / "AppData" / "Local" / app
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / app


def _runtime_root() -> Path:
    override = os.environ.get("CLADEX_RUNTIME_DATA_ROOT")
    base = Path(override).expanduser() if override else _stdlib_user_data_dir(PACKAGE_NAME)
    return base / "runtime"


def _runtime_python(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _venv_base_python(root: Path) -> Path | None:
    """Return the venv's base-interpreter path, or None if root isn't a venv.

    A non-venv Python install has no pyvenv.cfg; in that case the file
    existing IS the interpreter and there is nothing further to validate.
    """
    cfg = root / "pyvenv.cfg"
    if not cfg.exists():
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() != "home":
            continue
        home = Path(value.strip()).expanduser()
        if os.name == "nt":
            return home / "python.exe"
        return home / "python"
    return None


def _venv_is_healthy(python: Path) -> bool:
    """Return True if `python` is a usable interpreter, not a stub orphan.

    The stub-orphan check is: if `python` lives inside a venv, its
    pyvenv.cfg `home = ...` must point at a base interpreter that still
    exists. If not, invoking this `python` would re-exec the missing
    base — which on Windows triggers the unblockable Python Launcher
    modal.
    """
    if not python.exists():
        return False
    # Walk up from Scripts/python.exe -> Scripts -> root, or bin/python -> bin -> root.
    venv_root = python.parent.parent
    base = _venv_base_python(venv_root)
    if base is None:
        return True
    return base.exists()


def _rmtree_force_handler(func, path, exc_info):
    """shutil.rmtree onerror handler: clear read-only bit then retry once."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    try:
        func(path)
    except OSError:
        pass


def _purge_pending(root: Path) -> None:
    """Remove any leftover `<root>.purging-*` siblings from prior crashes."""
    parent = root.parent
    if not parent.exists():
        return
    prefix = f"{root.name}.purging-"
    try:
        for item in parent.iterdir():
            if item.name.startswith(prefix):
                shutil.rmtree(item, onerror=_rmtree_force_handler)
    except OSError:
        pass


def _ensure_venv(root: Path) -> Path:
    """Create the venv if missing OR rebuild it if the existing venv is orphaned.

    Crash-safe rebuild:
      1. Write `.rebuilding` sentinel under root.parent.
      2. `os.rename` root -> `<root>.purging-<ts>` (atomic on Win+POSIX).
      3. `shutil.rmtree` purge target with onerror chmod handler.
      4. `venv.create` root.
      5. Remove sentinel.
    On entry the sentinel forces purge regardless of detection so a half-deleted
    state from a prior crash cannot be mistaken for a healthy interpreter.
    """
    root.parent.mkdir(parents=True, exist_ok=True)
    sentinel = root.parent / f".{root.name}.rebuilding"
    _purge_pending(root)
    python = _runtime_python(root)
    needs_rebuild = sentinel.exists() or (python.exists() and not _venv_is_healthy(python))
    if needs_rebuild:
        try:
            sentinel.write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass
        if root.exists():
            # R0008: include pid + token_hex(4) + nanosecond timestamp so two
            # bootstrap invocations in the same second cannot pick the same
            # purge target (one would clobber the other's pending purge).
            purge_tag = f"{os.getpid()}-{_stdlib_secrets.token_hex(4)}-{time.time_ns()}"
            purge_target = root.with_name(f"{root.name}.purging-{purge_tag}")
            try:
                os.rename(root, purge_target)
            except OSError as exc:
                raise RuntimeError(
                    f"Could not move orphaned managed runtime at {root} aside: {exc}. "
                    "Delete it manually and retry."
                ) from exc
            try:
                shutil.rmtree(purge_target, onerror=_rmtree_force_handler)
            except OSError as exc:
                raise RuntimeError(
                    f"Could not remove orphaned managed runtime copy at {purge_target}: {exc}. "
                    "Delete it manually and retry."
                ) from exc
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(root)
    if not python.exists():
        raise RuntimeError(f"venv created without Python interpreter at {python}")
    if sentinel.exists():
        try:
            sentinel.unlink()
        except OSError:
            pass
    return python


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_install_source() -> str:
    """Resolve the pip install target without importing install_plugin.

    Mirrors install_plugin._install_source but stdlib-only:
      - If CLADEX_INSTALL_SOURCE is set, use it as-is.
      - If pyproject.toml lives next to this script (packaged bundle or
        dev tree), install from that local path.
      - Otherwise fall back to the published package on PyPI by name.
    """
    override = os.environ.get("CLADEX_INSTALL_SOURCE")
    if override:
        return override
    backend = _backend_dir()
    if (backend / "pyproject.toml").exists():
        return str(backend)
    return PACKAGE_NAME


def _resolve_constraints_path(install_target: str) -> Path | None:
    """Find a constraints.txt next to the install target or the backend dir."""
    candidates: list[Path] = []
    target = Path(install_target)
    if target.exists() and target.is_dir():
        candidates.append(target / "constraints.txt")
    candidates.append(_backend_dir() / "constraints.txt")
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.exists():
            return resolved
    return None


def _pip_install(python: Path) -> int:
    install_target = _resolve_install_source()
    constraints = _resolve_constraints_path(install_target)
    cmd = [
        str(python),
        "-m",
        "pip",
        "install",
        "--upgrade",
    ]
    target_path = Path(install_target)
    if (
        target_path.exists()
        or install_target.startswith((".", "/", "\\"))
        or install_target.endswith((".whl", ".zip", ".tar.gz"))
    ):
        cmd.append("--force-reinstall")
    if constraints is not None:
        cmd.extend(["-c", str(constraints)])
    cmd.append(install_target)
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    timeout_raw = os.environ.get("CLADEX_INSTALL_SUBPROCESS_TIMEOUT") or os.environ.get(
        "CLADEX_BOOTSTRAP_TIMEOUT_SECONDS"
    )
    try:
        timeout = max(int(str(timeout_raw or DEFAULT_TIMEOUT_SECONDS).strip()), 1)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    # R0006/F0002: capture pip output so we can surface the LAST 40 lines on
    # failure. Without this, a packaged-user machine sees an opaque non-zero
    # exit from cladex bootstrap with no hint of which install_target failed
    # or why pip refused (network, hash mismatch, missing wheel, etc.).
    proc = subprocess.run(cmd, env=env, timeout=timeout, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        try:
            tail_lines: list[str] = []
            for stream_name, stream_value in (("stderr", proc.stderr), ("stdout", proc.stdout)):
                stream_text = stream_value or ""
                if stream_text.strip():
                    tail_lines.append(f"--- pip {stream_name} (last 40 lines) ---")
                    tail_lines.extend(stream_text.splitlines()[-40:])
            tail = "\n".join(tail_lines)
            print(
                f"cladex bootstrap: pip install for {install_target} exited with {proc.returncode}.\n{tail}",
                file=sys.stderr,
            )
        except Exception:
            print(
                f"cladex bootstrap: pip install for {install_target} exited with {proc.returncode}.",
                file=sys.stderr,
            )
    return proc.returncode


def main() -> int:
    runtime_root = _runtime_root()
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        python = _ensure_venv(runtime_root)
    except RuntimeError as exc:
        print(f"cladex bootstrap: venv setup failed at {runtime_root}: {exc}", file=sys.stderr)
        return 2
    try:
        return _pip_install(python)
    except subprocess.TimeoutExpired as exc:
        print(
            f"cladex bootstrap: pip install timed out after {exc.timeout}s installing {_resolve_install_source()}",
            file=sys.stderr,
        )
        return 3
    except OSError as exc:
        print(
            f"cladex bootstrap: pip install OS error installing {_resolve_install_source()}: {exc}",
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    sys.exit(main())
