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
import shutil
import subprocess
import sys
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


def _ensure_venv(root: Path) -> Path:
    """Create the venv if missing OR rebuild it if the existing venv is orphaned."""
    python = _runtime_python(root)
    if python.exists() and not _venv_is_healthy(python):
        # Stub-orphan: base interpreter was uninstalled. The stub will fire
        # the Python Launcher modal on every invocation. Wipe and rebuild.
        try:
            shutil.rmtree(root)
        except OSError as exc:
            raise RuntimeError(
                f"Could not remove orphaned managed runtime at {root}: {exc}. "
                "Delete it manually and retry."
            ) from exc
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(root)
    if not python.exists():
        raise RuntimeError(f"venv created without Python interpreter at {python}")
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
    proc = subprocess.run(cmd, env=env, timeout=timeout, check=False)
    return proc.returncode


def main() -> int:
    runtime_root = _runtime_root()
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    python = _ensure_venv(runtime_root)
    return _pip_install(python)


if __name__ == "__main__":
    sys.exit(main())
