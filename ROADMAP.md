# CLADEX Production Roadmap

CLADEX stays focused on Claude Code and OpenAI Codex relays. The current production path is to keep clone-to-run setup clean, keep model/account behavior provider-led, and add scaling only after compatibility and load tests prove the lower layers.

Everything that has shipped is tracked in [DONE_ROADMAP.md](DONE_ROADMAP.md). This file is only the remaining work, ordered by what unlocks what.

---

## 1. Fix Review (orchestrator + button)

The current Project Review swarm finds problems but does not fix them. The user-visible deliverable here is a single **Fix Review** button that appears next to the existing Fix Plan / Cancel controls on a completed review job card and turns the swarm's findings into actual source edits, end to end.

### Behavior the user sees

- Run the review swarm against any project (existing flow). Lanes report findings into the universal `CLADEX_PROJECT_REVIEW.md` and `findings.json`.
- When the job's status is `completed`, a **Fix Review** button appears on the job card. (Hidden on `running`, `queued`, `failed`, `cancelled` jobs.)
- Clicking **Fix Review** does the following without further user dialog beyond the initial confirm:
  1. Spawns a single high-reasoning **orchestrator agent**.
  2. Orchestrator reads `findings.json` plus the project shape (file inventory, `package.json`/`pyproject.toml`/`Cargo.toml`/etc., language mix, test targets) and decides the best route to fix everything.
  3. Orchestrator outputs a structured **fix plan** that names every change as one or more **fix tasks**, with for each task: target file(s), provider choice (Codex or Claude — chosen per task based on task fit, project language, and currently-available provider authentication), reasoning effort, validation command, dependency on earlier tasks.
  4. Orchestrator decides how many concurrent **fix-worker agents** to spawn, and which provider per worker.
  5. Workers execute their assigned tasks against the project workspace (or a per-task git worktree) with a mandatory source backup taken first.
  6. After each phase, the orchestrator runs the validation commands the project ships (`npm run lint`, `npm run build`, `pytest`, `cargo test`, etc.) and either advances to the next phase or asks a worker to repair.
  7. On a phase that fails to validate after the planned repair budget, the orchestrator stops, reports what was tried, and offers `cladex backup restore`.

### Why this design

- One button — no project-specific configuration knobs. Every project gets the same flow.
- The orchestrator decides Codex vs Claude per task because each is better at different things (Codex for code-grounded refactors with shell access; Claude for narrative reasoning, tests, and docs). The user does not pick.
- The orchestrator decides agent count because a project with 4 high-severity bugs in one file is one task, but a project with 30 medium findings spread across 12 files can fan out.
- The orchestrator owns sequencing so dependency-heavy fixes (regenerate constraints → reinstall → rerun tests) don't race.

### Deliverables

- **Backend orchestrator module** (`backend/fix_orchestrator.py`):
  - `start_fix_run(review_id)` — claims a per-review run lock, creates a mandatory source backup, plans, persists state under the local CLADEX data directory.
  - Plan persisted as `CLADEX_FIX_RUN.md` (human readable) plus `fix_run.json` (structured: phases, tasks, provider, status, dependencies, validation results).
  - Worker pool launches `cladex fix run-task <run-id> <task-id>` subprocesses, mirroring the review-swarm subprocess model.
  - Phase-level validate after every batch, with bounded retries; restore offered on hard failure.
- **CLI**:
  - `cladex fix start --review <review-id> [--max-agents N] [--no-backup]`
  - `cladex fix list --json`
  - `cladex fix show <run-id> --json`
  - `cladex fix run-task <run-id> <task-id> --json` (used by the worker pool)
  - `cladex fix cancel <run-id>`
- **API**:
  - `POST /api/reviews/:id/fix` → kicks off a fix run, returns `{ runId, plan }`.
  - `GET /api/fix-runs` → list.
  - `GET /api/fix-runs/:id` → status + per-task progress + which provider each worker is using + last validation output.
  - `POST /api/fix-runs/:id/cancel` → terminate in-flight workers.
- **UI** (`src/App.tsx` — Review Project view):
  - **Fix Review** button on completed review cards.
  - Confirmation modal that shows the orchestrator's plan before any worker is spawned (what tasks, how many agents of each provider, source backup id), with **Confirm and start fixing** / **Cancel** options.
  - Live fix-run progress card that mirrors review job cards: total tasks, running, done, failed, cancelled; per-task status, assigned provider, last validation output; **Cancel run** button.
  - **Restore from backup** action surfaced when a run finishes with status `failed` or `cancelled`, gated behind the same confirm-the-id flow CLI uses.

### Safety contract

- Mandatory source backup before any worker edits.
- Workers edit only assigned files in the targeted project workspace; never CLADEX itself unless `--allow-cladex-self-review` plus `--allow-cladex-self-fix` are both explicit.
- Each phase validates before the next phase runs. If validation fails twice, the run halts.
- The orchestrator never invents project-wide rewrites; every change must trace back to a specific finding id from `findings.json`.
- Cancellation terminates the in-flight subprocess like review cancel does.

---

## 2. Project Review reducer / sharding / evidence improvements

- Planner shards by package boundaries, recent git hotspots, test surfaces, and security-sensitive paths instead of only deterministic file distribution.
- Reducer de-duplicates findings by evidence, agent focus, and recommended fix as well (current reducer is title/path/line/category exact-match).
- Live AI review lanes emit validated structured JSON findings with command-attempt evidence (stop wrapping any non-JSON tail as an "Unstructured reviewer notes" finding).
- Expose queue/concurrency controls and clearer rate-limit/account-pressure reporting in the UI.
- UI grows interactive severity/category/agent filtering, retry, and export. Snapshot restore button surfaced behind explicit confirmation (CLI restore stays the source of truth).

## 3. Supervisor / queue / account-pooling runtime

- Pool Discord clients by bot token where safe.
- Pool Codex app-server workers by account home where safe.
- Launch Claude workers on demand with idle TTL shutdown.
- Add per-account and global concurrency limits.
- Expand provider fake tests from registry/load checks to full queued-turn simulations.

## 4. Provider observability

- Surface Codex account / rate-limit / model discovery from app-server RPCs in profile health and the UI.
- Add Discord gateway, invalid-request, and backpressure metrics with auto-throttle on repeated 401/403/429.
- Expose remote-token rotation and revoke controls in the UI.

## 5. Optional Claude Code Channels evaluation

Evaluate Channels as an alternate Claude transport once preview stability and access-control behavior are proven. Do not replace the existing custom Claude bridge until org-policy and multi-profile behavior are confirmed.

---

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

The GitHub CI mirrors the source validation path and runs backend tests across Python 3.10, 3.11, and 3.12. The public repo must contain no personal profile env files, auth homes, relay logs, local memory, generated release output, or user-specific paths. Users bring their own locally installed and logged-in `codex` and `claude` CLIs.
