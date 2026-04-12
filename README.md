# CLADEX

**CL**aude + Co**DEX** - Unified Discord Relay Manager

A desktop app and CLI for managing Discord relays to both Claude Code and Codex CLI.

The shipped product is local-first:
- the desktop app talks to a local API server on loopback only
- readable bot labels are surfaced ahead of technical profile ids

## Features

- **Unified Manager**: Control both Claude and Codex relays from one interface
- **Desktop App**: Native Electron app with animated UI
- **Durable Runtime**: Both relays use per-channel session binding and persistent memory
- **CLI Tools**: `cladex`, `claude-discord`, `codex-discord` commands
- **Session Persistence**: Automatic session management with resume capability
- **Workspace-Scoped**: Each workspace can have multiple relay profiles

## Quick Start

### Fastest Path For Most People

1. Install Node.js 18+ and Python 3.10+.
2. Install the AI CLIs you want to use:
   - `codex`
   - `claude`
3. From this repo root, run:

```bash
npm install
py -m pip install -e backend
npm run app
```

That gives you the desktop manager plus the Python relay commands:
- `cladex`
- `codex-discord`
- `claude-discord`

### Desktop App

```bash
npm install
npm run app
```

### Development Mode

```bash
npm run dev:stack  # Runs API server + Vite dev server
```

Optional desktop UI environment variables live in `.env.example`.
They are local settings for the Electron app and local API server only.
By default the local API binds to `127.0.0.1:3001`.

### Build Installer

```bash
npm run electron:build  # Creates installer in release/
```

Packaged launchers produced by the build:
- `release\CLADEX Setup 2.0.5.exe`
- `release\CLADEX 2.0.5.exe`
- `release\win-unpacked\CLADEX.exe`

### Backend CLI

```bash
cd backend
pip install -e .
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
cladex start --type X    # Start relay (claude/codex)
cladex stop --type X     # Stop relay
cladex gui               # Open the desktop relay manager

# Claude relay
claude-discord setup
claude-discord register --discord-bot-token <token> --operator-ids <id>
claude-discord run
claude-discord status
claude-discord stop

# Codex relay
codex-discord setup
codex-discord register --discord-bot-token <token>
codex-discord run
codex-discord status
codex-discord stop
```

## Runtime Details

### Claude
- Uses `DurableRuntime` for per-channel session binding
- CLI: `claude -p --output-format stream-json --model claude-opus-4-5-20251101`
- First turn: `--session-id <uuid>`
- Later turns: `--resume <session_id>`
- Adaptive effort policy through the relay: quick turns use `medium`, implementation and repair use `high`, and `xhigh` can be enabled explicitly
- Turn artifacts recorded to STATUS.md, HANDOFF.md, TASKS.json
- Auto-recovery on stale sessions

### Codex
- App-server primary backend with CLI fallback
- Adaptive reasoning effort (medium/high/xhigh)
- Durable memory in SQLite + repo memory files
- Per-channel worktree binding

## Requirements

- Node.js 18+ (frontend/Electron)
- Python 3.10+ (backend)
- Claude Code CLI (`claude`)
- Codex CLI (`codex`)
- Discord bot token

## Distribution Notes

- Local generated runtime files at the repo root are ignored and not meant for git.
- Release builds are written to `release/`.
- The Python backend package name stays `discord-codex-relay` for command/package compatibility.
- The desktop product name remains `CLADEX`.

## License

MIT
