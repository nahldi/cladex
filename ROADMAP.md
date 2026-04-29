# CLADEX Production Roadmap

CLADEX stays focused on Claude Code and OpenAI Codex relays. Shipped work is tracked in [DONE_ROADMAP.md](DONE_ROADMAP.md); release narrative lives in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

As of **2.5.1**, the April 2026 roadmap is closed: production-blocking and follow-up verification items have either shipped or have an explicit recorded decision.

- Clone-to-run setup, review swarms, Fix Review orchestration, local security, CI gates, and visible queue/concurrency limits all shipped (2.1.0 through 2.3.3).
- Provider-native Codex account/rate-limit surfacing is wired into `cladex doctor --json` via the app-server `account/read` and `account/rateLimits/read` RPCs (2.4.0).
- Claude relay subprocess pooling now ships with idle TTL + LRU cap on per-channel persistent processes (`CLADEX_CLAUDE_WORKER_IDLE_TTL`, `CLADEX_CLAUDE_WORKER_MAX_LIVE`) so a multi-channel relay no longer accumulates processes forever (2.4.0).
- Interactive review-finding filter (severity, category) + JSON export ship in the desktop Review Project view, backed by `GET /api/reviews/:id/findings` and `cladex review findings <id>` (2.4.0).
- Claude Code Channels was evaluated and explicitly **not adopted**. Rationale and re-evaluation criteria are documented in [backend/docs/CLAUDE_CHANNELS_EVALUATION.md](backend/docs/CLAUDE_CHANNELS_EVALUATION.md) (2.4.0).
- Fix Review orchestration is now AI-driven: an upstream Codex/Claude planner subprocess decides how many fix workers to spawn, what type each one should be, and which findings each one owns, with a per-task `dependsOn` graph and a deterministic 1:1 fallback when the planner subprocess fails (2.5.0).
- Post-2.5.0 verification hardening shipped in 2.5.1: review subprocess idle-timeout behavior, Fix Review scope/cancel/restore/self-fix safety, backend resource bounds, profile validation, pinned provider CLI CI, runtime-version doctor gates, explicit relay-management skill invocation, resilient review dashboard polling, partial cancelled-review findings, 50-lane visibility, and release metadata alignment.

## Current Production Contract

- Users bring their own locally installed and logged-in `codex` and `claude` CLIs.
- Relay creation supports either channel-scoped operation or scoped direct-message operation. Codex DM relays require `--allow-dms` plus an approved `--allowed-user-id`; Claude relays require at least a channel or approved user/operator allowlist.
- Review swarms accept 1-50 requested lanes and queue them behind the configured machine/account worker limit. `CLADEX_REVIEW_MAX_PARALLEL` controls reviewer subprocess parallelism.
- Fix Review creates a mandatory backup, runs an AI orchestrator that decides how many fix workers to spawn and which provider (Codex vs Claude) handles each task, records the orchestrator plan + per-task rationale + dependency graph alongside the run, runs provider workers against the selected project, records validation results, and exposes the exact CLI restore command on failure.
- Fix Review is idempotent for active runs: repeated starts for the same review return the active run instead of launching duplicate write workers.
- CLADEX self-review/self-fix remains explicit. Normal relays and review/fix jobs are still blocked from targeting the CLADEX runtime repo unless the operator opts into CLADEX development mode; write-capable self-fix requires a separate self-fix approval after self-review.
- Per-channel Claude subprocesses are evicted by idle TTL and capped by an LRU live-process limit so a multi-channel relay does not accumulate processes forever.
- `cladex doctor --json` reports Codex account type/plan and rate-limit windows when the Codex app-server is reachable, and surfaces it as a soft warning (never a hard fail) when it isn't.
- The desktop Review Project view exposes interactive findings filtering (severity + category) and JSON export per review job.
- No release claim promises unlimited provider capacity. Codex/Claude account plans, local CPU/RAM, Discord limits, and provider rate limits still control how much real work can run at once.

## Open Roadmap Items

None. Every item from the April 2026 roadmap, including the previously non-blocking enhancements, has shipped or has an explicit recorded decision in [DONE_ROADMAP.md](DONE_ROADMAP.md) or [backend/docs/](backend/docs/).

Future enhancements should be opened as new roadmap entries with their own scope, not picked up from a stale "future" list.

## Release Gates

- `cmd /c npm ci`
- `cmd /c npm audit`
- `cmd /c npm run lint`
- `cmd /c npm run frontend:smoke`
- `cmd /c npm run build`
- `cmd /c npm run api:smoke`
- `py -m pip install -e "backend[dev]" -c backend\constraints.txt`
- `py backend\relayctl.py privacy-audit --tracked-only .`
- `py -m pytest --tb=short -q` from `backend/`
- `py backend\cladex.py doctor --json`
- CLI smoke for review/fix/backup/findings
- API smoke for review/fix/backup/findings
- `cmd /c npm run electron:build`

The public repo must contain no personal profile env files, auth homes, relay logs, local memory, generated release output, or user-specific paths.
