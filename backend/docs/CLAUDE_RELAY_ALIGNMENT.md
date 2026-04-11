# Claude Relay Integration Notes

Claude support is now built into this repo and uses the same durable runtime as Codex.

## Current Shape

- `claude-discord` lives in this package
- `cladex` manages both `codex-discord` and `claude-discord`
- Claude now uses `DurableRuntime` for per-channel session binding and memory
- Per-channel worktree-aware execution
- Turn artifacts recorded (success and failure) to STATUS.md, HANDOFF.md, TASKS.json
- Session recovery/rebind on restart or stale sessions
- Pinned to Opus 4.5 (`claude-opus-4-5-20251101`)

## Verified Runtime Contract

The current Claude CLI contract used here is:

- first successful turn:
  - `claude -p --output-format stream-json --model claude-opus-4-5-20251101 --session-id <uuid>`
- later turns:
  - `claude -p --output-format stream-json --model claude-opus-4-5-20251101 --resume <session_id>`

That replaced the earlier incorrect assumption that `claude -p` could be kept alive as a long-lived chat subprocess.

## Durable Runtime Integration

Claude now inherits the same durable memory lifecycle as Codex:

- STATUS.md updates after each turn
- HANDOFF.md for session continuity
- TASKS.json for task tracking
- Turn artifacts with summary, files changed, commands, blockers, next steps
- Per-channel thread/session binding
- Worktree-aware execution per Discord channel

## Remaining Differences from Codex

- Claude uses CLI print-mode (`-p`), not app-server
- No SQLite state (uses file-based durable memory only)
- Still needs longer-run soak testing under restart/recovery scenarios

Claude does have relay-side adaptive effort selection now.
The remaining gap is backend depth and long-run validation, not the absence of an effort policy.

## Practical Rule

Claude is now materially closer to Codex durability but still uses a simpler CLI-based backend. Treat Codex as the stronger baseline for production workloads until Claude has equivalent soak validation.
