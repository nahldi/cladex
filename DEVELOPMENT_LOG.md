# CLADEX Development Log

This file is tracked on purpose. It gives future Claude/Codex agents a concise source of truth for what has been done, what is in progress, and what still needs care. Runtime-only memory files under `memory/` are useful locally, but GitHub users and new agents need this public handoff too.

## 2.2.2 Self-Review Findings Pass (2026-04-28)

After 2.2.1 shipped, the project's own review swarm was run on the project (10 Codex lanes, `--allow-cladex-self-review`, with a backup snapshot before launch). The review surfaced 70 high-severity items, mostly secret-pattern hits in code that just names those concepts, but several real bugs landed in this tranche.

### Real bugs fixed

- **Wrong bot launched from `claude-discord run`** (`backend/claude_relay.py`). `cmd_run` was launching `bot.py` (the Codex bot), not `claude_bot.py`. The packaged-via-cladex path always launched `claude_bot.py` correctly, so this only affected callers using the documented `claude-discord run` console script directly. Now launches `claude_bot.py`.
- **`review_swarm` and `api_runner` missing from the Python package** (`backend/pyproject.toml`). `cladex.py` imports `review_swarm` at module load time and `server.cjs` invokes `api_runner.py`, but neither was listed in `[tool.setuptools].py-modules`. A non-editable wheel/sdist install would fail before any `cladex` command could run. Both modules added.
- **Host-header spoofing could bypass the remote-API token** (`server.cjs`). `isLoopbackRequest` based the loopback decision on `Host` / `X-Forwarded-Host`, both client-controlled. A remote caller could send `Host: 127.0.0.1` or `X-Forwarded-Host: 127.0.0.1`, omit `Origin`, be classified as loopback, and have `/api/runtime-info` echo the remote access token. The check now derives loopback from `req.socket.remoteAddress` only, treats any forwarded header as proxy presence (token required), and still validates `Origin` against loopback.
- **Claude relay `register` could create open allowlists** (`backend/claude_relay.py`). `cmd_register` accepted empty `--allowed-channel-id` and could enable `--allow-dms` without any operator/user allowlist. Now requires at least one of channel/user allowlist, and `--allow-dms` requires a user allowlist; both rules covered by tests.
- **Claude subprocess stderr was never drained** (`backend/claude_backend.py`). The persistent Claude process spawned with `stderr=PIPE` but `_run_turn` only read stdout, so stderr-heavy output could fill the pipe and deadlock the child. Added a per-process stderr drain task with a bounded 50-line tail for diagnostics, cancellation alongside `reader_task`, and process termination on turn timeout so the next turn restarts cleanly.
- **AI reviewer failures masqueraded as completed reviews** (`backend/review_swarm.py`). `_run_cli` returned plain text on timeout, missing-binary, and nonzero-exit, which `parse_ai_findings` wrapped as a medium-severity "Unstructured reviewer notes" finding. Lanes that crashed at startup looked like clean reviews. Now `_run_cli` returns a structured `AIRunResult(text, ok, error)`, `process_agent` raises on `ok=False`, and the lane is marked `failed` with the underlying error.
- **AI reviewer subprocesses inherited the host environment** (`backend/review_swarm.py`). Codex/Claude review lanes received `os.environ.copy()`, which carries Discord tokens, AWS credentials, Anthropic/OpenAI API keys, and the CLADEX remote token into every reviewer process — and into review artifacts via stdout/stderr. New `_minimal_reviewer_env` drops everything except a small allowlist (PATH, USERPROFILE, TEMP, locale, etc.) and the explicit account-home override. `Bash` is also removed from the Claude reviewer toolset since it could write outside the scratch workspace under `--permission-mode dontAsk`.
- **Source backups copied non-env local secrets** (`backend/review_swarm.py`). `_review_artifact_ignore` only skipped fixed cache dirs, `.env*`, and names matching the secret-filename regex, so files like `.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, and directories like `.ssh`, `.aws`, `.gnupg`, `.kube` could be copied into CLADEX backups and reviewer scratch trees. New `LOCAL_SECRET_FILE_NAMES` and `LOCAL_SECRET_DIR_NAMES` sets cover the common credential locations and feed both `_review_artifact_ignore` and `_preserve_on_restore`.
- **Packaged desktop app did not bootstrap Python backend deps** (`server.cjs`). A fresh install would call `python backend/cladex.py list --json` against the system Python, which lacks `psutil`, `discord.py`, etc., before the user could create any profile. Added `bootstrapBackendRuntime()` that runs once on the first `runPython` call and creates the managed runtime venv via `install_plugin._ensure_runtime`. Subsequent calls short-circuit. `CLADEX_SKIP_BACKEND_BOOTSTRAP=1` opts out for environments that manage Python differently.

### Tests

- 217 → 223 passed (+6: register allowlist x3, reviewer failure propagation, reviewer env stripping, local-credential ignore).

### Validation

- `cmd /c npm ci`, `cmd /c npm audit` (0 vulnerabilities), `cmd /c npm run lint`, `cmd /c npm run build`.
- Fresh-venv `pip install -e backend[dev]` reproduces the trimmed 20-package constraints.
- Backend full suite `223 passed, 1 warning`.
- `python backend/relayctl.py privacy-audit --tracked-only .` -> no findings.
- `python backend/cladex.py doctor --json` -> ok=True.
- `cmd /c npm run electron:build` -> `release/CLADEX Setup 2.2.2.exe`, `release/CLADEX 2.2.2.exe`.
- 10-agent self-review (job `review-20260428-142423-7261b80f`) re-run is recommended after this tranche to confirm the high-severity items are gone.

## Post-2.2.0 Cleanup Tranche (2026-04-28)

Audit-driven follow-up to 2.2.0. No new architectural surface; tightens existing review/backup behavior and clears one stale runtime dependency. Versions aligned at 2.2.1 for delivery.

### Done

- Dropped the dead `claude-code-sdk>=0.0.25,<0.1` runtime dependency from `backend/pyproject.toml`. Source already moved to a subprocess-based Claude transport long ago, so the SDK was never imported. Regenerated `backend/constraints.txt` from a fresh venv install: 47 pins → 20, removing `mcp`, `pydantic*`, `httpx*`, `starlette`, `uvicorn`, `cryptography`, `cffi`, `pycparser`, `jsonschema*`, `referencing`, `rpds-py`, `python-multipart`, `pyjwt`, `sse-starlette`, `pywin32`, `click`, `anyio`, `annotated-types`, `certifi`, `h11`, `httpcore`, `httpx-sse`, `typing-inspection`. Backend test suite still passes.
- Allowlisted template/example secret-like filenames so review swarm no longer flags `.env.example`, `.env.template`, `.env.sample`, `secrets.example.json`, etc. as high-severity secret hygiene findings. New `is_template_secret_filename` helper splits on `.` and matches segments in `{example, sample, template, tmpl, dist}`. Only triggers after the existing `SECRET_FILENAME_RE` match, so unrelated names are unaffected.
- Tightened the maintenance-marker rule. Old `"todo" in lowered or ...` substring check produced false positives on words like `podcast`, `shack`, doc strings mentioning "todo", and string literals. New rule requires `\b(TODO|FIXME|HACK|XXX)\b` AND a comment context (`#`, `//`, `/*`, `<!--`, `;`, `-- `, `* `). Detail message now names which marker was found.
- Added cross-lane finding deduplication. `dedup_findings` collapses entries that share `(category, path, line, title)`, records every contributing agent in a new `seenByAgents` array, and promotes the entry to the highest severity any contributor reported. Run after all lane futures complete and before the final sort/id assignment.
- Added review job cancellation. New `cancel_review(job_id)` writes a `cancel.flag` file inside the review's artifact directory; workers check `_cancel_requested(job_id)` via flag file existence (no contention with `job.json` writes from other lanes). Queued jobs are marked `cancelled` immediately. Running jobs see lanes that have not yet started transition to `cancelled` and lanes that finish their preflight scan stop before the AI subprocess kicks off. New CLI: `cladex review cancel <id>`. New API: `POST /api/reviews/:id/cancel`. New UI button on running/queued review cards.
- Hardened `_atomic_write_text` with a 5-attempt backoff on `PermissionError`. Windows `Path.replace()` can transiently fail when another process or thread has the destination open — surfaced when adding cross-thread cancel checks. Backoff (50/100/150/200/250 ms) is short enough that a stuck write still surfaces fast.
- Surfaced severity counts on review jobs. `_public_job` now returns `severityCounts: {high, medium, low}` from the persisted findings file. UI renders colored severity pills in `ReviewJobCard` once any finding lands.
- Wired the backup management UI that was already plumbed in the API but never displayed. `App.tsx` now polls `api.backups()` every 5 s alongside profiles/reviews and renders a `BackupListCard` under the review jobs column. Adds a "Save snapshot only" secondary button next to "Review Project" so users can capture a snapshot without launching a review. Restore is still CLI-only on purpose (`cladex backup restore <id> --confirm <id>`); the UI shows that hint inline.
- Added regression coverage for every change: secret-name allowlist, marker word boundaries, dedup, cancel (queued + mid-run + completed-no-op), and severity counts. Backend suite: 211 → 218 passed.

### Validation

- `cmd /c npm ci`
- `cmd /c npm audit` -> 0 vulnerabilities
- `cmd /c npm run lint`
- `cmd /c npm run build`
- `.venv\Scripts\python.exe -m pip install -e "backend[dev]" -c backend\constraints.txt`
- `.venv\Scripts\python.exe backend\relayctl.py privacy-audit --tracked-only .` -> no findings
- `.venv\Scripts\python.exe -m pytest --tb=short -q` from `backend/` -> `218 passed, 1 warning`
- `.venv\Scripts\python.exe backend\cladex.py doctor --json` -> ok=True, no issues
- Fresh venv install of `backend[dev]` against the regenerated constraints reproduces the trimmed 20-package set.

### Known Cleanup Remaining

- The "Unstructured reviewer notes" fallback in `parse_ai_findings` still wraps any non-JSON AI lane output as a medium-severity finding. Will be replaced by structured command-attempt evidence in the live-AI tranche.

## Current Direction

CLADEX is a Claude Code + OpenAI Codex relay manager and project-review orchestrator. Do not add other model providers unless the user explicitly changes scope.

The next production target is a real Review Project swarm:
- user chooses a project folder,
- user chooses Codex or Claude,
- user chooses 1-50 reviewer lanes,
- every lane has a different focus and a different shard,
- lanes search deeply for bugs, failures, test gaps, smoke/stress risks, vulnerabilities, stale code, and missed production issues,
- findings merge into one universal Markdown report plus structured JSON,
- Fix Plan generates an ordered plan but does not edit source,
- future Fix Now must be approval-gated, backed up, and validated phase by phase.

## Completed In This Tranche

- Claimed active task `phase-5-roadmap-completion-review-swarm` in `memory/TASKS.json`.
- Added Phase 5 implementation plan to `memory/PLAN.md`.
- Verified current official GitHub action releases with `gh api` and confirmed v6 action metadata uses `node24`.
- Updated CI actions to:
  - `actions/checkout@v6`
  - `actions/setup-node@v6`
  - `actions/setup-python@v6`
- Fixed protected-root env parsing so `CLADEX_PROTECTED_ROOT` and `CLADEX_PROTECTED_ROOTS` combine.
- Added focused guardrail test coverage for singular + plural protected roots.
- Added `backend/review_swarm.py` with:
  - durable review jobs under local CLADEX data,
  - 1-50 lane validation,
  - bounded default AI lane concurrency with `CLADEX_REVIEW_MAX_PARALLEL` override,
  - review/backup id validation before filesystem path use,
  - generated/vendor/secret-heavy folder skips,
  - internal preflight heuristics,
  - distinct reviewer specialties,
  - scratch workspace creation,
  - scratch-copy hard failure for AI review lanes if a safe copy cannot be created,
  - AI reviewer prompt scaffolding,
  - AI output truncation before storing unstructured notes,
  - JSON finding parsing/redaction,
  - unified report generation,
  - fix-plan generation,
  - backup create/list/restore primitives,
  - nested stale-file cleanup during restore while preserving ignored and secret-like local paths.
- Added `cladex review` CLI commands for list/start/run/show/fix-plan.
- Added `cladex backup` CLI commands for list/create/restore.
- Added API endpoints for review jobs and backup listing/creation.
- Added React `Review Project` view with folder picker, provider selector, 1-50 slider, account-home field, backup toggle, CLADEX self-review toggle, progress cards, report preview, and Fix Plan button.
- Updated README, INSTALL, SECURITY, and ROADMAP to describe the review swarm and backup/self-review boundaries.

## Important Security Decisions

- Normal relays and normal review jobs still cannot target the CLADEX repo.
- CLADEX self-review requires explicit opt-in. The job creates a source backup first.
- Review jobs do not apply fixes.
- Review artifacts and backups are stored under the local CLADEX data directory, not in the reviewed project.
- Restore is CLI-only and requires `--confirm <backup-id>` exactly matching the backup id.
- Codex review lanes run against a scratch workspace with no approval escalation.
- Claude review lanes run against scratch with write/edit tools disabled; Bash is available for safe validation commands in scratch.
- Detected credential values are redacted from findings/reports.
- Review and backup ids are pattern-validated before they are mapped into local artifact paths.
- AI review lanes are queued behind a bounded default worker pool so selecting 50 lanes does not automatically launch 50 CLI processes at once.

## Validation Passed

- `cmd /c npm ci`
- `cmd /c npm audit` -> zero vulnerabilities
- `cmd /c npm run lint`
- `cmd /c npm run build`
- `.venv\Scripts\python.exe -m pip install -e "backend[dev]" -c backend\constraints.txt` -> installed `discord-codex-relay==2.2.0`
- `.venv\Scripts\python.exe -m pytest backend\tests --tb=short -q` -> `211 passed, 1 warning`
- `.venv\Scripts\python.exe backend\relayctl.py privacy-audit --tracked-only .` -> no tracked-file privacy findings
- `.venv\Scripts\python.exe backend\cladex.py doctor --json` -> ok, with expected Codex PowerShell shim warning
- `git diff --check`
- CLI smoke for review start/run/fix-plan and backup create/restore
- local server API smoke for review listing/show/fix-plan and backup creation

## Current Implementation State

The visible `static vs ai` split has been removed. Static scanning remains only as internal preflight/fallback coverage for deterministic tests and red-flag findings.

Known cleanup required before commit:
- rebuild packaged Electron artifacts after final docs/memory updates,
- update memory files,
- commit, push, and watch CI.

## Next Work

- Finish the current review-swarm/self-review/backup implementation.
- Add cancel/retry/export and richer report filtering.
- Add structured command-attempt evidence from AI lanes.
- Add a guarded Fix Now implementation phase:
  - claim task,
  - mandatory source backup,
  - planner groups fixes,
  - workers edit only assigned workspaces,
  - validate after every phase,
  - restore from backup if validation fails and no safe repair is available.
- Build supervisor/account-pooling for true sustained 50 Codex + 50 Claude operation.
