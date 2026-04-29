# CLADEX Done Roadmap

Items that started life on `ROADMAP.md` and have shipped. Newest tranches first. The active work-in-progress list lives in [ROADMAP.md](ROADMAP.md); release-by-release narrative lives in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

## Completed For 2.5.1

Closed the post-2.5.0 verification/self-review findings and hardened the new Fix Review path for production use.

- **Review/Fix Review execution safety.** Review subprocesses now use output-aware idle timeout with a generous initial silent grace, async stdin writing for large prompts, and a max-runtime ceiling. Fix Review now retries transient scope snapshots, forbids stash/reset/checkout hiding in worker prompts, exposes restore commands, supports cancel-aware validation, and keeps CLADEX self-fix behind a separate explicit flag.
- **Backend reliability and resource bounds.** Codex attachment downloads are bounded and cleaned up on rejection; startup notices are best-effort; channel-history/workspace inputs are validated before persistence; Claude stream capture, inbound queues, installer subprocesses, Codex fallback cleanup, and log tails are bounded.
- **Security and policy.** Existing/manual Claude profiles fail closed at runtime when allowlists are empty; CI pins required provider CLI versions; `cladex doctor` enforces declared Node/npm/Python floors; the bundled relay-management skill no longer allows implicit mutating invocation.
- **Review dashboard.** Polling uses per-request timeouts and partial-refresh handling; cancelled reviews with partial findings can be browsed/exported; 50-lane swarms expose all lanes through an expander; `npm run frontend:smoke` covers these workflows and runs in CI.
- **Release metadata.** Package/backend/plugin/server/docs metadata aligned to 2.5.1, `package-lock.json` regenerated, and install docs clarify packaged release assets versus locally built `release/` files.

## Completed For 2.5.0

The "Fix Review orchestrator" line on the roadmap is now done. Summary in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

- **AI orchestrator behind the Fix Review button.** `start_fix_run` now invokes `_ai_plan_fix_tasks`, which builds a workspace inventory, asks the upstream provider (`codex` or `claude`) for a structured plan via JSON Schema, and emits per-task `provider`, `reasoningEffort`, `phase`, `dependsOn`, and `rationale`. The orchestrator's `recommendedAgentCount` caps parallelism (capped further by `min(operator_max_agents, recommended)`).
- **Deterministic fallback preserved.** When the planner subprocess errors, returns nothing, or is intentionally disabled (`CLADEX_FIX_PLANNER_DISABLE=1` for tests), the existing 1:1 mapping runs as `_deterministic_fix_tasks` and the run records why in `plan.fallbackReason`. Existing behavior is therefore preserved on regression.
- **Residual safety net.** If the planner ever silently drops a finding, the orchestrator appends a catch-all task so every finding from the upstream review still reaches a fix worker.
- **Frontend visibility.** The Fix Review card now shows the orchestrator decision: source (AI vs deterministic), recommended agent count, summary text, rationale, plus per-task badges for provider, effort, phase, severity, category, dependency chain, and rationale. Fallback reasons render in their own warning band.
- **Tests.** Three orchestrator-specific tests added; suite is green at 268 passing.

## Completed For 2.4.0

Closes the four "non-blocking future work" items previously listed on the roadmap, leaving nothing else to do.

- **Provider-native Codex account / rate-limit / model surfacing.** `cladex doctor --json` now spawns `codex app-server`, sends `initialize` + `account/read` + `account/rateLimits/read` over JSON-RPC, parses the responses, and surfaces account type / plan / rate-limit windows in the doctor warnings list. Reachability errors collapse to a soft warning (never a hard fail) so a non-Codex install still passes doctor.
- **Supervisor pooling — Claude worker lifecycle.** Per-channel Claude subprocesses now have an idle TTL eviction (`CLADEX_CLAUDE_WORKER_IDLE_TTL`, default 1800 s) and an LRU live-process cap (`CLADEX_CLAUDE_WORKER_MAX_LIVE`, default 16). Active channels are never evicted while serving a turn; least-recently-used inactive channels release their process when the cap is exceeded so a multi-channel relay no longer accumulates processes forever.
- **Interactive review findings filter + export.** New `GET /api/reviews/:id/findings` endpoint and `cladex review findings <id>` CLI surface the structured findings JSON. The desktop Review Project view ships a "Findings explorer" expander on completed/failed jobs with severity toggles, category dropdown, an "Export JSON" download button, and an inline list of the matching findings (capped at 200 with an "export for the full set" hint).
- **Claude Code Channels evaluation.** Decision recorded: do not adopt as a Claude transport in 2.4.0. Full rationale and re-evaluation criteria in [backend/docs/CLAUDE_CHANNELS_EVALUATION.md](backend/docs/CLAUDE_CHANNELS_EVALUATION.md). Channels stays in research preview, requires Claude.ai login, doesn't map cleanly onto our per-profile `CLAUDE_CONFIG_DIR` model, and the existing `claude -p` stream-json bridge already covers the bridge surface.

## Completed For 2.3.3

Three small residuals from the verification reviews that earlier tranches didn't reach.

- F0089: `_save_cladex_projects` and `_save_claude_registry` now use atomic write-temp-then-rename via `relayctl.atomic_write_text` so a crash mid-write cannot leave a half-truncated registry/workgroups file.
- F0058: `ClaudeBackend._git_status` is bounded by a 30s timeout (override `CLADEX_CLAUDE_GIT_STATUS_TIMEOUT`) and uses `--untracked-files=no` so a wedged git or large untracked tree no longer hangs a Claude turn.
- F0084: dashboard `loadAll` polling guards against concurrent silent refreshes via a `loadAllInFlight` ref so a slow backend cannot queue a backlog of polls.

## Completed For 2.3.2

Final production closeout after the 2.3.1 verification sweep.

- Fixed the desktop/API profile-create contract so scoped DM-only relay creation works instead of forcing a channel id. Codex still requires `--allow-dms` plus at least one approved user id; Claude requires a channel or approved user/operator allowlist.
- Added Codex `register` support for explicit startup DM recipients (`--startup-dm-user-id` / `--startup-dm-user-ids`) and startup channel text (`--startup-channel-text`) so fields exposed by the React create form are persisted instead of ignored. Startup DM recipients are no longer inferred from `--allowed-user-id` unless DMs are enabled.
- Added a Node API contract smoke gate (`npm run api:smoke`) and wired it into CI so profile-create access validation cannot silently drift again.
- Updated public install/README/plugin examples so Codex DM examples include `--allowed-user-id`, matching the hardened CLI behavior.

## Completed For 2.3.1

Audit closeout for the 2.3.0 push, driven by re-running the project's own review swarm on itself.

- **Phase A — six bugs the 2.3.0 push left open:**
  - F0003: `cladex.py` Claude profile updates called undefined `_save_registry` and crashed on every Claude profile edit. Added `_save_claude_registry`.
  - F0018: `_verify_test_claim` shelled out via `cmd /c` / `sh -lc` with bot-supplied claim text → real command-injection surface. Replaced with shlex-based argv allowlist + no-shell exec.
  - F0034: Codex `register --allow-dms` accepted empty user allowlists. Now requires `--allowed-user-id` when `--allow-dms` is set.
  - F0060: Claude bot rejected DMs whenever a guild channel allowlist was set. Channel gate now only applies to non-DM messages.
  - F0083: Discord bot tokens passed through subprocess argv (visible in `tasklist`/`ps`). Tokens now flow via `CLADEX_REGISTER_DISCORD_BOT_TOKEN` env; consumed-and-unset.
  - F0014: Codex CLI degraded fallback used unbounded `process.communicate()`. Bounded read with timeout (`CLADEX_CODEX_FALLBACK_TIMEOUT`) and output cap (`CLADEX_CODEX_FALLBACK_MAX_OUTPUT_BYTES`).
- **Phase B — fixes from the verification review:**
  - F0012: `claude_relay.interactive_setup` could create overexposed relays (DMs default-on, empty allowlists). Setup now defaults DMs off, requires numeric operator id, refuses empty-channel + empty-operator + DMs.
  - F0013/F0014 fix-scope: `_workspace_change_snapshot` now content-hashes already-dirty paths so a worker editing a pre-existing dirty file is detected by the assigned-file enforcement.
  - F0015: `relay_codex_env` strips inherited credentials (Discord bot token, CLADEX remote token, AWS/Anthropic/OpenAI/etc. keys, generic `*_TOKEN`/`*_KEY`/`*_SECRET`) before passing env to the Codex CLI subprocess.
  - F0038: review swarms now run a `_scratch_disk_preflight` that estimates `bytes × (1 + agent_count)` and refuses runs that exceed `CLADEX_REVIEW_SCRATCH_MAX_BYTES` (default 16 GiB).
  - F0040/F0041: `fix_orchestrator.py` and its tests now in `LINE_PATTERN_SKIP_FILENAMES` so the line scanner doesn't flag intentional rule-fixture eval/exec calls.
  - F0044: `isLoopbackRequest` now also requires the Host header to be loopback and treats `cf-connecting-ip`/`x-real-ip`/`true-client-ip` as proxy signals so a local reverse proxy can't leak the remote token.

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
