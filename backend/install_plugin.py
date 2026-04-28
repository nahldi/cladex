from __future__ import annotations

import importlib.metadata
from importlib.resources import files
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from urllib.parse import urlparse, unquote

from relay_common import CONFIG_ROOT, DATA_ROOT, atomic_write_json, replace_directory


PLUGIN_NAME = "discord-codex-relay"
PACKAGE_NAME = "discord-codex-relay"
REPO_ROOT = Path(__file__).resolve().parent
HOME_PLUGIN_ROOT = Path.home() / "plugins" / PLUGIN_NAME
MARKETPLACE_PATH = Path.home() / ".agents" / "plugins" / "marketplace.json"
RUNTIME_ROOT = DATA_ROOT / "runtime"
EXTRAS_PREFS_PATH = CONFIG_ROOT / "extras.json"
AUTO_INSTALL_SKILLS = [
    "playwright",
    "playwright-interactive",
    "screenshot",
    "frontend-skill",
    "doc",
    "pdf",
    "openai-docs",
    "gh-address-comments",
    "gh-fix-ci",
    "security-best-practices",
    "security-threat-model",
    "security-ownership-map",
    "jupyter-notebook",
    "spreadsheet",
    "cloudflare-deploy",
    "vercel-deploy",
    "netlify-deploy",
    "render-deploy",
    "chatgpt-apps",
    "imagegen",
    "speech",
    "transcribe",
    "sora",
]
REQUIRED_PLUGIN_FILES = [
    Path(".codex-plugin/plugin.json"),
    Path("SOUL.md"),
    Path("assets/icon.svg"),
    Path("assets/logo.svg"),
    Path("skills/workspace-discord-relay/SKILL.md"),
    Path("skills/workspace-discord-relay/agents/openai.yaml"),
    Path("skills/workspace-discord-relay/scripts/bootstrap.py"),
]


def _windows_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _bundle_root() -> Path:
    repo_bundle = REPO_ROOT
    if (repo_bundle / ".codex-plugin" / "plugin.json").exists() and (repo_bundle / "skills").exists():
        return repo_bundle
    return Path(str(files("discord_codex_relay_plugin").joinpath("bundle")))


def _load_marketplace() -> dict:
    if not MARKETPLACE_PATH.exists():
        return {
            "name": "Local Plugins",
            "interface": {"displayName": "Local Plugins"},
            "plugins": [],
        }
    try:
        data = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("name", "Local Plugins")
    interface = data.setdefault("interface", {})
    interface.setdefault("displayName", "Local Plugins")
    data.setdefault("plugins", [])
    return data


def plugin_install_root() -> Path:
    return HOME_PLUGIN_ROOT


def marketplace_path() -> Path:
    return MARKETPLACE_PATH


def runtime_root() -> Path:
    return RUNTIME_ROOT


def runtime_python_path(root: Path | None = None) -> Path:
    base = root or RUNTIME_ROOT
    if os.name == "nt":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def runtime_site_packages_path(root: Path | None = None) -> Path:
    base = root or RUNTIME_ROOT
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if os.name == "nt":
        return base / "Lib" / "site-packages"
    return base / "lib" / version / "site-packages"


def marketplace_has_plugin() -> bool:
    marketplace = _load_marketplace()
    for plugin in marketplace.get("plugins", []):
        if plugin.get("name") == PLUGIN_NAME:
            return True
    return False


def load_extras_preferences() -> dict:
    if not EXTRAS_PREFS_PATH.exists():
        return {"disabled": []}
    try:
        payload = json.loads(EXTRAS_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"disabled": []}
    disabled = [str(name).strip() for name in payload.get("disabled", []) if str(name).strip()]
    return {"disabled": sorted(set(disabled))}


def save_extras_preferences(payload: dict) -> None:
    normalized = {"disabled": sorted(set(str(name).strip() for name in payload.get("disabled", []) if str(name).strip()))}
    atomic_write_json(EXTRAS_PREFS_PATH, normalized)


def set_auto_skill_disabled(name: str, disabled: bool) -> None:
    prefs = load_extras_preferences()
    disabled_names = set(prefs.get("disabled", []))
    if disabled:
        disabled_names.add(name)
    else:
        disabled_names.discard(name)
    save_extras_preferences({"disabled": sorted(disabled_names)})


def enabled_auto_skills() -> list[str]:
    disabled_names = set(load_extras_preferences().get("disabled", []))
    return [name for name in AUTO_INSTALL_SKILLS if name not in disabled_names]


def installed_plugin_is_complete(root: Path | None = None) -> bool:
    install_root = root or HOME_PLUGIN_ROOT
    return all((install_root / relative_path).exists() for relative_path in REQUIRED_PLUGIN_FILES)


def cleanup_runtime_site_packages(root: Path | None = None) -> list[Path]:
    site_packages = runtime_site_packages_path(root)
    if not site_packages.exists():
        return []
    removed: list[Path] = []
    for path in sorted(site_packages.iterdir(), key=lambda item: item.name.lower()):
        if not path.name.startswith("~"):
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            removed.append(path)
        except OSError:
            continue
    return removed


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".codex"


def _skill_installer_script(name: str) -> Path:
    return _codex_home() / "skills" / ".system" / "skill-installer" / "scripts" / name


def _skill_listing() -> dict[str, bool]:
    script = _skill_installer_script("list-skills.py")
    if not script.exists():
        return {}
    result = subprocess.run(
        [sys.executable, str(script), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
        **_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return {
        str(item.get("name", "")).strip(): bool(item.get("installed"))
        for item in payload
        if str(item.get("name", "")).strip()
    }


def auto_install_enabled_skills() -> tuple[list[str], list[str]]:
    script = _skill_installer_script("install-skill-from-github.py")
    if not script.exists():
        return [], enabled_auto_skills()
    listing = _skill_listing()
    wanted = enabled_auto_skills()
    available = {name for name in wanted if name in listing}
    missing = [name for name in wanted if name in available and not listing.get(name, False)]
    unavailable = [name for name in wanted if name not in available]
    if not missing:
        return [], unavailable
    installed: list[str] = []
    failed: list[str] = list(unavailable)
    for name in missing:
        command = [
            sys.executable,
            str(script),
            "--repo",
            "openai/skills",
            "--path",
            f"skills/.curated/{name}",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            **_windows_subprocess_kwargs(),
        )
        if result.returncode == 0:
            installed.append(name)
            continue
        failed.append(name)
        print(f"Automatic optional skill install failed for `{name}`.")
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if output:
            print(output)
    if failed:
        failed = sorted(set(failed))
    if installed:
        installed = [name for name in missing if name in installed]
    if not installed and failed:
        return [], failed
    return installed, failed


def _install_source() -> str:
    if (REPO_ROOT / "pyproject.toml").exists():
        return str(REPO_ROOT)
    try:
        distribution = importlib.metadata.distribution(PACKAGE_NAME)
        direct_url_path = Path(distribution._path) / "direct_url.json"
        if direct_url_path.exists():
            payload = json.loads(direct_url_path.read_text(encoding="utf-8"))
            url = str(payload.get("url", "")).strip()
            if url:
                parsed = urlparse(url)
                if parsed.scheme == "file":
                    local_path = Path(unquote(parsed.path.lstrip("/")))
                    if os.name == "nt" and parsed.netloc:
                        local_path = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
                    return str(local_path)
                return url
    except Exception:
        pass
    version = importlib.metadata.version(PACKAGE_NAME)
    return f"{PACKAGE_NAME}=={version}"


def _runtime_constraints_path(install_target: str) -> Path | None:
    candidate_dirs: list[Path] = []
    target_path = Path(install_target)
    if target_path.exists() and target_path.is_dir():
        candidate_dirs.append(target_path)
    candidate_dirs.append(REPO_ROOT)
    for directory in candidate_dirs:
        candidate = (directory / "constraints.txt").resolve()
        if candidate.exists():
            return candidate
    return None


def _ensure_runtime(source: str | None = None) -> Path:
    python_path = runtime_python_path()
    if not python_path.exists():
        builder = venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False)
        builder.create(RUNTIME_ROOT)
    install_target = source or _install_source()
    force_reinstall = (
        Path(install_target).exists()
        or install_target.startswith((".", "/", "\\"))
        or install_target.endswith((".whl", ".zip", ".tar.gz"))
    )
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    command = [
        str(python_path),
        "-m",
        "pip",
        "install",
        "--upgrade",
    ]
    if force_reinstall:
        command.append("--force-reinstall")
    constraints_path = _runtime_constraints_path(install_target)
    if constraints_path is not None:
        command.extend(["-c", str(constraints_path)])
    command.append(install_target)
    cleanup_runtime_site_packages()

    def _run_install() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            **_windows_subprocess_kwargs(),
        )

    result = _run_install()
    if result.returncode != 0 and cleanup_runtime_site_packages():
        result = _run_install()
    if result.returncode != 0:
        message = ((result.stdout or "") + (result.stderr or "")).strip()
        raise RuntimeError(message or "Failed to prepare isolated relay runtime.")
    cleanup_runtime_site_packages()
    return python_path


def _windows_python_scripts_dir() -> Path | None:
    if os.name != "nt":
        return None
    candidate = Path(shutil.which("python") or "").resolve()
    if not candidate.exists():
        return None
    if candidate.parent.name.lower() != "python310" and candidate.parent.name.lower() != "python311" and candidate.parent.name.lower() != "python312":
        return candidate.parent / "Scripts"
    return candidate.parent / "Scripts"


def _windows_user_scripts_dir() -> Path | None:
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts"


def _windows_npm_dir() -> Path | None:
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "npm"


def _windows_candidate_shim_dirs() -> list[Path]:
    candidates = [
        _windows_python_scripts_dir(),
        _windows_user_scripts_dir(),
        _windows_npm_dir(),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _install_windows_path_shims(python_exe: Path) -> list[Path]:
    script_dirs = _windows_candidate_shim_dirs()
    if not script_dirs:
        return []
    shims = {
        "cladex.cmd": "cladex",
        "codex-discord.cmd": "relayctl",
        "codex-discord-install-plugin.cmd": "install_plugin",
    }
    installed: list[Path] = []
    for scripts_dir in script_dirs:
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for name, module in shims.items():
            target = scripts_dir / name
            target.write_text(
                "@echo off\r\n"
                "setlocal\r\n"
                f"\"{python_exe}\" -m {module} %*\r\n",
                encoding="utf-8",
            )
            installed.append(target)
    return installed


def _install_posix_path_shims(python_exe: Path) -> list[Path]:
    scripts_dir = Path.home() / ".local" / "bin"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shims = {
        "cladex": "cladex",
        "codex-discord": "relayctl",
        "codex-discord-install-plugin": "install_plugin",
    }
    installed: list[Path] = []
    for name, module in shims.items():
        target = scripts_dir / name
        target.write_text(
            "#!/usr/bin/env sh\n"
            f"exec '{python_exe}' -m {module} \"$@\"\n",
            encoding="utf-8",
        )
        target.chmod(0o755)
        installed.append(target)
    return installed


def _upsert_plugin_entry(marketplace: dict) -> None:
    plugins = marketplace.setdefault("plugins", [])
    plugins[:] = [item for item in plugins if item.get("name") != PLUGIN_NAME]
    plugins.append(
        {
            "name": PLUGIN_NAME,
            "source": {
                "source": "local",
                "path": f"./plugins/{PLUGIN_NAME}",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    )


def main(source: str | None = None) -> int:
    bundle_root = _bundle_root()
    HOME_PLUGIN_ROOT.parent.mkdir(parents=True, exist_ok=True)
    MARKETPLACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime_python = _ensure_runtime(source=source)

    replace_directory(bundle_root / ".codex-plugin", HOME_PLUGIN_ROOT / ".codex-plugin")
    replace_directory(bundle_root / "skills", HOME_PLUGIN_ROOT / "skills")
    soul_source = bundle_root / "SOUL.md"
    if soul_source.exists():
        HOME_PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copy2(soul_source, HOME_PLUGIN_ROOT / "SOUL.md")
    assets_dir = bundle_root / "assets"
    if assets_dir.exists():
        replace_directory(assets_dir, HOME_PLUGIN_ROOT / "assets")

    marketplace = _load_marketplace()
    _upsert_plugin_entry(marketplace)
    atomic_write_json(MARKETPLACE_PATH, marketplace)
    if not installed_plugin_is_complete(HOME_PLUGIN_ROOT):
        raise RuntimeError(f"Installed plugin at {HOME_PLUGIN_ROOT} is missing required files.")

    print(f"Installed Codex plugin files to {HOME_PLUGIN_ROOT}")
    print(f"Updated marketplace file at {MARKETPLACE_PATH}")
    print(f"Prepared isolated runtime at {runtime_root()}")
    if os.name == "nt":
        shims = _install_windows_path_shims(runtime_python)
        for shim in shims:
            print(f"Installed Windows PATH shim at {shim}")
    else:
        shims = _install_posix_path_shims(runtime_python)
        for shim in shims:
            print(f"Installed PATH shim at {shim}")
    if shutil.which("codex-discord") is None:
        print("`codex-discord` must be installed on PATH for the relay command itself to run.")
    installed_skills, failed_skills = auto_install_enabled_skills()
    if installed_skills:
        print("Installed recommended optional skills: " + ", ".join(installed_skills))
    if failed_skills:
        print("Could not auto-install optional skills: " + ", ".join(failed_skills))
    if not installed_skills and not failed_skills:
        print("Recommended optional skills already present or disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
