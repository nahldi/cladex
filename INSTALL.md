# Install CLADEX

## Windows

### Option 1: Use a packaged desktop release

1. Install Python 3.10+ first.
2. Install the AI CLI you plan to use:
   - `codex` for Codex relays
   - `claude` for Claude relays
3. Download the packaged release asset from GitHub, or build it locally with `cmd /c npm run electron:build`.
4. Open `release\CLADEX Setup 2.5.3.exe` and install it.
5. Or run `release\CLADEX 2.5.3.exe` or `release\win-unpacked\CLADEX.exe` directly.
6. In CLADEX, choose `Add Relay`, then enter:
   - a workspace folder
   - a Discord bot token
   - the allowed channel id or scoped DM user/operator allowlist
   - an optional account folder when the relay should use a separate `CODEX_HOME` or `CLAUDE_CONFIG_DIR`
7. Start the saved relay and wait for `Ready`.

Project reviews are available without creating a Discord relay. Use the `Review Swarm` view to choose a target folder; Project Scout will inspect the project and recommend a reviewer count before you scan. The swarm uses your installed and authenticated CLI, queues review lanes behind a bounded worker pool, runs reviewers in CLADEX-managed scratch copies, and writes one merged report plus a fix plan. Completed scans move to History. A completed review can then start **Fix Review**, which creates a backup before any fix worker edits the selected project. CLADEX self-fix requires the completed self-review job plus a separate explicit self-fix confirmation.

On first launch, the packaged app creates a local Python runtime from the bundled backend files using the pinned backend constraints. That first bootstrap may need normal `pip` access to the Python package index unless the packages are already cached on the machine. If the app opens but the relay runtime never becomes ready, close CLADEX, confirm `py --version` works, check proxy/firewall access for `pip`, then run `py -m pip install -e backend -c backend\constraints.txt` from a source checkout to see the dependency error directly. Slow networks can raise the bootstrap ceiling with `CLADEX_BACKEND_BOOTSTRAP_TIMEOUT_MS`.

Security notes for packaged users:
- `CLADEX.exe` is local-first. It should be run on the same machine that owns the relays.
- The local API should stay on loopback. Do not expose it externally unless you know exactly why and have added your own auth/network controls.
- Treat the Discord bot token and the saved profile env files as secrets.
- CLADEX does not ship Codex or Claude credentials. Each user must install and authenticate their own `codex` and `claude` CLIs before starting those relay types.
- Before publishing a source checkout, run `python backend/relayctl.py privacy-audit --tracked-only .` and keep profile env files, auth homes, logs, local memory, and generated builds out of git.

### Option 2: Run from source

1. Install Node.js 22.12+.
2. Install Python 3.10+.
3. Install the CLIs you plan to use:
   - `codex`
   - `claude`
4. From the repo root:

```powershell
cmd /c npm ci
py -m pip install -e backend -c backend\constraints.txt
cmd /c npm run app
```

To verify a source checkout before running relays:

```powershell
cmd /c npm audit
cmd /c npm run lint
cmd /c npm run build
py -m pip install -e "backend[dev]" -c backend\constraints.txt
py backend\cladex.py doctor --json
Push-Location backend
py -m pytest --tb=short -q
Pop-Location
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
- Non-loopback API requests require the CLADEX remote token; remote filesystem browsing is limited to saved profile workspaces/account homes and any explicit `CLADEX_REMOTE_FS_ROOTS`
- The desktop app name is `CLADEX`
- The Python package name remains `discord-codex-relay` for compatibility with existing relay commands
- The packaged app bundles the CLADEX UI and backend files, but not Python itself or the external `codex` / `claude` CLIs
- Blank model fields use the installed `codex` / `claude` CLI defaults. Pin a model only when you intentionally need a specific one.
- Use `CODEX_HOME` or `CLAUDE_CONFIG_DIR` per profile when different relays should use different subscriptions/accounts.
- Review-swarm artifacts and source snapshots are stored in the local CLADEX data directory and are not written into the reviewed project unless you later copy them there deliberately.
- Optional Codex skill auto-install is opt-in so setup cannot hang on many network installs. Set `CLADEX_AUTO_INSTALL_OPTIONAL_SKILLS=1` only when you want the installer to attempt recommended skill downloads.
- `CLADEX_REMOTE_FS_UNRESTRICTED=1` restores arbitrary host browsing and should only be used on a trusted private machine.
- New Codex profiles default to sandboxed `workspace-write` behavior. Full bypass requires explicitly setting `CODEX_FULL_ACCESS=true`.
- If the packaged app opens but the runtime does not start, first verify that Python is installed and then verify the required AI CLI command is available on PATH
- Before sharing logs or state folders, review them for tokens, prompts, replies, channel ids, and workspace paths
