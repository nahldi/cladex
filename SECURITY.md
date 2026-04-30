# Security Notes

CLADEX is designed for local, same-machine relay management.

## Supported security posture

- The desktop app talks to a local API on loopback by default: `127.0.0.1:3001`
- The API is intended for the same machine only
- The packaged app does not expose a remote management surface by default
- Browser requests are restricted to local/file origins
- Non-browser local callers without an `Origin` header are still allowed
- Non-loopback API requests require the CLADEX remote token

## Safe defaults

- `server.cjs` binds to `127.0.0.1` unless you deliberately change `API_HOST`
- CLADEX now refuses to bind the API to a non-loopback host unless `CLADEX_ALLOW_REMOTE_API=1` is set
- Remote filesystem browsing is scoped to saved profile workspaces/account homes and `CLADEX_REMOTE_FS_ROOTS`
- The local API is not meant to sit directly on a public interface
- If you deliberately override that guard, you are responsible for putting the API behind real authentication and network controls
- `CLADEX_REMOTE_FS_UNRESTRICTED=1` restores arbitrary host filesystem browsing and should only be used on a trusted private machine

## Secrets

Treat these as secrets:

- Discord bot tokens
- any local profile `.env` files
- `%LOCALAPPDATA%\discord-codex-relay\profiles\*`
- `%LOCALAPPDATA%\discord-claude-relay\profiles\*`
- `%LOCALAPPDATA%\cladex\secrets\*` (DPAPI-encrypted blobs; see "Secret-at-rest storage" below)
- relay logs and state directories if they contain prompts, replies, channel ids, or workspace paths

Do not:

- commit profile env files
- paste tokens into screenshots or shared logs
- publish `%LOCALAPPDATA%` relay state directories
- reuse broad-permission Discord bot tokens across unrelated projects

## Secret-at-rest storage

CLADEX 3.0+ stores Discord bot tokens encrypted at rest. Profile env files no longer contain the literal token — they contain a placeholder of the form `DISCORD_BOT_TOKEN=secret-ref:dpapi:<id>`, and the actual token is encrypted under `%LOCALAPPDATA%\cladex\secrets\<id>.bin`.

How it works:

- **Windows: DPAPI** via `CryptProtectData` / `CryptUnprotectData` (`backend/secret_store.py`). Each secret is bound to the current Windows user account AND mixed with per-secret entropy (`hashlib.sha256(f"cladex/v1/{secret_id}")`), so a single secret-blob leak does not allow decrypting other secrets even by the same user.
- **macOS / Linux: 0o600 file fallback** (`fs0600` scheme). Filesystem permissions are the protection. Acceptable for single-user dev machines; the documentation calls out the reduced threat surface.
- **Backward compatibility:** legacy plaintext profile env values pass through `materialize_env_secrets` unchanged. The next time a profile is saved, the writer transparently migrates to the secret-ref format.

What this protects against:
- Cloud sync (OneDrive / Dropbox / iCloud) of `%LOCALAPPDATA%\discord-codex-relay\profiles\` no longer leaks the token (the env file no longer contains it).
- Accidental `git add` of a profile env captures only the placeholder, not the token.
- Support-bundle copies, screenshots, and casual `cat`/`ls` no longer expose the token.

What this does NOT protect against:
- A kernel-level adversary on the host.
- In-process malware running as the same Windows user (it can call DPAPI itself).
- A relay process that has already loaded the token into its own memory.

Recovery:
- If the secret blob is deleted (or DPAPI fails because the user account changed), CLADEX will not be able to decrypt the token. Re-enter the token via the desktop app's Edit Relay flow or `cladex update <profile>` and the new write goes through the secret store.

Operator visibility:
- `cladex doctor --gc` walks the secret store and removes orphan blobs (blobs whose id is not referenced by any profile env). Run with `--dry-run` to preview.

## Public repo hygiene

The git repository must not contain personal runtime state:

- profile env files other than `.env.example`
- Codex or Claude auth homes
- relay logs, JSONL transcripts, or generated local memory
- packaged `release/`, `dist/`, `build/`, `node_modules/`, or virtualenv output
- user-specific absolute paths

Before publishing or cutting a release, run:

```bash
python backend/relayctl.py privacy-audit --tracked-only .
```

CI runs the same tracked-file privacy gate. Ignored local folders can still contain private runtime state on a developer machine, but they must not be committed.

## Packaged app expectations

`CLADEX.exe` bundles the app UI and backend files, but it does not bundle:

- Python
- the `codex` CLI
- the `claude` CLI

A packaged user still needs those installed and authenticated locally before starting relays. CLADEX never ships maintainer Codex or Claude credentials.

## Workspace and machine trust

CLADEX relays can read and act within the configured workspace through the installed model CLIs. Only point a relay at a workspace and machine you trust for that model to access.

The Codex relay copies the user's `auth.json` and `cap_sid` from `~/.codex` into a managed relay `CODEX_HOME` and writes a `config.toml` that marks the workspace as trusted (and on Windows, sets `sandbox = "elevated"`). This is intentional so the relayed Codex CLI can authenticate and act on the workspace without prompting for approval per command; treat the managed relay home as the same trust boundary as `~/.codex`. Discord-driven Codex prompts inside the relay can read their own `CODEX_HOME`, so do not point the relay at workspaces or accounts you would not trust the operator to use through the same Codex CLI directly.

## Reviewer + fix-worker isolation

Review and fix subprocesses run with an isolated per-lane HOME directory so a prompt-injected repository cannot ask Claude/Codex to Read/Grep host secret stores like `%LOCALAPPDATA%\cladex\remote-access-token.json`, `~/.codex/auth.json`, `~/.aws/credentials`, or `~/.ssh/`. The reviewer/fix subprocess sees an empty HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_* set rooted under the lane's scratch workspace.

The provider's actual credential home (CODEX_HOME / CLAUDE_CONFIG_DIR) is still passed through explicitly so the CLI can authenticate. Discord-driven reviewer/fix prompts can still walk into that directory through the read-only filesystem tools — treat the chosen `accountHome` as on-the-record state for the duration of the review or fix run, the same way you would treat `~/.codex/auth.json` if you ran `codex` in a directory under unknown-quality input. A future CLADEX release may broker provider auth through a low-privilege account home or sandbox deny rule; for now, do not point a CLADEX self-review or self-fix at an `accountHome` you would not trust the operator to read directly.

## Project Review swarm

- Review jobs read project source and write artifacts only under the local CLADEX data directory.
- Review jobs can consume the user's local Codex or Claude subscription/account. Use the optional account-home field when a review should use a specific `CODEX_HOME` or `CLAUDE_CONFIG_DIR`.
- Review lanes are queued behind a bounded default worker pool. Raising `CLADEX_REVIEW_MAX_PARALLEL` should be treated as a resource and account-rate-limit decision.
- Codex and Claude review lanes run against isolated scratch workspaces with no approval escalation. Claude review lanes allow only read/search/list tools; Bash and write/edit tools are disabled for review.
- Review jobs do not apply fixes. The fix-plan action writes a plan artifact only. The separate Fix Review action creates a mandatory source backup before launching any write-capable fix worker. Active Fix Review starts are idempotent per review so duplicate clicks do not launch duplicate write workers.
- Fix Review serializes write-capable phases in the shared workspace and rejects a successful task if it touched files outside the task assignment.
- CLADEX self-review is blocked by default and requires an explicit opt-in. A source backup is created before self-review starts, and Fix Review for the CLADEX repo requires both that completed self-review flag and a separate self-fix approval.
- Backup restore is CLI-only and requires `--confirm <backup-id>` to avoid accidental source overwrite through the remote UI.
- Review and backup ids are pattern-validated before filesystem access; restore preserves ignored dependency/cache folders and secret-like local files.
- Review reports intentionally avoid storing detected credential values. If a finding says a secret-like value exists, rotate the value before publishing.
- Do not run review swarms against untrusted repositories with broad AI permissions. Repository instructions and files can be prompt-injection input.

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
