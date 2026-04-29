from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
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
DEFAULT_CAPTURE_LIMIT = 2 * 1024 * 1024


def _capture_limit() -> int:
    raw = os.environ.get("CLADEX_API_RUNNER_CAPTURE_LIMIT", "")
    try:
        value = int(raw) if raw else DEFAULT_CAPTURE_LIMIT
    except ValueError:
        value = DEFAULT_CAPTURE_LIMIT
    return max(16 * 1024, min(value, 16 * 1024 * 1024))


class BoundedTextCapture(io.TextIOBase):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._buffer = io.StringIO()
        self._size = 0
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value)
        incoming = len(text)
        remaining = self.limit - self._size
        if remaining > 0:
            kept = text[:remaining]
            self._buffer.write(kept)
            self._size += len(kept)
        if incoming > remaining:
            self.truncated = True
        return incoming

    def getvalue(self) -> str:
        text = self._buffer.getvalue()
        if self.truncated:
            text = text.rstrip() + "\n...[truncated by CLADEX api_runner]"
        return text


def _run_module(target: str, argv: list[str]) -> dict[str, object]:
    module = MODULES.get(target)
    if module is None:
        raise RuntimeError(f"Unsupported backend target: {target}")
    capture_limit = _capture_limit()
    stdout_buffer = BoundedTextCapture(capture_limit)
    stderr_buffer = BoundedTextCapture(capture_limit)
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
                if isinstance(code, int):
                    exit_code = code
                else:
                    exit_code = 1
                    if code:
                        stderr_buffer.write(str(code))
                        stderr_buffer.write("\n")
    finally:
        sys.argv = previous_argv
    return {
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "code": exit_code,
        "stdoutTruncated": stdout_buffer.truncated,
        "stderrTruncated": stderr_buffer.truncated,
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
