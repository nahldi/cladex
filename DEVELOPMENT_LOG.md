# CLADEX Development Log

This file is tracked on purpose. It gives future Claude/Codex agents a concise source of truth for what has been done, what is in progress, and what still needs care. Runtime-only memory files under `memory/` are useful locally, but GitHub users and new agents need this public handoff too.

## 2.5.1 Production Hardening (2026-04-29)

**Status.** Shipped as commit `8950b7b` on `origin/master`; GitHub Actions run `25086373753` passed frontend, backend Python 3.10/3.11/3.12, doctor, and package jobs. This tranche closes the verified findings from self-review `review-20260428-212527-cf81fdd1` and the follow-up swarm review of the dirty 2.5.1 tree. Source backup for the original Fix Review attempt remains `backup-20260428-233430-34791a4e`.

**Review source.** The 10-lane Codex self-review at `8bd8793` produced 82 raw findings, triaged to 32 real findings. The first AI Fix Review run (`fix-20260428-233430-921bd2e8`) landed phase-1 worker fixes for 10 findings and exposed two orchestrator defects: review subprocesses used a hard wall-clock timeout, and the assigned-file detector could misclassify transient workspace operations.

**Fixes shipped in this tranche.**
- Fixed phase-1 worker findings: Claude profile start idempotence, profile access invariants, safe `.env` serialization, runtime lease/worktree/memory-write races, local API origin handling, CSP/anti-framing headers, and real HTTP API smoke coverage.
- Hardened the Fix Review orchestrator: AI planner prompt/schema ordering, finding-id salvage from planner `files`, bounded planner retries, Claude fix-worker `--allowedTools` with write-capable permission mode, duplicate-start idempotence, explicit `--allow-cladex-self-fix`, exact restore-command exposure, cancel-aware validation, and stable assigned-file rechecks.
- Replaced review subprocess wall-clock termination with output-aware idle timeout, a generous initial silent grace, max-runtime ceiling, stdout/stderr reader threads, async stdin writing for large prompts, and focused regression tests.
- Closed remaining backend reliability findings: bounded Codex attachment handling, best-effort startup notices, channel-history validation, workspace path validation, Claude stream capture caps, bounded Claude inbound queue/status, installer subprocess timeouts/output caps, Codex fallback process-tree cleanup, and bounded log tailing.
- Closed CI/policy/release findings: pinned provider CLI versions in required CI, doctor runtime-version gates for Node/npm/Python, disabled implicit invocation for the mutating relay-management skill, aligned package/backend/plugin/server metadata to 2.5.1, regenerated `package-lock.json`, and clarified packaged release install paths.
- Closed review dashboard findings: fetch timeouts, resilient `Promise.allSettled` polling, cancelled reviews with partial findings visible/exportable, full 1-50 lane visibility through an expander, and `npm run frontend:smoke` wired into CI.

**Final validation evidence.**
- Focused review/fix suites: `backend/tests/test_review_swarm.py` -> 40 passed, 1 skipped; `backend/tests/test_fix_orchestrator.py` -> 20 passed.
- Combined touched backend/UI gate: `backend/tests/test_bot_logic.py backend/tests/test_cladex.py backend/tests/test_claude_relay.py backend/tests/test_relayctl.py backend/tests/test_backend.py backend/tests/test_relay_backend.py backend/tests/test_resource_bounds.py backend/tests/test_fix_orchestrator.py backend/tests/test_review_swarm.py` -> 297 passed, 1 skipped, 1 warning.
- Frontend: `npm run frontend:smoke` passed; `npm run lint` passed.
- API smoke passed after updating `Origin: null` behavior to require the token while still allowing authenticated opaque/file-origin preflight.
- Full gate evidence: `npm ci`, `npm audit`, `npm run lint`, `npm run frontend:smoke`, `npm run build`, `npm run api:smoke`, editable backend install (`discord-codex-relay==2.5.1`), tracked privacy audit, `cladex doctor --json`, backend full suite (`325 passed, 1 skipped, 1 warning`), `git diff --check`, and `npm run electron:build` all passed. Electron build produced ignored local artifacts: `release\CLADEX Setup 2.5.1.exe`, `release\CLADEX 2.5.1.exe`, and `release\win-unpacked\CLADEX.exe`. GitHub Actions run `25086373753` also passed the package job.

## Historical 2.5.1-pre Handoff - Superseded (2026-04-28)

This interrupted handoff is retained only as provenance for why 2.5.1 existed. Do not use it as current task state. The old handoff claimed 22 findings, review subprocess idle-timeout work, and scope-detector fixes were still open; all of those were implemented, validated, committed, pushed, and CI-confirmed in `8950b7b`.

## 2.5.0 Fix Review AI Orchestrator (2026-04-28)

**Why.** The previous Fix Review path was a deterministic 1:1 mapping: every finding became its own task, all using the same provider as the upstream review. The user explicitly called this out: "the fix button should spawn an orchestrator that decides how many, what type, and where each agent goes." 2.5.0 makes that real.

**Design.**
- `backend/fix_orchestrator.py` now runs an AI planner before tasks are constructed. It:
  1. Walks the workspace into a small inventory (languages, sample files, has-tests).
  2. Renders a planner prompt that bundles the inventory, the full findings list, the provider strengths guide, and the JSON schema the planner must satisfy (`FIX_PLAN_SCHEMA`).
  3. Calls one of two planners depending on the upstream review provider:
     - `_run_codex_planner` → `codex --sandbox read-only --ask-for-approval never exec --ephemeral --skip-git-repo-check -`
     - `_run_claude_planner` → `claude -p --tools "" --disallowedTools Bash,Edit,MultiEdit,Write,NotebookEdit --permission-mode dontAsk --json-schema <schema>`
  4. Validates the returned JSON, groups findings into tasks, assigns each task its own `provider`, `reasoningEffort`, `phase`, `dependsOn`, and `rationale`. A residual catch-all task is added if the planner ever silently drops a finding.
  5. Honors `min(operator_max_agents, recommended_count)` so the orchestrator's recommended_count caps parallelism unless the operator chose lower.
- The deterministic 1:1 builder still exists as `_deterministic_fix_tasks` and is the fallback when the planner subprocess fails or is disabled, so existing behavior is preserved on regression.
- Tests must never burn live planner calls. `backend/tests/conftest.py` sets `CLADEX_FIX_PLANNER_DISABLE=1` by default; the three new orchestrator-specific tests opt back in via `monkeypatch.delenv`.
- Frontend: new `FixRunPlan` type + `plan`/`requestedMaxAgents` fields on `FixRun`, a new `FixRunPlanSection` block in `FixRunCard` that shows the planner source, recommended-agent count, summary, rationale, and fallback reason, plus a richer `FixTaskTile` that surfaces per-task provider, effort, phase, severity/category badges, rationale, and dependency chain.

**Tests.** `pytest backend/tests` → 268 passing. New cases:
- `test_ai_planner_groups_findings_and_picks_provider`
- `test_ai_planner_falls_back_to_deterministic_when_provider_returns_nothing`
- `test_ai_planner_adds_residual_task_for_skipped_findings`

**Frontend.** `npm run lint` (tsc --noEmit) and `npm run build` both clean.

## 2.4.0 Roadmap Closeout — Non-Blocking Items (2026-04-28)

The "Non-Blocking Future Work" section that has lived on the roadmap since 2.2.x is now empty. Each item shipped or has an explicit recorded decision.

### Provider observability — Codex account/rate-limit/model in `cladex doctor`

`cladex doctor --json` now spawns `codex app-server` over stdio and drives a short JSON-RPC sequence: `initialize` → `account/read` → `account/rateLimits/read`. Account type, plan, email (when exposed), and rate-limit windows land in a new `codex-account` warning entry alongside the existing PowerShell shim warnings. Errors collapse to soft warnings (`ok: True, warning: True`) rather than hard fail so a non-Codex install still passes doctor.

Method names were verified against the live Codex CLI 0.125.0 schema (`generate-json-schema`); the `getAccount` / `getAccountRateLimits` names from older docs do not exist in the current protocol.

### Supervisor pooling — Claude worker lifecycle (F0004 closeout)

`PersistentClaudeProcess` gained a `last_used_at` timestamp. `_persistent_process_for_channel` now:

- Calls `_evict_idle_processes` first to drop any process that has been idle past `CLADEX_CLAUDE_WORKER_IDLE_TTL` seconds (default 1800).
- For new channel allocations, calls `_enforce_worker_max_live` to LRU-evict the oldest inactive channel when live process count would exceed `CLADEX_CLAUDE_WORKER_MAX_LIVE` (default 16).
- Updates `last_used_at` whenever a turn starts on a process so active channels are never picked for eviction.

`_run_turn` also stamps `last_used_at` so a long-running turn keeps its slot warm.

This closes the F0004 supervisor-fanout finding from the verification reviews. Two regression tests cover the idle-TTL drop and the LRU cap.

### Interactive review findings filter + export

- New backend command `cladex review findings <id>` and API endpoint `GET /api/reviews/:id/findings` return the structured findings JSON from `findings.json` for a given review id.
- Desktop Review Project view ships a "Findings explorer" expander on completed/`completed_with_warnings`/failed jobs. Severity toggles (high/medium/low), category dropdown, and "Export JSON" download (browser blob, no server hop). Inline list shows up to 200 matching findings with severity pill, id, category, agent, file path/line, and recommendation.
- Lazy-loads on first open so the extra endpoint isn't called for in-flight reviews.

### Claude Code Channels evaluation

Documented in `backend/docs/CLAUDE_CHANNELS_EVALUATION.md` and recorded in `memory/DECISIONS.md`. Decision: do not adopt as a Claude transport in 2.4.0. Reasons: research-preview status, Claude.ai-login coupling, no clean multi-account `CLAUDE_CONFIG_DIR` mapping, org-policy gating risk, and the existing `claude -p` stream-json bridge already covers the bridge surface. Re-evaluation criteria spelled out for future agents.

### Tests

- 261 → **265 passed**, 1 skipped, 1 warning. Four new tests:
  - `test_doctor_codex_account_falls_back_to_warning_when_binary_missing` — doctor never hard-fails when codex is unreachable.
  - `test_doctor_codex_account_parses_app_server_responses` — JSON-RPC initialize+account/read+account/rateLimits/read flow drives the warning entry correctly.
  - `test_idle_processes_are_evicted_after_ttl` — Claude per-channel processes drop after the configured idle TTL.
  - `test_lru_cap_evicts_least_recently_used_inactive_channel` — once over `CLADEX_CLAUDE_WORKER_MAX_LIVE`, the oldest inactive channel is evicted, the active channel is preserved.

### Validation

- `cmd /c npm ci`, `cmd /c npm audit` (0 vulnerabilities), `cmd /c npm run lint`, `cmd /c npm run build`, `cmd /c npm run api:smoke` (server contract smoke passed).
- Backend full suite `265 passed, 1 skipped, 1 warning`.
- `python backend/relayctl.py privacy-audit --tracked-only .` -> no findings.
- `python backend/cladex.py doctor --json` -> ok=True; new `codex-account` warning entry surfaces real account/rate-limit data when codex is logged in.

## 2.3.3 Polish Tranche (2026-04-28)

Three small residual items the prior verification rounds surfaced but didn't fix. All small, all bounded, no behavior change for the happy path.

- **F0089 atomic projects.json**: `_save_cladex_projects` and `_save_claude_registry` now write through `relayctl.atomic_write_text` (write-temp-then-rename) instead of plain `Path.write_text`. A crash mid-write can no longer leave a half-truncated registry/workgroups file.
- **F0058 Claude git status timeout**: `ClaudeBackend._git_status` was unbounded — a wedged git process or a slow network-mounted repo could hang the entire Claude turn. Now passes `timeout=` (default 30s, override `CLADEX_CLAUDE_GIT_STATUS_TIMEOUT`), uses `--untracked-files=no` to keep the call cheap on big repos, and treats `TimeoutExpired` as "no diff" with a warning log.
- **F0084 dashboard polling overlap**: the 5s `loadAll(silent=true)` interval could stack up if a refresh took longer than 5s (slow backend bootstrap, slow `Promise.all`). Added a `loadAllInFlight` ref guard so concurrent silent refreshes are dropped instead of queued.

### Validation

- `cmd /c npm ci`, `cmd /c npm audit` (0 vulnerabilities), `cmd /c npm run lint`, `cmd /c npm run build`, `cmd /c npm run api:smoke` (server contract smoke passed).
- Backend full suite still `261 passed, 1 skipped, 1 warning`.
- `python backend/relayctl.py privacy-audit --tracked-only .` -> no findings.
- `python backend/cladex.py doctor --json` -> ok=True.

## 2.3.2 Production Closeout (2026-04-28)

Audit after the 2.3.1 self-review fixes found the codebase healthy but the profile-create surface still lagged behind backend behavior.

### Fixes

- Desktop/API relay creation no longer requires a channel id when the requested relay is safely scoped by direct-message allowlists. Codex DM-only creation requires `allowDms=true` plus an approved user/operator id; Claude creation requires either a channel id or an approved user/operator id.
- Codex `register` now accepts explicit startup DM recipients (`--startup-dm-user-id` / `--startup-dm-user-ids`) and `--startup-channel-text`. The React create form now passes those fields through instead of the server warning and dropping them.
- Codex register no longer infers startup DM recipients from `--allowed-user-id` unless DMs are enabled. Explicit startup recipients still work when the operator deliberately sets them.
- Added `scripts/server-contract-smoke.cjs` plus `npm run api:smoke`, and CI now runs it after the frontend build.
- Updated README/INSTALL/plugin examples so public commands match the hardened allowlist rules.

### Validation

- `cmd /c npm ci` -> clean install, 0 vulnerabilities reported by npm audit output (npm deprecation warnings only).
- `.venv\Scripts\python.exe -m pip install -e "backend[dev]" -c backend\constraints.txt` -> `discord-codex-relay==2.3.2`.
- `cmd /c npm audit` -> 0 vulnerabilities.
- `cmd /c npm run lint`.
- `cmd /c npm run build`.
- `cmd /c npm run api:smoke` -> server contract smoke passed.
- `.venv\Scripts\python.exe -m pytest backend\tests --tb=short -q` -> `261 passed, 1 skipped, 1 warning`.
- `.venv\Scripts\python.exe backend\relayctl.py privacy-audit --tracked-only .` -> no findings.
- `.venv\Scripts\python.exe backend\cladex.py doctor --json` -> ok=True; Codex CLI `0.125.0`; Claude Code `2.1.117`; expected Codex PowerShell shim warning; no unsafe workspaces.
- CLI review/fix/backup smoke passed on a temp project: preflight review, review run, fix-plan generation, fix start, and backup create.
- `git diff --check` -> clean.
- `cmd /c npm run electron:build` -> produced `release\CLADEX Setup 2.3.2.exe`, `release\CLADEX 2.3.2.exe`, and `release\win-unpacked\CLADEX.exe`.

## 2.3.1 Post-2.3.0 Audit Closeout (2026-04-28)

Two-phase pass driven by the project's own review swarm running on itself.

### Phase A — six hard-confirmed bugs the 2.3.0 push left open (commit `95f9dc2`)

- **F0003** — `cladex.py:_update_claude_profile` called a bare `_save_registry` that was never defined, NameError'ing on every Claude profile edit. Added `_save_claude_registry`, switched the call site, regression test.
- **F0018** — `relay_runtime._verify_test_claim` shelled out via `cmd /c <command>` / `sh -lc <command>` with bot-supplied text. The cheap-prefix allowlist still let `;`/`&&`/`|`/backticks/`$()` through. Now: reject shell metacharacters, parse with `shlex`, require argv-head allowlist, run `subprocess.run(argv)` with no shell. Two regression tests.
- **F0034** — Codex `relayctl register --allow-dms` accepted an empty user allowlist. Mirrors the 2.2.2 fix on the Claude side; Codex now requires `--allowed-user-id` whenever `--allow-dms` is set.
- **F0060** — Claude bot rejected DMs whenever a guild channel allowlist was configured (regression I introduced in 2.2.2). The DM branch now runs above the channel allowlist gate; channel gate only applies to non-DM messages.
- **F0083** — Discord bot tokens flowed through subprocess argv (`tasklist`/`ps` visible). Token now flows via `CLADEX_REGISTER_DISCORD_BOT_TOKEN`; backend register/update reads the env, consumes (unsets) it, and refuses if neither argv nor env supplies a token. `cladex update-profile` gets `--discord-bot-token-env`.
- **F0014** — Codex CLI degraded fallback used unbounded `process.communicate()` then dumped the full stdout to disk. Replaced with bounded line-by-line read, `CLADEX_CODEX_FALLBACK_TIMEOUT` (default 600s), `CLADEX_CODEX_FALLBACK_MAX_OUTPUT_BYTES` (default 8 MiB), process termination on timeout/truncation.

Backend tests 247 → 254 passed.

### Phase B — verification review on the post-Phase-A code

10-agent Codex self-review (`review-20260428-174500-40782230`) against `95f9dc2`. Surfaced 83 findings (49 high / 27 medium / 7 low) — secret-hygiene line noise dominated as expected, but six new real bugs were addressed:

- **F0012 authz** — `claude_relay.interactive_setup` defaulted DMs on, allowed empty operator id and empty channel id, then wrote `ALLOW_DMS=true` with empty allowlists. Setup now requires a numeric operator id, defaults DMs OFF, and refuses empty-channel + empty-operator + DMs combinations the same way `cmd_register` does.
- **F0013/F0014 fix-scope** — `fix_orchestrator._workspace_change_snapshot` for git workspaces stored only the dirty path SET. A worker editing an already-dirty unrelated file slipped through `_changed_outside_assigned`. Snapshot now also hashes the contents of every dirty path so content edits to pre-existing dirty files are detected.
- **F0015 secret-exposure** — `relay_codex_env` previously did `dict(os.environ)` and passed it to Codex CLI subprocesses, exposing `DISCORD_BOT_TOKEN`, `CLADEX_REMOTE_ACCESS_TOKEN`, cloud creds, and any `*_TOKEN`/`*_KEY`/`*_SECRET` to the Codex agent and any shell command it spawns. New `_strip_relay_secrets` removes the well-known credential names plus generic suffix-matching keys before the env reaches the child.
- **F0038 scratch-io** — Review swarms could request 50 lanes × full workspace copy with no preflight. New `_scratch_disk_preflight` walks the workspace, estimates `bytes × (1 + agent_count)`, and refuses with a clear error if the projection exceeds `CLADEX_REVIEW_SCRATCH_MAX_BYTES` (default 16 GiB).
- **F0040/F0041 unsafe-execution noise** — `test_fix_orchestrator.py` triggered the line-pattern rule on intentional `eval(...)` test fixtures (same shape as `test_review_swarm.py` previously). Added `fix_orchestrator.py` and `test_fix_orchestrator.py` to `LINE_PATTERN_SKIP_FILENAMES` so the rule never flags its own tests.
- **F0044 remote-auth** — `isLoopbackRequest` accepted any loopback socket with no forwarded headers as local. A local reverse proxy could drop `Origin` and `X-Forwarded-*` while forwarding off-host traffic to 127.0.0.1, getting `/api/runtime-info` to leak the token. Now also requires the `Host` header to be loopback and adds `cf-connecting-ip` / `x-real-ip` / `true-client-ip` to the proxy-signal list.

Backend tests 254 → 258 passed (+4).

### Validation

- `cmd /c npm ci`, `cmd /c npm audit` (0 vulnerabilities), `cmd /c npm run lint`, `cmd /c npm run build`.
- `python -m pip install -e "backend[dev]" -c backend/constraints.txt` -> `discord-codex-relay==2.3.1`.
- Backend full suite `258 passed, 1 skipped, 1 warning`.
- `python backend/relayctl.py privacy-audit --tracked-only .` -> no findings.
- `python backend/cladex.py doctor --json` -> ok=True.

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
- Electron packaging now passes `--publish never` so CI verifies installer/portable creation without requiring `GH_TOKEN` or attempting an implicit GitHub release publish.
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
