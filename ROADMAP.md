# CLADEX Production Roadmap

CLADEX stays focused on Claude Code and OpenAI Codex relays. The current production path is to keep clone-to-run setup clean, keep model/account behavior provider-led, and add scaling only after compatibility and load tests prove the lower layers.

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

## Remaining Work

- Advance Project Review from the safe foundation to a full production repair loop:
  - planner shards by package boundaries, recent git hotspots, test surfaces, and security-sensitive paths instead of only deterministic file distribution,
  - reducer de-duplicates findings by evidence, agent focus, and recommended fix as well (current 2.2.1 reducer is title/path/line/category exact-match),
  - live AI review lanes should emit validated structured JSON findings with command-attempt evidence,
  - expose queue/concurrency controls and clearer rate-limit/account-pressure reporting,
  - UI should grow retry/export, richer interactive severity/category/agent filtering, and a snapshot restore button gated behind explicit confirmation (CLI restore stays the source of truth).
- Add a guarded fix phase for review reports:
  - one planner converts findings into ordered fix phases,
  - user approval is required before edits,
  - write workers edit only assigned project workspaces/worktrees,
  - source backup is mandatory before edits,
  - planner validates diffs and tests between phases before continuing,
  - restore command remains explicit and confirmation-gated.
- Build the full supervisor/queue/account-pooling runtime:
  - pool Discord clients by bot token where safe,
  - pool Codex app-server workers by account home where safe,
  - launch Claude workers on demand with idle shutdown,
  - add per-account and global concurrency limits.
- Expand provider fake tests from registry/load checks to full queued-turn simulations.
- Surface Codex account/rate-limit/model discovery from app-server RPCs in profile health and the UI.
- Add remote-token rotation/revoke controls.
- Add Discord gateway, invalid-request, and backpressure metrics.
- Evaluate Claude Code Channels as an optional Claude transport only after preview stability and access-control behavior are proven.

## Release Gates

- `cmd /c npm ci`
- `cmd /c npm audit`
- `cmd /c npm run lint`
- `cmd /c npm run build`
- `py -m pip install -e "backend[dev]" -c backend\constraints.txt`
- `py backend\relayctl.py privacy-audit --tracked-only .`
- `py -m pytest --tb=short -q` from `backend/`
- `py backend\cladex.py doctor --json`
- `cmd /c npm run electron:build`

The GitHub CI mirrors the source validation path and runs backend tests across Python 3.10, 3.11, and 3.12.
The public repo must contain no personal profile env files, auth homes, relay logs, local memory, generated release output, or user-specific paths. Users bring their own locally installed and logged-in `codex` and `claude` CLIs.
