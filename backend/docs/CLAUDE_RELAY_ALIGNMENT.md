# Claude Relay Integration Notes

Claude support is now built into this repo.

## Current Shape

- `claude-discord` lives in this package
- `cladex` manages both `codex-discord` and `claude-discord`
- the Claude runtime is intentionally simpler than the Codex durable runtime
- Discord and GUI usage are separate sessions unless a future implementation explicitly shares the same Claude session metadata

## Verified Runtime Contract

The current Claude CLI contract used here is:

- first successful turn:
  - `claude -p --output-format stream-json --session-id <uuid>`
- later turns:
  - `claude -p --output-format stream-json --resume <session_id>`

That replaced the earlier incorrect assumption that `claude -p` could be kept alive as a long-lived chat subprocess.

## Current Gaps

Compared with the Codex runtime in this repo, the Claude path still needs:

- deeper runtime/state introspection
- stronger end-to-end soak testing
- richer durable state and verification features
- cleanup of vestigial TypeScript/MCP leftovers if that path remains unused

## Practical Rule

Treat Claude support as built-in but still less mature than the Codex runtime until it has the same level of long-run validation.
