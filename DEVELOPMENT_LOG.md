# CLADEX Development Log

This file is tracked on purpose. It gives future Claude/Codex agents a concise source of truth for what has been done, what is in progress, and what still needs care. Runtime-only memory files under `memory/` are useful locally, but GitHub users and new agents need this public handoff too.

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
