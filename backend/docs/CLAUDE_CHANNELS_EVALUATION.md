# Claude Code Channels Evaluation

Status: **decision recorded — do not adopt as a Claude transport replacement at this time**.

## What Channels are

[Claude Code Channels](https://docs.claude.com/en/docs/claude-code/channels) is an Anthropic-shipped research preview that bridges Claude Code into chat surfaces (Discord, Slack, etc.) with a built-in permission relay. It's distributed as part of the Claude Code CLI (>= v2.1.80) and is gated behind Claude.ai login.

CLADEX already runs its own Claude Code subprocess bridge (`backend/claude_backend.py`) using `claude -p --input-format stream-json --output-format stream-json --verbose`, so the question is whether to swap to Channels for Claude relays.

## What we evaluated

- The Channels research-preview docs as of CLADEX 2.4.0.
- Anthropic's published Discord plugin and its access-control contract.
- The current CLADEX Claude bridge: per-channel persistent process, durable session resume, Discord-message-content intent, allowlist gates, idle TTL + LRU eviction (added in 2.4.0), restart churn detection, operator request bridging, runtime memory artifacts.

## Why CLADEX is not adopting Channels right now

1. **Research preview status.** The product is marked preview by Anthropic. CLADEX's contract is "users bring their own logged-in CLIs and ship to production"; that is at odds with running on a transport whose API and behavior are explicitly subject to change.
2. **Authentication coupling.** Channels require Claude.ai login. CLADEX deliberately supports `CLAUDE_CONFIG_DIR` per profile so the same machine can host multiple subscriptions/accounts. The Channels permission relay assumes one logged-in identity per process; mapping our per-profile account home model onto it is non-trivial.
3. **Multi-profile scaling.** CLADEX is designed for many always-on profiles (50+) sharing pooled subprocess state. Channels does not currently document an API for spawning multiple isolated channel sessions per process, so a multi-profile relay would still need one Claude Code CLI per profile — i.e. no scaling win over the current bridge.
4. **Org-policy gating.** Anthropic supports org-level disabling of Channels. A clone-to-run user that has Channels disabled by their org would simply not have a Claude relay, which would break clone-to-run guarantees.
5. **Equivalent feature set already exists locally.** The current bridge already covers: per-channel session binding, durable memory + resume, allowlist gates (channel/user/bot/operator), DM scoping with explicit `--allow-dms` + user allowlist, mention/prefix triggers, Discord allowed-mentions hygiene, Bash-disabled review lanes, Fix Review write workers, source backups, restart churn detection. Channels duplicates the chat bridge piece but does not, at preview state, eliminate any of the rest.

## What would change our mind

We would re-evaluate Channels for adoption if/when:

- Channels exits research preview with an explicit stability commitment.
- A documented multi-account / `CLAUDE_CONFIG_DIR`-equivalent API exists so CLADEX's per-profile model maps cleanly.
- Anthropic's permission relay supports the granular allowlists we already enforce (DM-only allowlist, channel author allowlist, operator-only DM scoping).
- Org-policy interaction is documented well enough that we can detect "Channels disabled by org" and fall back to the existing bridge instead of failing.

## What we ARE keeping from Channels research

- The "permission relay" concept — explicit per-action approval prompts in chat — is a useful UX direction. CLADEX's Fix Review confirm-before-edit flow already does this for the write phase. We will port additional Channels-style surfaces (per-tool approval requests in chat) on a case-by-case basis without taking the full transport dependency.

## Decision

**Do not adopt Claude Code Channels as a Claude transport for CLADEX in 2.4.0.** The current `claude -p` stream-json subprocess bridge stays as the supported Claude transport. Re-evaluate when Channels reaches stable / GA and the open questions above are resolved.

This decision is documented in [memory/DECISIONS.md](../../memory/DECISIONS.md) and on the public roadmap so future agents do not redo this evaluation under the assumption it was simply "deferred".
