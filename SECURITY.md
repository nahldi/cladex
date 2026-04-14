# Security Notes

CLADEX is designed for local, same-machine relay management.

## Supported security posture

- The desktop app talks to a local API on loopback by default: `127.0.0.1:3001`
- The API is intended for the same machine only
- The packaged app does not expose a remote management surface by default
- Browser requests are restricted to local/file origins
- Non-browser local callers without an `Origin` header are still allowed

## Safe defaults

- `server.cjs` binds to `127.0.0.1` unless you deliberately change `API_HOST`
- CLADEX now refuses to bind the API to a non-loopback host unless `CLADEX_ALLOW_REMOTE_API=1` is set
- The local API is not meant to sit directly on a public interface
- If you deliberately override that guard, you are responsible for putting the API behind real authentication and network controls

## Secrets

Treat these as secrets:

- Discord bot tokens
- any local profile `.env` files
- `%LOCALAPPDATA%\discord-codex-relay\profiles\*`
- `%LOCALAPPDATA%\discord-claude-relay\profiles\*`
- relay logs and state directories if they contain prompts, replies, channel ids, or workspace paths

Do not:

- commit profile env files
- paste tokens into screenshots or shared logs
- publish `%LOCALAPPDATA%` relay state directories
- reuse broad-permission Discord bot tokens across unrelated projects

## Packaged app expectations

`CLADEX.exe` bundles the app UI and backend files, but it does not bundle:

- Python
- the `codex` CLI
- the `claude` CLI

A packaged user still needs those installed locally before starting relays.

## Workspace and machine trust

CLADEX relays can read and act within the configured workspace through the installed model CLIs. Only point a relay at a workspace and machine you trust for that model to access.

## Discord setup guidance

- Use dedicated bot tokens for relay bots
- Limit `ALLOWED_CHANNEL_IDS`, `ALLOWED_USER_IDS`, `ALLOWED_BOT_IDS`, and related allowlists to the smallest set you actually need
- Prefer `mention_or_dm` over whole-channel ingestion unless you intentionally want broad channel capture

## Logs and retention

Relay logs and durable memory are operational artifacts, not public telemetry. Review them before sharing because they can include:

- prompts
- model replies
- channel ids
- user ids
- workspace paths
- error payloads from external CLIs

## Reporting

If you find a security issue in CLADEX itself, report it privately to the maintainer before publishing a public exploit path or token-bearing reproduction.
