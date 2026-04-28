# CLADEX Development Log

This file is tracked on purpose. It gives future Claude/Codex agents a concise source of truth for what has been done, what is in progress, and what still needs care. Runtime-only memory files under `memory/` are useful locally, but GitHub users and new agents need this public handoff too.

## 2.3.0 Roadmap Completion / Fix Review (2026-04-28)

This tranche completes the April 2026 production-readiness roadmap for the Claude Code + Codex scope. CLADEX now has a production-grade review swarm, explicit Fix Review workflow, visible queue/concurrency limits, updated packaging metadata, and CI/release gates for clone-to-run users.

### Review swarm coordination + scale safety

- Added a per-job `CLADEX_REVIEW_COORDINATION.md` artifact with project briefing, lane assignments, assigned files, and per-agent sections. Review prompts now tell each lane to treat the target as an unknown project, infer the project from files/tests/docs, use its own section, and avoid duplicate lane work.
- Review jobs still accept 1-50 requested lanes, but the job now exposes `maxParallel`, `limits`, and `limitWarnings` so the UI can show that lanes queue behind `CLADEX_REVIEW_MAX_PARALLEL` and the machine/account limit instead of implying all lanes launch at once.
- AI review lanes now use isolated per-agent scratch copies derived from a sanitized base scratch workspace. Scratch copies skip symlinks so reviewer commands cannot follow a project symlink outside the selected workspace.
- Mixed AI lane failures now finish as `completed_with_warnings`; all-lane failures still finish as `failed`. Reports include `seenByAgents` on deduplicated findings.
- Claude review prompts now flow through stdin with a short command prompt, avoiding Windows argv-length failures on large projects.

### Guarded Fix Review

- Added `backend/fix_orchestrator.py` and `cladex fix list/start/show/run/run-task/cancel`.
- Completed or completed-with-warning review jobs can start a durable fix run. Fix Review always creates a source backup first, converts review findings into ordered tasks, launches Codex or Claude fix workers, runs discovered validation commands after each phase, records `fix_run.json` plus `CLADEX_FIX_RUN.md`, and returns a restore command hint when validation fails.
- Fix workers run with a minimal environment and provider account-home override only. Claude fix prompts now flow through stdin to avoid argv-length failures.
- Active Fix Review starts are idempotent per review job, so duplicate clicks/API calls return the existing active run instead of launching parallel write workers against the same workspace.
- Fix phases serialize write-capable workers in the shared workspace and reject successful task runs that touched files outside their assigned task files.
- CLADEX self-fix is guarded by both the completed self-review flag and a separate self-fix approval. A protected CLADEX workspace cannot enter Fix Review unless the originating review job was explicitly started as a CLADEX self-review and the operator supplies `--allow-cladex-self-fix` or confirms the UI self-fix prompt.
- Validation commands are cancellation-aware and fix-run API/UI/report output includes the exact `cladex backup restore <id> --confirm <id>` command.
- Added backend tests for backup creation, duplicate start idempotence, validation failure restore hints, cancellation, protected self-fix gating, assigned-file enforcement, validation command cancellation, validation command discovery, and Claude stdin prompt transport.

### API, desktop UI, and packaging

- Added API endpoints for fix runs: `POST /api/reviews/:id/fix`, `GET /api/fix-runs`, `GET /api/fix-runs/:id`, and `POST /api/fix-runs/:id/cancel`.
- Hardened API input handling: strict integer parsing keeps `agents: 0` invalid, string booleans parse deliberately, review/fix ids validate before filesystem use, fix/review errors map to 400/404 where appropriate, and CORS preflight allows `X-CLADEX-Access-Token`.
- The desktop Review Project view now shows active fix runs, queued/running/done/failed/cancelled progress, backup/report/artifact/restore paths, task summaries, cancel controls, duplicate-start suppression, and a confirmation-gated **Fix Review** button on completed review jobs.
- CI now includes a `cladex doctor --json` job and Electron package job. Backend package metadata now includes `fix_orchestrator`, `README.md`, and `constraints.txt`.
- Release metadata is aligned at 2.3.0 across `package.json`, `package-lock.json`, `backend/pyproject.toml`, plugin manifests, `server.cjs`, README, and INSTALL.

### Validation

- Targeted review/fix regression: `40 passed, 1 skipped`.
- Full validation to run before commit: `npm ci`, `npm audit`, `npm run lint`, `npm run build`, editable backend install with constraints, backend full suite, privacy audit, `cladex doctor --json`, API smoke for review/fix/backup, `npm run electron:build`, and `git diff --check`.

## 2.2.3 Deeper Self-Review Sweep (2026-04-28)

Continuation of the 2.2.2 self-review work. Triaged the remaining medium-severity findings the swarm surfaced (32 medium + 14 low) and shipped a broad correctness/security/UX pass. 218 → 230 backend tests.

### Frontend security + UX

- **F0098 token-exfiltration** — `src/App.tsx` blindly trusted `?apiBase=` from `window.location.search` for file:// launches and attached the stored remote token to every fetch. A crafted file-mode URL with `apiBase` pointing at an attacker-controlled origin could leak `X-CLADEX-Access-Token` off-host. Now `apiBase` is validated against an http(s) loopback allowlist before use, and the token header is only attached to same-origin or loopback fetches.
- **F0099 form-state** — Switching the Review Project provider between Codex and Claude reused a single `accountHome` value, so the Codex path could be submitted as `CLAUDE_CONFIG_DIR` (or vice versa) just because the label changed. Now the form holds separate Codex/Claude account-home state and submits only the value matching the selected provider.
- **F0100 duplicate-action** — `Review Project` and `Save snapshot only` accepted clicks while a request was in flight, which could launch duplicate review jobs / duplicate backups. `PrimaryButton`/`SecondaryButton` now accept a `busy` prop, the Review form disables both controls when either action is pending or the workspace is empty, and the click handlers also guard against re-entry.

### Review swarm correctness

- **F0093/94/95 report integrity** — `CLADEX_PROJECT_REVIEW.md` was rendered before `job["status"]` and `finishedAt` were finalized, so completed reports said `Status: running` / `Finished: not finished`. Report write moved to after the final job state is saved.
- **F0091 path-normalization** — AI lanes run inside the scratch workspace, but `parse_ai_findings` only relativized absolute paths against the original workspace. New `_relativize_finding_path` checks both workspace and scratch, refuses traversal (`..`), and returns `.` only when neither base wins.
- **F0088 duplicate-run-race** — `run_review_job` had no lock and `prepare_scratch_workspace` raced on a deterministic `scratch/workspace` path. Two concurrent `cladex review run` invocations on the same job could both flip status to `running` and one would die on `WinError 183`. Now `_acquire_job_run_lock` uses `O_CREAT|O_EXCL` plus dead-PID reclamation, and the public `run_review_job` short-circuits when the job is already finished or another worker holds the lock.
- **F0092 cancel-flow** — Queued cancel set `cancel.flag` but `run_review_job` still flipped status to `running` and did inventory + scratch copy before the per-agent cancel check fired. Now an early cancel-or-finished check returns immediately, and `cancel_review` flips queued lanes to `cancelled` so the public job reports it cleanly.
- **F0090 cancellation in-flight** — The AI subprocess ran inside `subprocess.run`, so once a Codex/Claude lane had launched, cancel waited for the full 30-min timeout. `_run_cli` now uses `Popen` plus a 1-second poll loop that checks the cancel flag, terminates the process tree (`taskkill /F /T /PID` on Windows, SIGTERM/SIGKILL elsewhere), and returns `AIRunResult(ok=False, error="cancelled by operator.")`. `process_agent` recognizes that error and marks the lane `cancelled`.
- **F0087 backup-restore symlinks** — `_review_artifact_ignore` skipped every symlink, but `shutil.copytree` was called with `symlinks=True`. The combined effect was that snapshots dropped target symlinks entirely and restore would treat them as removed. Removed the unconditional symlink skip so symlinks are copied as symlinks; the credential / cache filters still apply.

### Backend correctness

- **F0078 install-reproducibility** — `install_plugin._ensure_runtime` ran `pip install --upgrade <target>` without a constraints file, so end-user runtime installs could resolve newer transitive versions than CI. `_ensure_runtime` now finds `constraints.txt` next to the install target (or in the repo root) and passes `-c <path>` automatically.
- **F0082 process-lifecycle** — `_codex_login_status` ran `codex login status` with no timeout and no `OSError`/`TimeoutExpired` handling. With the resolved binary literal `codex` (no PATH match) it could surface raw failures or hang `cladex doctor`. Now bounded at 15s, catches missing-PATH/`FileNotFoundError`/`OSError`, and reports a structured diagnostic instead.
- **F0073 readiness** — Claude profiles reported `ready=running` from PID alone, racing past Discord login. PID-only state is now `ready=False`; readiness flips when `claude_bot` writes a non-error `status.json`. The legacy test was updated to match and a new test covers the post-status path.
- **F0072 history-scan** — `_collect_relevant_channel_history` invoked `channel.history(limit=None)` for any `channel_history_limit` value. New behavior: legacy `0` (unlimited) is honored only when `RELAY_UNLIMITED_HISTORY_SCAN=1` is explicit; otherwise `0` falls back to the default 20. The raw scan also caps at `max(relevant_limit * 10, 200)` so a busy channel can't drain the rate limit chasing one match.
- **F0097 api-cli-contract** — `server.cjs` Codex profile create passed `--startup-channel-text` (no such arg) and routed `startupDmUserIds` to `--allowed-user-id` (wrong semantics — those are DM allowlists, not startup notification recipients). Both are now ignored with a warning until the backend grows the matching args; operator/user IDs are also deduped before being passed.
- **F0081 atomic-replace** — `replace_directory` could leave the destination missing if the second `os.replace` failed mid-swap. Now wraps the swap in try/except, restores the backup if the temp move fails, and tries to clean up the temp directory on the way out.
- **F0077 crash-recovery** — `claude_bot._operator_bridge_loop` only scanned `*.json` requests, so any `.processing` file left behind by a previous crash blocked the caller until its client-side timeout. New `_reclaim_orphaned_operator_requests` runs at startup, renames orphans back to `.json` (or writes a structured failure response if a duplicate `.json` already exists).

### Review tooling noise reduction

- **Smarter secret-filename matching** — Replaced the regex-based filename hit (`vite-env.d.ts` was a false positive) with `has_secret_token_segment`. A name is now flagged only if a dot-segment IS a secret token name (`.env`, `secrets.json`) or a hyphen/underscore compound has a recognized credential prefix (`auth-token`, `api-key`, `private-key`). Frameworks like `vite-env.d.ts` no longer trip it.
- **Skip docs/config from line-pattern scan** — `scan_file` now skips `.md`, `.rst`, `.txt`, `.toml`, `.yaml`, `.yml`, `.json`, `.css`, `.html`, `.xml`, `.svg`. Documentation lines that quote rule patterns ("0.0.0.0 example listen line", "shell=True") no longer become high-severity findings.
- **Skip rule-definition files** — `review_swarm.py` and `tests/test_review_swarm.py` are exempt from the line-pattern scan since they DEFINE the patterns; flagging them was self-referential noise.

### CI + dev

- **F0103 ci-supply-chain** — `.github/workflows/ci.yml` now sets a top-level `permissions: contents: read` block. (Pinning third-party actions to commit SHAs is left for a follow-up; this is the immediate blast-radius reduction.)
- **F0102 dev:stack proxy** — `vite.config.ts` now proxies `/api` → `http://127.0.0.1:3001` so the `npm run dev:stack` workflow's UI can talk to the API server without a separate `VITE_API_BASE` override. `API_HOST`/`API_PORT` env vars override the proxy target.

### Tests

- 218 → 230 passed (+12: secret-segment matching, doc-skip in scan, ai-failure propagation, env-stripping, credential-ignore, scratch path normalization, report integrity, run short-circuit, register-allowlist x3, hyphenated env, status.json readiness gate).

### Validation

- `cmd /c npm ci`, `cmd /c npm audit` (0 vulnerabilities), `cmd /c npm run lint`, `cmd /c npm run build`.
- `python -m pip install -e "backend[dev]" -c backend/constraints.txt` -> `discord-codex-relay==2.2.3`.
- Backend full suite `230 passed, 1 warning`.
- `python backend/relayctl.py privacy-audit --tracked-only .` -> no findings.
- `python backend/cladex.py doctor --json` -> ok=True.
- `cmd /c npm run electron:build` -> `release/CLADEX Setup 2.2.3.exe`, `release/CLADEX 2.2.3.exe`.

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
