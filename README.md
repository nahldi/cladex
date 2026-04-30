# CLADEX

**CL**aude + Co**DEX** - Unified Discord Relay Manager

A desktop app and CLI for managing Discord relays to both Claude Code and Codex CLI.

The shipped product is local-first:
- the desktop app talks to a local API server on loopback only
- readable bot labels are surfaced ahead of technical profile ids
- non-loopback API binding is blocked by default unless `CLADEX_ALLOW_REMOTE_API=1` is set deliberately

## Features

- **Unified Manager**: Control both Claude and Codex relays from one interface
- **Desktop App**: Native Electron app with animated UI
- **Durable Runtime**: Both relays use per-channel session binding and persistent memory
- **CLI Tools**: `cladex`, `claude-discord`, `codex-discord` commands
- **Session Persistence**: Automatic session management with resume capability
- **Workspace-Scoped**: Each workspace can have multiple relay profiles
- **Local Operator Chat**: Talk to a running relay from inside CLADEX without using Discord while staying on the same bound relay session
- **Saved Workgroups**: Start or stop related relays together, including migrated legacy Codex project groups
- **Project Review Swarm**: Run 1-50 read-only Codex or Claude review lanes against a selected project and merge findings into one Markdown report plus a separate fix plan

## Quick Start

CLADEX has two install paths. Pick one:

### Path A — Packaged desktop app (most users)

This is the easiest path. Download the latest `.exe` from [GitHub Releases](https://github.com/nahldi/cladex/releases) and follow [INSTALL.md](./INSTALL.md). Required prerequisites:

- Python 3.10+
- `codex` for Codex relays, installed and logged in with your own account/subscription
- `claude` for Claude relays, installed and logged in with your own account/subscription

The packaged `.exe` bundles the CLADEX UI and backend files but **does not** bundle Python or the AI CLIs — those still install separately.

### Path B — Source checkout (for development)

1. Install Node.js 22.12+ and Python 3.10+.
2. Install the AI CLIs you want to use (`codex`, `claude`).
3. From this repo root:

```bash
cmd /c npm ci
py -m pip install -e backend -c backend/constraints.txt
cmd /c npm run app
```

That gives you the desktop manager plus the Python relay commands:
- `cladex`
- `codex-discord`
- `claude-discord`

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the validation gates and the audit-fix-ship loop.
- a Discord bot token plus an allowed channel id or approved DM user/operator id

```bash
cmd /c npm ci
cmd /c npm run app
```

### Development Mode

```bash
cmd /c npm run dev:stack  # Runs API server + Vite dev server
```

Optional desktop UI environment variables live in `.env.example`.
They are local settings for the Electron app and local API server only.
By default the local API binds to `127.0.0.1:3001`.

### Build Installer

```bash
cmd /c npm ci
cmd /c npm run electron:build  # Creates installer in release/
```

Packaged launchers produced by the build:
- `release\CLADEX Setup 3.0.0.exe`
- `release\CLADEX 3.0.0.exe`
- `release\win-unpacked\CLADEX.exe`

Portable/installer first run:
1. Install Python 3.10+.
2. Install the AI CLI you want to use: `codex`, `claude`, or both.
3. Launch `CLADEX.exe`.
4. In the app, choose `Add Relay`, then enter the workspace path, Discord bot token, allowed channel id or scoped DM allowlist, and optional per-relay account home (`CODEX_HOME` or `CLAUDE_CONFIG_DIR`).
5. Start the profile and verify it reaches `Ready` before testing in Discord.

The packaged app bootstraps its bundled Python backend into a local runtime on first launch using the pinned backend constraints. That first bootstrap may need normal `pip` package-index access unless dependencies are already cached. If it fails, close CLADEX, verify `py --version` works, check proxy/firewall access for `pip`, then run the source install command from this repo root: `py -m pip install -e backend -c backend/constraints.txt`. Slow networks can raise the bootstrap ceiling with `CLADEX_BACKEND_BOOTSTRAP_TIMEOUT_MS`.

Optional Codex skill auto-install is disabled by default so setup cannot stall on a long list of network downloads. Set `CLADEX_AUTO_INSTALL_OPTIONAL_SKILLS=1` only when you want the installer to attempt recommended skill installs.

Project reviews do not require a Discord relay profile. Open `Review Swarm`, choose a target folder, and let Project Scout inspect the project shape before launch. CLADEX recommends a reviewer count, pre-fills the scan title/provider, gives every lane a different focus and shard, queues AI lanes behind a bounded worker pool, creates a local source snapshot when enabled, runs reviewers against CLADEX-managed scratch copies instead of editing the target project, and merges results into one report plus a fix plan. Completed scans live under the History tab; the active pane only shows scans that are queued or running.

## Security

- CLADEX is intended for same-machine use. Do not expose the local API directly to your LAN or the public internet.
- Non-loopback API requests require the CLADEX remote token, and remote filesystem browsing is scoped to saved profile workspaces/account homes plus `CLADEX_REMOTE_FS_ROOTS`.
- Relay profile env files and relay logs can contain secrets and sensitive workspace metadata. Keep them local.
- The public git repo should stay source-only: no profile env files, auth homes, relay logs, local memory, generated release output, or user-specific paths. Run `python backend/relayctl.py privacy-audit --tracked-only .` before publishing.
- Use least-privilege Discord allowlists: `ALLOWED_CHANNEL_IDS`, `ALLOWED_USER_IDS`, `ALLOWED_BOT_IDS`, and related fields should stay as narrow as possible.
- See [SECURITY.md](SECURITY.md) for the expected operating model and secret-handling guidance.

### Backend CLI

```bash
cd backend
pip install -e . -c constraints.txt
cladex list
cladex start --type claude
cladex start --type codex
```

## Structure

```
cladex/
  src/              # React frontend (Vite + Tailwind + Framer Motion)
  electron/         # Electron main process
  server.cjs        # API server
  backend/          # Python relay backend
    cladex.py           # Unified manager
    claude_relay.py     # Claude Code relay
    claude_backend.py   # Claude durable runtime
    relayctl.py         # Codex relay
    bot.py              # Codex Discord bot
    relay_runtime.py    # Shared durable runtime
```

## CLI Commands

```bash
# Unified manager
cladex list              # List all profiles
cladex status            # Show running relays
cladex doctor --json     # Check prerequisites, CLI shims, Codex app-server schema, and profile collisions
cladex start --type X    # Start relay (claude/codex)
cladex stop --type X     # Stop relay
cladex gui               # Open the desktop relay manager
cladex review start --workspace C:\path\repo --provider codex --agents 12 --json
cladex review list --json
cladex review fix-plan <review-id> --json
cladex review cancel <review-id> --json
cladex fix start --review <review-id> --json
cladex fix start --review <review-id> --allow-cladex-self-fix --json
cladex fix list --json
cladex fix show <fix-run-id> --json
cladex fix cancel <fix-run-id> --json
cladex backup list --json
cladex backup create --workspace C:\path\repo --reason manual --json
cladex backup restore <backup-id> --confirm <backup-id>

# Claude relay
claude-discord setup
# Prefer CLADEX_REGISTER_DISCORD_BOT_TOKEN over --discord-bot-token so the token is not written to shell history.
CLADEX_REGISTER_DISCORD_BOT_TOKEN=<token> claude-discord register --operator-ids <id>
claude-discord run
claude-discord status
claude-discord stop

# Codex relay
codex-discord setup
CLADEX_REGISTER_DISCORD_BOT_TOKEN=<token> codex-discord register --allowed-channel-id <channel_id>
CLADEX_REGISTER_DISCORD_BOT_TOKEN=<token> codex-discord register --allow-dms --allowed-user-id <user_id>
codex-discord run
codex-discord status
codex-discord stop
```

## Runtime Details

### Claude
- Uses `DurableRuntime` for per-channel session binding
- CLI: `claude -p --input-format stream-json --output-format stream-json --verbose`
- Model override is optional. Blank means the installed Claude CLI chooses its configured/current default.
- Set `CLAUDE_CONFIG_DIR` on a profile when it should use a separate Claude account/subscription home.
- Permission mode defaults to Claude `default`; set `CLAUDE_PERMISSION_MODE=bypassPermissions` only when you deliberately want bypass behavior.
- Adaptive effort policy through the relay: quick turns use `medium`, implementation and repair use `high`, and `xhigh` can be enabled explicitly
- Turn artifacts recorded to STATUS.md, HANDOFF.md, TASKS.json
- Auto-recovery on stale sessions

### Codex
- App-server primary backend with CLI fallback
- Model override is optional. Blank means the installed Codex CLI chooses its configured/current default.
- Set `CODEX_HOME` on a profile when it should use a separate Codex account/subscription home.
- New profiles default to `workspace-write` sandboxing with `on-request` app-server approvals; set `CODEX_FULL_ACCESS=true` only when the machine is externally sandboxed.
- Adaptive reasoning effort (medium/high/xhigh)
- Durable memory in SQLite + repo memory files
- Per-channel worktree binding

## Requirements

- Node.js 22.12+ and npm 10+ (frontend/Electron)
- Python 3.10+ (backend)
- Claude Code CLI (`claude`)
- Codex CLI (`codex`)
- Discord bot token

## Distribution Notes

- Local generated runtime files at the repo root are ignored and not meant for git.
- Release builds are written to `release/`.
- The Python backend package name stays `discord-codex-relay` for command/package compatibility.
- The desktop product name remains `CLADEX`.
- The packaged desktop app uses a loopback-only local API. It is meant to manage relays on the same machine, not expose a remote control surface.
- If you intentionally override the loopback-only API guard, set `CLADEX_ALLOW_REMOTE_API=1`, protect the remote token, and add any extra browse roots with `CLADEX_REMOTE_FS_ROOTS`.
- `CLADEX_REMOTE_FS_UNRESTRICTED=1` restores arbitrary host browsing and should only be used on a trusted private machine.
- Review Swarm artifacts, coordination notes, scratch workspaces, fix-run reports, and source snapshots are stored under the local CLADEX data directory. Review workers do not apply fixes and do not create working folders inside the selected project by default. **Fix Review** is a separate explicit action that creates a backup before any fix worker edits the selected project. Write-capable CLADEX self-fix requires the completed self-review job plus a separate self-fix approval.
- The Review Swarm page is for the selected project. The session-only override that allows targeting the CLADEX app repo itself lives in Runtime settings and should stay off for normal project reviews.
- Set `CLADEX_REVIEW_MAX_PARALLEL` if a machine/account pool can safely run more reviewer CLI processes than the default. The UI shows the effective parallel limit and queues requested lanes behind it.
- Reviewer subprocesses use generous defaults: 30-min idle timeout (`CLADEX_REVIEW_AGENT_IDLE_TIMEOUT`), 1-hour initial-idle grace (`CLADEX_REVIEW_AGENT_INITIAL_IDLE_TIMEOUT`), and a 6-hour absolute wall-clock ceiling (`CLADEX_REVIEW_AGENT_MAX_RUNTIME`). Set `CLADEX_REVIEW_AGENT_MAX_RUNTIME=0` to disable the absolute ceiling for very deep audits; cancel and idle remain the only kill paths in that mode.
- Each AI lane retries once on transient failure (`CLADEX_REVIEW_AGENT_MAX_RETRIES`, default `1`; set to `0` to disable). Operator cancel and provider rate-limit errors short-circuit retries.
- A best-effort cross-cutting synthesizer pass runs after the lanes and emits findings that need multiple lanes' evidence (contradictions, half-fixes, doc/code drift). Set `CLADEX_REVIEW_SYNTHESIZER=0` to disable it on tightly rate-limited accounts.

## License

MIT
