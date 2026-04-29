from __future__ import annotations

import os
import sys
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
