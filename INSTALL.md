# Install CLADEX

## Windows

### Option 1: Use the packaged desktop app

1. Install Python 3.10+ first.
2. Install the AI CLI you plan to use:
   - `codex` for Codex relays
   - `claude` for Claude relays
3. Open `release\CLADEX Setup 2.0.10.exe` and install it.
4. Or run `release\CLADEX 2.0.10.exe` or `release\win-unpacked\CLADEX.exe` directly.
5. In CLADEX, choose `Add Relay`, then enter:
   - a workspace folder
   - a Discord bot token
   - the allowed channel id
6. Start the saved relay and wait for `Ready`.

Security notes for packaged users:
- `CLADEX.exe` is local-first. It should be run on the same machine that owns the relays.
- The local API should stay on loopback. Do not expose it externally unless you know exactly why and have added your own auth/network controls.
- Treat the Discord bot token and the saved profile env files as secrets.

### Option 2: Run from source

1. Install Node.js 18+.
2. Install Python 3.10+.
3. Install the CLIs you plan to use:
   - `codex`
   - `claude`
4. From the repo root:

```powershell
npm install
py -m pip install -e backend
npm run app
```

## Commands installed by the backend

After `py -m pip install -e backend`, these commands are available:

```text
cladex
codex-discord
claude-discord
```

## Notes

- The desktop app uses a local API on loopback by default: `127.0.0.1:3001`
- Non-loopback API binding is blocked by default unless `CLADEX_ALLOW_REMOTE_API=1` is set deliberately
- The desktop app name is `CLADEX`
- The Python package name remains `discord-codex-relay` for compatibility with existing relay commands
- The packaged app bundles the CLADEX UI and backend files, but not Python itself or the external `codex` / `claude` CLIs
- If the packaged app opens but the runtime does not start, first verify that Python is installed and then verify the required AI CLI command is available on PATH
- Before sharing logs or state folders, review them for tokens, prompts, replies, channel ids, and workspace paths
