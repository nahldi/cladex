from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root_text = str(REPO_ROOT)
if not sys.path or sys.path[0] != repo_root_text:
    sys.path.insert(0, repo_root_text)

for module_name in ("bot", "install_plugin", "relay_common", "relayctl"):
    sys.modules.pop(module_name, None)

# Tests must never trigger a real Codex/Claude AI planner call by default.
# Individual tests that exercise the planner explicitly opt back in via
# monkeypatching `_ai_plan_fix_tasks`.
os.environ["CLADEX_FIX_PLANNER_DISABLE"] = "1"

# The post-lane synthesizer pass invokes `_run_cli` against the real Codex/Claude
# binary, which would block tests that mock only `_run_codex_ai_review` or
# `_run_claude_ai_review`. Disable it by default; tests that exercise the
# synthesizer set CLADEX_REVIEW_SYNTHESIZER=1 explicitly.
os.environ.setdefault("CLADEX_REVIEW_SYNTHESIZER", "0")

# Critical: the test suite must NEVER write encrypted secret blobs into the
# operator's real DPAPI store at %LOCALAPPDATA%\cladex\secrets\. Without this
# isolation every test pass through `secret_store.store_secret()` leaves a
# permanent .bin file in production state — the post-v3 audit found 2,372
# leaked test blobs on a single dev machine. Pin the secret root to a
# pytest-session tempdir BEFORE any test code can import secret_store.
# Auto-cleaned on Python exit.
_PYTEST_SECRETS_ROOT = tempfile.mkdtemp(prefix="cladex-pytest-secrets-")
os.environ["CLADEX_SECRETS_ROOT"] = _PYTEST_SECRETS_ROOT
