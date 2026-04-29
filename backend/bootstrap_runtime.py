"""Stdlib-only bootstrap entry point for the CLADEX backend runtime.

`server.cjs` invokes this on a clean packaged-user machine before the
managed venv exists. Importing `install_plugin._ensure_runtime` directly
also pulls in `relay_common`, which requires `psutil` / `platformdirs` —
neither is on a fresh machine yet, so the import fails before pip can run.

This script does the minimum needed to land the runtime venv:
  1. Resolve the same RUNTIME_ROOT / CONFIG_ROOT layout `relay_common`
     would compute (platformdirs.user_data_dir / user_config_dir for
     "discord-codex-relay"), using only the standard library.
  2. Create the venv if missing.
  3. Hand off to the venv's Python: `python -m install_plugin --bootstrap`.
     Once we are inside the venv, importing `relay_common` is safe because
     the package will be installed in the next step.

Operators can override the runtime root with CLADEX_RUNTIME_DATA_ROOT and
the install source with CLADEX_INSTALL_SOURCE.
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

PACKAGE_NAME = "discord-codex-relay"


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


def _ensure_venv(root: Path) -> Path:
    python = _runtime_python(root)
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(root)
    if not python.exists():
        raise RuntimeError(f"venv created without Python interpreter at {python}")
    return python


def _delegate_to_install_plugin(python: Path) -> int:
    """Re-enter `install_plugin._ensure_runtime` from inside the runtime venv,
    where `relay_common` and its native deps are about to be available.
    The first run installs the package; later runs are idempotent."""
    backend_dir = Path(__file__).resolve().parent
    cmd = [
        str(python),
        "-c",
        "import sys; sys.path.insert(0, '.'); "
        "from install_plugin import _ensure_runtime; _ensure_runtime()",
    ]
    proc = subprocess.run(cmd, cwd=str(backend_dir), check=False)
    return proc.returncode


def main() -> int:
    runtime_root = _runtime_root()
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    python = _ensure_venv(runtime_root)
    rc = _delegate_to_install_plugin(python)
    return rc


if __name__ == "__main__":
    sys.exit(main())
