# Contributing to CLADEX

Thanks for considering a contribution. CLADEX is a focused Claude Code + OpenAI Codex Discord relay manager — see `README.md` for what it does and `SECURITY.md` for the safety posture.

## Local development

```bash
# Prereqs: Node 22.12+, Python 3.10+, Codex CLI and/or Claude Code CLI
git clone https://github.com/nahldi/cladex
cd cladex
npm ci
python -m pip install -e "backend[dev]" -c backend/constraints.txt
```

## Validation gates (run before opening a PR)

```bash
# Frontend + lint + smokes (fast)
npm run test                # = npm run lint && npm run frontend:smoke && npm run api:smoke
npm run build               # Vite production build

# Backend
python -m pytest backend/tests --tb=short -q
python backend/relayctl.py privacy-audit --tracked-only .
python backend/cladex.py doctor --json    # expect ok=True

# Optional: full electron build + GC dry-run
npm run electron:build
python backend/cladex.py doctor --gc --dry-run --json
```

CI runs all of the above on Ubuntu/Windows/macOS × Python 3.10/3.11/3.12.

## Audit-fix-ship loop

CLADEX uses its own Project Review Swarm to audit changes before each release. The loop:

1. Self-review swarm runs against the working tree (`cladex review start --workspace . --provider codex --allow-cladex-self-review`).
2. Triage findings — high-severity items + synthesizer findings get fixed in the same tranche.
3. Re-validate (gates above).
4. Bump version, commit, push, build electron, publish GitHub Release.

The synthesizer's job is to spot cross-cutting bugs that need evidence from multiple lanes. Our experience is that every cycle finds at least one regression in the previous cycle's fixes — assume the same will be true of yours.

## Test-driven hardening

When fixing a finding, add a regression test in the same commit that fails on the unfixed code and passes on your patch. This is what keeps the audit-fix-ship loop from regressing on the next round.

## Secret handling

- **Never** commit `.env` files, OAuth tokens, or `~/.codex` / `~/.claude` content.
- Profile tokens are stored at rest via `backend/secret_store.py` (Windows DPAPI / fs0600 elsewhere). New sensitive env keys go in `secret_store.SENSITIVE_KEYS`.
- Run `python backend/relayctl.py privacy-audit --tracked-only .` before pushing to catch accidental tracked profile/auth content.

## Issue templates

- **Bug**: `.github/ISSUE_TEMPLATE/bug.md` — minimum repro + log output + `cladex doctor --json` result.
- **Security**: `.github/ISSUE_TEMPLATE/security.md` — please follow the SECURITY.md "Reporting" section instead of opening a public issue for vulnerabilities.

## Style

- Match existing style in the file you're editing.
- Surgical changes only — touch what the finding/feature demands. No "improve adjacent code" reformat passes.
- Comments explain the **why** for non-obvious choices (especially security or ordering invariants), not the **what**.

## Roadmap visibility

`memory/RESEARCH_v3_*.md` documents the comparative landscape and what's deferred to v3.x:

- Slash command parity with `zebbern/claude-code-discord` (M)
- Scheduler + REST/webhook trigger surface (M)
- ACP provider abstraction (L)
- Mac/Linux installer artifacts in `release/` (S, needs CI infra)
- Multi-machine fan-out (M)
- Findings consensus weighting + reviewer personas (M)
- Per-profile spend/budget meter (S)

If you want to tackle one of these, open a discussion first so we can scope it together.
