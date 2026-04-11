# Models And Backends

## What This Repo Runs

This repo now contains two relay runtimes under one package:

- Codex relay
  - primary backend: Codex app-server
  - degraded fallback: Codex CLI `exec` / `exec resume`
  - default model: `gpt-5.4`
- Claude relay
  - backend: Claude Code CLI one-shot print mode per turn
  - continuity: explicit persisted session ID with `--session-id` then `--resume`

`cladex` is the unified manager for both.

## Codex Model Selection

The selected Codex model comes from:

1. `RELAY_MODEL`
2. `CODEX_MODEL`
3. relay default `gpt-5.4`

The model is pinned when the relay starts or resumes a Codex thread.

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
   - Codex default: `gpt-5.4`
   - Claude model: whatever the local Claude CLI is configured to use unless explicitly overridden there

Skills, browser tools, media tools, and deploy helpers are not providers.

## CLADEX

`cladex` lives in this repo and manages both relay types.

It can:

- read Codex relay profiles using the real current Codex runtime state logic
- read Claude relay profiles from the built-in Claude registry/runtime
- start/stop/restart both kinds of profiles from one manager

For Codex profiles, `cladex` uses the current runtime control paths and does not rely on stale `relay.pid` assumptions.
