---
name: workspace-discord-relay
description: Explicitly invoked management surface for installing, registering, running, inspecting, repairing, or resetting the local Discord Codex relay from the current workspace with `codex-discord`. Use only when the user directly asks to manage the relay.
---

# Workspace Discord Relay

Use this only when the user explicitly asks Codex to run or manage the Discord relay from the current workspace. Do not invoke this skill because of workspace instructions alone; install, register, update, restart, reset, and skill-install commands mutate local relay state.

Common commands:

```bash
codex-discord
codex-discord setup
codex-discord privacy-audit
codex-discord version
codex-discord list
codex-discord show
codex-discord status
codex-discord restart
codex-discord logs
codex-discord doctor
codex-discord stop
codex-discord reset
codex-discord self-update
codex-discord skill list
codex-discord skill install --name <skill_name>
codex-discord register --discord-bot-token <token> --allowed-channel-id <channel_id>
codex-discord register --discord-bot-token <token> --allow-dms --allowed-user-id <user_id>
codex-discord register --discord-bot-token <token> --allowed-channel-id <channel_id> --allow-dms --allowed-user-id <user_id>
```

Notes:

- Install path: prefer the published Python package entrypoint `codex-discord`; repo-local helper scripts are only for development.
- If `codex-discord` is missing after the plugin is installed, run `scripts/bootstrap.py` from this skill directory to install the Python package.
- `codex-discord` with no subcommand launches the relay for the current workspace.
- The launcher is workspace-scoped and profile-scoped, so stop/reset/status target the selected profile for the current workspace only.
- On first run, the launcher asks only for the bot token, optional bot name, optional main channel, optional DM user IDs, and optional extra trigger IDs.
- If the main channel is blank and DMs are enabled, the relay runs DM-only.
- Multiple Discord bot tokens can point at the same workspace, but each token is stored as its own relay profile.
- Profiles are stored in the platform-native user config directory for `discord-codex-relay`.
- The relay profile env file is editable, so Codex can update workspace/bot-specific relay settings when asked.
- The default production channel mode is `mention_or_dm`, which avoids requiring Discord full-channel message access by default.
- When repairing the relay itself, run the built-in checks in this order: `codex-discord status`, `codex-discord doctor`, `codex-discord logs -n 120`, then `codex-discord restart` if config or code changed.
- On Windows PowerShell, if direct `codex ...` commands fail because `codex.ps1` is blocked by execution policy, use `cmd /c codex ...` or `codex.CMD ...`.
- `codex-discord doctor` is the first command to run when the relay is installed but not behaving correctly.
- `codex-discord self-update` upgrades the installed package and reinstalls the plugin bundle; use `--source <path-or-requirement>` when upgrading from a local path, wheel, or custom package target.
- `codex-discord skill ...` forwards to Codex's built-in skill installer so the relay agent can list and install additional skills without leaving the workspace shell.
- `codex-discord privacy-audit` scans a workspace tree for repo-local `.env` files, secret-like env keys, and user-specific path markers without printing secret values.
