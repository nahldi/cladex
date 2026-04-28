# CLADEX Done Roadmap

Items that started life on `ROADMAP.md` and have shipped. Newest tranches first. The active work-in-progress list lives in [ROADMAP.md](ROADMAP.md); release-by-release narrative lives in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

## Completed For 2.3.0

- Added guarded **Fix Review** runs. Completed review jobs can now start a durable fix run with a mandatory source backup, generated task plan, phase validation, cancellation, markdown report, and structured `fix_run.json` state under the local CLADEX data directory.
- Added `backend/fix_orchestrator.py` plus `cladex fix list/start/show/run/run-task/cancel`. Fix tasks map directly to finding ids and use the selected provider account home when launching Codex or Claude workers.
- Added fix-run API endpoints: `POST /api/reviews/:id/fix`, `GET /api/fix-runs`, `GET /api/fix-runs/:id`, and `POST /api/fix-runs/:id/cancel`.
- Added the desktop Fix Review surface: completed and completed-with-warning review cards show **Fix Review**, fix runs poll with the rest of the Review Project view, progress includes queued/running/done/failed/cancelled counts, and the UI shows backup/report/artifact paths plus task summaries.
- Made active Fix Review starts idempotent per review job so duplicate clicks/API calls return the existing active run instead of launching parallel write workers against the same workspace.
- Serialized write-capable fix phases in the shared workspace and reject successful task runs that edit outside their assigned files. This keeps Fix Review conservative until per-task worktree isolation and merge orchestration are added.
- Added a per-review `CLADEX_REVIEW_COORDINATION.md` artifact with a project briefing, lane assignments, and per-agent sections. Reviewer prompts now tell lanes to treat the target as an unknown project, use their assigned coordination section, and avoid duplicate work.
- Isolated AI review lanes into per-agent scratch workspaces and skipped symlinks from AI scratch copies so reviewer commands cannot follow workspace symlinks outside the selected project. Source backups still preserve safe source structure while skipping local credential material.
- Moved Claude review and fix prompts through stdin with short stable command prompts so large project instructions do not fail on Windows command-line length limits.
- Changed mixed AI lane failures from silent `completed` to explicit `completed_with_warnings`; all-lane failures still report `failed`. Reports now show `seenByAgents` for deduplicated findings.
- Added protected CLADEX self-fix gating. A Fix Review run against the CLADEX repo requires the originating review job to have been explicitly started as a CLADEX self-review and a separate `--allow-cladex-self-fix`/UI approval.
- Added cancellation-aware validation execution for Fix Review and exposed exact restore commands through fix-run API/UI/report output.
- Added review limit metadata. Jobs now expose `maxParallel`, `limitWarnings`, and `limits` so the UI can explain that 1-50 requested lanes queue behind the configured worker/account limit instead of implying every lane launches at once.
- Hardened the local API: strict integer parsing keeps `agents: 0` invalid, string booleans are parsed deliberately, invalid/missing review/fix ids return 400/404 style responses, and CORS allows `X-CLADEX-Access-Token` for legitimate token preflights.
- Hardened release validation and packaging: CI now includes `cladex doctor --json` and Electron packaging jobs; backend package metadata includes `fix_orchestrator`, `README.md`, and `constraints.txt`.
- Updated release metadata to 2.3.0 across package, backend, plugin manifests, runtime-info fallback, and install docs.

## Completed For 2.2.3

- Frontend security: file-mode `?apiBase=` is now validated against an http(s) loopback allowlist before use, and `X-CLADEX-Access-Token` is only attached to same-origin or loopback fetches.
- Frontend UX: Review Project tracks Codex/Claude account-home values separately and submits only the one matching the active provider; Review and "Save snapshot only" buttons disable while in flight or when the workspace is empty.
- Review correctness: report markdown is rendered after the final job state is saved (no more `Status: running` on completed reports), AI-finding paths normalize against both the original workspace and the scratch copy, traversal segments collapse to `.`, and `_review_artifact_ignore` no longer drops symlinks unconditionally so backups round-trip correctly.
- Review concurrency: `run_review_job` now takes a per-job exclusive run lock (PID-aware reclaim of stale locks), short-circuits if the job is already finished, and runs AI lanes through `Popen` with a 1-second cancel-poll loop so cancellation terminates the in-flight subprocess instead of waiting on the timeout.
- Review noise: smarter `has_secret_token_segment` only flags credential-prefix + token compounds (`auth-token`, `api-key`) so framework files like `vite-env.d.ts` stop tripping the scanner; line-pattern rules skip docs/config (`.md`, `.toml`, `.yaml`, …) and the rule-definition files (`review_swarm.py`, `test_review_swarm.py`).
- Backend correctness: `install_plugin._ensure_runtime` always passes `-c constraints.txt` when the file is present so end-user runtime installs match CI; `_codex_login_status` runs with a 15s timeout and surfaces structured errors instead of hanging; Claude profile readiness now requires a non-error `status.json` rather than a bare PID; channel-history bootstrap caps the raw scan at `max(limit*10, 200)` and treats `channel_history_limit=0` as the default 20 unless `RELAY_UNLIMITED_HISTORY_SCAN=1` is explicitly set.
- API contract: `server.cjs` profile-create no longer forwards the unsupported `--startup-channel-text` flag and no longer mis-maps `startupDmUserIds` to the DM allowlist. Operator/user IDs deduplicate before they reach `relayctl register`.
- Crash-recovery: `claude_bot` reclaims orphaned `.processing` operator requests at startup so callers never wait for a response a crashed worker will never deliver. `relay_common.replace_directory` rolls back to its backup if the temp swap fails so plugin/asset installs cannot leave the destination missing.
- CI hardening: `.github/workflows/ci.yml` sets a top-level `permissions: contents: read`. Vite dev server proxies `/api` to the backend port so `npm run dev:stack` works without a `VITE_API_BASE` override.

## Completed For 2.2.2

- Fixed `claude-discord run` launching the Codex bot (`backend/bot.py`) instead of the Claude bot (`backend/claude_bot.py`). The packaged manager already routed correctly; only the documented direct-CLI path was broken.
- Added `review_swarm` and `api_runner` to `[tool.setuptools].py-modules` in `backend/pyproject.toml`. Non-editable wheel/sdist installs now actually carry the modules `cladex.py` imports at startup.
- Hardened the local API auth gate against `Host` / `X-Forwarded-Host` spoofing. `isLoopbackRequest` now derives loopback status from `req.socket.remoteAddress` and treats the presence of any `X-Forwarded-*` header as a proxy signal that requires the remote token. `/api/runtime-info` no longer hands out the remote access token to clients that merely claim to be local.
- Required at least one allowed channel or allowed user when registering a Claude relay, and required a user/operator allowlist when `--allow-dms` is set. Empty allowlists are now rejected with exit code 2 and a clear error.
- Drained Claude subprocess stderr in the background to remove the deadlock window where stderr-heavy output could block the persistent process. On turn timeout the process is now terminated so the next turn starts on a fresh subprocess. Last 50 stderr lines are kept as a bounded tail for diagnostics.
- Returned structured AI reviewer outcomes from `review_swarm._run_cli`. Timeout, missing binary, and nonzero exit now mark the lane `failed` with the underlying error instead of being silently wrapped as an "Unstructured reviewer notes" finding.
- Stripped inherited credentials from AI reviewer subprocesses. Lanes now run with a small allowlisted environment (PATH, locale, temp dirs, account-home overrides) instead of `os.environ.copy()`. Removed `Bash` from Claude reviewer tools since `--permission-mode dontAsk` plus Bash could write outside the scratch workspace.
- Broadened `_review_artifact_ignore` to skip common local-credential files and directories (`.npmrc`, `.pypirc`, `.netrc`, `.git-credentials`, `.ssh`, `.aws`, `.gnupg`, `.kube`, etc.) so source backups and reviewer scratch trees don't carry host secrets.
- Bootstrapped the managed Python backend runtime on the first `server.cjs` `runPython` call. A fresh packaged install now installs the bundled backend into `%LOCALAPPDATA%\discord-codex-relay\runtime\` automatically; subsequent calls short-circuit. `CLADEX_SKIP_BACKEND_BOOTSTRAP=1` opts out.

## Completed For 2.2.1

- Dropped the dead `claude-code-sdk` runtime dependency from `backend/pyproject.toml` and regenerated `backend/constraints.txt`. Trims 27 transitive packages (`mcp`, `pydantic*`, `httpx*`, `starlette`, `uvicorn`, `cryptography`, `cffi`, `pycparser`, `jsonschema*`, etc.) that were pulled in only for the SDK chain. Source has used a subprocess-based Claude transport for several releases, so no import path needs the package.
- Stopped the project review swarm from flagging template/sample env files. `.env.example`, `.env.template`, `.env.sample`, `secrets.example.json`, and similar conventional placeholders are now allowlisted (any `.example/.sample/.template/.tmpl/.dist` segment) while the underlying secret-filename match still triggers on `.env`, `secrets.json`, etc.
- Tightened the TODO/FIXME/HACK/XXX maintenance marker rule. Substring matches like `podcast`, `shack`, doc string mentions of "todo", or marker tokens inside string literals no longer fire. The rule now requires a word boundary around the marker AND a comment context (`#`, `//`, `/*`, `<!--`, `;`, `-- `, `* `).
- Added cross-lane finding deduplication. Findings sharing category + path + line + title from multiple reviewer lanes collapse to a single entry that lists every contributor in `seenByAgents` and is promoted to the highest severity any contributor reported.
- Added review job cancellation. `cladex review cancel <id>` (CLI) and `POST /api/reviews/:id/cancel` (API) write a per-job `cancel.flag` file. Queued jobs are marked cancelled immediately; running jobs stop launching further AI lanes and finalize as `cancelled`. Cancel control surfaced in the desktop UI alongside Fix Plan.
- Surfaced severity counts on review jobs. Backend `_public_job` now returns `severityCounts: {high, medium, low}` and the desktop UI renders colored severity pills on the review job card once any finding has been recorded.
- Wired the backup management UI that was previously plumbed only at the API layer. Desktop `Review Project` view now lists CLADEX-managed source snapshots, polls them with the rest of state, and exposes a "Save snapshot only" button next to "Review Project". Restore stays CLI-only and confirmation-gated.
- Hardened `_atomic_write_text` against transient Windows `PermissionError` during concurrent rename. Five-attempt 50ms backoff covers the race window, then re-raises so a real lockout still surfaces fast.

## Completed For 2.2.0

- Updated GitHub Actions workflow references to official Node 24-based action majors (`actions/checkout@v6`, `actions/setup-node@v6`, and `actions/setup-python@v6`) after verifying the action metadata.
- Added the Project Review swarm foundation:
  - desktop `Review Project` view with folder picker, Codex/Claude selector, account-home field, explicit CLADEX self-review toggle, source-backup toggle, and a 1-50 reviewer slider,
  - API and CLI review job commands,
  - durable job state under the local CLADEX data directory,
  - deterministic file sharding, generated/vendor/secret-heavy folder skips, internal preflight heuristics, and per-lane progress,
  - distinct reviewer lane focuses covering security, runtime, testing, concurrency, backend, frontend, release, dependencies, performance, and data integrity,
  - AI reviewer execution against a CLADEX-owned scratch copy where possible so smoke/stress experiments do not touch the selected source tree,
  - bounded AI reviewer concurrency by default so 1-50 lane jobs queue safely instead of bursting every lane at once,
  - one universal `CLADEX_PROJECT_REVIEW.md` report plus structured `findings.json`,
  - a separate `CLADEX_FIX_PLAN.md` generator that does not edit source.
- Added source snapshot support:
  - review jobs can create local snapshots under the CLADEX data directory,
  - CLADEX self-review requires explicit opt-in and creates a backup before the job is launched,
  - CLI backup commands can list, create, and restore snapshots with exact-id confirmation,
  - restore removes stale nested source files while preserving ignored dependency/cache folders and secret-like local files.
- Kept review workers from applying fixes. Codex and Claude review lanes use a scratch workspace and no approval escalation; Claude write/edit tools are disabled while Bash remains available for safe validation commands in scratch.
- Validated review/backup ids before mapping them into local artifact paths.
- Tightened protected-root parsing so `CLADEX_PROTECTED_ROOT` and `CLADEX_PROTECTED_ROOTS` combine instead of overriding each other.

## Completed For 2.1.1

- Added shared workspace guardrails for Codex and Claude profiles. Registration, profile update, GUI/CLI start, and doctor now block workspaces that overlap the CLADEX runtime repo unless explicitly enabled for CLADEX development.
- Added compact workspace-local rule/skill discovery to Codex and Claude prompt context so agents see AGENTS/CLAUDE files, Codex skills, Claude subagents, and slash commands without dumping full files every turn.
- Added `cladex doctor` profile health checks for unsafe workspaces, duplicate Codex app-server ports, and shared account homes.
- Added a tracked-file privacy gate with `codex-discord privacy-audit --tracked-only .`; CI now fails on committed profile env files, logs, personal path literals, or other public-repo hygiene leaks.
- Documented that CLADEX never ships maintainer Codex/Claude credentials. Each user must install and authenticate their own `codex` and `claude` CLIs.

## Completed For 2.1.0

- Modernized the desktop stack and CI baseline for Node 22, npm 10, Vite 8, Express 5, React 19, Electron 41, and TypeScript 6.
- Removed stale model pins. Blank model fields now let installed Claude/Codex CLIs choose their defaults.
- Switched to safer default permissions: Codex profiles default away from full bypass, and Claude profiles default to normal permission mode unless explicitly changed.
- Added per-profile account homes:
  - Codex: `CODEX_HOME` / `codexHome`
  - Claude: `CLAUDE_CONFIG_DIR` / `claudeConfigDir`
- Scoped remote filesystem browsing for non-loopback callers to saved profile workspaces/account homes and explicit `CLADEX_REMOTE_FS_ROOTS`.
- Added Discord mention suppression defaults with `AllowedMentions.none()` for relay-authored sends/replies.
- Added `cladex doctor --json` checks for Node, npm, Python, Claude, Codex, Codex app-server schema generation, profile port collisions, and Windows PowerShell shim warnings.
- Added Codex app-server payload builders and schema-summary fixture tests for the stable thread/turn request shapes.
- Added fake high-count coverage for 100 isolated Codex profiles with unique account homes and collision-free app-server ports.
- Removed stale source entrypoints: `server.ts` and `electron/main.ts`. Runtime and packaging use `server.cjs` and `electron/main.cjs`.
- Added Python dependency constraints for reproducible local and CI installs.
</content>
</invoke>
