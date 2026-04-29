from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


PACKAGE_NAME = "discord-codex-relay"
PACKAGE_VERSION = "2.5.4"
DEFAULT_PACKAGE_SPEC = f"{PACKAGE_NAME}=={PACKAGE_VERSION}"
DEFAULT_TIMEOUT_SECONDS = 900
MAX_CAPTURED_OUTPUT = 12000


def _timeout_seconds() -> int:
    raw = os.environ.get("CLADEX_BOOTSTRAP_TIMEOUT_SECONDS") or os.environ.get("CLADEX_INSTALL_SUBPROCESS_TIMEOUT")
    try:
        value = int(str(raw or "").strip(), 10)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _run(command: list[str]) -> int:
    print("$ " + " ".join(command))
    try:
        result = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=_timeout_seconds(),
        )
    except subprocess.TimeoutExpired:
        print(f"Command timed out after {_timeout_seconds()}s: {' '.join(command)}", file=sys.stderr)
        return 124
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if output:
        if len(output) > MAX_CAPTURED_OUTPUT:
            output = output[:MAX_CAPTURED_OUTPUT].rstrip() + "\n...[truncated by CLADEX]"
        print(output, end="" if output.endswith("\n") else "\n")
    return result.returncode


def _install_with_pipx(spec: str) -> int:
    pipx = shutil.which("pipx")
    if not pipx:
        return 1
    return _run([pipx, "install", spec])


def _install_with_pip(spec: str) -> int:
    command = [sys.executable, "-m", "pip", "install"]
    if os.name != "nt":
        command.append("--user")
    command.append(spec)
    return _run(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the discord-codex-relay Python package.")
    parser.add_argument("--spec", default=DEFAULT_PACKAGE_SPEC, help="Package spec to install. Defaults to the pinned published package.")
    parser.add_argument("--prefer-pip", action="store_true", default=False, help="Use pip instead of pipx even when pipx is available.")
    args = parser.parse_args()

    if shutil.which("codex-discord"):
        print("`codex-discord` is already available on PATH.")
        return 0

    if not args.prefer_pip:
        if _install_with_pipx(args.spec) == 0:
            return 0

    return _install_with_pip(args.spec)


if __name__ == "__main__":
    raise SystemExit(main())
