# Claude Relay Integration Notes

Claude support is now built into this repo and uses the same durable runtime as Codex.

## Current Shape

- `claude-discord` lives in this package
- `cladex` manages both `codex-discord` and `claude-discord`
- Claude now uses `DurableRuntime` for per-channel session binding and memory
- Per-channel worktree-aware execution
- Turn artifacts recorded (success and failure) to STATUS.md, HANDOFF.md, TASKS.json
- Session recovery/rebind on restart or stale sessions
- Model override is optional. Blank means the installed Claude CLI chooses its configured/current default.

## Verified Runtime Contract

The current Claude CLI transport used here is a persistent Claude CLI print-mode
subprocess using JSON stdin/stdout. The spawned Claude process looks like:

- persistent process transport:
  - `claude -p --input-format stream-json --output-format stream-json --verbose --permission-mode default`
- per-message session behavior:
  - Discord/operator messages are written into the existing per-channel Claude process over stdin as stream-json `user` messages.
  - The same process can handle multiple turns before shutdown.
  - A fresh Claude session id/process is created only for explicit recovery cases such as stale/broken session state.

Permission mode remains a spawned Claude CLI argument. It is not injected into
chat content or sent as a separate preflight message. Production defaults use
Claude `default`; `bypassPermissions` is an explicit operator opt-in.

This is still distinct from the older one-shot `claude -p -- ...prompt` relay
transport because the current backend keeps stdin open and streams multiple turns
through the same CLI process.

## Durable Runtime Integration

Claude now inherits the same durable memory lifecycle as Codex:

- STATUS.md updates after each turn
- HANDOFF.md for session continuity
- TASKS.json for task tracking
- Turn artifacts with summary, files changed, commands, blockers, next steps
- Per-channel thread/session binding
- Worktree-aware execution per Discord channel

## Remaining Differences from Codex

- Claude uses a persistent Claude CLI print-mode subprocess, not Codex app-server
- No SQLite state (uses file-based durable memory only)
- Still needs longer-run soak testing under restart/recovery scenarios

Claude does have relay-side adaptive effort selection now.
The remaining gap is backend depth and long-run validation, not the absence of an effort policy.

## Practical Rule

Claude now keeps a persistent per-channel session transport like the user
expects, but it still needs soak validation under restart/recovery load before
it should be treated as fully equivalent to Codex in production.
