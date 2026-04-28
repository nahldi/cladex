# Models And Backends

## What This Repo Runs

This repo now contains two relay runtimes under one package:

- Codex relay
  - primary backend: Codex app-server
  - degraded fallback: Codex CLI `exec` / `exec resume`
  - default model: blank, meaning the installed Codex CLI chooses its configured/current default
  - account home: optional per-profile `CODEX_HOME`
- Claude relay
  - backend: persistent Claude Code CLI print-mode subprocess with stream-json stdin/stdout
  - default model: blank, meaning the installed Claude CLI chooses its configured/current default
  - account home: optional per-profile `CLAUDE_CONFIG_DIR`

`cladex` is the unified manager for both.

## Codex Model Selection

The selected Codex model comes from:

1. `RELAY_MODEL`
2. `CODEX_MODEL`
3. blank/omitted, which lets Codex use its CLI default

The model is only pinned when a relay profile explicitly sets one.

## Account Home Selection

Codex profiles may set `CODEX_HOME` to point at a specific Codex account home.
When omitted, CLADEX keeps the existing shared relay home behavior. When set,
the relay uses that explicit home directly and does not copy default `~/.codex`
auth into it; log in that home intentionally before starting the profile.

Claude profiles may set `CLAUDE_CONFIG_DIR` to point at a specific Claude Code
configuration/account home. When omitted, Claude Code uses its normal default.

## Reasoning Effort

Adaptive effort is separate from model choice.

- quick status and short questions: `medium`
- implementation, verification, repair: `high`
- hardest long-horizon tasks: optional `xhigh`

## What "Provider" Means Here

There are two different meanings:

1. Runtime backend/provider for a relay profile
   - `codex-app-server`
   - `codex-cli-resume`
   - `claude-code`

2. AI model choice inside the backend
   - Codex model: profile override if set, otherwise the Codex CLI default
   - Claude model: profile override if set, otherwise the Claude CLI default

Skills, browser tools, media tools, and deploy helpers are not providers.

## CLADEX

`cladex` lives in this repo and manages both relay types.

It can:

- read Codex relay profiles using the real current Codex runtime state logic
- read Claude relay profiles from the built-in Claude registry/runtime
- start/stop/restart both kinds of profiles from one manager

For Codex profiles, `cladex` uses the current runtime control paths and does not rely on stale `relay.pid` assumptions.
