# Install CLADEX

## Windows

### Option 1: Use the packaged desktop app

1. Open `release\CLADEX Setup 2.0.4.exe` and install it.
2. Or run `release\win-unpacked\CLADEX.exe` directly.

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
- The desktop app name is `CLADEX`
- The Python package name remains `discord-codex-relay` for compatibility with existing relay commands
