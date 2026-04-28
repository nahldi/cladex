# CLADEX Production Roadmap

CLADEX stays focused on Claude Code and OpenAI Codex relays. The current production path is to keep clone-to-run setup clean, keep model/account behavior provider-led, and add scaling only after compatibility and load tests prove the lower layers.

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
- `py -m pytest --tb=short -q` from `backend/`
- `py backend\cladex.py doctor --json`
- `cmd /c npm run electron:build`

The GitHub CI mirrors the source validation path and runs backend tests across Python 3.10, 3.11, and 3.12.
