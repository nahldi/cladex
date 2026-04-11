# CLADEX

**CL**aude + Co**DEX** - Unified Discord Relay Manager

A beautiful GUI and CLI for managing Discord relays to both Claude Code and Codex CLI.

## Features

- **Unified Manager**: Control both Claude and Codex relays from one interface
- **Modern GUI**: React + Vite frontend with 3D interactive cards and animations
- **CLI Tools**: `cladex`, `claude-discord`, `codex-discord` commands
- **Session Persistence**: Automatic session management with resume capability
- **Workspace-Scoped**: Each workspace can have multiple relay profiles

## Structure

```
cladex/
  src/           # React frontend (Vite + Tailwind + Framer Motion)
  backend/       # Python relay backend
    cladex.py         # Unified manager
    claude_relay.py   # Claude Code relay
    relayctl.py       # Codex relay
    bot.py            # Discord bot
    ...
```

## Quick Start

### Frontend (GUI)

```bash
npm install
npm run dev
```

### Backend (CLI)

```bash
cd backend
pip install -e .
cladex list
cladex start --type claude
cladex start --type codex
```

## CLI Commands

```bash
# Unified manager
cladex list              # List all profiles
cladex status            # Show running relays
cladex start --type X    # Start relay (claude/codex)
cladex stop --type X     # Stop relay
cladex gui               # Open GUI

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

## Session Lifecycle

### Claude
- First turn: `claude -p --output-format stream-json --session-id <uuid>`
- Later turns: `claude -p --output-format stream-json --resume <session_id>`
- Auto-recovery on stale sessions

### Codex
- App-server primary backend with CLI fallback
- Adaptive reasoning effort (medium/high/xhigh)
- Durable memory in SQLite + repo memory files

## Requirements

- Node.js 18+ (frontend)
- Python 3.10+ (backend)
- Claude Code CLI (`claude`)
- Codex CLI (`codex`)
- Discord bot token

## License

MIT
