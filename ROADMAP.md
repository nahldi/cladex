# CLADEX Production Roadmap

CLADEX stays focused on Claude Code and OpenAI Codex relays. Shipped work is tracked in [DONE_ROADMAP.md](DONE_ROADMAP.md); release narrative lives in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

As of 2.3.2, the production-blocking roadmap from the April 2026 audit is complete for clone-to-run setup, review swarms, guarded Fix Review, local security, CI gates, profile create flows, and visible queue/concurrency limits.

## Current Production Contract

- Users bring their own locally installed and logged-in `codex` and `claude` CLIs.
- Relay creation supports either channel-scoped operation or scoped direct-message operation. Codex DM relays require `--allow-dms` plus an approved `--allowed-user-id`; Claude relays require at least a channel or approved user/operator allowlist.
- Review swarms accept 1-50 requested lanes and queue them behind the configured machine/account worker limit. `CLADEX_REVIEW_MAX_PARALLEL` controls reviewer subprocess parallelism.
- Fix Review creates a mandatory backup, converts review findings into fix tasks, runs provider workers against the selected project, records validation results, and exposes the exact CLI restore command on failure.
- Fix Review is idempotent for active runs: repeated starts for the same review return the active run instead of launching duplicate write workers.
- CLADEX self-review/self-fix remains explicit. Normal relays and review/fix jobs are still blocked from targeting the CLADEX runtime repo unless the operator opts into CLADEX development mode; write-capable self-fix requires a separate self-fix approval after self-review.
- No release claim promises unlimited provider capacity. Codex/Claude account plans, local CPU/RAM, Discord limits, and provider rate limits still control how much real work can run at once.

## Non-Blocking Future Work

These are product enhancements, not release blockers:

- Provider-native account/rate-limit dashboards using Codex/Claude APIs when those local surfaces are stable enough to rely on.
- A deeper supervisor rewrite that pools Discord clients and provider workers across many always-on relay profiles. Current CLADEX handles high-count review swarms through bounded queues and serializes write-capable fix phases for safety; live relay pooling remains an optimization, not a prerequisite for safe use.
- Interactive report filtering/export/retry controls beyond the current markdown/JSON artifacts and progress cards.
- Optional Claude Code Channels evaluation once preview stability, multi-profile behavior, and org-policy controls are proven.

## Release Gates

- `cmd /c npm ci`
- `cmd /c npm audit`
- `cmd /c npm run lint`
- `cmd /c npm run build`
- `cmd /c npm run api:smoke`
- `py -m pip install -e "backend[dev]" -c backend\constraints.txt`
- `py backend\relayctl.py privacy-audit --tracked-only .`
- `py -m pytest --tb=short -q` from `backend/`
- `py backend\cladex.py doctor --json`
- CLI smoke for review/fix/backup
- API smoke for review/fix/backup
- `cmd /c npm run electron:build`

The public repo must contain no personal profile env files, auth homes, relay logs, local memory, generated release output, or user-specific paths.
