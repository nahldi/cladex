from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl  # type: ignore[import]

from relay_common import atomic_write_json, atomic_write_text, slugify, workspace_root


_FILE_LOCK_REGISTRY_GUARD = threading.Lock()
_FILE_LOCK_REGISTRY: dict[str, threading.Lock] = {}


def _path_lock_key(path: Path) -> str:
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return os.path.abspath(str(path))


def _thread_lock_for(path: Path) -> threading.Lock:
    key = _path_lock_key(path)
    with _FILE_LOCK_REGISTRY_GUARD:
        lock = _FILE_LOCK_REGISTRY.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCK_REGISTRY[key] = lock
        return lock


@contextlib.contextmanager
def _serialize_path(path: Path):
    """Serialize read-modify-write of ``path`` across threads and processes."""
    thread_lock = _thread_lock_for(path)
    thread_lock.acquire()
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / f".{path.name}.lock"
        handle = open(lock_path, "a+b")
        if os.name == "nt":
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if handle is not None:
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                handle.close()
            except OSError:
                pass
        thread_lock.release()


MEMORY_DIR_NAME = "memory"
RELAY_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATUS_SECTIONS = (
    "Current objective",
    "Active task",
    "Owner",
    "Worktree / branch",
    "Last verified commit or diff scope",
    "Last validation result",
    "Current blocker",
    "Exact next step",
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_iso_from_ts(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def _repo_branch(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.stdout.strip() or "HEAD"
    except Exception:
        return "HEAD"


def _head_commit(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.stdout.strip() or ""
    except Exception:
        return ""


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _ensure_sectioned_markdown(path: Path, title: str, sections: dict[str, str]) -> None:
    lines = [f"# {title}", ""]
    headings = STATUS_SECTIONS if title == "STATUS" else tuple(sections.keys())
    for heading in headings:
        body = sections.get(heading, "").strip() or "_none_"
        lines.extend([f"## {heading}", body, ""])
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def _write_status_file(path: Path, fields: dict[str, str | None]) -> None:
    with _serialize_path(path):
        current = _parse_sections(_read_text(path))
        for key, value in fields.items():
            if value is not None:
                current[key] = value.strip()
        _ensure_sectioned_markdown(path, "STATUS", {heading: current.get(heading, "") for heading in STATUS_SECTIONS})


def _append_markdown_entry(path: Path, title: str, body: str, *, prepend: bool = False) -> None:
    with _serialize_path(path):
        existing = _read_text(path).strip()
        header = f"# {title}\n"
        if existing.startswith(header):
            existing_body = existing[len(header) :].lstrip("\n")
        else:
            existing_body = existing
        entry = body.strip() + "\n"
        if prepend:
            payload = header + "\n" + entry + ("\n" + existing_body if existing_body else "")
        else:
            payload = header + "\n" + ((existing_body + "\n\n") if existing_body else "") + entry
        atomic_write_text(path, payload.rstrip() + "\n")


def _prune_markdown_history(path: Path, title: str, *, keep_entries: int, max_chars: int | None = None) -> None:
    with _serialize_path(path):
        _prune_markdown_history_locked(path, title, keep_entries=keep_entries, max_chars=max_chars)


def _prune_markdown_history_locked(path: Path, title: str, *, keep_entries: int, max_chars: int | None = None) -> None:
    existing = _read_text(path).strip()
    header = f"# {title}"
    if not existing:
        atomic_write_text(path, header + "\n")
        return
    body = existing[len(header) :].lstrip("\n") if existing.startswith(header) else existing
    entries = _iter_markdown_entries(body)
    kept_entries: list[str] = []
    char_budget = None if max_chars is None else max(max_chars - len(header) - 2, 0)
    used_chars = 0
    for entry in entries[:keep_entries]:
        entry_len = len(entry) + (2 if kept_entries else 0)
        if char_budget is not None and kept_entries and used_chars + entry_len > char_budget:
            break
        if char_budget is not None and not kept_entries and len(entry) > char_budget:
            kept_entries.append(entry[: max(char_budget - 15, 0)].rstrip() + " ...[truncated]")
            used_chars = len(kept_entries[0])
            break
        kept_entries.append(entry)
        used_chars += entry_len
    payload = header + "\n"
    if kept_entries:
        payload += "\n" + "\n\n".join(kept_entries).rstrip() + "\n"
    if max_chars is not None and len(payload) > max_chars:
        overflow = len(payload) - max_chars
        if kept_entries:
            trimmed_last = kept_entries[-1]
            keep_len = max(len(trimmed_last) - overflow - 15, 0)
            kept_entries[-1] = trimmed_last[:keep_len].rstrip() + " ...[truncated]"
            payload = header + "\n"
            if kept_entries:
                payload += "\n" + "\n\n".join(kept_entries).rstrip() + "\n"
        if len(payload) > max_chars:
            payload = payload[:max_chars].rstrip() + "\n"
    atomic_write_text(path, payload)


def _normalize_paths(files: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in files:
        text = item.strip().replace("\\", "/")
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _glob_prefix(value: str) -> str:
    prefix = value.replace("\\", "/")
    for token in ("**", "*", "?"):
        if token in prefix:
            prefix = prefix.split(token, 1)[0]
    return prefix.strip("/").lower()


def _glob_is_broad(value: str) -> bool:
    normalized = value.replace("\\", "/").strip().strip("/")
    return normalized in {"", "*", "**", "**/*"}


def _globs_conflict(left: str, right: str) -> bool:
    if _glob_is_broad(left) or _glob_is_broad(right):
        return True
    lprefix = _glob_prefix(left)
    rprefix = _glob_prefix(right)
    if not lprefix or not rprefix:
        left_norm = left.replace("\\", "/").strip().lower()
        right_norm = right.replace("\\", "/").strip().lower()
        if left_norm == right_norm:
            return True
        if left_norm.startswith("**/*."):
            return right_norm.endswith(left_norm[4:])
        if right_norm.startswith("**/*."):
            return left_norm.endswith(right_norm[4:])
        return False
    return _path_prefixes_overlap(lprefix, rprefix)


def _path_prefixes_overlap(left: str, right: str) -> bool:
    left = left.strip("/").lower()
    right = right.strip("/").lower()
    if left == right:
        return True
    return left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _extract_bullets(text: str, patterns: tuple[str, ...], *, limit: int = 6) -> list[str]:
    lowered_patterns = tuple(pattern.lower() for pattern in patterns)
    results: list[str] = []
    for raw_line in text.splitlines():
        cleaned = raw_line.strip().lstrip("-* ").strip()
        lowered = cleaned.lower()
        if cleaned and any(pattern in lowered for pattern in lowered_patterns):
            if cleaned not in results:
                results.append(cleaned)
        if len(results) >= limit:
            break
    return results


def _normalize_compare_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower()).strip(" .")


def _trim_command_entry(command: str, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", command.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + " ...[truncated]"


def _summarize_commands(commands_run: list[str], *, limit: int = 8) -> list[str]:
    cleaned: list[str] = []
    for command in commands_run:
        text = _trim_command_entry(command)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _iter_markdown_entries(text: str) -> list[str]:
    entries: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            if current:
                entries.append(current)
            current = [raw_line]
            continue
        if current:
            current.append(raw_line)
    if current:
        entries.append(current)
    return ["\n".join(lines).strip() for lines in entries if any(line.strip() for line in lines)]


def _extract_latest_handoff_highlights(text: str, *, limit: int = 6) -> list[str]:
    generic_next_steps = {
        "continue from status.md",
        "continue from status.md and handoff.md",
        "continue from status.md and the latest handoff",
        "continue the current task using repo memory as source of truth",
    }
    for entry in _iter_markdown_entries(text):
        results: list[str] = []
        seen_values: set[str] = set()
        for raw_line in entry.splitlines():
            cleaned = raw_line.strip().lstrip("-* ").strip()
            if not cleaned or ":" not in cleaned:
                continue
            label, _, value = cleaned.partition(":")
            label_normalized = _normalize_compare_text(label)
            value_normalized = _normalize_compare_text(value)
            if not value_normalized:
                continue
            if label_normalized == "exact next step" and value_normalized in generic_next_steps:
                continue
            if value_normalized in seen_values:
                continue
            if label_normalized in {"result", "blocker", "exact next step", "changed files"}:
                results.append(cleaned)
                seen_values.add(value_normalized)
            if len(results) >= limit:
                break
        if results:
            return results
    return []


def _extract_preferences_and_constraints(text: str) -> tuple[list[str], list[str]]:
    preferences: list[str] = []
    constraints: list[str] = []
    for raw_sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = re.sub(r"\s+", " ", raw_sentence.strip())
        if not sentence:
            continue
        if _is_transient_constraint_candidate(sentence):
            continue
        lowered = sentence.lower()
        if any(token in lowered for token in ("prefer ", "default ", "usually ", "by default", "keep it ")):
            if sentence not in preferences:
                preferences.append(sentence)
        if any(token in lowered for token in ("must ", "must not", "never ", "do not ", "don't ", "only ", "non-negotiable", "forbidden")):
            if sentence not in constraints:
                constraints.append(sentence)
    return preferences[:8], constraints[:12]


def _is_transient_constraint_candidate(text: str) -> bool:
    sentence = re.sub(r"\s+", " ", text.strip())
    lowered = sentence.lower()
    if not sentence:
        return True
    if len(sentence) > 240:
        return True
    if sentence.endswith("?"):
        return True
    transient_markers = (
        "reply with",
        "reply only",
        "reply in one",
        "respond with",
        "answer with",
        "only answer",
        "just say",
        "say yes",
        "say no",
        "do nothing else",
        "only the path",
        "for relay audits",
        "what repo is source of truth",
        "standing by",
        "awaiting ",
        "ready to work",
        "no blocker",
        "audit complete",
        "both audits complete",
        "same on my side",
        "how we feelin",
        "hows the relays",
        "sage?",
        "forge?",
    )
    if any(marker in lowered for marker in transient_markers):
        return True
    if re.match(r"^[a-z0-9_-]{2,24}\s+(are|r|why|what|when|where|how|do|did|will|can|could|should)\b", lowered):
        return True
    if any(token in sentence for token in ("OBSERVE ", "-> exit", "C:\\", ".exe", "Get-Content", "powershell.exe", "cmd /c")):
        return True
    return False


def _prune_known_facts_payload(facts: dict[str, Any]) -> dict[str, Any]:
    cleaned = {"preferences": [], "constraints": [], "facts": []}
    for key, limit in (("preferences", 8), ("constraints", 12), ("facts", 24)):
        seen: set[str] = set()
        for raw_item in facts.get(key, []) or []:
            item = re.sub(r"\s+", " ", str(raw_item).strip())
            if not item:
                continue
            if key in {"preferences", "constraints"} and _is_transient_constraint_candidate(item):
                continue
            normalized = _normalize_compare_text(item)
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned[key].append(item)
            if len(cleaned[key]) >= limit:
                break
    return cleaned


def _extract_next_step(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-* ").strip()
        lowered = line.lower()
        if any(token in lowered for token in ("next step", "next:", "todo:", "remaining:", "follow-up")):
            return line
    cleaned = re.sub(r"\s+", " ", text.strip())
    return cleaned[:220]


def _extract_blocker(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-* ").strip()
        lowered = line.lower()
        if "blocker" in lowered or "blocked" in lowered:
            return line
    return ""


def _extract_decision_candidates(text: str, *, limit: int = 4) -> list[str]:
    decisions: list[str] = []
    for raw_sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = re.sub(r"\s+", " ", raw_sentence.strip())
        lowered = sentence.lower()
        if not sentence:
            continue
        if any(token in lowered for token in ("decide", "decision", "we will", "use ", "prefer ", "must ", "should ")) and sentence not in decisions:
            decisions.append(sentence)
        if len(decisions) >= limit:
            break
    return decisions


def _compact_join(items: list[str], *, limit: int = 6) -> str:
    return "; ".join(item for item in items[:limit] if item.strip())


@dataclass(slots=True)
class ChannelBinding:
    channel_id: str
    project_id: str
    repo_path: Path
    worktree_path: Path
    current_branch: str
    primary_thread_id: str | None = None
    backend: str = "codex-app-server"


@dataclass(slots=True)
class LeaseConflict:
    task_id: str
    owner_agent: str
    path_glob: str


class TaskLeaseConflictError(RuntimeError):
    def __init__(self, conflicts: list[LeaseConflict]) -> None:
        self.conflicts = conflicts
        details = ", ".join(f"{item.owner_agent}:{item.path_glob}" for item in conflicts)
        super().__init__(f"Task lease conflict: {details}")


class RuntimeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def synthetic_thread_id(self, seed: str) -> str:
        return f"cli-{slugify(seed)}"

    def list_threads_for_project(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT thread_id, backend, status, channel_id, updated_at FROM codex_threads WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    discord_channel_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    primary_thread_id TEXT,
                    current_branch TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codex_threads (
                    thread_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_turn_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    owner_agent TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target_files_glob TEXT NOT NULL,
                    status TEXT NOT NULL,
                    validation_plan_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence TEXT,
                    confidence REAL NOT NULL,
                    supersedes_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification_records (
                    id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    evidence_text TEXT NOT NULL,
                    command_log TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turn_records (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    files_changed_json TEXT NOT NULL,
                    commands_run_json TEXT NOT NULL,
                    validations_json TEXT NOT NULL,
                    next_step TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_claims (
                    id TEXT PRIMARY KEY,
                    source_agent TEXT NOT NULL,
                    message_ref TEXT NOT NULL,
                    claim_text TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_ownership (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    path_glob TEXT NOT NULL,
                    owner_agent TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS compaction_events (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_state (
                    project_id TEXT PRIMARY KEY,
                    last_memory_sync_at TEXT,
                    last_handoff_sync_at TEXT,
                    last_status_sync_at TEXT
                );
                CREATE TABLE IF NOT EXISTS relay_message_receipts (
                    direction TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    receipt_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(direction, channel_id, receipt_key)
                );
                CREATE TABLE IF NOT EXISTS relay_restart_events (
                    id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "turn_records", "command_exit_codes_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "turn_records", "cwd", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "approvals_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "turn_records", "blocker", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "error_category", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "started_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "completed_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "backend", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "degraded", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "turn_records", "side_effects_synced_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "turn_records", "side_effects_claimed_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "channels", "last_rebind_at", "TEXT")
            conn.execute(
                """
                DELETE FROM turn_records
                WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM turn_records GROUP BY turn_id
                )
                """
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_records_turn_id ON turn_records(turn_id)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")

    def upsert_channel(self, binding: ChannelBinding) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO channels(discord_channel_id, project_id, repo_path, worktree_path, primary_thread_id, current_branch, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_channel_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    repo_path=excluded.repo_path,
                    worktree_path=excluded.worktree_path,
                    primary_thread_id=COALESCE(excluded.primary_thread_id, channels.primary_thread_id),
                    current_branch=excluded.current_branch,
                    updated_at=excluded.updated_at
                """,
                (
                    binding.channel_id,
                    binding.project_id,
                    str(binding.repo_path),
                    str(binding.worktree_path),
                    binding.primary_thread_id,
                    binding.current_branch,
                    now,
                    now,
                ),
            )

    def get_channel(self, channel_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM channels WHERE discord_channel_id = ?",
                (channel_id,),
            ).fetchone()

    def claim_message_receipt(
        self,
        *,
        direction: str,
        channel_id: str,
        receipt_key: str,
        fingerprint: str = "",
    ) -> bool:
        normalized_key = str(receipt_key or "").strip()
        if not normalized_key:
            return True
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO relay_message_receipts(direction, channel_id, receipt_key, fingerprint, created_at)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (direction, channel_id, normalized_key, fingerprint, _now_iso()),
                )
                self._prune_message_receipts(conn, direction=direction, channel_id=channel_id)
                return True
            except sqlite3.IntegrityError:
                return False

    def _prune_message_receipts(self, conn: sqlite3.Connection, *, direction: str, channel_id: str) -> None:
        try:
            max_per_channel = int(os.environ.get("CLADEX_RELAY_RECEIPT_MAX_PER_CHANNEL", "1000") or "1000")
        except ValueError:
            max_per_channel = 1000
        try:
            max_age_days = int(os.environ.get("CLADEX_RELAY_RECEIPT_MAX_AGE_DAYS", "14") or "14")
        except ValueError:
            max_age_days = 14
        max_per_channel = max(max_per_channel, 100)
        max_age_days = max(max_age_days, 1)
        cutoff = _now_iso_from_ts(time.time() - (max_age_days * 24 * 60 * 60))
        conn.execute(
            "DELETE FROM relay_message_receipts WHERE created_at < ?",
            (cutoff,),
        )
        conn.execute(
            """
            DELETE FROM relay_message_receipts
            WHERE direction = ? AND channel_id = ?
              AND rowid NOT IN (
                SELECT rowid FROM relay_message_receipts
                WHERE direction = ? AND channel_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
              )
            """,
            (direction, channel_id, direction, channel_id, max_per_channel),
        )

    def has_message_receipt(self, *, direction: str, channel_id: str, receipt_key: str) -> bool:
        normalized_key = str(receipt_key or "").strip()
        if not normalized_key:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM relay_message_receipts
                WHERE direction = ? AND channel_id = ? AND receipt_key = ?
                LIMIT 1
                """,
                (direction, channel_id, normalized_key),
            ).fetchone()
            return row is not None

    def release_message_receipt(self, *, direction: str, channel_id: str, receipt_key: str) -> None:
        normalized_key = str(receipt_key or "").strip()
        if not normalized_key:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                DELETE FROM relay_message_receipts
                WHERE direction = ? AND channel_id = ? AND receipt_key = ?
                """,
                (direction, channel_id, normalized_key),
            )

    def record_restart(self, agent_name: str, reason: str = "normal") -> None:
        """Record a relay restart event for churn detection."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO relay_restart_events(id, agent_name, reason, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), agent_name, reason, _now_iso()),
            )

    def count_recent_restarts(self, agent_name: str, window_seconds: int = 300) -> int:
        """Count restarts in the last N seconds to detect churn."""
        cutoff = time.time() - window_seconds
        cutoff_iso = _now_iso_from_ts(cutoff)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM relay_restart_events WHERE agent_name = ? AND created_at > ?",
                (agent_name, cutoff_iso),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def is_restart_churn(self, agent_name: str, threshold: int = 5, window_seconds: int = 300) -> bool:
        """Check if the relay is in a restart churn loop."""
        return self.count_recent_restarts(agent_name, window_seconds) >= threshold

    def bind_thread(
        self,
        *,
        thread_id: str,
        project_id: str,
        channel_id: str,
        backend: str,
        status: str,
        last_turn_id: str | None = None,
        rebound: bool = False,
    ) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_threads(thread_id, project_id, channel_id, backend, status, last_turn_id, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    channel_id=excluded.channel_id,
                    backend=excluded.backend,
                    status=excluded.status,
                    last_turn_id=COALESCE(excluded.last_turn_id, codex_threads.last_turn_id),
                    updated_at=excluded.updated_at
                """,
                (thread_id, project_id, channel_id, backend, status, last_turn_id, now, now),
            )
            conn.execute(
                "UPDATE channels SET primary_thread_id = ?, updated_at = ?, last_rebind_at = ? WHERE discord_channel_id = ?",
                (thread_id, now, now if rebound else None, channel_id),
            )

    def is_turn_recorded(self, turn_id: str) -> bool:
        """Check if a turn has already been recorded (dedup check)."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM turn_records WHERE turn_id = ? LIMIT 1",
                (turn_id,),
            ).fetchone()
            return row is not None

    def record_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        summary: str,
        files_changed: list[str],
        commands_run: list[str],
        validations: list[str],
        next_step: str,
        command_exit_codes: list[int] | None = None,
        cwd: str = "",
        approvals: list[str] | None = None,
        blocker: str = "",
        error_category: str = "",
        started_at: str = "",
        completed_at: str = "",
        backend: str = "",
        degraded: bool = False,
    ) -> bool:
        """Record a turn. Returns False if turn_id was already recorded (dedup)."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO turn_records(
                    id, thread_id, turn_id, summary, files_changed_json, commands_run_json, validations_json,
                    next_step, created_at, command_exit_codes_json, cwd, approvals_json, blocker,
                    error_category, started_at, completed_at, backend, degraded
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    thread_id,
                    turn_id,
                    summary,
                    json.dumps(_normalize_paths(files_changed)),
                    json.dumps(commands_run),
                    json.dumps(validations),
                    next_step,
                    _now_iso(),
                    json.dumps(command_exit_codes or []),
                    cwd,
                    json.dumps(approvals or []),
                    blocker,
                    error_category,
                    started_at,
                    completed_at,
                    backend,
                    1 if degraded else 0,
                ),
            )
            if cursor.rowcount == 0:
                return False
            conn.execute(
                "UPDATE codex_threads SET last_turn_id = ?, updated_at = ? WHERE thread_id = ?",
                (turn_id, _now_iso(), thread_id),
            )
            return True

    def latest_memory_entry(self, *, project_id: str, entry_type: str, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_entries
                WHERE project_id = ? AND type = ? AND key = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (project_id, entry_type, key),
            ).fetchone()
        return dict(row) if row else None

    def add_memory_entry(
        self,
        *,
        project_id: str,
        entry_type: str,
        key: str,
        content: str,
        source: str,
        evidence: str = "",
        confidence: float = 1.0,
        supersedes_id: str | None = None,
    ) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries(id, project_id, type, key, content, source, evidence, confidence, supersedes_id, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    project_id,
                    entry_type,
                    key,
                    content,
                    source,
                    evidence,
                    confidence,
                    supersedes_id,
                    now,
                    now,
                ),
            )

    def upsert_memory_entry(
        self,
        *,
        project_id: str,
        entry_type: str,
        key: str,
        content: str,
        source: str,
        evidence: str = "",
        confidence: float = 1.0,
    ) -> str:
        latest = self.latest_memory_entry(project_id=project_id, entry_type=entry_type, key=key)
        if latest and latest.get("content") == content and latest.get("evidence", "") == evidence:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE memory_entries SET updated_at = ?, confidence = ? WHERE id = ?",
                    (_now_iso(), confidence, latest["id"]),
                )
            return str(latest["id"])
        new_id = str(uuid.uuid4())
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries(id, project_id, type, key, content, source, evidence, confidence, supersedes_id, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    project_id,
                    entry_type,
                    key,
                    content,
                    source,
                    evidence,
                    confidence,
                    latest["id"] if latest else None,
                    now,
                    now,
                ),
            )
        return new_id

    def upsert_sync_state(self, project_id: str, *, memory: bool = False, handoff: bool = False, status: bool = False) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_state WHERE project_id = ?", (project_id,)).fetchone()
            payload = {
                "last_memory_sync_at": now if memory else (row["last_memory_sync_at"] if row else None),
                "last_handoff_sync_at": now if handoff else (row["last_handoff_sync_at"] if row else None),
                "last_status_sync_at": now if status else (row["last_status_sync_at"] if row else None),
            }
            conn.execute(
                """
                INSERT INTO sync_state(project_id, last_memory_sync_at, last_handoff_sync_at, last_status_sync_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    last_memory_sync_at=excluded.last_memory_sync_at,
                    last_handoff_sync_at=excluded.last_handoff_sync_at,
                    last_status_sync_at=excluded.last_status_sync_at
                """,
                (project_id, payload["last_memory_sync_at"], payload["last_handoff_sync_at"], payload["last_status_sync_at"]),
            )

    def turn_side_effects_synced(self, turn_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT side_effects_synced_at FROM turn_records WHERE turn_id = ? LIMIT 1",
                (turn_id,),
            ).fetchone()
        return bool(row and row["side_effects_synced_at"])

    def claim_turn_side_effects(self, turn_id: str, *, stale_after_seconds: int = 600) -> bool:
        cutoff = _now_iso_from_ts(time.time() - max(stale_after_seconds, 1))
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE turn_records
                SET side_effects_claimed_at = ?
                WHERE turn_id = ?
                  AND side_effects_synced_at = ''
                  AND (side_effects_claimed_at = '' OR side_effects_claimed_at < ?)
                """,
                (_now_iso(), turn_id, cutoff),
            )
            return cursor.rowcount > 0

    def clear_turn_side_effect_claim(self, turn_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE turn_records SET side_effects_claimed_at = '' WHERE turn_id = ? AND side_effects_synced_at = ''",
                (turn_id,),
            )

    def mark_turn_side_effects_synced(self, turn_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE turn_records SET side_effects_synced_at = ?, side_effects_claimed_at = '' WHERE turn_id = ?",
                (_now_iso(), turn_id),
            )

    def record_claim(self, *, source_agent: str, message_ref: str, claim_text: str, claim_type: str, verification_status: str) -> str:
        claim_id = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_claims(id, source_agent, message_ref, claim_text, claim_type, verification_status, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (claim_id, source_agent, message_ref, claim_text, claim_type, verification_status, _now_iso()),
            )
        return claim_id

    def update_claim_verdict(self, *, claim_id: str, verdict: str, evidence_text: str, command_log: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE agent_claims SET verification_status = ? WHERE id = ?",
                (verdict, claim_id),
            )
            conn.execute(
                """
                INSERT INTO verification_records(id, claim_id, verdict, evidence_text, command_log, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), claim_id, verdict, evidence_text, command_log, _now_iso()),
            )

    def unresolved_claims(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_claims.*
                FROM agent_claims
                JOIN channels ON channels.discord_channel_id = SUBSTR(agent_claims.message_ref, 1, INSTR(agent_claims.message_ref, ':') - 1)
                WHERE channels.project_id = ? AND agent_claims.verification_status = 'unresolved'
                ORDER BY agent_claims.created_at DESC
                LIMIT 10
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_claims(self, project_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_claims.*
                FROM agent_claims
                JOIN channels ON channels.discord_channel_id = SUBSTR(agent_claims.message_ref, 1, INSTR(agent_claims.message_ref, ':') - 1)
                WHERE channels.project_id = ?
                ORDER BY agent_claims.created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_turns(self, thread_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM turn_records WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_task(
        self,
        *,
        channel_id: str,
        project_id: str,
        owner_agent: str,
        title: str,
        target_files: list[str],
        validation: list[str],
        lease_seconds: int = 1800,
        task_id: str | None = None,
    ) -> str:
        now = time.time()
        task_id = task_id or f"task-{uuid.uuid4().hex[:8]}"
        expires_at = _now_iso_from_ts(now + lease_seconds)
        started_at = _now_iso_from_ts(now)
        heartbeat_at = started_at
        targets = _normalize_paths(target_files)
        with self._lock:
            conn = self._connect()
            conn.isolation_level = None
            try:
                conn.execute("BEGIN IMMEDIATE")
                if targets:
                    conflicts = self._find_conflicts_in_conn(
                        conn,
                        project_id=project_id,
                        owner_agent=owner_agent,
                        target_files=targets,
                        now_ts=now,
                    )
                    if conflicts:
                        conn.execute("ROLLBACK")
                        raise TaskLeaseConflictError(conflicts)
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO task_leases(task_id, channel_id, owner_agent, title, target_files_glob, status, validation_plan_json, started_at, heartbeat_at, lease_expires_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            channel_id,
                            owner_agent,
                            title,
                            json.dumps(targets),
                            "claimed",
                            json.dumps(validation),
                            started_at,
                            heartbeat_at,
                            expires_at,
                        ),
                    )
                    conn.execute("DELETE FROM file_ownership WHERE task_id = ?", (task_id,))
                    for item in targets:
                        conn.execute(
                            """
                            INSERT INTO file_ownership(id, project_id, path_glob, owner_agent, task_id, lease_expires_at)
                            VALUES(?, ?, ?, ?, ?, ?)
                            """,
                            (str(uuid.uuid4()), project_id, item, owner_agent, task_id, expires_at),
                        )
                    conn.execute("COMMIT")
                except TaskLeaseConflictError:
                    raise
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.close()

        return task_id

    def heartbeat_task(self, task_id: str, *, lease_seconds: int = 1800) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM task_leases WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                return
            expires_at = _now_iso_from_ts(now + lease_seconds)
            conn.execute(
                "UPDATE task_leases SET heartbeat_at = ?, lease_expires_at = ? WHERE task_id = ?",
                (_now_iso_from_ts(now), expires_at, task_id),
            )
            conn.execute(
                "UPDATE file_ownership SET lease_expires_at = ? WHERE task_id = ?",
                (expires_at, task_id),
            )

    def release_task(self, task_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE task_leases SET status = 'released' WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM file_ownership WHERE task_id = ?", (task_id,))

    def active_task(self, channel_id: str) -> dict[str, Any] | None:
        now = _now_iso_from_ts(time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_leases
                WHERE channel_id = ? AND status = 'claimed' AND lease_expires_at >= ?
                ORDER BY heartbeat_at DESC, rowid DESC
                LIMIT 1
                """,
                (channel_id, now),
            ).fetchone()
        return dict(row) if row else None

    def list_tasks(self, channel_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_leases
                WHERE channel_id = ?
                ORDER BY CASE WHEN status = 'claimed' THEN 0 ELSE 1 END, heartbeat_at DESC, started_at DESC, rowid DESC
                """,
                (channel_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_conflicts(self, *, project_id: str, owner_agent: str, target_files: list[str], now_ts: float | None = None) -> list[LeaseConflict]:
        with self._connect() as conn:
            return self._find_conflicts_in_conn(
                conn,
                project_id=project_id,
                owner_agent=owner_agent,
                target_files=target_files,
                now_ts=now_ts,
            )

    def _find_conflicts_in_conn(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        owner_agent: str,
        target_files: list[str],
        now_ts: float | None = None,
    ) -> list[LeaseConflict]:
        now_text = _now_iso_from_ts(now_ts or time.time())
        rows = conn.execute(
            """
            SELECT file_ownership.task_id, file_ownership.owner_agent, file_ownership.path_glob
            FROM file_ownership
            WHERE project_id = ? AND owner_agent != ? AND lease_expires_at >= ?
            """,
            (project_id, owner_agent, now_text),
        ).fetchall()
        conflicts: list[LeaseConflict] = []
        for row in rows:
            for target in target_files:
                if _globs_conflict(target, row["path_glob"]):
                    conflicts.append(
                        LeaseConflict(
                            task_id=str(row["task_id"]),
                            owner_agent=str(row["owner_agent"]),
                            path_glob=str(row["path_glob"]),
                        )
                    )
                    break
        return conflicts

    def record_compaction(self, thread_id: str, event_type: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO compaction_events(id, thread_id, event_type, created_at) VALUES(?, ?, ?, ?)",
                (str(uuid.uuid4()), thread_id, event_type, _now_iso()),
            )


class WorktreeManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _valid_git_worktree(path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
                check=False,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception:
            return False
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def ensure(self, repo_path: Path, *, project_id: str, channel_id: str) -> tuple[Path, str]:
        repo_root = workspace_root(repo_path)
        if not (repo_root / ".git").exists() and not (repo_root / ".git").is_file():
            return repo_root, _repo_branch(repo_root)
        worktree_root = self.root / slugify(project_id)
        worktree_root.mkdir(parents=True, exist_ok=True)
        worktree_path = worktree_root / slugify(channel_id)
        branch_name = f"relay/{slugify(project_id)}-{slugify(channel_id)}"
        with _serialize_path(worktree_path):
            if worktree_path.exists():
                if not self._valid_git_worktree(worktree_path):
                    if worktree_path.is_dir():
                        shutil.rmtree(worktree_path)
                    else:
                        worktree_path.unlink()
                else:
                    return worktree_path, _repo_branch(worktree_path)
            if worktree_path.exists():
                return worktree_path, _repo_branch(worktree_path)
            try:
                branch_exists = subprocess.run(
                    ["git", "-C", str(repo_root), "branch", "--list", branch_name],
                    check=True,
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                ).stdout.strip()
                if branch_exists:
                    subprocess.run(
                        ["git", "-C", str(repo_root), "worktree", "add", str(worktree_path), branch_name],
                        check=True,
                        capture_output=True,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                else:
                    subprocess.run(
                        ["git", "-C", str(repo_root), "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"],
                        check=True,
                        capture_output=True,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                return worktree_path, _repo_branch(worktree_path)
            except Exception:
                if self._valid_git_worktree(worktree_path):
                    return worktree_path, _repo_branch(worktree_path)
                raise


class VerificationEngine:
    FILE_BLOCK_PATTERN = re.compile(r"files?\s+on\s+disk\s*:\s*(?P<body>.+)", re.IGNORECASE | re.DOTALL)
    FILE_NAME_PATTERN = re.compile(r"(?mi)^(?:[-*]\s+)?(?P<name>[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)$")
    COMMIT_PATTERN = re.compile(r"\bcommit\s+([0-9a-f]{7,40})\b", re.IGNORECASE)
    BRANCH_PATTERN = re.compile(r"\bbranch\s+([A-Za-z0-9._/-]+)\b", re.IGNORECASE)
    SYMBOL_PATTERN = re.compile(r"\b(?:class|function|symbol|type|method)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE)
    TEST_PATTERN = re.compile(r"(?im)^(.+?(?:pytest|vitest|tsc|lint|build).+?(?:->\s*pass|passed|\bpass\b).*)$")
    DIFF_PATTERN = re.compile(r"\b(?:changed|modified|updated|added)\s+([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)\b", re.IGNORECASE)

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def ingest_message(self, binding: ChannelBinding, *, source_agent: str, message_ref: str, text: str) -> list[dict[str, str]]:
        claims: list[tuple[str, str]] = []
        match = self.FILE_BLOCK_PATTERN.search(text)
        if match:
            names = [file_match.group("name") for file_match in self.FILE_NAME_PATTERN.finditer(match.group("body"))]
            for name in names:
                claims.append(("file_exists", name))
        for commit_id in self.COMMIT_PATTERN.findall(text):
            claims.append(("commit_exists", commit_id))
        for branch_name in self.BRANCH_PATTERN.findall(text):
            claims.append(("branch_exists", branch_name))
        for symbol in self.SYMBOL_PATTERN.findall(text):
            claims.append(("symbol_exists", symbol))
        for changed_path in self.DIFF_PATTERN.findall(text):
            claims.append(("diff_exists", changed_path))
        for command_line in self.TEST_PATTERN.findall(text):
            claims.append(("tests_passed", command_line.strip()))
        lowered = text.lower()
        if "done" in lowered or "complete" in lowered:
            claims.append(("milestone_completed", text.strip()[:280]))
        if "decision" in lowered:
            claims.append(("decision_claim", text.strip()[:280]))
        if "owner" in lowered or "claimed" in lowered:
            claims.append(("ownership_claim", text.strip()[:280]))
        if "blocker" in lowered or "status" in lowered:
            claims.append(("status_claim", text.strip()[:280]))
        results: list[dict[str, str]] = []
        for claim_type, claim_text in claims:
            claim_id = self.store.record_claim(
                source_agent=source_agent,
                message_ref=message_ref,
                claim_text=claim_text,
                claim_type=claim_type,
                verification_status="unresolved",
            )
            verdict, evidence = self.verify_claim(binding, claim_type=claim_type, claim_text=claim_text)
            self.store.update_claim_verdict(claim_id=claim_id, verdict=verdict, evidence_text=evidence)
            results.append({"claim_id": claim_id, "verdict": verdict, "evidence": evidence, "claim_text": claim_text})
        return results

    def verify_claim(self, binding: ChannelBinding, *, claim_type: str, claim_text: str) -> tuple[str, str]:
        if claim_type == "file_exists":
            candidate = binding.worktree_path / claim_text
            if candidate.exists():
                return "verified", f"{claim_text} exists at {candidate}"
            repo_candidate = binding.repo_path / claim_text
            if repo_candidate.exists():
                return "verified", f"{claim_text} exists at {repo_candidate}"
            return "false", f"{claim_text} was not found in {binding.worktree_path} or {binding.repo_path}"
        if claim_type == "diff_exists":
            result = subprocess.run(
                ["git", "-C", str(binding.worktree_path), "diff", "--name-only"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            changed = {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}
            target = claim_text.strip().replace("\\", "/")
            if target in changed:
                return "verified", f"`git diff --name-only` includes {target}"
            return "false", f"`git diff --name-only` does not include {target}"
        if claim_type == "commit_exists":
            try:
                subprocess.run(
                    ["git", "-C", str(binding.worktree_path), "cat-file", "-e", f"{claim_text}^{{commit}}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                return "verified", f"git cat-file confirmed commit {claim_text}"
            except Exception:
                return "false", f"git cat-file could not find commit {claim_text}"
        if claim_type == "branch_exists":
            result = subprocess.run(
                ["git", "-C", str(binding.worktree_path), "branch", "--list", claim_text],
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.stdout.strip():
                return "verified", f"git branch lists `{claim_text}`"
            return "false", f"git branch does not list `{claim_text}`"
        if claim_type == "symbol_exists":
            result = subprocess.run(
                ["rg", "-n", rf"\b{re.escape(claim_text)}\b", str(binding.worktree_path)],
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0 and result.stdout.strip():
                first = result.stdout.splitlines()[0].strip()
                return "verified", f"ripgrep matched `{claim_text}` at {first}"
            return "false", f"ripgrep did not find symbol `{claim_text}`"
        if claim_type == "tests_passed":
            evidence = self._verify_test_claim(binding, claim_text)
            return evidence
        if claim_type == "decision_claim":
            decisions = _read_text(binding.worktree_path / MEMORY_DIR_NAME / "DECISIONS.md")
            if claim_text[:120] in decisions:
                return "verified", "Decision text already exists in memory/DECISIONS.md"
            return "unresolved", "Decision claim is not yet mirrored in memory/DECISIONS.md"
        if claim_type == "ownership_claim":
            tasks = _read_json(binding.worktree_path / MEMORY_DIR_NAME / "TASKS.json", {"tasks": []})
            text = json.dumps(tasks)
            if claim_text[:80] in text:
                return "verified", "Ownership claim matched memory/TASKS.json"
            return "unresolved", "Ownership claim needs explicit task/lease evidence."
        if claim_type == "milestone_completed":
            handoff = _read_text(binding.worktree_path / MEMORY_DIR_NAME / "HANDOFF.md")
            if claim_text[:120] in handoff:
                return "verified", "Milestone claim matched memory/HANDOFF.md"
            return "unresolved", "Milestone claim needs validation evidence or handoff entry."
        if claim_type == "status_claim":
            status = _read_text(binding.worktree_path / MEMORY_DIR_NAME / "STATUS.md")
            if claim_text[:120] in status:
                return "verified", "Status claim matched memory/STATUS.md"
            return "unresolved", "Status claim needs current STATUS/HANDOFF evidence."
        return "unresolved", "Claim requires explicit validation before it can be trusted."

    def _verify_test_claim(self, binding: ChannelBinding, claim_text: str) -> tuple[str, str]:
        recent = self.store.recent_turns(binding.primary_thread_id or "", limit=8) if binding.primary_thread_id else []
        for row in recent:
            commands = json.loads(row.get("commands_run_json") or "[]")
            validations = json.loads(row.get("validations_json") or "[]")
            haystack = "\n".join(commands + validations)
            if claim_text in haystack:
                return "verified", f"Matched recent recorded validation in turn {row['turn_id']}"
        command = claim_text.split("->", 1)[0].strip()
        # Allowlist + argv parse instead of shell exec. Bot turn data feeds
        # `claim_text`, so anything that touches a shell metacharacter must
        # short-circuit to "unresolved" instead of running through cmd /c
        # or sh -lc where `;`, `&&`, `|`, backticks, redirects, etc. would
        # be re-interpreted as additional commands.
        if any(ch in command for ch in (";", "&", "|", "`", "$(", ">", "<", "\n", "\r")):
            return "unresolved", "Validation claim contains shell metacharacters; refusing to rerun automatically."
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            return "unresolved", "Validation claim could not be parsed into argv form; refusing to rerun automatically."
        if not argv:
            return "unresolved", "Empty validation claim; refusing to rerun automatically."
        cheap_argv_heads: tuple[tuple[str, ...], ...] = (
            ("pytest",),
            ("python", "-m", "pytest"),
            ("py", "-m", "pytest"),
            ("npm", "run", "lint"),
            ("npm", "run", "build"),
            ("npx", "tsc", "--noEmit"),
            ("npx", "vitest", "run"),
        )
        head_lower = tuple(part.lower() for part in argv)
        if not any(head_lower[: len(allowed)] == allowed for allowed in cheap_argv_heads):
            return "unresolved", "Validation claim is not one of the allowlisted cheap validators; refusing to rerun automatically."
        try:
            result = subprocess.run(
                argv,
                cwd=binding.worktree_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            return "false", f"Cheap validation rerun timed out: {command}"
        except (FileNotFoundError, OSError) as exc:
            return "unresolved", f"Cheap validation rerun could not launch: {exc}"
        if result.returncode == 0:
            return "verified", f"Reran cheap validation successfully: {command}"
        stderr = (result.stderr or result.stdout or "").strip().splitlines()
        tail = stderr[-1] if stderr else f"exit {result.returncode}"
        return "false", f"Cheap validation rerun failed for `{command}`: {tail}"


class DurableRuntime:
    def __init__(self, *, state_dir: Path, repo_path: Path, state_namespace: str, agent_name: str) -> None:
        self.state_dir = state_dir
        self.repo_path = workspace_root(repo_path)
        self.agent_name = agent_name or "codex"
        self.project_id = f"{slugify(self.repo_path.name)}-{slugify(state_namespace)}"
        self.turn_artifacts_dir = state_dir / "turn-artifacts"
        self.turn_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.store = RuntimeStore(state_dir / "durable-runtime.sqlite3")
        self.worktrees = WorktreeManager(state_dir / "worktrees")
        self.verifier = VerificationEngine(self.store)
        self.ensure_repo_contract(self.repo_path)

    def _append_turn_artifact(
        self,
        binding: ChannelBinding,
        *,
        thread_id: str,
        turn_id: str,
        summary: str,
        files_changed: list[str],
        commands_run: list[str],
        validations: list[str],
        next_step: str,
        command_exit_codes: list[int] | None,
        cwd: str,
        approvals: list[str] | None,
        blocker: str,
        error_category: str,
        started_at: str,
        completed_at: str,
        backend: str,
        degraded: bool,
    ) -> None:
        artifact_path = self.turn_artifacts_dir / f"{binding.project_id}.jsonl"
        record = {
            "recorded_at": _now_iso(),
            "project_id": binding.project_id,
            "channel_id": binding.channel_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "summary": summary,
            "files_changed": _normalize_paths(files_changed),
            "commands_run": commands_run,
            "command_exit_codes": command_exit_codes or [],
            "validations": validations,
            "cwd": cwd,
            "approvals": approvals or [],
            "blocker": blocker,
            "next_step": next_step,
            "error_category": error_category,
            "started_at": started_at,
            "completed_at": completed_at,
            "backend": backend,
            "degraded": degraded,
        }
        with _serialize_path(artifact_path), artifact_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _turn_artifact_exists(self, binding: ChannelBinding, turn_id: str) -> bool:
        artifact_path = self.turn_artifacts_dir / f"{binding.project_id}.jsonl"
        if not artifact_path.exists():
            return False
        try:
            for line in artifact_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(payload.get("turn_id") or "") == str(turn_id):
                    return True
        except OSError:
            return False
        return False

    def _upsert_memory_fact(
        self,
        binding: ChannelBinding,
        *,
        entry_type: str,
        key: str,
        content: str,
        source: str,
        evidence: str = "",
        confidence: float = 1.0,
    ) -> None:
        self.store.upsert_memory_entry(
            project_id=binding.project_id,
            entry_type=entry_type,
            key=key,
            content=content.strip(),
            source=source,
            evidence=evidence,
            confidence=confidence,
        )

    def ensure_repo_contract(self, repo_path: Path) -> None:
        repo_path.mkdir(parents=True, exist_ok=True)
        memory_dir = repo_path / MEMORY_DIR_NAME
        memory_dir.mkdir(parents=True, exist_ok=True)
        agents_path = repo_path / "AGENTS.md"
        if not agents_path.exists():
            atomic_write_text(agents_path, self._agents_markdown())
        project_spec = memory_dir / "PROJECT_SPEC.md"
        if not project_spec.exists():
            atomic_write_text(
                project_spec,
                "\n".join(
                    [
                        "# PROJECT_SPEC",
                        "",
                        f"- Project root: `{repo_path}`",
                        "- Durable runtime contract is active.",
                        "- Discord is transport, not memory.",
                        "- Repo files + relay state are source of truth.",
                    ]
                )
                + "\n",
            )
        if not (memory_dir / "PLAN.md").exists():
            atomic_write_text(
                memory_dir / "PLAN.md",
                "# PLAN\n\n## Milestones\n- Define milestones here.\n\n## Acceptance Criteria\n- Define acceptance checks here.\n\n## Validation Commands\n- Add the minimal relevant commands before implementation.\n\n## Stop-And-Fix Rules\n- If validation fails, fix it before claiming success.\n",
            )
        if not (memory_dir / "STATUS.md").exists():
            _ensure_sectioned_markdown(memory_dir / "STATUS.md", "STATUS", {heading: "" for heading in STATUS_SECTIONS})
        if not (memory_dir / "TASKS.json").exists():
            atomic_write_json(memory_dir / "TASKS.json", {"tasks": []})
        if not (memory_dir / "DECISIONS.md").exists():
            atomic_write_text(memory_dir / "DECISIONS.md", "# DECISIONS\n")
        if not (memory_dir / "HANDOFF.md").exists():
            atomic_write_text(memory_dir / "HANDOFF.md", "# HANDOFF\n")
        if not (memory_dir / "DRIFT_LOG.md").exists():
            atomic_write_text(memory_dir / "DRIFT_LOG.md", "# DRIFT_LOG\n")
        if not (memory_dir / "KNOWN_FACTS.json").exists():
            atomic_write_json(memory_dir / "KNOWN_FACTS.json", {"preferences": [], "constraints": [], "facts": []})

    def _agents_markdown(self) -> str:
        return "\n".join(
            [
                "# AGENTS",
                "",
                "- Discord messages are transport, not source of truth.",
                f"- For relay implementation, runtime, packaging, or audit questions, the source of truth is `{RELAY_PROJECT_ROOT}`; the active worktree is only the source of truth for workspace/project code.",
                "- Edit only the active worktree/workspace unless the user explicitly assigns another allowed workspace.",
                "- Do not edit the CLADEX relay/runtime repository from a managed relay profile unless that profile was deliberately configured for CLADEX development.",
                "- Use workspace-local skills, subagents, commands, and rules when their trigger matches the task; keep discovery compact instead of pasting full skill files into every message.",
                "- Before editing files or answering factual repo questions, read `memory/STATUS.md`, `memory/TASKS.json`, `memory/DECISIONS.md`, `memory/HANDOFF.md`, and relevant code/tests.",
                "- Verify claims from other agents against files, git diff, tests, or docs before accepting them.",
                "- Claim a task before editing files.",
                "- Do not edit files owned by another fresh lease.",
                "- For medium or large tasks, plan first in `memory/PLAN.md`, then implement, validate, repair, and update memory files.",
                "- After every milestone, run validation and fix failures before proceeding.",
                "- Before ending a turn, update STATUS, TASKS, DECISIONS if changed, and HANDOFF.",
                "- If another agent drifted, correct it with evidence and log it in `memory/DRIFT_LOG.md`.",
                "- Success claims require evidence.",
            ]
        ) + "\n"

    def ensure_binding(self, channel_key: str) -> ChannelBinding:
        existing = self.store.get_channel(channel_key)
        if existing is not None:
            binding = ChannelBinding(
                channel_id=str(existing["discord_channel_id"]),
                project_id=str(existing["project_id"]),
                repo_path=Path(str(existing["repo_path"])),
                worktree_path=Path(str(existing["worktree_path"])),
                current_branch=str(existing["current_branch"] or "HEAD"),
                primary_thread_id=str(existing["primary_thread_id"]) if existing["primary_thread_id"] else None,
            )
            self.ensure_repo_contract(binding.worktree_path)
            return binding
        worktree_path, branch = self.worktrees.ensure(self.repo_path, project_id=self.project_id, channel_id=channel_key)
        binding = ChannelBinding(
            channel_id=channel_key,
            project_id=self.project_id,
            repo_path=self.repo_path,
            worktree_path=worktree_path,
            current_branch=branch,
        )
        self.store.upsert_channel(binding)
        self.ensure_repo_contract(worktree_path)
        self._sync_status(binding, next_step="Resume the existing objective from durable memory.")
        return binding

    def bind_thread(self, channel_key: str, *, thread_id: str, backend: str, status: str, last_turn_id: str | None = None) -> ChannelBinding:
        binding = self.ensure_binding(channel_key)
        binding.primary_thread_id = thread_id
        binding.backend = backend
        self.store.bind_thread(
            thread_id=thread_id,
            project_id=binding.project_id,
            channel_id=binding.channel_id,
            backend=backend,
            status=status,
            last_turn_id=last_turn_id,
            rebound=status == "rebound",
        )
        self.store.upsert_channel(binding)
        self._sync_status(binding, worktree_branch=f"{binding.worktree_path} @ {binding.current_branch}")
        return binding

    def active_thread_id(self, channel_key: str) -> str | None:
        binding = self.ensure_binding(channel_key)
        return binding.primary_thread_id

    @staticmethod
    def _reply_fingerprint(content: str) -> str:
        normalized = re.sub(r"\s+", " ", str(content or "").strip())
        if not normalized:
            return ""
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def claim_inbound_discord_message(self, channel_key: str, message_id: str | int | None) -> bool:
        normalized = str(message_id or "").strip()
        if not normalized:
            return True
        binding = self.ensure_binding(channel_key)
        return self.store.claim_message_receipt(
            direction="discord-inbound",
            channel_id=binding.channel_id,
            receipt_key=normalized,
            fingerprint="",
        )

    def has_inbound_discord_message(self, channel_key: str, message_id: str | int | None) -> bool:
        normalized = str(message_id or "").strip()
        if not normalized:
            return False
        binding = self.ensure_binding(channel_key)
        return self.store.has_message_receipt(
            direction="discord-inbound",
            channel_id=binding.channel_id,
            receipt_key=normalized,
        )

    def release_inbound_discord_message(self, channel_key: str, message_id: str | int | None) -> None:
        normalized = str(message_id or "").strip()
        if not normalized:
            return
        binding = self.ensure_binding(channel_key)
        self.store.release_message_receipt(
            direction="discord-inbound",
            channel_id=binding.channel_id,
            receipt_key=normalized,
        )

    def claim_outbound_discord_reply(
        self,
        channel_key: str,
        source_message_id: str | int | None,
        content: str,
        *,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        binding = self.ensure_binding(channel_key)
        reply_hash = self._reply_fingerprint(content)
        if not reply_hash:
            return True
        source_key = str(source_message_id or "").strip() or "no-source"
        return self.store.claim_message_receipt(
            direction="discord-outbound",
            channel_id=binding.channel_id,
            receipt_key=f"{source_key}:{reply_hash}",
            fingerprint=reply_hash,
        )

    def release_outbound_discord_reply(
        self,
        channel_key: str,
        source_message_id: str | int | None,
        content: str,
    ) -> None:
        binding = self.ensure_binding(channel_key)
        reply_hash = self._reply_fingerprint(content)
        if not reply_hash:
            return
        source_key = str(source_message_id or "").strip() or "no-source"
        self.store.release_message_receipt(
            direction="discord-outbound",
            channel_id=binding.channel_id,
            receipt_key=f"{source_key}:{reply_hash}",
        )

    def has_outbound_discord_reply(
        self,
        channel_key: str,
        source_message_id: str | int | None,
        content: str,
    ) -> bool:
        binding = self.ensure_binding(channel_key)
        reply_hash = self._reply_fingerprint(content)
        if not reply_hash:
            return False
        source_key = str(source_message_id or "").strip() or "no-source"
        return self.store.has_message_receipt(
            direction="discord-outbound",
            channel_id=binding.channel_id,
            receipt_key=f"{source_key}:{reply_hash}",
        )

    def observe_incoming_message(
        self,
        *,
        channel_key: str,
        author_name: str,
        author_id: int,
        author_is_bot: bool,
        text: str,
    ) -> ChannelBinding:
        binding = self.ensure_binding(channel_key)
        sender_type = "other-ai" if author_is_bot else "user"
        content = text.strip()
        facts = _prune_known_facts_payload(
            _read_json(binding.worktree_path / MEMORY_DIR_NAME / "KNOWN_FACTS.json", {"preferences": [], "constraints": [], "facts": []})
        )
        atomic_write_json(binding.worktree_path / MEMORY_DIR_NAME / "KNOWN_FACTS.json", _prune_known_facts_payload(facts))
        if sender_type == "user" and content:
            self._upsert_memory_fact(
                binding,
                entry_type="objective",
                key="current-objective",
                content=content,
                source=f"user:{author_name}",
            )
            self._upsert_memory_fact(
                binding,
                entry_type="instruction",
                key="latest-authoritative-human-instruction",
                content=content,
                source=f"user:{author_name}",
            )
            task = self.ensure_task(
                channel_key=channel_key,
                title=content[:120],
                owner_agent=self.agent_name,
                target_files=[],
                validation=[],
                supersede_active=True,
            )
            self._sync_status(
                binding,
                objective=f"{author_name}: {content}",
                active_task=task["title"],
                owner=task["owner"],
                last_verified_scope=_head_commit(binding.worktree_path) or "working tree",
                blocker="none",
                next_step=_extract_next_step(content) or "Continue the current task using repo memory as source of truth.",
            )
        elif sender_type == "other-ai" and content:
            message_ref = f"{channel_key}:{author_id}:{int(time.time())}"
            verification_results = self.verifier.ingest_message(
                binding,
                source_agent=author_name,
                message_ref=message_ref,
                text=content,
            )
            for result in verification_results:
                if result["verdict"] == "false":
                    self.append_drift(
                        binding,
                        source_agent=author_name,
                        claim=result["claim_text"],
                        verdict="false",
                        evidence=result["evidence"],
                        correction="Do not trust the claim without repo evidence.",
                    )
                elif result["verdict"] == "verified":
                    self._upsert_memory_fact(
                        binding,
                        entry_type="verified-claim",
                        key=slugify(result["claim_text"])[:64],
                        content=result["claim_text"],
                        source=f"other-ai:{author_name}",
                        evidence=result["evidence"],
                        confidence=0.8,
                    )
            self._sync_status(binding, next_step="Verify external claims before relying on them.")
        blocker = _extract_blocker(content)
        if blocker:
            self._upsert_memory_fact(
                binding,
                entry_type="blocker",
                key="current-blocker",
                content=blocker,
                source=f"{sender_type}:{author_name}",
            )
            self._sync_status(binding, blocker=blocker)
        for decision in _extract_decision_candidates(content):
            self._upsert_memory_fact(
                binding,
                entry_type="decision-candidate",
                key=slugify(decision)[:64],
                content=decision,
                source=f"{sender_type}:{author_name}",
                confidence=0.6 if sender_type == "other-ai" else 0.8,
            )
        self.store.upsert_sync_state(binding.project_id, memory=True, status=True)
        return binding

    def ensure_task(
        self,
        *,
        channel_key: str,
        title: str,
        owner_agent: str,
        target_files: list[str],
        validation: list[str],
        supersede_active: bool = False,
    ) -> dict[str, Any]:
        binding = self.ensure_binding(channel_key)
        active = self.store.active_task(channel_key)
        if active is not None:
            if supersede_active and _normalize_compare_text(str(active["title"])) != _normalize_compare_text(title):
                self.store.release_task(str(active["task_id"]))
                self._write_tasks_file(binding)
            else:
                self.store.heartbeat_task(str(active["task_id"]))
                self._write_tasks_file(binding)
                return {
                    "id": str(active["task_id"]),
                    "title": str(active["title"]),
                    "owner": str(active["owner_agent"]),
                }
        task_id = self.store.claim_task(
            channel_id=channel_key,
            project_id=binding.project_id,
            owner_agent=owner_agent,
            title=title,
            target_files=target_files,
            validation=validation,
        )
        self._write_tasks_file(binding)
        return {"id": task_id, "title": title, "owner": owner_agent}

    def claim_task(
        self,
        *,
        channel_key: str,
        title: str,
        owner_agent: str,
        target_files: list[str],
        validation: list[str],
    ) -> dict[str, Any]:
        binding = self.ensure_binding(channel_key)
        task_id = self.store.claim_task(
            channel_id=channel_key,
            project_id=binding.project_id,
            owner_agent=owner_agent,
            title=title,
            target_files=target_files,
            validation=validation,
        )
        self._write_tasks_file(binding)
        self._sync_status(binding, active_task=title, owner=owner_agent)
        return {"id": task_id, "title": title, "owner": owner_agent}

    def release_task(self, *, channel_key: str, task_id: str) -> None:
        binding = self.ensure_binding(channel_key)
        self.store.release_task(task_id)
        self._write_tasks_file(binding)
        self._sync_status(binding, active_task="none", blocker="none")

    def heartbeat_active_task(self, channel_key: str, *, lease_seconds: int = 1800) -> bool:
        binding = self.ensure_binding(channel_key)
        active = self.store.active_task(channel_key)
        if active is None:
            return False
        self.store.heartbeat_task(str(active["task_id"]), lease_seconds=lease_seconds)
        self._write_tasks_file(binding)
        return True

    def build_context_bundle(self, channel_key: str, *, max_chars: int = 3200) -> str:
        binding = self.ensure_binding(channel_key)
        memory_dir = binding.worktree_path / MEMORY_DIR_NAME
        status = _read_text(memory_dir / "STATUS.md").strip()
        decisions = _read_text(memory_dir / "DECISIONS.md")
        handoff = _read_text(memory_dir / "HANDOFF.md")
        raw_facts = _read_json(memory_dir / "KNOWN_FACTS.json", {"preferences": [], "constraints": [], "facts": []})
        facts = _prune_known_facts_payload(raw_facts)
        if facts != raw_facts:
            atomic_write_json(memory_dir / "KNOWN_FACTS.json", facts)
        unresolved = self.store.unresolved_claims(binding.project_id)
        active = self.store.active_task(channel_key)
        lines = [
            "Durable runtime context.",
            "Discord is transport, not memory.",
            f"Relay implementation source of truth: {RELAY_PROJECT_ROOT}",
            "Use the active worktree as source of truth for workspace code/tasks, but use the CLADEX repo and live relay status/logs for relay audits and relay bug claims.",
            "Editable scope: active worktree/workspace unless the user explicitly assigns another allowed workspace; managed relay profiles must not edit the CLADEX runtime repo unless deliberately configured for CLADEX development.",
            "Rule/skill context: discover workspace-local AGENTS/CLAUDE files, Codex skills, Claude subagents, and slash commands when needed, but keep this context compact.",
            "For relay audits, treat historical HANDOFF/DECISIONS entries and older log events as background only; report them as current issues only if the latest code or current run still reproduces them.",
            f"Project id: {binding.project_id}",
            f"Repo path: {binding.repo_path}",
            f"Worktree path: {binding.worktree_path}",
            f"Current branch: {binding.current_branch}",
            "",
            "Source of truth files:",
            "- AGENTS.md",
            "- memory/STATUS.md",
            "- memory/TASKS.json",
            "- memory/DECISIONS.md",
            "- memory/HANDOFF.md",
            "- memory/KNOWN_FACTS.json",
            "",
            "Current verified status:",
            status or "_none_",
        ]
        if active:
            lines.extend(
                [
                    "",
                    "Active lease:",
                    f"- task_id: {active['task_id']}",
                    f"- owner: {active['owner_agent']}",
                    f"- title: {active['title']}",
                    f"- target_files: {', '.join(json.loads(active['target_files_glob']))}",
                    f"- validation: {', '.join(json.loads(active['validation_plan_json'])) or 'pending'}",
                ]
            )
        if facts.get("preferences") or facts.get("constraints"):
            lines.append("")
            lines.append("Stable constraints:")
            for item in facts.get("constraints", [])[:4]:
                lines.append(f"- {item}")
            for item in facts.get("preferences", [])[:3]:
                lines.append(f"- prefer: {item}")
        decision_highlights = _extract_bullets(decisions, ("decision", "rationale", "evidence"))
        if decision_highlights:
            lines.append("")
            lines.append("Recent decisions:")
            lines.extend(f"- {item}" for item in decision_highlights[:3])
        handoff_highlights = _extract_latest_handoff_highlights(handoff, limit=3)
        if handoff_highlights:
            lines.append("")
            lines.append("Latest handoff:")
            lines.extend(f"- {item}" for item in handoff_highlights[:3])
        if unresolved:
            lines.append("")
            lines.append("Unresolved claims needing verification:")
            for item in unresolved[:3]:
                lines.append(f"- {item['source_agent']}: {item['claim_text']}")
        lines.append("")
        lines.append("Do not rely on Discord transcript recall when repo memory already captures the truth.")
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        trimmed: list[str] = []
        running = 0
        for line in lines:
            if running + len(line) + 1 > max_chars:
                break
            trimmed.append(line)
            running += len(line) + 1
        trimmed.append("")
        trimmed.append("[context truncated to fit budget]")
        return "\n".join(trimmed)

    def record_turn_result(
        self,
        *,
        channel_key: str,
        thread_id: str,
        turn_id: str,
        summary: str,
        files_changed: list[str],
        commands_run: list[str],
        validations: list[str],
        blocker: str = "",
        next_step: str = "",
        command_exit_codes: list[int] | None = None,
        cwd: str = "",
        approvals: list[str] | None = None,
        error_category: str = "",
        started_at: str = "",
        completed_at: str = "",
        backend: str = "",
        degraded: bool = False,
    ) -> bool:
        """Record turn result. Returns False if turn_id was already recorded (dedup)."""
        binding = self.ensure_binding(channel_key)
        completed_at_value = completed_at or _now_iso()
        next_step_value = next_step or "Continue from STATUS.md and HANDOFF.md."
        if not self.store.record_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            summary=summary,
            files_changed=files_changed,
            commands_run=commands_run,
            validations=validations,
            next_step=next_step_value,
            command_exit_codes=command_exit_codes,
            cwd=cwd or str(binding.worktree_path),
            approvals=approvals,
            blocker=blocker,
            error_category=error_category,
            started_at=started_at,
            completed_at=completed_at_value,
            backend=backend or binding.backend,
            degraded=degraded,
        ):
            recovered_artifact = False
            missing_side_effects = not self.store.turn_side_effects_synced(turn_id)
            if not self._turn_artifact_exists(binding, turn_id):
                self._append_turn_artifact(
                    binding,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    summary=summary,
                    files_changed=files_changed,
                    commands_run=commands_run,
                    validations=validations,
                    next_step=next_step_value,
                    command_exit_codes=command_exit_codes,
                    cwd=cwd or str(binding.worktree_path),
                    approvals=approvals,
                    blocker=blocker,
                    error_category=error_category,
                    started_at=started_at,
                    completed_at=completed_at_value,
                    backend=backend or binding.backend,
                    degraded=degraded,
                )
                recovered_artifact = True
            if (recovered_artifact or missing_side_effects) and self.store.claim_turn_side_effects(turn_id):
                try:
                    self._record_turn_side_effects(
                        binding,
                        channel_key=channel_key,
                        summary=summary,
                        files_changed=files_changed,
                        commands_run=commands_run,
                        validations=validations,
                        blocker=blocker,
                        next_step=next_step,
                    )
                except Exception:
                    self.store.clear_turn_side_effect_claim(turn_id)
                    raise
                self.store.mark_turn_side_effects_synced(turn_id)
            return False
        self._append_turn_artifact(
            binding,
            thread_id=thread_id,
            turn_id=turn_id,
            summary=summary,
            files_changed=files_changed,
            commands_run=commands_run,
            validations=validations,
            next_step=next_step_value,
            command_exit_codes=command_exit_codes,
            cwd=cwd or str(binding.worktree_path),
            approvals=approvals,
            blocker=blocker,
            error_category=error_category,
            started_at=started_at,
            completed_at=completed_at_value,
            backend=backend or binding.backend,
            degraded=degraded,
        )
        if self.store.claim_turn_side_effects(turn_id):
            try:
                self._record_turn_side_effects(
                    binding,
                    channel_key=channel_key,
                    summary=summary,
                    files_changed=files_changed,
                    commands_run=commands_run,
                    validations=validations,
                    blocker=blocker,
                    next_step=next_step,
                )
            except Exception:
                self.store.clear_turn_side_effect_claim(turn_id)
                raise
            self.store.mark_turn_side_effects_synced(turn_id)
        return True

    def _record_turn_side_effects(
        self,
        binding: ChannelBinding,
        *,
        channel_key: str,
        summary: str,
        files_changed: list[str],
        commands_run: list[str],
        validations: list[str],
        blocker: str,
        next_step: str,
    ) -> None:
        active = self.store.active_task(channel_key)
        if active:
            self.store.heartbeat_task(str(active["task_id"]))
        validation_text = "; ".join(validations) if validations else ("pending verification" if files_changed else "no validation required")
        objective_entry = self.store.latest_memory_entry(
            project_id=binding.project_id,
            entry_type="instruction",
            key="latest-authoritative-human-instruction",
        )
        self._sync_status(
            binding,
            objective=(objective_entry or {}).get("content"),
            owner=self.agent_name,
            worktree_branch=f"{binding.worktree_path} @ {binding.current_branch}",
            last_verified_scope=_head_commit(binding.worktree_path) or "working tree",
            last_validation_result=validation_text,
            blocker=blocker or "none",
            next_step=next_step or "Continue from the latest handoff entry.",
        )
        self._upsert_memory_fact(
            binding,
            entry_type="status",
            key="latest-summary",
            content=summary,
            source=f"{self.agent_name}-turn",
            evidence=_compact_join(validations or commands_run),
            confidence=0.9,
        )
        if next_step:
            self._upsert_memory_fact(
                binding,
                entry_type="next-step",
                key="exact-next-step",
                content=next_step,
                source=f"{self.agent_name}-turn",
                evidence=summary,
                confidence=0.95,
            )
        if blocker:
            self._upsert_memory_fact(
                binding,
                entry_type="blocker",
                key="current-blocker",
                content=blocker,
                source=f"{self.agent_name}-turn",
                evidence=summary,
                confidence=0.95,
            )
        for decision in _extract_decision_candidates(summary):
            self.append_decision(binding, decision=decision, rationale="Turn summary recorded a durable decision.", evidence=_compact_join(validations or commands_run))
        self._write_tasks_file(binding)
        self.write_handoff(
            binding,
            task_id=str(active["task_id"]) if active else "unclaimed",
            changed_files=files_changed,
            commands_run=commands_run,
            result=summary,
            blocker=blocker or "none",
            next_step=next_step or "Continue from STATUS.md.",
        )
        self.store.upsert_sync_state(binding.project_id, handoff=True, status=True)

    def record_shutdown(self, channel_key: str, *, reason: str) -> None:
        binding = self.ensure_binding(channel_key)
        self._upsert_memory_fact(
            binding,
            entry_type="lifecycle",
            key="last-shutdown",
            content=reason,
            source="relay",
            confidence=0.8,
        )
        self._sync_status(binding, blocker=reason, next_step="Resume the same thread and continue from durable memory.")

    def record_startup(self, channel_key: str) -> None:
        binding = self.ensure_binding(channel_key)
        self._upsert_memory_fact(
            binding,
            entry_type="lifecycle",
            key="last-startup",
            content="Relay started or resumed.",
            source="relay",
            confidence=0.8,
        )
        self._sync_status(binding, next_step="Resume the same thread and continue without asking for a recap.")

    def record_restart_event(self, reason: str = "normal") -> None:
        """Record a relay restart for churn detection."""
        self.store.record_restart(self.agent_name, reason)

    def count_recent_restarts(self, window_seconds: int = 300) -> int:
        """Count restarts in the last N seconds."""
        return self.store.count_recent_restarts(self.agent_name, window_seconds)

    def is_restart_churn(self, threshold: int = 5, window_seconds: int = 300) -> bool:
        """Check if relay is in a restart churn loop (5+ restarts in 5 minutes)."""
        return self.store.is_restart_churn(self.agent_name, threshold, window_seconds)

    def record_compaction_event(self, channel_key: str, *, thread_id: str, event_type: str) -> None:
        binding = self.ensure_binding(channel_key)
        self.store.record_compaction(thread_id, event_type)
        self._upsert_memory_fact(
            binding,
            entry_type="compaction",
            key="last-compaction",
            content=f"{event_type} on {thread_id}",
            source="relay",
            confidence=0.9,
        )
        self.write_handoff(
            binding,
            task_id=(self.store.active_task(channel_key) or {}).get("task_id", "unclaimed"),
            changed_files=[],
            commands_run=[],
            result=f"Compaction event recorded: {event_type}",
            blocker="none",
            next_step="Rehydrate from durable memory and continue the same objective.",
        )
        self._sync_status(binding, next_step="Rehydrate from durable memory and continue the same objective.")

    def recent_handoff(self, channel_key: str) -> str:
        binding = self.ensure_binding(channel_key)
        return _read_text(binding.worktree_path / MEMORY_DIR_NAME / "HANDOFF.md")

    def append_decision(self, binding: ChannelBinding, *, decision: str, rationale: str, evidence: str) -> None:
        body = "\n".join(
            [
                f"## {_now_iso()}",
                f"- Decision: {decision}",
                f"- Rationale: {rationale}",
                f"- Evidence: {evidence}",
            ]
        )
        _append_markdown_entry(binding.worktree_path / MEMORY_DIR_NAME / "DECISIONS.md", "DECISIONS", body)

    def append_drift(self, binding: ChannelBinding, *, source_agent: str, claim: str, verdict: str, evidence: str, correction: str) -> None:
        body = "\n".join(
            [
                f"## {_now_iso()}",
                f"- Source agent: {source_agent}",
                f"- Claim: {claim}",
                f"- Verdict: {verdict}",
                f"- Evidence: {evidence}",
                f"- Correction posted: {correction}",
            ]
        )
        _append_markdown_entry(binding.worktree_path / MEMORY_DIR_NAME / "DRIFT_LOG.md", "DRIFT_LOG", body)

    def write_handoff(
        self,
        binding: ChannelBinding,
        *,
        task_id: str,
        changed_files: list[str],
        commands_run: list[str],
        result: str,
        blocker: str,
        next_step: str,
    ) -> None:
        result_text = result.strip() or "none"
        next_step_text = next_step.strip()
        if next_step_text and _normalize_compare_text(next_step_text) == _normalize_compare_text(result_text):
            next_step_text = "Continue from STATUS.md."
        entry = "\n".join(
            [
                f"## {_now_iso()}",
                f"- task id: {task_id}",
                f"- changed files: {', '.join(_normalize_paths(changed_files)) or 'none'}",
                f"- commands/tests run: {', '.join(_summarize_commands(commands_run)) or 'none captured'}",
                f"- result: {result_text}",
                f"- blocker: {blocker or 'none'}",
                f"- exact next step: {next_step_text or 'Continue from STATUS.md.'}",
            ]
        )
        _append_markdown_entry(binding.worktree_path / MEMORY_DIR_NAME / "HANDOFF.md", "HANDOFF", entry, prepend=True)
        _prune_markdown_history(binding.worktree_path / MEMORY_DIR_NAME / "HANDOFF.md", "HANDOFF", keep_entries=20, max_chars=8000)

    def _write_tasks_file(self, binding: ChannelBinding) -> None:
        task_rows = self.store.list_tasks(binding.channel_id)
        claimed = [row for row in task_rows if row["status"] == "claimed"]
        history = [row for row in task_rows if row["status"] != "claimed"][:24]
        tasks = []
        for row in claimed + history:
            tasks.append(
                {
                    "id": row["task_id"],
                    "title": row["title"],
                    "status": row["status"],
                    "owner": row["owner_agent"],
                    "target_files": json.loads(row["target_files_glob"]),
                    "validation": json.loads(row["validation_plan_json"]),
                    "started_at": row["started_at"],
                    "heartbeat_at": row["heartbeat_at"],
                    "lease_expires_at": row["lease_expires_at"],
                }
            )
        atomic_write_json(binding.worktree_path / MEMORY_DIR_NAME / "TASKS.json", {"tasks": tasks})

    def _sync_status(
        self,
        binding: ChannelBinding,
        *,
        objective: str | None = None,
        active_task: str | None = None,
        owner: str | None = None,
        worktree_branch: str | None = None,
        last_verified_scope: str | None = None,
        last_validation_result: str | None = None,
        blocker: str | None = None,
        next_step: str | None = None,
    ) -> None:
        status_fields = {
            "Current objective": objective,
            "Active task": active_task,
            "Owner": owner,
            "Worktree / branch": worktree_branch if worktree_branch is not None else f"{binding.worktree_path} @ {binding.current_branch}",
            "Last verified commit or diff scope": last_verified_scope if last_verified_scope is not None else _head_commit(binding.worktree_path) or "working tree",
            "Last validation result": last_validation_result,
            "Current blocker": blocker,
            "Exact next step": next_step,
        }
        _write_status_file(binding.worktree_path / MEMORY_DIR_NAME / "STATUS.md", status_fields)

    def status_snapshot(self, channel_key: str) -> dict[str, Any]:
        binding = self.ensure_binding(channel_key)
        active = self.store.active_task(channel_key)
        channel_row = self.store.get_channel(channel_key)
        return {
            "project_id": binding.project_id,
            "repo_path": str(binding.repo_path),
            "worktree_path": str(binding.worktree_path),
            "branch": binding.current_branch,
            "thread_id": binding.primary_thread_id,
            "backend": binding.backend,
            "last_rebind_at": str(channel_row["last_rebind_at"]) if channel_row and channel_row["last_rebind_at"] else "",
            "active_task": active,
            "status_md": _read_text(binding.worktree_path / MEMORY_DIR_NAME / "STATUS.md"),
            "handoff_md": _read_text(binding.worktree_path / MEMORY_DIR_NAME / "HANDOFF.md"),
            "claims": self.store.recent_claims(binding.project_id),
        }
