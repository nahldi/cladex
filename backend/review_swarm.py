from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from agent_guardrails import assert_workspace_allowed, workspace_protection_violation


PROVIDER_CHOICES = {"codex", "claude"}
MAX_AGENTS = 50
MAX_TEXT_BYTES = 512 * 1024
DEFAULT_AI_MAX_PARALLEL = 4
DEFAULT_AGENT_OUTPUT_LIMIT = 120_000
REVIEW_DATA_ROOT = Path(user_data_dir("cladex", False)) / "reviews"
BACKUP_DATA_ROOT = Path(user_data_dir("cladex", False)) / "backups"
REVIEW_STRATEGY = "ai-review-swarm"
REVIEW_ID_RE = re.compile(r"^review-\d{8}-\d{6}-[a-f0-9]{8}$")
BACKUP_ID_RE = re.compile(r"^backup-\d{8}-\d{6}-[a-f0-9]{8}$")
AGENT_SPECIALTIES = (
    (
        "security",
        "Threat model the codebase, authentication, authorization, input validation, secrets, filesystem/network exposure, dependency risk, and prompt-injection surfaces.",
    ),
    (
        "runtime",
        "Trace startup, shutdown, process lifecycle, configuration loading, path handling, error recovery, and user-facing failure modes.",
    ),
    (
        "testing",
        "Look for broken or missing validation, smoke-test paths, flaky test risks, untested edge cases, and commands that should fail before production.",
    ),
    (
        "concurrency",
        "Stress the design mentally and with safe commands where possible: queues, locks, race conditions, duplicate work, rate limits, and many-agent scaling.",
    ),
    (
        "backend",
        "Review backend APIs, CLI contracts, data validation, serialization, state persistence, migrations, and compatibility boundaries.",
    ),
    (
        "frontend",
        "Review UI state flows, error states, long-running job progress, responsive layout, confusing controls, and data-shape assumptions.",
    ),
    (
        "release",
        "Review install, packaging, CI, public-repo hygiene, generated artifacts, version alignment, and clone-to-run reliability.",
    ),
    (
        "dependencies",
        "Review dependency metadata, lockfiles, vulnerable patterns, runtime version assumptions, and provider CLI compatibility risks.",
    ),
    (
        "performance",
        "Review large-repo behavior, memory use, expensive scans, unbounded logs, subprocess fan-out, and slow-path degradation.",
    ),
    (
        "data-integrity",
        "Review saved state, reports, logs, atomic writes, crash recovery, duplicate findings, and artifact correctness.",
    ),
)

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".idea",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "release",
    "target",
    "vendor",
    "venv",
}
REVIEW_EXTENSIONS = {
    ".bat",
    ".c",
    ".cjs",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".mjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".tsx",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SECRET_FILENAME_RE = re.compile(r"(^|[._-])(env|secret|secrets|token|tokens|key|keys|credential|credentials)([._-]|$)", re.I)
SAFE_TEMPLATE_SEGMENTS = frozenset({"example", "sample", "template", "tmpl", "dist"})
SECRET_VALUE_RE = re.compile(
    r"\b(api[_-]?key|auth[_-]?token|client[_-]?secret|discord[_-]?token|password|private[_-]?key|secret|token)\b\s*[:=]",
    re.I,
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(api[_-]?key|auth[_-]?token|client[_-]?secret|discord[_-]?token|password|private[_-]?key|secret|token)\b\s*[:=]\s*([\"']?)[^\s,\"'}]+",
    re.I,
)
DISCORD_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,45}\b")
GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
TODO_MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.I)
COMMENT_PREFIX_RE = re.compile(r"(?:#|//|/\*|<!--|--\s|;|\*\s)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def path_key(path: Path) -> str:
    resolved = str(path.expanduser().resolve())
    return resolved.lower() if os.name == "nt" else resolved


def job_dir(job_id: str) -> Path:
    return REVIEW_DATA_ROOT / validate_review_id(job_id)


def job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def findings_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "findings.json"


def report_markdown_path(job_id: str) -> Path:
    return job_dir(job_id) / "CLADEX_PROJECT_REVIEW.md"


def fix_plan_path(job_id: str) -> Path:
    return job_dir(job_id) / "CLADEX_FIX_PLAN.md"


def backup_dir(backup_id: str) -> Path:
    return BACKUP_DATA_ROOT / validate_backup_id(backup_id)


def backup_manifest_path(backup_id: str) -> Path:
    return backup_dir(backup_id) / "backup.json"


def backup_snapshot_path(backup_id: str) -> Path:
    return backup_dir(backup_id) / "snapshot"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    last_error: BaseException | None = None
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    if last_error is not None:
        raise last_error


def _write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def _safe_remove_path(path: Path, root: Path) -> None:
    target = path.resolve()
    base = root.resolve()
    if target == base:
        raise ValueError("refusing to remove restore root")
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"refusing to remove path outside restore root: {target}") from exc
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _copy_into(source: Path, target: Path, root: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        if target.exists() and not target.is_dir():
            _safe_remove_path(target, root)
        target.mkdir(parents=True, exist_ok=True)
        source_names = {child.name for child in source.iterdir()}
        for child in target.iterdir():
            if _preserve_on_restore(child.name):
                continue
            if child.name not in source_names:
                _safe_remove_path(child, root)
        for child in source.iterdir():
            if _preserve_on_restore(child.name):
                continue
            _copy_into(child, target / child.name, root)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        _safe_remove_path(target, root)
    shutil.copy2(source, target, follow_symlinks=False)


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except Exception:
        return default


def validate_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized not in PROVIDER_CHOICES:
        raise ValueError("provider must be codex or claude")
    return normalized


def validate_agent_count(value: Any) -> int:
    count = _safe_int(value, 1)
    if count < 1 or count > MAX_AGENTS:
        raise ValueError(f"agents must be between 1 and {MAX_AGENTS}")
    return count


def validate_review_id(value: str) -> str:
    review_id = str(value or "").strip()
    if not REVIEW_ID_RE.fullmatch(review_id):
        raise ValueError("invalid review id")
    return review_id


def validate_backup_id(value: str) -> str:
    backup_id = str(value or "").strip()
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise ValueError("invalid backup id")
    return backup_id


def _default_title(workspace: Path) -> str:
    return f"{workspace.name or 'Project'} review"


def agent_specialty(index: int) -> tuple[str, str]:
    return AGENT_SPECIALTIES[index % len(AGENT_SPECIALTIES)]


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = dict(job)
    report_path = report_markdown_path(job["id"])
    plan_path = fix_plan_path(job["id"])
    public["reportPath"] = str(report_path) if report_path.exists() else str(report_path)
    public["findingsPath"] = str(findings_json_path(job["id"]))
    public["fixPlanPath"] = str(plan_path) if plan_path.exists() else ""
    if report_path.exists():
        public["reportPreview"] = report_path.read_text(encoding="utf-8", errors="replace")[:12000]
    findings_payload = _read_json(findings_json_path(job["id"]), default={"findings": []})
    findings = findings_payload.get("findings") if isinstance(findings_payload, dict) else None
    public["severityCounts"] = _severity_counts(findings if isinstance(findings, list) else [])
    return public


def load_job(job_id: str) -> dict[str, Any]:
    payload = _read_json(job_json_path(job_id), default={})
    if not payload:
        raise FileNotFoundError(f"No review job found for `{job_id}`.")
    return payload


def save_job(job: dict[str, Any]) -> None:
    job["updatedAt"] = utc_now()
    _write_json(job_json_path(job["id"]), job)


def list_reviews() -> list[dict[str, Any]]:
    REVIEW_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []
    for path in REVIEW_DATA_ROOT.glob("*/job.json"):
        payload = _read_json(path, default={})
        if payload.get("id"):
            jobs.append(_public_job(payload))
    jobs.sort(key=lambda item: str(item.get("createdAt", "")), reverse=True)
    return jobs


def show_review(job_id: str) -> dict[str, Any]:
    return _public_job(load_job(job_id))


def cancel_flag_path(job_id: str) -> Path:
    return job_dir(job_id) / "cancel.flag"


def _cancel_requested(job_id: str) -> bool:
    try:
        return cancel_flag_path(job_id).exists()
    except (OSError, ValueError):
        return False


def cancel_review(job_id: str) -> dict[str, Any]:
    job = load_job(job_id)
    if job.get("status") in {"completed", "failed", "cancelled"}:
        return show_review(job_id)
    flag = cancel_flag_path(job_id)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(utc_now(), encoding="utf-8")
    job["cancelRequested"] = True
    if job.get("status") == "queued":
        job["status"] = "cancelled"
        job["finishedAt"] = utc_now()
        job["error"] = job.get("error") or "Cancelled before execution."
    save_job(job)
    return show_review(job_id)


def create_source_backup(workspace: str | Path, *, reason: str = "manual", source_job_id: str = "") -> dict[str, Any]:
    source = Path(workspace).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {source}")
    backup_id = f"backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root = backup_dir(backup_id)
    snapshot = backup_snapshot_path(backup_id)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, snapshot, symlinks=True, ignore=_review_artifact_ignore)
    manifest = {
        "id": backup_id,
        "workspace": str(source),
        "snapshot": str(snapshot),
        "reason": str(reason or "manual"),
        "sourceJobId": str(source_job_id or ""),
        "createdAt": utc_now(),
        "status": "ready",
    }
    _write_json(backup_manifest_path(backup_id), manifest)
    return manifest


def list_backups() -> list[dict[str, Any]]:
    BACKUP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for path in BACKUP_DATA_ROOT.glob("*/backup.json"):
        payload = _read_json(path, default={})
        if payload.get("id"):
            records.append(payload)
    records.sort(key=lambda item: str(item.get("createdAt", "")), reverse=True)
    return records


def load_backup(backup_id: str) -> dict[str, Any]:
    payload = _read_json(backup_manifest_path(backup_id), default={})
    if not payload:
        raise FileNotFoundError(f"No CLADEX backup found for `{backup_id}`.")
    return payload


def _preserve_on_restore(name: str) -> bool:
    lower = name.lower()
    return lower in SKIP_DIRS or lower.startswith(".env") or bool(SECRET_FILENAME_RE.search(name))


def restore_backup(backup_id: str, *, confirm: str) -> dict[str, Any]:
    if confirm != backup_id:
        raise ValueError("restore requires --confirm with the exact backup id")
    backup = load_backup(backup_id)
    snapshot = Path(str(backup.get("snapshot", ""))).expanduser().resolve()
    target = Path(str(backup.get("workspace", ""))).expanduser().resolve()
    if not snapshot.exists() or not snapshot.is_dir():
        raise ValueError(f"backup snapshot is missing: {snapshot}")
    if not target.exists() or not target.is_dir():
        raise ValueError(f"restore target is missing: {target}")

    pre_restore = create_source_backup(target, reason=f"pre-restore:{backup_id}")
    snapshot_names = {child.name for child in snapshot.iterdir()}
    for child in target.iterdir():
        if _preserve_on_restore(child.name):
            continue
        if child.name not in snapshot_names:
            _safe_remove_path(child, target)
    for child in snapshot.iterdir():
        if _preserve_on_restore(child.name):
            continue
        _copy_into(child, target / child.name, target)

    restored = dict(backup)
    restored["restoredAt"] = utc_now()
    restored["preRestoreBackupId"] = pre_restore["id"]
    return restored


def start_review(
    workspace: str | Path,
    *,
    provider: str = "codex",
    agents: int = 4,
    title: str = "",
    account_home: str = "",
    launch: bool = True,
    preflight_only: bool = False,
    allow_self_review: bool = False,
    backup_before_review: bool = True,
) -> dict[str, Any]:
    provider_name = validate_provider(provider)
    agent_count = validate_agent_count(agents)
    target = Path(workspace).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {target}")
    protection_env = {"CLADEX_ALLOW_CLADEX_WORKSPACE": "", "CLADEX_ALLOW_SELF_WORKSPACE": ""}
    protection_violation = workspace_protection_violation(target, env=protection_env)
    if protection_violation and not allow_self_review:
        raise ValueError(
            protection_violation
            + " To review CLADEX itself, use the explicit CLADEX self-review option so a source backup is created first."
        )
    if not protection_violation:
        assert_workspace_allowed(target)

    job_id = f"review-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    artifact_dir = job_dir(job_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "title": str(title or "").strip() or _default_title(target),
        "workspace": str(target),
        "provider": provider_name,
        "strategy": REVIEW_STRATEGY,
        "preflightOnly": bool(preflight_only),
        "agentCount": agent_count,
        "accountHome": str(account_home or "").strip(),
        "status": "queued",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "startedAt": "",
        "finishedAt": "",
        "artifactDir": str(artifact_dir),
        "progress": {"total": agent_count, "queued": agent_count, "running": 0, "done": 0, "failed": 0},
        "agents": [],
        "error": "",
        "scratchWorkspace": "",
        "scratchError": "",
        "allowSelfReview": bool(allow_self_review),
        "selfReview": bool(protection_violation),
        "backupBeforeReview": bool(backup_before_review or protection_violation),
        "sourceBackup": {},
    }
    for index in range(agent_count):
        focus, focus_prompt = agent_specialty(index)
        job["agents"].append(
            {
                "id": f"agent-{index + 1:02d}",
                "provider": provider_name,
                "focus": focus,
                "focusPrompt": focus_prompt,
                "status": "queued",
                "assignedFiles": 0,
                "findings": 0,
                "detail": "",
            }
        )
    save_job(job)
    if job["backupBeforeReview"]:
        try:
            backup = create_source_backup(target, reason="review-start", source_job_id=job_id)
            job["sourceBackup"] = backup
            save_job(job)
        except Exception as exc:
            if protection_violation:
                job["status"] = "failed"
                job["error"] = f"CLADEX self-review requires a source backup first, but backup creation failed: {exc}"
                save_job(job)
                raise ValueError(job["error"]) from exc
            job["sourceBackup"] = {"error": str(exc)}
            save_job(job)
    _write_json(findings_json_path(job_id), {"jobId": job_id, "findings": []})
    if launch:
        launch_review_worker(job_id)
    return show_review(job_id)


def launch_review_worker(job_id: str) -> None:
    command = [sys.executable, str(Path(__file__).with_name("cladex.py")), "review", "run", job_id]
    kwargs: dict[str, Any] = {
        "cwd": str(Path(__file__).parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(command, **kwargs)


def _is_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in sample


def _should_skip_dir(path: Path) -> bool:
    return path.name in SKIP_DIRS


def _should_review_file(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith(".env"):
        return False
    if path.suffix.lower() not in REVIEW_EXTENSIONS and name not in {"dockerfile", "makefile", "procfile"}:
        return False
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return False
    except OSError:
        return False
    return not _is_binary(path)


def inventory_files(workspace: str | Path) -> list[Path]:
    root = Path(workspace).expanduser().resolve()
    files: list[Path] = []
    for current, dirs, filenames in os.walk(root):
        current_path = Path(current)
        dirs[:] = sorted([name for name in dirs if not _should_skip_dir(current_path / name)], key=str.lower)
        for filename in sorted(filenames, key=str.lower):
            path = current_path / filename
            if _should_review_file(path):
                files.append(path)
    files.sort(key=lambda item: item.relative_to(root).as_posix().lower())
    return files


def is_template_secret_filename(name: str) -> bool:
    return any(part in SAFE_TEMPLATE_SEGMENTS for part in name.lower().split("."))


def secret_name_findings(workspace: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for current, dirs, filenames in os.walk(workspace):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if not _should_skip_dir(current_path / name)]
        for filename in filenames:
            if not SECRET_FILENAME_RE.search(filename):
                continue
            if is_template_secret_filename(filename):
                continue
            path = current_path / filename
            findings.append(
                {
                    "severity": "high",
                    "category": "secret-hygiene",
                    "path": path.relative_to(workspace).as_posix(),
                    "line": 0,
                    "title": "Secret-like file is present in the workspace",
                    "detail": "A file name looks like it may contain credentials. CLADEX did not read or store its contents.",
                    "recommendation": "Confirm the file is ignored or remove secrets from the repository before publishing.",
                    "confidence": "medium",
                }
            )
    return findings


def _finding(
    *,
    severity: str,
    category: str,
    relative_path: str,
    line: int,
    title: str,
    detail: str,
    recommendation: str,
    confidence: str = "medium",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "path": relative_path,
        "line": line,
        "title": title,
        "detail": detail,
        "recommendation": recommendation,
        "confidence": confidence,
    }


def scan_file(path: Path, workspace: Path) -> list[dict[str, Any]]:
    relative = path.relative_to(workspace).as_posix()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    findings: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        lowered = line.lower()
        stripped = line.strip()
        if SECRET_VALUE_RE.search(line) or DISCORD_TOKEN_RE.search(line) or GITHUB_TOKEN_RE.search(line):
            findings.append(
                _finding(
                    severity="high",
                    category="secret-hygiene",
                    relative_path=relative,
                    line=index,
                    title="Potential credential pattern in source",
                    detail="A secret-like assignment or token pattern was detected. The value was redacted and not stored.",
                    recommendation="Move secrets to local environment/configuration and rotate the value if it was committed.",
                    confidence="medium",
                )
            )
        marker_match = TODO_MARKER_RE.search(line)
        if marker_match and COMMENT_PREFIX_RE.search(line):
            findings.append(
                _finding(
                    severity="low",
                    category="maintenance",
                    relative_path=relative,
                    line=index,
                    title="Unresolved maintenance marker",
                    detail=f"A {marker_match.group(1).upper()} comment remains in source.",
                    recommendation="Convert the marker into a tracked task or finish the underlying cleanup.",
                    confidence="medium",
                )
            )
        if re.search(r"\beval\s*\(", line):
            findings.append(
                _finding(
                    severity="high",
                    category="unsafe-execution",
                    relative_path=relative,
                    line=index,
                    title="Dynamic eval call",
                    detail="Dynamic evaluation can execute attacker-controlled input if any input path reaches it.",
                    recommendation="Replace eval with structured parsing, explicit dispatch, or a constrained interpreter.",
                    confidence="medium",
                )
            )
        if re.search(r"\bexec\s*\(", line) and path.suffix.lower() == ".py":
            findings.append(
                _finding(
                    severity="high",
                    category="unsafe-execution",
                    relative_path=relative,
                    line=index,
                    title="Dynamic exec call",
                    detail="Dynamic execution can run arbitrary Python when input is not fully trusted.",
                    recommendation="Replace exec with explicit functions or data-driven configuration.",
                    confidence="medium",
                )
            )
        if "shell=true" in lowered:
            findings.append(
                _finding(
                    severity="high",
                    category="command-execution",
                    relative_path=relative,
                    line=index,
                    title="Shell command execution enabled",
                    detail="Using shell=True increases command-injection risk when arguments can include user-controlled text.",
                    recommendation="Pass argv lists directly to subprocess APIs and validate all dynamic arguments.",
                    confidence="medium",
                )
            )
        if "verify=false" in lowered:
            findings.append(
                _finding(
                    severity="high",
                    category="transport-security",
                    relative_path=relative,
                    line=index,
                    title="TLS certificate verification disabled",
                    detail="Disabling TLS verification can allow man-in-the-middle attacks.",
                    recommendation="Keep certificate verification enabled and fix the trust store or certificate chain.",
                    confidence="high",
                )
            )
        if "dangerouslysetinnerhtml" in lowered or re.search(r"\.innerHTML\s*=", line):
            findings.append(
                _finding(
                    severity="medium",
                    category="xss",
                    relative_path=relative,
                    line=index,
                    title="Raw HTML injection surface",
                    detail="Direct HTML injection can become XSS if the content is not strictly sanitized.",
                    recommendation="Render text by default or sanitize HTML with a reviewed sanitizer at the boundary.",
                    confidence="medium",
                )
            )
        if re.search(r"\byaml\.load\s*\(", line) and "safeloader" not in lowered:
            findings.append(
                _finding(
                    severity="medium",
                    category="unsafe-deserialization",
                    relative_path=relative,
                    line=index,
                    title="YAML load without SafeLoader",
                    detail="Unsafe YAML loading can construct arbitrary Python objects.",
                    recommendation="Use yaml.safe_load or pass SafeLoader explicitly.",
                    confidence="medium",
                )
            )
        if "pickle.loads" in lowered or "pickle.load(" in lowered:
            findings.append(
                _finding(
                    severity="medium",
                    category="unsafe-deserialization",
                    relative_path=relative,
                    line=index,
                    title="Pickle deserialization surface",
                    detail="Pickle can execute code when data is attacker-controlled.",
                    recommendation="Avoid pickle for untrusted data; prefer JSON or another safe format.",
                    confidence="medium",
                )
            )
        if "debug=true" in lowered:
            findings.append(
                _finding(
                    severity="medium",
                    category="production-hardening",
                    relative_path=relative,
                    line=index,
                    title="Debug mode appears enabled",
                    detail="Debug modes can expose stack traces, secrets, or interactive consoles in production.",
                    recommendation="Gate debug mode behind local-only configuration and default it off.",
                    confidence="medium",
                )
            )
        if "0.0.0.0" in stripped and any(term in lowered for term in ("listen", "bind", "host", "server")):
            findings.append(
                _finding(
                    severity="medium",
                    category="network-exposure",
                    relative_path=relative,
                    line=index,
                    title="Wide network bind",
                    detail="Binding to every interface is risky unless authentication and deployment controls are explicit.",
                    recommendation="Default to loopback for local tools and require an explicit remote-access opt-in.",
                    confidence="medium",
                )
            )
    return findings


def project_shape_findings(workspace: Path, files: list[Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    rels = {path.relative_to(workspace).as_posix().lower() for path in files}
    if "package.json" in rels:
        try:
            package = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
        except Exception:
            package = {}
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        if isinstance(scripts, dict):
            for script in ("lint", "build"):
                if script not in scripts:
                    findings.append(
                        _finding(
                            severity="low",
                            category="validation",
                            relative_path="package.json",
                            line=0,
                            title=f"Missing npm {script} script",
                            detail=f"The package does not define an npm `{script}` script.",
                            recommendation=f"Add a deterministic `{script}` script or document why this project does not need one.",
                            confidence="medium",
                        )
                    )
    source_files = [path for path in files if path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs"}]
    has_tests = any("test" in part.lower() or "spec" in part.lower() for path in files for part in path.relative_to(workspace).parts)
    if source_files and not has_tests:
        findings.append(
            _finding(
                severity="medium",
                category="validation",
                relative_path=".",
                line=0,
                title="No obvious test files found",
                detail="The scanner found source files but no test/spec paths.",
                recommendation="Add focused regression tests for the main runtime paths before production release.",
                confidence="low",
            )
        )
    if not (workspace / ".gitignore").exists():
        findings.append(
            _finding(
                severity="medium",
                category="repo-hygiene",
                relative_path=".",
                line=0,
                title="Missing .gitignore",
                detail="No .gitignore was found at the selected project root.",
                recommendation="Add a .gitignore for generated artifacts, dependency folders, and local secret files.",
                confidence="medium",
            )
        )
    return findings


def sanitize_text(text: str, *, limit: int = 6000) -> str:
    redacted = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    redacted = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    redacted = DISCORD_TOKEN_RE.sub("[REDACTED_DISCORD_TOKEN]", redacted)
    redacted = GITHUB_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", redacted)
    if len(redacted) > limit:
        return redacted[: limit - 18].rstrip() + "\n...[truncated]"
    return redacted


def _review_artifact_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lower = name.lower()
        path = Path(_directory) / name
        if path.is_symlink() or lower in SKIP_DIRS or lower.startswith(".env") or SECRET_FILENAME_RE.search(name):
            ignored.add(name)
    return ignored


def prepare_scratch_workspace(job: dict[str, Any]) -> Path:
    source = Path(job["workspace"]).expanduser().resolve()
    scratch = job_dir(job["id"]) / "scratch" / "workspace"
    if scratch.exists():
        return scratch
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, scratch, symlinks=True, ignore=_review_artifact_ignore)
    return scratch


def _json_schema_prompt() -> str:
    return (
        "Return only JSON using this shape: "
        '{"summary":"short summary","findings":[{"severity":"high|medium|low",'
        '"category":"short-category","path":"relative/path","line":0,'
        '"title":"short title","detail":"specific evidence without secrets",'
        '"recommendation":"concrete fix","confidence":"high|medium|low"}]}. '
        "If you found nothing concrete, return an empty findings array."
    )


def _ai_prompt(job: dict[str, Any], agent: dict[str, Any], files: list[Path]) -> str:
    workspace = Path(job["workspace"])
    scratch = Path(str(job.get("scratchWorkspace") or job["workspace"]))
    listed = "\n".join(f"- {path.relative_to(workspace).as_posix()}" for path in files[:220])
    if len(files) > 220:
        listed += f"\n- ... {len(files) - 220} additional files in this shard"
    return (
        "You are a read-only CLADEX project review agent.\n"
        "Go deep. Look for bugs, errors, broken workflows, test failures, smoke-test gaps, stress/scaling risks, security vulnerabilities, stale code, and anything likely to break production.\n"
        "Your lane has a distinct focus so the swarm does not duplicate itself. Stay focused, but follow evidence wherever it leads.\n"
        "You may run safe validation commands only inside the scratch workspace. Do not install dependencies, call external networks, delete files, or run destructive commands. Do not edit the original source, do not implement fixes, and do not write outside the scratch workspace or CLADEX job artifacts.\n"
        "Do not reveal credential values. If a secret is found, describe only the file, location, and risk.\n"
        "Prioritize concrete, reproducible findings with file paths and recommended fixes.\n\n"
        f"Provider lane: {job['provider']}\n"
        f"Review job: {job['id']}\n"
        f"Agent: {agent['id']}\n"
        f"Agent focus: {agent.get('focus')} - {agent.get('focusPrompt')}\n"
        f"Original workspace: {workspace}\n"
        f"Scratch workspace for any commands: {scratch}\n"
        "Suggested safe command strategy: inspect files first, then run targeted existing validation commands only when they make sense. Prefer no-install commands. Report commands attempted and failures.\n"
        "Assigned files:\n"
        f"{listed or '- no files assigned'}\n\n"
        f"{_json_schema_prompt()}"
    )


def _run_codex_ai_review(job: dict[str, Any], agent: dict[str, Any], files: list[Path]) -> str:
    import relayctl

    output_path = job_dir(job["id"]) / f"{agent['id']}-codex.md"
    scratch = Path(str(job.get("scratchWorkspace") or job["workspace"]))
    command = [
        relayctl.resolve_codex_bin(),
        "--cd",
        str(scratch),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
        "-",
    ]
    env = os.environ.copy()
    if job.get("accountHome"):
        env["CODEX_HOME"] = str(job["accountHome"])
    result = _run_cli(command, _ai_prompt(job, agent, files), env=env)
    if output_path.exists():
        return output_path.read_text(encoding="utf-8", errors="replace")
    return result


def _run_claude_ai_review(job: dict[str, Any], agent: dict[str, Any], files: list[Path]) -> str:
    import claude_relay

    command = [
        claude_relay.claude_code_bin(),
        "-p",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Read,Grep,Glob,LS,Bash",
        "--disallowedTools",
        "Edit,MultiEdit,Write,NotebookEdit",
        "--no-session-persistence",
        "--output-format",
        "text",
        _ai_prompt(job, agent, files),
    ]
    env = os.environ.copy()
    if job.get("accountHome"):
        env["CLAUDE_CONFIG_DIR"] = str(job["accountHome"])
    return _run_cli(command, "", env=env, cwd=Path(str(job.get("scratchWorkspace") or job["workspace"])))


def _run_cli(command: list[str], prompt: str, *, env: dict[str, str], cwd: Path | None = None) -> str:
    timeout = _safe_int(os.environ.get("CLADEX_REVIEW_AGENT_TIMEOUT"), 1800)
    output_limit = max(_safe_int(os.environ.get("CLADEX_REVIEW_AGENT_OUTPUT_LIMIT"), DEFAULT_AGENT_OUTPUT_LIMIT), 1000)
    kwargs: dict[str, Any] = {
        "input": prompt,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
        "cwd": str(cwd) if cwd else None,
        "timeout": max(timeout, 30),
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired:
        return "AI reviewer timed out before producing a complete result."
    except Exception as exc:
        return f"AI reviewer failed to start: {exc}"
    text = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0 and not text.strip():
        text = f"AI reviewer exited with code {result.returncode}."
    if len(text) > output_limit:
        text = text[:output_limit].rstrip() + "\n...[truncated by CLADEX]"
    return text


def _agent_finding_prefix(agent_id: str, provider: str, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefixed = []
    for finding in findings:
        item = dict(finding)
        item["agentId"] = agent_id
        item["provider"] = provider
        prefixed.append(item)
    return prefixed


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def dedup_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate findings reported by multiple lanes into one entry.

    Two findings are duplicates when they share category, path, line, and title.
    The kept entry tracks every contributing agent in `seenByAgents` and is
    promoted to the highest severity any contributor reported.
    """
    deduped: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for finding in findings:
        key = (
            str(finding.get("category", "")),
            str(finding.get("path", "")),
            int(finding.get("line", 0) or 0),
            str(finding.get("title", "")),
        )
        existing = deduped.get(key)
        agent_id = str(finding.get("agentId", "")).strip()
        if existing is None:
            entry = dict(finding)
            entry["seenByAgents"] = [agent_id] if agent_id else []
            deduped[key] = entry
            continue
        agents = existing.setdefault("seenByAgents", [])
        if agent_id and agent_id not in agents:
            agents.append(agent_id)
        existing_rank = SEVERITY_ORDER.get(str(existing.get("severity")), 3)
        new_rank = SEVERITY_ORDER.get(str(finding.get("severity")), 3)
        if new_rank < existing_rank:
            existing["severity"] = finding.get("severity")
    return list(deduped.values())


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.I | re.S):
        candidates.append(match.group(1))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        candidates.append(stripped[first : last + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def parse_ai_findings(text: str, *, workspace: Path, agent: dict[str, Any]) -> list[dict[str, Any]]:
    sanitized = sanitize_text(text)
    payload = _extract_json_payload(sanitized)
    parsed = payload.get("findings") if isinstance(payload, dict) else None
    findings: list[dict[str, Any]] = []
    if isinstance(parsed, list):
        for raw in parsed:
            if not isinstance(raw, dict):
                continue
            severity = str(raw.get("severity") or "medium").strip().lower()
            if severity not in {"high", "medium", "low"}:
                severity = "medium"
            confidence = str(raw.get("confidence") or "medium").strip().lower()
            if confidence not in {"high", "medium", "low"}:
                confidence = "medium"
            relative_path = str(raw.get("path") or ".").strip() or "."
            if Path(relative_path).is_absolute():
                try:
                    relative_path = Path(relative_path).resolve().relative_to(workspace).as_posix()
                except Exception:
                    relative_path = "."
            findings.append(
                _finding(
                    severity=severity,
                    category=str(raw.get("category") or f"ai-{agent.get('focus') or 'review'}").strip()[:80],
                    relative_path=relative_path,
                    line=max(_safe_int(raw.get("line"), 0), 0),
                    title=str(raw.get("title") or "AI reviewer finding").strip()[:180],
                    detail=sanitize_text(str(raw.get("detail") or raw.get("evidence") or "").strip()),
                    recommendation=sanitize_text(str(raw.get("recommendation") or "Inspect this finding and apply a targeted fix.").strip()),
                    confidence=confidence,
                )
            )
    if findings:
        return findings
    if sanitized.strip():
        return [
            _finding(
                severity="medium",
                category=f"ai-{agent.get('focus') or 'review'}",
                relative_path=".",
                line=0,
                title=f"Unstructured reviewer notes from {agent['id']} ({agent.get('focus', 'review')})",
                detail=sanitized,
                recommendation="Verify these reviewer notes against the source and convert concrete items into targeted fixes.",
                confidence="medium",
            )
        ]
    return []


def _update_progress(job: dict[str, Any]) -> None:
    agents = job.get("agents", [])
    job["progress"] = {
        "total": len(agents),
        "queued": sum(1 for item in agents if item.get("status") == "queued"),
        "running": sum(1 for item in agents if item.get("status") == "running"),
        "done": sum(1 for item in agents if item.get("status") == "done"),
        "failed": sum(1 for item in agents if item.get("status") == "failed"),
        "cancelled": sum(1 for item in agents if item.get("status") == "cancelled"),
    }


def run_review_job(job_id: str) -> dict[str, Any]:
    job = load_job(job_id)
    lock = threading.Lock()
    workspace = Path(job["workspace"]).expanduser().resolve()
    findings: list[dict[str, Any]] = []
    started = utc_now()
    job["status"] = "running"
    job["startedAt"] = job.get("startedAt") or started
    save_job(job)

    try:
        files = inventory_files(workspace)
        try:
            scratch = prepare_scratch_workspace(job)
            job["scratchWorkspace"] = str(scratch)
            job["scratchError"] = ""
        except Exception as exc:
            job["scratchWorkspace"] = str(workspace)
            job["scratchError"] = f"Could not create scratch workspace; review lanes will use the original workspace with strict no-edit instructions: {exc}"
            if not job.get("preflightOnly"):
                raise RuntimeError(f"Could not create scratch workspace for AI review lanes: {exc}") from exc
        save_job(job)

        shards = [[] for _ in range(validate_agent_count(job.get("agentCount", 1)))]
        for index, path in enumerate(files):
            shards[index % len(shards)].append(path)

        base_findings = secret_name_findings(workspace) + project_shape_findings(workspace, files)
        findings.extend(_agent_finding_prefix("project", str(job["provider"]), base_findings))

        def process_agent(index: int, shard: list[Path]) -> None:
            agent = job["agents"][index]
            if _cancel_requested(job["id"]):
                with lock:
                    agent["status"] = "cancelled"
                    agent["assignedFiles"] = len(shard)
                    agent["detail"] = "Cancelled before launch."
                    _update_progress(job)
                    save_job(job)
                return
            with lock:
                agent["status"] = "running"
                agent["assignedFiles"] = len(shard)
                agent["detail"] = "Reviewing assigned shard."
                _update_progress(job)
                save_job(job)

            agent_findings: list[dict[str, Any]] = []
            try:
                for path in shard:
                    agent_findings.extend(scan_file(path, workspace))
                if not job.get("preflightOnly") and not _cancel_requested(job["id"]):
                    if str(job.get("provider")) == "codex":
                        ai_output = _run_codex_ai_review(job, agent, shard)
                    else:
                        ai_output = _run_claude_ai_review(job, agent, shard)
                    agent_findings.extend(parse_ai_findings(ai_output, workspace=workspace, agent=agent))
                prefixed = _agent_finding_prefix(agent["id"], str(job["provider"]), agent_findings)
                with lock:
                    findings.extend(prefixed)
                    if _cancel_requested(job["id"]):
                        agent["status"] = "cancelled"
                        agent["findings"] = len(prefixed)
                        agent["detail"] = f"Cancelled mid-shard after {len(prefixed)} preflight finding(s)."
                    else:
                        agent["status"] = "done"
                        agent["findings"] = len(prefixed)
                        agent["detail"] = f"Reviewed {len(shard)} file(s); found {len(prefixed)} item(s)."
                    _update_progress(job)
                    save_job(job)
            except Exception as exc:
                with lock:
                    agent["status"] = "failed"
                    agent["detail"] = str(exc)
                    _update_progress(job)
                    save_job(job)

        default_parallel = min(len(shards), 16) if job.get("preflightOnly") else min(len(shards), DEFAULT_AI_MAX_PARALLEL)
        max_workers = _safe_int(os.environ.get("CLADEX_REVIEW_MAX_PARALLEL"), default_parallel)
        max_workers = max(1, min(max_workers, len(shards)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_agent, index, shard) for index, shard in enumerate(shards)]
            for future in concurrent.futures.as_completed(futures):
                future.result()

        findings = dedup_findings(findings)
        findings.sort(
            key=lambda item: (
                SEVERITY_ORDER.get(str(item.get("severity")), 3),
                str(item.get("path", "")),
                int(item.get("line", 0) or 0),
                str(item.get("title", "")),
            )
        )
        for index, finding in enumerate(findings, start=1):
            finding["id"] = f"F{index:04d}"
        _write_json(findings_json_path(job_id), {"jobId": job_id, "findings": findings})
        _atomic_write_text(report_markdown_path(job_id), build_report(job, findings, files))
        statuses = [str(item.get("status")) for item in job["agents"]]
        cancelled = sum(1 for status in statuses if status == "cancelled")
        failed = sum(1 for status in statuses if status == "failed")
        if cancelled and (cancelled + failed) == len(job["agents"]):
            job["status"] = "cancelled"
            job["error"] = "Cancelled before all lanes finished."
        elif failed == len(job["agents"]):
            job["status"] = "failed"
            job["error"] = "All reviewer lanes failed."
        elif _cancel_requested(job_id) and cancelled:
            job["status"] = "cancelled"
            job["error"] = "Cancelled before all lanes finished."
        else:
            job["status"] = "completed"
            job["error"] = ""
        job["finishedAt"] = utc_now()
        _update_progress(job)
        save_job(job)
    except Exception as exc:
        job["status"] = "failed"
        job["finishedAt"] = utc_now()
        job["error"] = str(exc)
        save_job(job)
        _atomic_write_text(report_markdown_path(job_id), build_report(job, findings, []))
    return show_review(job_id)


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "high": sum(1 for item in findings if item.get("severity") == "high"),
        "medium": sum(1 for item in findings if item.get("severity") == "medium"),
        "low": sum(1 for item in findings if item.get("severity") == "low"),
    }


def build_report(job: dict[str, Any], findings: list[dict[str, Any]], files: list[Path]) -> str:
    counts = _severity_counts(findings)
    lines = [
        f"# CLADEX Project Review - {job.get('title') or job.get('id')}",
        "",
        f"- Job: `{job.get('id')}`",
        f"- Workspace: `{job.get('workspace')}`",
        f"- Provider lane: `{job.get('provider')}`",
        f"- Strategy: `{job.get('strategy') or REVIEW_STRATEGY}`",
        f"- Reviewer lanes: `{job.get('agentCount')}`",
        f"- Status: `{job.get('status')}`",
        f"- Created: `{job.get('createdAt')}`",
        f"- Finished: `{job.get('finishedAt') or 'not finished'}`",
        f"- Files inventoried: `{len(files)}`",
        f"- Scratch workspace: `{job.get('scratchWorkspace') or 'not created yet'}`",
        f"- Source backup: `{(job.get('sourceBackup') or {}).get('id', 'not created')}`",
        "",
        "Boundary: reviewer lanes do not apply fixes to the selected project. Commands, caches, and experiments belong in the CLADEX scratch workspace for this job.",
        "",
        "## Summary",
        "",
        f"- High: `{counts['high']}`",
        f"- Medium: `{counts['medium']}`",
        f"- Low: `{counts['low']}`",
        "",
    ]
    if job.get("error"):
        lines.extend(["## Job Error", "", str(job["error"]), ""])
    if job.get("scratchError"):
        lines.extend(["## Scratch Workspace Warning", "", str(job["scratchError"]), ""])
    progress = job.get("progress", {})
    lines.extend(
        [
            "## Progress",
            "",
            f"- Running: `{progress.get('running', 0)}/{progress.get('total', 0)}`",
            f"- Done: `{progress.get('done', 0)}/{progress.get('total', 0)}`",
            f"- Failed: `{progress.get('failed', 0)}/{progress.get('total', 0)}`",
            "",
            "## Reviewer Lanes",
            "",
        ]
    )
    for agent in job.get("agents", []):
        lines.append(
            f"- `{agent.get('id')}` `{agent.get('focus', 'review')}` {agent.get('status')}: {agent.get('assignedFiles', 0)} file(s), "
            f"{agent.get('findings', 0)} finding(s). {agent.get('detail', '')}"
        )
    lines.extend(["", "## Findings", ""])
    if not findings:
        lines.extend(["No findings were recorded.", ""])
    else:
        for finding in findings:
            location = str(finding.get("path") or ".")
            line = int(finding.get("line", 0) or 0)
            if line > 0:
                location = f"{location}:{line}"
            lines.extend(
                [
                    f"### {finding.get('id')} - {finding.get('title')}",
                    "",
                    f"- Severity: `{finding.get('severity')}`",
                    f"- Category: `{finding.get('category')}`",
                    f"- Location: `{location}`",
                    f"- Agent: `{finding.get('agentId', '-')}`",
                    f"- Confidence: `{finding.get('confidence', 'medium')}`",
                    "",
                    str(finding.get("detail", "")).strip(),
                    "",
                    f"Recommended fix: {finding.get('recommendation', '')}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Next Step",
            "",
            "Use the CLADEX fix-plan action to turn these findings into an ordered implementation plan. The fix-plan action does not edit source code.",
            "",
        ]
    )
    return "\n".join(lines)


def create_fix_plan(job_id: str) -> dict[str, Any]:
    job = load_job(job_id)
    findings_payload = _read_json(findings_json_path(job_id), default={"findings": []})
    findings = findings_payload.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    lines = [
        f"# CLADEX Fix Plan - {job.get('title') or job.get('id')}",
        "",
        "No fixes have been applied. This plan orders the review findings so a later implementation pass can work safely.",
        "",
        "## Phase 1 - Stop Shipping Risks",
        "",
    ]
    high = [item for item in findings if item.get("severity") == "high"]
    medium = [item for item in findings if item.get("severity") == "medium"]
    low = [item for item in findings if item.get("severity") == "low"]
    if high:
        for item in high:
            lines.append(f"- `{item.get('id')}` {item.get('path')}: {item.get('recommendation')}")
    else:
        lines.append("- No high-severity findings were recorded.")
    lines.extend(["", "## Phase 2 - Stabilize Runtime And Validation", ""])
    if medium:
        for item in medium:
            lines.append(f"- `{item.get('id')}` {item.get('path')}: {item.get('recommendation')}")
    else:
        lines.append("- No medium-severity findings were recorded.")
    lines.extend(["", "## Phase 3 - Maintenance Cleanup", ""])
    if low:
        for item in low:
            lines.append(f"- `{item.get('id')}` {item.get('path')}: {item.get('recommendation')}")
    else:
        lines.append("- No low-severity findings were recorded.")
    lines.extend(
        [
            "",
            "## Implementation Gate",
            "",
            "Before source edits, claim the implementation task, inspect the referenced files, patch in small groups, and run the project's validation commands after each group.",
            "",
        ]
    )
    _atomic_write_text(fix_plan_path(job_id), "\n".join(lines))
    job["fixPlanPath"] = str(fix_plan_path(job_id))
    save_job(job)
    return show_review(job_id)
