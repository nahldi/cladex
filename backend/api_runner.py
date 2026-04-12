from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import cladex
import claude_relay
import relayctl


MODULES = {
    "cladex.py": cladex,
    "claude_relay.py": claude_relay,
    "relayctl.py": relayctl,
}


def _run_module(target: str, argv: list[str]) -> dict[str, object]:
    module = MODULES.get(target)
    if module is None:
        raise RuntimeError(f"Unsupported backend target: {target}")
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    exit_code = 0
    previous_argv = sys.argv
    try:
        sys.argv = [target, *argv]
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            try:
                result = module.main()
                if isinstance(result, int):
                    exit_code = result
            except SystemExit as exc:
                code = exc.code
                exit_code = int(code) if isinstance(code, int) else 1
    finally:
        sys.argv = previous_argv
    return {
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "code": exit_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("target")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args()
    output_path = Path(ns.output)
    payload: dict[str, object]
    try:
        payload = _run_module(ns.target, list(ns.args))
    except Exception as exc:
        payload = {"stdout": "", "stderr": str(exc), "code": 1}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
