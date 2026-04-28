from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from platformdirs import user_data_dir

from agent_guardrails import workspace_protection_violation
import claude_relay
import relay_common
import review_swarm


FIX_DATA_ROOT = Path(user_data_dir("cladex", False)) / "fix-runs"
FIX_RUN_ID_RE = re.compile(r"^fix-\d{8}-\d{6}-[a-f0-9]{8}$")
DEFAULT_FIX_MAX_AGENTS = 1
MAX_FIX_AGENTS = 10
VALIDATION_TIMEOUT_SECONDS = 600
_TASK_STATE_LOCK = threading.Lock()
TERMINAL_FIX_STATUSES = {"completed", "completed_with_warnings", "failed", "cancelled"}


def utc_now() -> str:
    return review_swarm.utc_now()


def validate_fix_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if not FIX_RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid fix run id")
    return run_id


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def validate_max_agents(value: Any) -> int:
    count = _safe_int(value, DEFAULT_FIX_MAX_AGENTS)
    if count < 1 or count > MAX_FIX_AGENTS:
        raise ValueError(f"fix agents must be between 1 and {MAX_FIX_AGENTS}")
    return count


def run_dir(run_id: str) -> Path:
    return FIX_DATA_ROOT / validate_fix_run_id(run_id)


def run_json_path(run_id: str) -> Path:
    return run_dir(run_id) / "fix_run.json"


def run_markdown_path(run_id: str) -> Path:
    return run_dir(run_id) / "CLADEX_FIX_RUN.md"


def cancel_flag_path(run_id: str) -> Path:
    return run_dir(run_id) / "cancel.flag"


def task_output_path(run_id: str, task_id: str) -> Path:
    return run_dir(run_id) / "tasks" / f"{task_id}.md"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    return review_swarm._read_json(path, default=default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    review_swarm._write_json(path, payload)


def _atomic_write_text(path: Path, text: str) -> None:
    review_swarm._atomic_write_text(path, text)


def _cancel_requested(run_id: str) -> bool:
    try:
        return cancel_flag_path(run_id).exists()
    except (OSError, ValueError):
        return False


def _run_lock_path(run_id: str) -> Path:
    return run_dir(run_id) / "run.lock"


def _start_lock_path(review_id: str) -> Path:
    return FIX_DATA_ROOT / "start-locks" / f"{review_swarm.validate_review_id(review_id)}.lock"


def _pid_alive(pid: int) -> bool:
    return review_swarm._pid_alive(pid)


def _acquire_run_lock(run_id: str) -> bool:
    lock_path = _run_lock_path(run_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            existing = lock_path.read_text(encoding="utf-8").strip()
            existing_pid = int(existing.split(":", 1)[0]) if existing else 0
        except Exception:
            existing_pid = 0
        if existing_pid and existing_pid != os.getpid() and not _pid_alive(existing_pid):
            try:
                lock_path.unlink()
            except OSError:
                return False
            return _acquire_run_lock(run_id)
        return False
    try:
        os.write(fd, f"{os.getpid()}:{utc_now()}".encode("utf-8"))
    finally:
        os.close(fd)
    return True


def _acquire_start_lock(review_id: str, *, wait_seconds: float = 8.0) -> bool:
    lock_path = _start_lock_path(review_id)
    deadline = time.monotonic() + wait_seconds
    while True:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                existing = lock_path.read_text(encoding="utf-8").strip()
                existing_pid = int(existing.split(":", 1)[0]) if existing else 0
            except Exception:
                existing_pid = 0
            if existing_pid and existing_pid != os.getpid() and not _pid_alive(existing_pid):
                try:
                    lock_path.unlink()
                except OSError:
                    return False
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
            continue
        try:
            os.write(fd, f"{os.getpid()}:{utc_now()}".encode("utf-8"))
        finally:
            os.close(fd)
        return True


def _release_start_lock(review_id: str) -> None:
    try:
        _start_lock_path(review_id).unlink()
    except OSError:
        pass


def _release_run_lock(run_id: str) -> None:
    try:
        _run_lock_path(run_id).unlink()
    except OSError:
        pass


def _severity_phase(severity: str) -> int:
    normalized = str(severity or "").strip().lower()
    if normalized == "high":
        return 1
    if normalized == "medium":
        return 2
    return 3


def _finding_path(value: Any) -> str:
    path = str(value or ".").strip().replace("\\", "/")
    if not path or path == "." or ".." in Path(path).parts:
        return "."
    return path


def _load_review_findings(review_id: str) -> list[dict[str, Any]]:
    payload = _read_json(review_swarm.findings_json_path(review_id), default={"findings": []})
    findings = payload.get("findings", [])
    return findings if isinstance(findings, list) else []


def _build_tasks(review_job: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    provider = review_swarm.validate_provider(str(review_job.get("provider") or "codex"))
    tasks: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("id") or f"F{index:04d}")
        path = _finding_path(finding.get("path"))
        files = [] if path == "." else [path]
        tasks.append(
            {
                "id": f"task-{index:04d}",
                "findingId": finding_id,
                "phase": _severity_phase(str(finding.get("severity") or "")),
                "provider": provider,
                "status": "queued",
                "title": str(finding.get("title") or "Review finding").strip()[:180],
                "severity": str(finding.get("severity") or "medium").strip().lower(),
                "category": str(finding.get("category") or "review").strip()[:80],
                "files": files,
                "recommendation": str(finding.get("recommendation") or "Apply a targeted fix.").strip(),
                "detail": str(finding.get("detail") or "").strip(),
                "startedAt": "",
                "finishedAt": "",
                "attempts": 0,
                "error": "",
                "outputPath": "",
            }
        )
    tasks.sort(key=lambda item: (int(item.get("phase", 3)), str(item.get("id", ""))))
    return tasks


def _load_package_json(workspace: Path) -> dict[str, Any]:
    path = workspace / "package.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def discover_validation_commands(workspace: str | Path) -> list[list[str]]:
    root = Path(workspace).expanduser().resolve()
    commands: list[list[str]] = []
    package_json = _load_package_json(root)
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    if isinstance(scripts, dict):
        for name in ("lint", "test", "build"):
            if name in scripts:
                commands.append(["cmd", "/c", "npm", "run", name] if os.name == "nt" else ["npm", "run", name])
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").exists():
        commands.append([sys.executable, "-m", "pytest", "--tb=short", "-q"])
    if (root / ".git").exists():
        commands.append(["git", "diff", "--check"])
    return commands[:4]


def _progress(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(tasks),
        "queued": sum(1 for item in tasks if item.get("status") == "queued"),
        "running": sum(1 for item in tasks if item.get("status") == "running"),
        "done": sum(1 for item in tasks if item.get("status") == "done"),
        "failed": sum(1 for item in tasks if item.get("status") == "failed"),
        "cancelled": sum(1 for item in tasks if item.get("status") == "cancelled"),
    }


def _safe_phase_parallelism(tasks: list[dict[str, Any]], requested: int) -> int:
    # Fix workers share one writable workspace. Keep phases serialized until
    # CLADEX has per-task worktree isolation and a merge step.
    return 1


def _save_run(run: dict[str, Any]) -> None:
    run["updatedAt"] = utc_now()
    run["progress"] = _progress(run.get("tasks", []))
    _write_json(run_json_path(run["id"]), run)
    _atomic_write_text(run_markdown_path(run["id"]), build_fix_run_markdown(run))


def _update_task(run_id: str, task_id: str, update: Callable[[dict[str, Any], dict[str, Any]], None]) -> dict[str, Any]:
    with _TASK_STATE_LOCK:
        run = load_fix_run(run_id)
        task = next((item for item in run.get("tasks", []) if item.get("id") == task_id), None)
        if task is None:
            raise ValueError("unknown fix task id")
        update(run, task)
        _save_run(run)
        return run


def load_fix_run(run_id: str) -> dict[str, Any]:
    payload = _read_json(run_json_path(run_id), default={})
    if not payload:
        raise FileNotFoundError(f"No fix run found for `{run_id}`.")
    return payload


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    public = dict(run)
    path = run_markdown_path(run["id"])
    public["reportPath"] = str(path)
    public["restoreCommand"] = restore_command_for_run(run)
    if path.exists():
        public["reportPreview"] = path.read_text(encoding="utf-8", errors="replace")[:12000]
    return public


def list_fix_runs() -> list[dict[str, Any]]:
    FIX_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for path in FIX_DATA_ROOT.glob("*/fix_run.json"):
        payload = _read_json(path, default={})
        if payload.get("id"):
            records.append(_public_run(payload))
    records.sort(key=lambda item: str(item.get("createdAt", "")), reverse=True)
    return records


def show_fix_run(run_id: str) -> dict[str, Any]:
    return _public_run(load_fix_run(run_id))


def build_fix_run_markdown(run: dict[str, Any]) -> str:
    progress = run.get("progress") or _progress(run.get("tasks", []))
    lines = [
        f"# CLADEX Fix Run - {run.get('title') or run.get('id')}",
        "",
        f"- Run: `{run.get('id')}`",
        f"- Review: `{run.get('reviewId')}`",
        f"- Workspace: `{run.get('workspace')}`",
        f"- Status: `{run.get('status')}`",
        f"- Source backup: `{(run.get('sourceBackup') or {}).get('id', 'not created')}`",
        f"- Max fix agents: `{run.get('maxAgents')}`",
        "",
        "## Progress",
        "",
        f"- Queued: `{progress.get('queued', 0)}/{progress.get('total', 0)}`",
        f"- Running: `{progress.get('running', 0)}/{progress.get('total', 0)}`",
        f"- Done: `{progress.get('done', 0)}/{progress.get('total', 0)}`",
        f"- Failed: `{progress.get('failed', 0)}/{progress.get('total', 0)}`",
        f"- Cancelled: `{progress.get('cancelled', 0)}/{progress.get('total', 0)}`",
        "",
    ]
    if run.get("error"):
        lines.extend(["## Error", "", str(run.get("error")), ""])
    restore_command = restore_command_for_run(run)
    if restore_command:
        lines.extend(["## Restore", "", f"`{restore_command}`", ""])
    lines.extend(["## Tasks", ""])
    tasks = run.get("tasks", [])
    if not tasks:
        lines.append("No fix tasks were generated.")
    for task in tasks:
        files = ", ".join(task.get("files") or ["."])
        lines.extend(
            [
                f"### {task.get('id')} - {task.get('title')}",
                "",
                f"- Finding: `{task.get('findingId')}`",
                f"- Phase: `{task.get('phase')}`",
                f"- Provider: `{task.get('provider')}`",
                f"- Status: `{task.get('status')}`",
                f"- Files: `{files}`",
                "",
                str(task.get("recommendation") or "").strip(),
                "",
            ]
        )
        if task.get("error"):
            lines.extend([f"Error: {task.get('error')}", ""])
    validations = run.get("validationResults", [])
    if validations:
        lines.extend(["## Validation", ""])
        for item in validations:
            command = " ".join(str(part) for part in item.get("command", []))
            lines.append(f"- `{item.get('status')}` `{command}` exit={item.get('returncode')}")
    return "\n".join(lines).rstrip() + "\n"


def launch_fix_worker(run_id: str) -> None:
    command = [sys.executable, str(Path(__file__).with_name("cladex.py")), "fix", "run", run_id]
    kwargs: dict[str, Any] = {
        "cwd": str(Path(__file__).parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(command, **kwargs)


def start_fix_run(
    review_id: str,
    *,
    max_agents: int = DEFAULT_FIX_MAX_AGENTS,
    allow_self_fix: bool = False,
    launch: bool = True,
) -> dict[str, Any]:
    review_id = review_swarm.validate_review_id(review_id)
    if not _acquire_start_lock(review_id):
        active = active_fix_run_for_review(review_id)
        if active:
            return active
        raise RuntimeError("Fix Review is already starting for this review.")
    try:
        active = active_fix_run_for_review(review_id)
        if active:
            return active
        review_job = review_swarm.load_job(review_id)
        if str(review_job.get("status")) not in {"completed", "completed_with_warnings"}:
            raise ValueError("review job must be completed before fixes can start")
        workspace = Path(str(review_job.get("workspace", ""))).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(f"review workspace is missing: {workspace}")
        self_review = bool(review_job.get("selfReview") or review_job.get("allowSelfReview"))
        protection_violation = workspace_protection_violation(
            workspace,
            env={"CLADEX_ALLOW_CLADEX_WORKSPACE": "0", "CLADEX_ALLOW_SELF_WORKSPACE": "0"},
        )
        if protection_violation:
            if not self_review:
                raise ValueError(
                    f"{protection_violation} Fix Review for CLADEX requires a completed review job that was started with explicit CLADEX self-review enabled."
                )
            if not allow_self_fix:
                raise ValueError("CLADEX self-fix requires explicit --allow-cladex-self-fix approval.")
        findings = _load_review_findings(review_id)
        max_agent_count = validate_max_agents(max_agents)
        run_id = f"fix-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:8]}"
        run_dir(run_id).mkdir(parents=True, exist_ok=True)
        backup = review_swarm.create_source_backup(workspace, reason="fix-start", source_job_id=review_id)
        validation_commands = discover_validation_commands(workspace)
        tasks = _build_tasks(review_job, findings)
        run = {
            "id": run_id,
            "reviewId": review_id,
            "title": f"{review_job.get('title') or review_id} fixes",
            "workspace": str(workspace),
            "provider": str(review_job.get("provider") or "codex"),
            "accountHome": str(review_job.get("accountHome") or ""),
            "selfReview": self_review,
            "selfFix": bool(protection_violation and allow_self_fix),
            "status": "queued" if tasks else "completed",
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "startedAt": "",
            "finishedAt": utc_now() if not tasks else "",
            "artifactDir": str(run_dir(run_id)),
            "error": "",
            "maxAgents": max_agent_count,
            "sourceBackup": backup,
            "validationCommands": validation_commands,
            "validationResults": [],
            "tasks": tasks,
            "progress": _progress(tasks),
        }
        _save_run(run)
        if launch and tasks:
            launch_fix_worker(run_id)
        return show_fix_run(run_id)
    finally:
        _release_start_lock(review_id)


def cancel_fix_run(run_id: str) -> dict[str, Any]:
    run = load_fix_run(run_id)
    if run.get("status") in TERMINAL_FIX_STATUSES:
        return show_fix_run(run_id)
    flag = cancel_flag_path(run_id)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(utc_now(), encoding="utf-8")
    run["cancelRequested"] = True
    if run.get("status") == "queued":
        run["status"] = "cancelled"
        run["finishedAt"] = utc_now()
        run["error"] = run.get("error") or "Cancelled before execution."
        for task in run.get("tasks", []):
            if task.get("status") == "queued":
                task["status"] = "cancelled"
                task["error"] = task.get("error") or "Cancelled before launch."
    _save_run(run)
    return show_fix_run(run_id)


def active_fix_run_for_review(review_id: str) -> dict[str, Any] | None:
    review_id = review_swarm.validate_review_id(review_id)
    for run in list_fix_runs():
        if run.get("reviewId") == review_id and run.get("status") not in TERMINAL_FIX_STATUSES:
            return run
    return None


def _minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = review_swarm._minimal_reviewer_env(account_home=extra or {})
    env["CLADEX_FIX_WORKER"] = "1"
    return env


def _task_prompt(run: dict[str, Any], task: dict[str, Any]) -> str:
    files = "\n".join(f"- {item}" for item in task.get("files") or ["."])
    return (
        "You are a CLADEX Fix Review worker.\n"
        "Apply one targeted fix only. The change must trace directly to the finding below.\n"
        "Do not edit files outside the assigned files unless the assignment is `.`. Do not edit CLADEX unless this workspace is CLADEX and the operator explicitly selected a CLADEX self-fix run.\n"
        "Keep the patch minimal, preserve existing style, and stop if the task requires secrets or external credentials.\n\n"
        f"Workspace: {run.get('workspace')}\n"
        f"Run: {run.get('id')}\n"
        f"Task: {task.get('id')}\n"
        f"Finding: {task.get('findingId')} {task.get('title')}\n"
        f"Severity/category: {task.get('severity')} / {task.get('category')}\n"
        f"Assigned files:\n{files}\n\n"
        f"Evidence:\n{task.get('detail')}\n\n"
        f"Required fix:\n{task.get('recommendation')}\n\n"
        "After editing, summarize changed files and validation commands you ran. Do not include secret values."
    )


def _normalize_rel_path(value: Any) -> str:
    return str(value or ".").replace("\\", "/").strip().strip("/") or "."


def _git_dirty_paths(workspace: Path) -> set[str] | None:
    if not (workspace / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(workspace),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    paths: set[str] = set()
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        if path:
            paths.add(_normalize_rel_path(path))
    return paths


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_file_snapshot(workspace: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [
            name
            for name in dirs
            if name.lower() not in review_swarm.SKIP_DIRS and name.lower() != ".git"
        ]
        current = Path(root)
        for name in files:
            path = current / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                rel = _normalize_rel_path(path.relative_to(workspace).as_posix())
                snapshot[rel] = _hash_file(path)
            except Exception:
                continue
    return snapshot


def _workspace_change_snapshot(workspace: Path) -> dict[str, Any]:
    dirty = _git_dirty_paths(workspace)
    if dirty is not None:
        # Hash every dirty path's contents so a worker editing an
        # already-dirty file (path stays in the git status set) is still
        # detected. Pure path-set comparison missed those edits.
        dirty_hashes: dict[str, str] = {}
        for rel_path in dirty:
            full_path = workspace / rel_path
            try:
                if full_path.is_file() and not full_path.is_symlink():
                    dirty_hashes[rel_path] = _hash_file(full_path)
                else:
                    dirty_hashes[rel_path] = "__missing__"
            except OSError:
                dirty_hashes[rel_path] = "__error__"
        return {"kind": "git", "paths": dirty, "hashes": dirty_hashes}
    return {"kind": "scan", "files": _scan_file_snapshot(workspace)}


def _workspace_touched_files(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    if before.get("kind") == "git" and after.get("kind") == "git":
        before_paths = set(before.get("paths") or set())
        after_paths = set(after.get("paths") or set())
        before_hashes = before.get("hashes") if isinstance(before.get("hashes"), dict) else {}
        after_hashes = after.get("hashes") if isinstance(after.get("hashes"), dict) else {}
        touched = after_paths - before_paths
        # Detect content edits to files that were already dirty (path stays
        # in both sets but its content hash changed).
        for path in before_paths & after_paths:
            if before_hashes.get(path) != after_hashes.get(path):
                touched.add(path)
        # Files that left the dirty set (e.g. the worker reverted them) also
        # count as touched so reverted unrelated edits are caught.
        touched.update(before_paths - after_paths)
        return touched
    before_files = before.get("files") if isinstance(before.get("files"), dict) else {}
    after_files = after.get("files") if isinstance(after.get("files"), dict) else {}
    touched = {
        path
        for path, digest in after_files.items()
        if before_files.get(path) != digest
    }
    touched.update(path for path in before_files if path not in after_files)
    return touched


def _changed_outside_assigned(changed_files: set[str], assigned_files: list[Any]) -> list[str]:
    assigned = [_normalize_rel_path(item) for item in assigned_files or ["."]]
    if "." in assigned:
        return []
    outside: list[str] = []
    for changed in sorted(changed_files):
        allowed = False
        for item in assigned:
            prefix = item.rstrip("/") + "/"
            if changed == item or changed.startswith(prefix):
                allowed = True
                break
        if not allowed:
            outside.append(changed)
    return outside


def _run_cli(
    command: list[str],
    prompt: str,
    *,
    env: dict[str, str],
    cwd: Path,
    cancel_check: Callable[[], bool] | None = None,
) -> review_swarm.AIRunResult:
    return review_swarm._run_cli(command, prompt, env=env, cwd=cwd, cancel_check=cancel_check)


def _run_provider_fix_task(run: dict[str, Any], task: dict[str, Any]) -> review_swarm.AIRunResult:
    workspace = Path(str(run.get("workspace"))).expanduser().resolve()
    provider = str(task.get("provider") or run.get("provider") or "codex")
    account_home = str(run.get("accountHome") or "").strip()
    if provider == "claude":
        command = [
            claude_relay.claude_code_bin(),
            "-p",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Grep,Glob,LS,Edit,MultiEdit,Write,Bash",
            "--no-session-persistence",
            "--output-format",
            "text",
            "Read the fix task from stdin, apply the targeted change, and summarize the result.",
        ]
        extra = {"CLAUDE_CONFIG_DIR": account_home} if account_home else {}
        return _run_cli(
            command,
            _task_prompt(run, task),
            env=_minimal_env(extra),
            cwd=workspace,
            cancel_check=lambda: _cancel_requested(str(run.get("id") or "")),
        )
    command = [
        relay_common.resolve_codex_bin(),
        "--cd",
        str(workspace),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-",
    ]
    extra = {"CODEX_HOME": account_home} if account_home else {}
    return _run_cli(
        command,
        _task_prompt(run, task),
        env=_minimal_env(extra),
        cwd=workspace,
        cancel_check=lambda: _cancel_requested(str(run.get("id") or "")),
    )


def run_fix_task_once(run_id: str, task_id: str) -> dict[str, Any]:
    run = load_fix_run(run_id)
    task = next((item for item in run.get("tasks", []) if item.get("id") == task_id), None)
    if task is None:
        raise ValueError("unknown fix task id")
    if task.get("status") in {"done", "failed", "cancelled"}:
        return show_fix_run(run_id)
    if _cancel_requested(run_id):
        def cancel_update(_run: dict[str, Any], current: dict[str, Any]) -> None:
            current["status"] = "cancelled"
            current["error"] = "Cancelled before launch."
            current["finishedAt"] = utc_now()

        _update_task(run_id, task_id, cancel_update)
        return show_fix_run(run_id)

    def start_update(_run: dict[str, Any], current: dict[str, Any]) -> None:
        current["status"] = "running"
        current["startedAt"] = current.get("startedAt") or utc_now()
        current["attempts"] = int(current.get("attempts", 0) or 0) + 1

    run = _update_task(run_id, task_id, start_update)
    task = next(item for item in run.get("tasks", []) if item.get("id") == task_id)
    workspace = Path(str(run.get("workspace"))).expanduser().resolve()
    before = _workspace_change_snapshot(workspace)
    result = _run_provider_fix_task(run, task)
    after = _workspace_change_snapshot(workspace)
    changed_files = _workspace_touched_files(before, after)
    outside_assigned = _changed_outside_assigned(changed_files, list(task.get("files") or ["."]))
    if result.ok and outside_assigned:
        result = review_swarm.AIRunResult(
            text=result.text,
            ok=False,
            error=f"Fix worker edited files outside assigned task scope: {', '.join(outside_assigned[:12])}",
        )
    output_path = task_output_path(run_id, task_id)
    _atomic_write_text(output_path, result.text or result.error or "")

    def finish_update(_run: dict[str, Any], current: dict[str, Any]) -> None:
        current["outputPath"] = str(output_path)
        current["changedFiles"] = sorted(changed_files)
        current["finishedAt"] = utc_now()
        if result.ok:
            current["status"] = "done"
            current["error"] = ""
        elif _cancel_requested(run_id) or "cancelled" in (result.error or "").lower():
            current["status"] = "cancelled"
            current["error"] = result.error or "Cancelled by operator."
        else:
            current["status"] = "failed"
            current["error"] = result.error or "Fix worker failed."

    _update_task(run_id, task_id, finish_update)
    return show_fix_run(run_id)


def _run_one_validation_command(
    command: list[Any],
    *,
    workspace: Path,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, int, str]:
    if cancel_check is not None and cancel_check():
        return "cancelled", -1, "Cancelled before validation command launched."
    popen_kwargs: dict[str, Any] = {
        "cwd": str(workspace),
        "env": _minimal_env(),
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen([str(part) for part in command], **popen_kwargs)
    except Exception as exc:
        return "failed", 1, str(exc)
    deadline = time.monotonic() + VALIDATION_TIMEOUT_SECONDS
    while True:
        try:
            stdout, stderr = process.communicate(timeout=1.0)
            output = "\n".join(part for part in (stdout, stderr) if part)
            return ("passed" if process.returncode == 0 else "failed"), int(process.returncode or 0), output
        except subprocess.TimeoutExpired:
            if cancel_check is not None and cancel_check():
                review_swarm._terminate_process_tree(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    stdout, stderr = "", ""
                output = "\n".join(part for part in (stdout, stderr, "Cancelled by operator.") if part)
                return "cancelled", -1, output
            if time.monotonic() >= deadline:
                review_swarm._terminate_process_tree(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except Exception:
                    stdout, stderr = "", ""
                output = "\n".join(part for part in (stdout, stderr, "Validation command timed out.") if part)
                return "failed", 124, output


def _run_validation_commands(
    run: dict[str, Any],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    workspace = Path(str(run.get("workspace"))).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for command in run.get("validationCommands", []):
        if not isinstance(command, list) or not command:
            continue
        started = utc_now()
        status, returncode, output = _run_one_validation_command(
            command,
            workspace=workspace,
            cancel_check=cancel_check,
        )
        results.append(
            {
                "command": command,
                "status": status,
                "returncode": returncode,
                "startedAt": started,
                "finishedAt": utc_now(),
                "output": review_swarm.sanitize_text(output, limit=12000),
            }
        )
        if status != "passed":
            break
    return results


def run_fix_run(run_id: str) -> dict[str, Any]:
    run = load_fix_run(run_id)
    if run.get("status") in TERMINAL_FIX_STATUSES:
        return show_fix_run(run_id)
    if not _acquire_run_lock(run_id):
        return show_fix_run(run_id)
    try:
        run = load_fix_run(run_id)
        if _cancel_requested(run_id):
            run["status"] = "cancelled"
            run["finishedAt"] = utc_now()
            run["error"] = run.get("error") or "Cancelled before execution."
            _save_run(run)
            return show_fix_run(run_id)
        run["status"] = "running"
        run["startedAt"] = run.get("startedAt") or utc_now()
        _save_run(run)
        phases = sorted({int(task.get("phase", 3) or 3) for task in run.get("tasks", [])})
        for phase in phases:
            if _cancel_requested(run_id):
                break
            phase_tasks = [
                task for task in load_fix_run(run_id).get("tasks", []) if int(task.get("phase", 3) or 3) == phase and task.get("status") == "queued"
            ]
            max_workers = _safe_phase_parallelism(
                phase_tasks,
                validate_max_agents(run.get("maxAgents", DEFAULT_FIX_MAX_AGENTS)),
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(run_fix_task_once, run_id, task["id"]) for task in phase_tasks]
                for future in concurrent.futures.as_completed(futures):
                    future.result()
            run = load_fix_run(run_id)
            if any(task.get("status") == "failed" for task in run.get("tasks", []) if int(task.get("phase", 3) or 3) == phase):
                run["status"] = "failed"
                run["finishedAt"] = utc_now()
                restore_command = restore_command_for_run(run)
                run["error"] = f"Phase {phase} failed. Restore with `{restore_command}`."
                _save_run(run)
                return show_fix_run(run_id)
            if _cancel_requested(run_id):
                break
            validation_results = _run_validation_commands(run, cancel_check=lambda: _cancel_requested(run_id))
            if validation_results:
                run = load_fix_run(run_id)
                run.setdefault("validationResults", []).extend(validation_results)
                if any(item.get("status") == "cancelled" for item in validation_results):
                    run["status"] = "cancelled"
                    run["finishedAt"] = utc_now()
                    run["error"] = "Cancelled during validation."
                    _save_run(run)
                    return show_fix_run(run_id)
                if any(item.get("status") != "passed" for item in validation_results):
                    run["status"] = "failed"
                    run["finishedAt"] = utc_now()
                    restore_command = restore_command_for_run(run)
                    run["error"] = f"Validation failed after phase {phase}. Restore with `{restore_command}`."
                    _save_run(run)
                    return show_fix_run(run_id)
                _save_run(run)
        run = load_fix_run(run_id)
        if _cancel_requested(run_id):
            run["status"] = "cancelled"
            run["error"] = "Cancelled before all fix tasks finished."
            for task in run.get("tasks", []):
                if task.get("status") == "queued":
                    task["status"] = "cancelled"
                    task["error"] = "Cancelled before launch."
        else:
            failed = sum(1 for task in run.get("tasks", []) if task.get("status") == "failed")
            cancelled = sum(1 for task in run.get("tasks", []) if task.get("status") == "cancelled")
            run["status"] = "completed_with_warnings" if failed or cancelled else "completed"
            run["error"] = "" if not failed and not cancelled else "Fix run finished with warnings."
        run["finishedAt"] = utc_now()
        _save_run(run)
        return show_fix_run(run_id)
    finally:
        _release_run_lock(run_id)


def restore_command_for_run(run: dict[str, Any]) -> str:
    backup_id = str((run.get("sourceBackup") or {}).get("id") or "").strip()
    if not backup_id:
        return ""
    return f"cladex backup restore {backup_id} --confirm {backup_id}"
