# Changelog

All notable changes to CLADEX will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Hardening on top of 3.0.0

Post-v3.0.0 fresh-eyes audit closeout. No version bump; pure hardening commits on `master`.

### Added
- `cladex doctor --gc` now prunes Codex relay-managed `CODEX_HOME` state for any profile whose relay is currently stopped: rotates `sessions/*.jsonl` by age + count, runs `PRAGMA wal_checkpoint(TRUNCATE)` on `logs_2.sqlite` and `state_5.sqlite`, and runs `VACUUM` if the db is older than `CLADEX_CODEX_LOGS_VACUUM_DAYS` (default 30). Operator can now reclaim a 368 MB unbounded `logs_2.sqlite` without restarting the relay. New JSON fields `codexHomePruned` and `codexHomeBytesRecovered`.
- `cladex doctor --gc` log-file truncation now walks the per-relay state log dirs (`%LOCALAPPDATA%\discord-codex-relay\state\<ns>\` and the Claude equivalent) in addition to `%LOCALAPPDATA%\cladex\`. Closes a gap where 40+ MB of stale `relay.log` content was untouched.
- `SECURITY.md` "Reviewer + fix-worker isolation" section now documents the agent-scratch hardlink trade-off and the `CLADEX_REVIEW_AGENT_SCRATCH_MODE=copy` opt-out.

### Fixed
- `backend/tests/conftest.py` now isolates `CLADEX_SECRETS_ROOT` to a session tempdir BEFORE any test code can import `secret_store`. Prior to this, every test pass through `secret_store.store_secret(...)` wrote permanent .bin files into the operator's real DPAPI store at `%LOCALAPPDATA%\cladex\secrets\`. Fix shipped with a one-shot cleanup of 2,371 already-leaked test blobs (the 7 real profile secrets were preserved).
- `backend/bot.py` Discord-fatal-close-code handlers (4004 / 4013 / 4014) now raise `SystemExit(13) from exc` so the chained gateway exception is preserved in the traceback for `cladex logs` debugging — parity with `claude_bot.py`.
- `src/App.tsx` `useEffect` for the dark-mode class no longer carries a dead `[isDark]` deps list (`isDark = true` is a constant in v3 — there is no toggle).

### Tests
- New `test_restore_command_for_run_returns_placeholder_when_snapshot_pruned` (T1.7 regression coverage).
- New `test_append_operator_history_redacts_secret_shaped_values` (T4.5 regression coverage).
- New `test_prune_codex_home_state_runs_wal_checkpoint_and_age_vacuum` (T2.4 regression coverage).
- Backend pytest count: 445 → 448 (3 new regressions; 0 removed).

## [3.0.0] — 2026-04-30

CLADEX 3.0.0 is the production-ready milestone after the 2.5.x audit-fix-ship loop closed every confirmed cross-cutting finding from the self-review swarm. The release also rolls in operator-flagged hardening (token-at-rest encryption, backup retention, stale-process detection) and the official-platform pattern fixes from a deep audit against Discord, Anthropic Claude Code, and OpenAI Codex CLI April 2026 guidance.

### Added

- **Token-at-rest encryption.** Profile bot tokens are now stored as `secret-ref:dpapi:<id>` placeholders in `.env` files, with the actual value encrypted via Windows DPAPI (per-secret entropy) under `%LOCALAPPDATA%\cladex\secrets\`. macOS/Linux fall back to a 0o600-permissioned blob. Existing plaintext profiles auto-migrate on next save. New `secret_store.py` module; documented in SECURITY.md "Secret-at-rest storage" section.
- **Backup retention policy.** `prune_backups()` runs after every `create_source_backup()`. Default keeps 10 most recent per workspace + anything newer than 7 days. Tunable via `CLADEX_BACKUP_KEEP_PER_WORKSPACE` and `CLADEX_BACKUP_MAX_AGE_DAYS`. Orphan-workspace backups (where the source workspace no longer exists) are always pruned.
- **`cladex doctor --gc` command.** Garbage-collects operator-visible cruft: prunes backups, removes finished review scratch trees (with Windows read-only retry), reaps stale workspace-start lock files, deletes state-namespace dirs whose profile no longer exists, removes orphan secret blobs, truncates old `*.log` files. Dry-run mode (`--dry-run`) is fully non-destructive.
- **Per-lane isolated reviewer HOME.** `_minimal_reviewer_env(isolated_home=...)` rewrites HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_* to a per-lane empty directory under the lane scratch workspace. Prompt-injected repos can no longer ask the reviewer's read-only Read/Grep/Glob/LS tools to read host secret stores like `~/.codex/auth.json` or `%LOCALAPPDATA%\cladex\remote-access-token.json`.
- **Cross-cutting synthesizer pass** in the Review Swarm. After all lanes finish, a synthesizer reviewer reads every lane's findings + the source workspace and emits issues that need multiple lanes' evidence to spot (contradictions, half-fixes, doc/code drift, ordering bugs across files). Best-effort; opt-out via `CLADEX_REVIEW_SYNTHESIZER=0`.
- **Codex relay env positive allowlist** (parity with Claude). Switched from prefix-allowlist+denylist to a positive allowlist + secret-suffix deny.
- **Karpathy-distilled fix-worker discipline** in the Fix Review prompt: think before editing, simplicity first, surgical changes, goal-driven verification.
- **Lane retry-once on transient failure** (`CLADEX_REVIEW_AGENT_MAX_RETRIES`, default 1).
- **Generous reviewer timeouts** (30-min idle, 1-hour initial-idle grace, 6-hour wall-clock ceiling) with `CLADEX_REVIEW_AGENT_MAX_RUNTIME=0` to disable the absolute ceiling for very deep audits.
- **Stdlib-only packaged-user bootstrap** (`backend/bootstrap_runtime.py`) so a clean Windows machine can create the runtime venv before psutil/platformdirs are pip-installed.
- **Codex CLI session/log pruning at relay startup** (`bot.main` calls `_prune_codex_home_state`): rollout file age + count cap, sqlite WAL checkpoint, optional VACUUM. Knobs: `CLADEX_CODEX_SESSIONS_MAX_AGE_DAYS`, `CLADEX_CODEX_SESSIONS_MAX_FILES`, `CLADEX_CODEX_LOGS_VACUUM_DAYS`.
- **Claude `relay.log` truncation on start** (parity with Codex).
- **Profile-remove cleanup**: deletes the encrypted secret blob and the state-namespace dir so deleted profiles leave no orphans.
- **Snapshot/restore deny-list** for AI/agent-tool local config dirs (`.claude`, `.codex`, `.cursor`, `.aider`, `.continue`, `.windsurf`, `.copilot`).
- **Strict findings loader** for Fix Review entry points: missing/corrupt/malformed/empty `findings.json` fails closed before backup or worker launch.
- **Same-process global AI lane slot tracking**: prevents Review Swarm self-deadlock when transient Windows unlink failures leave a current-PID slot file behind.
- **CHANGELOG.md, CONTRIBUTING.md, `.github/ISSUE_TEMPLATE/{bug,security}.md`, PR template.**

### Changed

- **Reviewer prompt** now carries bounded `detail` (1200 chars) and `recommendation` (600 chars) per lane finding so the synthesizer can correlate cross-lane assumptions, not just titles.
- **Snapshot ignore policy** is the source of truth for restore: out-of-scope cleanup never deletes preserved local files (`.env`, `memory/`, agent-config dirs).
- **Backup retention** with both `KEEP_PER_WORKSPACE=0` and `MAX_AGE_DAYS=0` now keeps everything (was: deleted everything).
- **`restore_backup`** suppresses backup pruning for the in-flight restore so the source snapshot can never be pruned mid-restore.
- **`_pid_matches_claude_relay`** now requires a matching `STATE_NAMESPACE` env var so a stale PID file cannot kill a different profile's process.
- **`_load_claude_env`** resolves `secret-ref:` values transparently (previously the unified Claude start path passed the literal placeholder to discord.py).
- **`secret_store` and `bootstrap_runtime`** added to `[tool.setuptools.py-modules]` so the published backend wheel includes them.
- **Fix Review restore-command surface** validates the backup snapshot still exists; emits a clearly-labeled placeholder when pruned.
- **`_doctor_command`** runs from a tempdir cwd so PowerShell's `ModuleAnalysisCache` no longer dirties the operator's git tree.

### Security

- See SECURITY.md "Secret-at-rest storage" for the new `secret-ref:dpapi:` model and the per-secret-entropy DPAPI mitigation.
- Reviewer + fix-worker isolation section explains that the provider's `accountHome` (`CODEX_HOME` / `CLAUDE_CONFIG_DIR`) is still reachable via the explicit account-home kwarg; treat the chosen `accountHome` as on-the-record state for the duration of a review or fix run.
- F0006 (relay Codex home auth/sandbox trust boundary) documented as a known design trade-off with a planned per-profile sandbox toggle in a future tranche.

### Validation

- Backend: `pytest backend/tests --tb=short -q` → green across Python 3.10/3.11/3.12 on Ubuntu/Windows/macOS in CI.
- Frontend: `npm run lint && npm run frontend:smoke && npm run api:smoke` green.
- Build: `npm run build` (Vite production build) green.
- Audit: `npm audit` → 0 vulnerabilities.
- Privacy: `python backend/relayctl.py privacy-audit --tracked-only .` → no findings.
- Doctor: `python backend/cladex.py doctor --json` → ok=True (with the expected codex PowerShell shim warning on Windows).
- Doctor GC: `python backend/cladex.py doctor --gc --dry-run --json` → ok=True, non-destructive.
- Packaging: `python -m pip install -e "backend[dev]" -c backend/constraints.txt` installs cleanly as `discord-codex-relay==3.0.0`; `python -m pip check` clean.
- Electron: `npm run electron:build` produced 3.0.0 setup + portable artifacts.

---

## [2.5.7] — 2026-04-29

Closed the four high-severity findings + the meta-important synthesizer-context finding from the post-2.5.6 self-review swarm.

### Added
- `_minimal_reviewer_env(isolated_home=...)` kwarg with per-lane empty HOME directory.
- Synthesizer prompt now ships bounded `detail` (1200 chars) and `recommendation` (600 chars) per finding.
- Strict findings loader `_load_review_findings_strict` for Fix Review.
- Stdlib-only `backend/bootstrap_runtime.py` (rewritten to actually be stdlib-only — the 2.5.6 attempt was a regression).

### Fixed
- F0001/F0002 bootstrap regression: 2.5.6 created a fresh venv then re-execed `from install_plugin import _ensure_runtime` inside it, but the venv had no deps yet. New version replicates the install pipeline via stdlib subprocess and never imports install_plugin.
- F0003/F0004 prompt-injection exfiltration: reviewer subprocesses inherit isolated HOME, so prompt-injected repos can no longer Read/Grep host secret stores via the read-only filesystem tools.
- F0035 synthesizer prompt detail-stripping (it was the synthesizer flagging its own design flaw).
- F0012 Fix Review silent empty-findings start.

Commit: `164336c`. CI: GitHub Actions run `25131998402`. Release: [v2.5.7](https://github.com/nahldi/cladex/releases/tag/v2.5.7).

---

## [2.5.6] — 2026-04-29

Closed the high-severity findings from the post-2.5.5 self-review swarm.

### Added
- Snapshot/restore deny list covers AI/agent-tool local config dirs (`.claude`, `.codex`, `.cursor`, `.aider`, `.continue`, `.windsurf`, `.copilot`).
- Stdlib-only `backend/bootstrap_runtime.py` (initial attempt; superseded by 2.5.7 fix).
- SECURITY.md section on relay Codex home auth/sandbox trust boundary.

### Changed
- `_claude_subprocess_env` switched from prefix-allowlist (`ANTHROPIC_*`/`CLAUDE_*`) to explicit allowlist + secret-suffix deny (`_TOKEN`/`_KEY`/`_SECRET`/`_PASSWORD`/`_PRIVATE_KEY`/`_CREDENTIALS`). `ANTHROPIC_API_KEY` no longer passes through.
- Synthesizer skips when `provider_limit_reached` is set.

Commit: `6840ee7`. Release: [v2.5.6](https://github.com/nahldi/cladex/releases/tag/v2.5.6).

---

## [2.5.5] — 2026-04-29

Review-swarm depth hardening.

### Added
- Generous default reviewer timeouts (30-min idle, 1-hour initial-idle, 6-hour wall-clock; `MAX_RUNTIME=0` disables the wall).
- AI lane retry-once on transient failure (`CLADEX_REVIEW_AGENT_MAX_RETRIES`).
- Post-lane synthesizer pass for cross-cutting findings.
- Karpathy-distilled discipline in fix-worker prompt.

Commit: `666bc48`. Release: [v2.5.5](https://github.com/nahldi/cladex/releases/tag/v2.5.5).

---

## [2.5.4] — 2026-04-29

CLADEX self-review hardening across backend provider adapters, review/fix orchestration, Electron/API startup, packaging, and Review Swarm UI.

Commit: `c7b166c`. Release: [v2.5.4](https://github.com/nahldi/cladex/releases/tag/v2.5.4).

## [2.5.3] — 2026-04-29

Stabilized relay worktrees on CI; Windows launcher test portability; cross-platform backend test regressions; CI privacy and smoke path checks.

Commit: `a158280`. Release: [v2.5.3](https://github.com/nahldi/cladex/releases/tag/v2.5.3).

## [2.5.2] — 2026-04-29

Review Swarm UX / Project Scout release. Active/history/fix/snapshot tabs, hive standby state, Project Scout pre-scan, Runtime-settings self-review opt-in.

Commit: `e3cd80d`. Release: [v2.5.2](https://github.com/nahldi/cladex/releases/tag/v2.5.2).

## [2.5.1] — 2026-04-29

Hardened CLADEX 2.5.1 production release.

Commit: `8950b7b`.

## [2.5.0] — 2026-04-29

Shipped AI Fix Review orchestrator.

Commit: `8bd8793`.

## [2.4.0] and earlier

See `git log v2.4.0` for the pre-2.5 history. Major milestones: 2.4.0 closed all non-blocking roadmap items; 2.3.x closed audit gaps; 2.2.0 introduced the Project Review Swarm foundation; 2.1.x added agent guardrails and CI polish; 2.1.0 was the first cleanly-clonable production-ready cut.
