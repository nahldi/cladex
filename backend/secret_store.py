"""Per-profile secret-at-rest storage for Discord bot tokens (and any other
sensitive profile env values).

Why this exists: profile `.env` files in `%LOCALAPPDATA%/discord-codex-relay/
profiles/` (and the Claude equivalent) historically held `DISCORD_BOT_TOKEN`
in plaintext. Those files have mode 0o600 on disk, but they still leak
through cloud-sync (OneDrive/Dropbox), accidental `git add`, screen
recordings, support-bundle copies, and casual filesystem inspection. A
hijacked Discord bot token can do real damage (DM spam, channel takeover,
data exfiltration on connected guilds).

Design:

- On write, the env-file writer hands the token to `store_secret(...)`,
  which encrypts/persists it under `%LOCALAPPDATA%/cladex/secrets/<id>.bin`
  using the OS-native keystore (Windows DPAPI today; macOS/Linux fall back
  to a 0o600 file with a clear note that filesystem permissions are the
  protection there). The function returns a `secret-ref:<scheme>:<id>`
  string that goes into the `.env` in place of the plaintext value.

- On read, the env-file loader detects the `secret-ref:` prefix and calls
  `resolve_secret(...)` to return the literal token. Existing plaintext
  `.env` values pass through unchanged so old profiles keep working. The
  next time the profile is saved, the writer transparently migrates the
  value to the new format.

- DPAPI binds the ciphertext to the current Windows user account, so a
  copy of the secret file is useless on another account or another
  machine. This addresses the "leak via cloud sync / git push / accidental
  share" risk the operator flagged.

- The `.env` file no longer carries the bot token in cleartext, so a
  support bundle, a one-off `cat`, or a casual `ls` reveals nothing
  exploitable.

Backward compatibility:

- `resolve_secret_value()` returns any literal value as-is, so existing
  plaintext `.env` keeps working.
- `cladex secrets migrate` (CLI) walks all profile `.env` files and
  rewrites them to the secret-ref format in one pass.

Threat model:

- IN scope: cloud sync of `%LOCALAPPDATA%/discord-codex-relay/profiles/`,
  accidental `git add` of a profile env, support-bundle copies, casual
  filesystem inspection on the host.
- OUT of scope: full kernel-level adversary, in-process malware running
  as the same Windows user (it can call DPAPI itself), or a relay process
  that has already loaded the token into its own memory.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import secrets as _stdlib_secrets
import sys
from ctypes import wintypes
from pathlib import Path

SECRET_REF_PREFIX = "secret-ref:"
_SECRET_REF_RE = re.compile(r"^secret-ref:(?P<scheme>[a-z0-9]+):(?P<sid>[a-zA-Z0-9_\-]+)$")
# Sensitive env keys that get auto-routed through the secret store.
SENSITIVE_KEYS = frozenset(
    {
        "DISCORD_BOT_TOKEN",
        "CLADEX_REGISTER_DISCORD_BOT_TOKEN",
    }
)


def _secrets_root() -> Path:
    """Return the on-disk root for secret blobs.

    Lives next to the existing CLADEX runtime data so backups + cleanup can
    treat it the same way. The root is created lazily with mode 0o700.
    """
    override = os.environ.get("CLADEX_SECRETS_ROOT")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "cladex" / "secrets" if local else Path.home() / "AppData" / "Local" / "cladex" / "secrets"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "cladex" / "secrets"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / "cladex" / "secrets"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _secret_blob_path(secret_id: str) -> Path:
    if not secret_id or not re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", secret_id):
        raise ValueError(f"invalid secret id: {secret_id!r}")
    return _secrets_root() / f"{secret_id}.bin"


# -----------------------------------------------------------------------------
# Windows DPAPI bindings via ctypes (stdlib only).
# -----------------------------------------------------------------------------


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.crypt32  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _dpapi_protect(data: bytes, *, entropy: bytes) -> bytes:
    if not _dpapi_available():
        raise OSError("DPAPI not available on this platform")
    in_blob = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
    entropy_blob = _DATA_BLOB(
        len(entropy),
        ctypes.cast(ctypes.create_string_buffer(entropy, len(entropy)), ctypes.POINTER(ctypes.c_byte)),
    )
    out_blob = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(f"CryptProtectData failed (GetLastError={ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore[attr-defined]


def _dpapi_unprotect(encrypted: bytes, *, entropy: bytes) -> bytes:
    if not _dpapi_available():
        raise OSError("DPAPI not available on this platform")
    in_blob = _DATA_BLOB(
        len(encrypted),
        ctypes.cast(ctypes.create_string_buffer(encrypted, len(encrypted)), ctypes.POINTER(ctypes.c_byte)),
    )
    entropy_blob = _DATA_BLOB(
        len(entropy),
        ctypes.cast(ctypes.create_string_buffer(entropy, len(entropy)), ctypes.POINTER(ctypes.c_byte)),
    )
    out_blob = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(f"CryptUnprotectData failed (GetLastError={ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore[attr-defined]


# -----------------------------------------------------------------------------
# Cross-platform store / resolve API.
# -----------------------------------------------------------------------------


def _new_secret_id(profile_hint: str | None = None) -> str:
    """Build a stable, opaque id for the secret blob.

    Includes a profile hint (sanitized) so operators inspecting
    `%LOCALAPPDATA%/cladex/secrets/` can correlate by eye, plus 16 bytes
    of entropy so two profiles with the same hint don't collide.
    """
    safe_hint = re.sub(r"[^A-Za-z0-9_\-]+", "-", str(profile_hint or "profile")).strip("-")[:32] or "profile"
    return f"{safe_hint}-{_stdlib_secrets.token_hex(8)}"


def _entropy_for(secret_id: str) -> bytes:
    """Per-secret entropy mixed into DPAPI so the ciphertext is bound both
    to the current Windows user AND to this specific secret id. Without
    per-secret entropy, an attacker who could read one secret blob could
    decrypt others as the same user."""
    return hashlib.sha256(f"cladex/v1/{secret_id}".encode("utf-8")).digest()


def store_secret(value: str, *, profile_hint: str | None = None) -> str:
    """Store `value` and return a `secret-ref:<scheme>:<id>` reference.

    Empty/None values pass through unchanged (returned as the empty string)
    so callers can store an empty value without us creating an empty
    encrypted blob.
    """
    if value is None or value == "":
        return ""
    secret_id = _new_secret_id(profile_hint)
    payload = value.encode("utf-8")
    if _dpapi_available():
        encrypted = _dpapi_protect(payload, entropy=_entropy_for(secret_id))
        body = base64.b64encode(encrypted).decode("ascii")
        scheme = "dpapi"
    else:
        body = base64.b64encode(payload).decode("ascii")
        scheme = "fs0600"
    blob = {"v": 1, "scheme": scheme, "id": secret_id, "body": body}
    blob_path = _secret_blob_path(secret_id)
    blob_path.write_text(json.dumps(blob), encoding="utf-8")
    try:
        os.chmod(blob_path, 0o600)
    except OSError:
        pass
    return f"{SECRET_REF_PREFIX}{scheme}:{secret_id}"


def is_secret_ref(value: str | None) -> bool:
    return bool(value and isinstance(value, str) and _SECRET_REF_RE.fullmatch(value.strip()))


def resolve_secret(reference: str) -> str:
    """Resolve a `secret-ref:<scheme>:<id>` reference to its plaintext.

    Raises FileNotFoundError if the blob is missing (e.g. operator deleted
    the secrets dir) — callers should surface this clearly so the operator
    knows to re-enter the token rather than running with `secret-ref:...`
    as the bot token literal.
    """
    match = _SECRET_REF_RE.fullmatch(reference.strip())
    if not match:
        raise ValueError(f"not a secret reference: {reference!r}")
    scheme = match.group("scheme")
    secret_id = match.group("sid")
    blob_path = _secret_blob_path(secret_id)
    if not blob_path.exists():
        raise FileNotFoundError(
            f"Secret blob missing for reference {reference!r} (looked at {blob_path}). "
            "The profile token must be re-entered."
        )
    blob = json.loads(blob_path.read_text(encoding="utf-8"))
    body = base64.b64decode(blob["body"])
    if scheme == "dpapi":
        return _dpapi_unprotect(body, entropy=_entropy_for(secret_id)).decode("utf-8")
    if scheme == "fs0600":
        return body.decode("utf-8")
    raise ValueError(f"unsupported secret scheme: {scheme!r}")


def resolve_secret_value(value: str | None) -> str:
    """Resolve a value if it looks like a secret reference; otherwise pass through.

    Returns the literal value for plaintext (backward compat) and the
    decrypted token for `secret-ref:...`. Empty/None becomes empty string.
    """
    if not value:
        return ""
    text = value.strip()
    if is_secret_ref(text):
        return resolve_secret(text)
    return value


def delete_secret(reference: str) -> None:
    """Best-effort delete of a stored secret. Safe to call with a literal
    plaintext value (no-op) so profile-removal code can call it
    unconditionally."""
    if not is_secret_ref(reference):
        return
    match = _SECRET_REF_RE.fullmatch(reference.strip())
    assert match is not None
    blob_path = _secret_blob_path(match.group("sid"))
    try:
        blob_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def materialize_env_secrets(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of `env` with every value resolved (`secret-ref:...`
    looked up). Callers that want to spawn a child subprocess pass the
    result; the .env on disk keeps the references."""
    resolved: dict[str, str] = {}
    for key, value in env.items():
        try:
            resolved[key] = resolve_secret_value(value)
        except (FileNotFoundError, ValueError, OSError):
            # Leave the literal in place; the consumer (relay startup,
            # discord.py login) will surface a clear auth failure rather
            # than us silently producing an empty token.
            resolved[key] = value
    return resolved


def encrypt_sensitive_env(env: dict[str, str], *, profile_hint: str | None = None) -> dict[str, str]:
    """Return a copy of `env` where each `SENSITIVE_KEYS` value that is
    currently a literal token has been moved to the secret store and
    replaced with a `secret-ref:...` reference. Already-encrypted values
    pass through. Empty values pass through.

    This is the function `_write_env_file` calls before persisting, so
    saving a profile is the migration trigger for that profile.
    """
    rewritten, _stale_refs = prepare_sensitive_env_for_write(env, profile_hint=profile_hint)
    return rewritten


def prepare_sensitive_env_for_write(
    env: dict[str, str],
    *,
    profile_hint: str | None = None,
    existing_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Prepare env values for disk persistence.

    Returns `(rewritten_env, stale_secret_refs)`. Existing secret refs are
    reused when their resolved plaintext matches the value being saved, so
    a metadata-only profile update does not create a new blob. If a token is
    changed or removed, the old ref is returned in `stale_secret_refs`; the
    caller should delete those refs only after the env file write succeeds.
    """
    rewritten: dict[str, str] = dict(env)
    previous = existing_env or {}
    stale_refs: list[str] = []
    for key in SENSITIVE_KEYS:
        old_value = previous.get(key, "")
        if key not in rewritten:
            if is_secret_ref(old_value):
                stale_refs.append(old_value)
            continue
        value = rewritten[key]
        if not value:
            if is_secret_ref(old_value):
                stale_refs.append(old_value)
            continue
        if is_secret_ref(value):
            if is_secret_ref(old_value) and old_value != value:
                stale_refs.append(old_value)
            continue
        if is_secret_ref(old_value):
            try:
                if resolve_secret_value(old_value) == value:
                    rewritten[key] = old_value
                    continue
            except (FileNotFoundError, ValueError, OSError):
                pass
        rewritten[key] = store_secret(value, profile_hint=profile_hint or key.lower())
        if is_secret_ref(old_value) and old_value != rewritten[key]:
            stale_refs.append(old_value)
    return rewritten, sorted(set(stale_refs))
