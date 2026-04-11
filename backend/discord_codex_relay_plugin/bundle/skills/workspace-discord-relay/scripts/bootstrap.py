from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


PACKAGE_NAME = "discord-codex-relay"


def _run(command: list[str]) -> int:
    print("$ " + " ".join(command))
    return subprocess.run(command, check=False).returncode


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
    parser.add_argument("--spec", default=PACKAGE_NAME, help="Package spec to install. Defaults to the published package name.")
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
