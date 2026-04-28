# CLADEX Production Roadmap

CLADEX stays focused on Claude Code and OpenAI Codex relays. The current production path is to keep clone-to-run setup clean, keep model/account behavior provider-led, and add scaling only after compatibility and load tests prove the lower layers.

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

- Add a local Project Review swarm mode inspired by ClawSweeper's proposal-only sweep/apply split:
  - provider selector for Codex or Claude,
  - agent-count slider from 1 to 50,
  - folder picker for the target project,
  - queued read-only review workers with progress like `Running 2/15` and `Done 13/15`,
  - one universal markdown report with evidence, severity, confidence, and recommended fixes.
- Implement review-job orchestration separate from Discord relay profiles:
  - planner shards the project by risk area, package boundaries, test surfaces, recent git hotspots, and security-sensitive paths,
  - workers run against read-only or throwaway workspaces,
  - reducer merges worker JSON into one durable report and de-duplicates by file, symptom, evidence, and recommended fix,
  - UI can resume job state after restart.
- Enforce review-mode safety:
  - no review worker receives write credentials,
  - Codex uses read-only sandbox/permission profiles where supported,
  - Claude uses throwaway/read-only checkouts plus mandatory clean-diff verification,
  - all workers are blocked from editing the CLADEX runtime repo unless explicitly in CLADEX development mode,
  - repository content is treated as evidence, not trusted instructions, to reduce prompt-injection risk.
- Add a guarded fix phase for review reports:
  - one planner converts findings into ordered fix phases,
  - user approval is required before edits,
  - write workers edit only assigned project workspaces/worktrees,
  - planner validates diffs and tests between phases before continuing.
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
