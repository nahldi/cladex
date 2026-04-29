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


FIX_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "recommendedAgentCount", "tasks"],
    "additionalProperties": True,
    "properties": {
        "summary": {"type": "string"},
        "recommendedAgentCount": {"type": "integer", "minimum": 1, "maximum": MAX_FIX_AGENTS},
        "rationale": {"type": "string"},
        "tasks": {
            "type": "array",
            "minItems": 0,
            "items": {
                "type": "object",
                "required": ["title", "provider", "findingIds", "files", "phase"],
                "additionalProperties": True,
                "properties": {
                    "title": {"type": "string"},
                    "provider": {"type": "string", "enum": ["codex", "claude"]},
                    "reasoningEffort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"]},
                    "findingIds": {"type": "array", "items": {"type": "string"}},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "phase": {"type": "integer", "minimum": 1, "maximum": 3},
                    "category": {"type": "string"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "recommendation": {"type": "string"},
                    "rationale": {"type": "string"},
                    "dependsOn": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


_PROVIDER_GUIDE = (
    "Provider strengths to inform per-task assignment:\n"
    "- Codex: fast iterative code edits, test authoring, focused single-file refactors, "
    "shell-driven validation of small changes. Stronger at narrow surgical patches.\n"
    "- Claude: cross-file semantic refactors, dependency graph reasoning, careful API or "
    "config rewrites, doc rewrites, multi-step plans with shared context. Stronger when a "
    "fix touches several files or needs holistic understanding."
)


def _project_inventory(workspace: Path, *, max_files: int = 200) -> dict[str, Any]:
    languages: dict[str, int] = {}
    files: list[str] = []
    has_tests = False
    for current, dirs, names in os.walk(workspace):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if name.lower() not in review_swarm.SKIP_DIRS and not name.startswith(".")]
        for name in names:
            if name.startswith(".env") or name.startswith("."):
                continue
            rel = (current_path / name).relative_to(workspace).as_posix()
            ext = Path(name).suffix.lower()
            if ext:
                languages[ext] = languages.get(ext, 0) + 1
            if "test" in rel.lower() or "spec" in rel.lower():
                has_tests = True
            if len(files) < max_files:
                files.append(rel)
    return {
        "fileCount": sum(languages.values()) or 0,
        "languages": dict(sorted(languages.items(), key=lambda item: -item[1])[:12]),
        "hasTests": has_tests,
        "filesSample": files,
    }


def _planner_prompt(review_job: dict[str, Any], findings: list[dict[str, Any]], inventory: dict[str, Any]) -> str:
    valid_ids = [str(item.get("id") or "").strip() for item in findings if isinstance(item, dict)]
    valid_ids = [fid for fid in valid_ids if fid]
    findings_blob = json.dumps(findings, ensure_ascii=False)[:80000]
    inventory_blob = json.dumps(inventory, ensure_ascii=False)[:4000]
    schema_blob = json.dumps(FIX_PLAN_SCHEMA, ensure_ascii=False)
    self_fix = bool(review_job.get("selfReview") or review_job.get("allowSelfReview"))
    self_fix_rule = (
        "- Self-fix run: CLADEX is BOTH the target project and the runtime, so "
        "tasks may edit CLADEX source files to close the listed findings.\n"
        if self_fix
        else "- Tasks must NOT include CLADEX self-fix targeting (we cannot edit the\n"
        "  CLADEX runtime repo from a normal project run).\n"
    )
    return (
        "Plan a Fix Review for a project that has just finished a structured\n"
        "review pass. The output goes to a deterministic CLADEX runtime that\n"
        "will reject any task whose findingIds do not match the input list.\n"
        "Do NOT invent your own task topics, do NOT reference past CLADEX\n"
        "roadmap phases, do NOT skip the JSON schema. Group/route the listed\n"
        "findings only.\n"
        "\n"
        f"=== VALID findingIds ({len(valid_ids)} total) ===\n"
        f"{json.dumps(valid_ids)}\n"
        "Every task you emit MUST have a non-empty `findingIds` array drawn\n"
        "ONLY from this exact list. Tasks without findingIds will be discarded.\n"
        "\n"
        f"=== JSON Schema (this is the ONLY valid output shape) ===\n"
        f"{schema_blob}\n"
        "\n"
        f"=== Findings (full JSON, {len(findings)} items) ===\n"
        f"{findings_blob}\n"
        "\n"
        "Hard rules:\n"
        "- One task can group several findings if they share files or root cause.\n"
        "- Phase 1 = blocking risks, phase 2 = stabilize/validate, phase 3 = polish.\n"
        "- recommendedAgentCount is the safe number of concurrent fix workers\n"
        "  given the dependency graph (1-10).\n"
        "- Pick `provider` per task: codex for surgical edits + shell-driven\n"
        "  validation, claude for cross-file refactors and documentation.\n"
        f"{self_fix_rule}"
        "\n"
        f"=== Provider strengths ===\n{_PROVIDER_GUIDE}\n"
        "\n"
        f"=== Project context (advisory) ===\n"
        f"Review provider: {review_job.get('provider')}\n"
        f"Workspace: {review_job.get('workspace')}\n"
        f"Inventory: {inventory_blob}\n"
        "\n"
        "Return ONLY a JSON object matching the schema above. No prose, no\n"
        "markdown fences, no commentary outside the JSON.\n"
    )


def _run_claude_planner(prompt: str, account_home: str | None) -> dict[str, Any] | None:
    cmd = [
        claude_relay.claude_code_bin(),
        "-p",
        "--tools",
        "",
        "--disallowedTools",
        "Bash,Edit,MultiEdit,Write,NotebookEdit",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--output-format",
        "text",
        "--json-schema",
        json.dumps(FIX_PLAN_SCHEMA),
    ]
    extra = {"CLAUDE_CONFIG_DIR": account_home} if account_home else {}
    env = review_swarm._minimal_reviewer_env(account_home=extra)
    result = review_swarm._run_cli(cmd, prompt, env=env, cwd=None)
    if not result.ok or not result.text.strip():
        return None
    return _parse_planner_payload(result.text)


def _run_codex_planner(prompt: str, account_home: str | None) -> dict[str, Any] | None:
    import relayctl

    cmd = [
        relayctl.resolve_codex_bin(),
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-",
    ]
    extra = {"CODEX_HOME": account_home} if account_home else {}
    env = review_swarm._minimal_reviewer_env(account_home=extra)
    result = review_swarm._run_cli(cmd, prompt, env=env, cwd=None)
    if not result.ok or not result.text.strip():
        return None
    return _parse_planner_payload(result.text)


def _parse_planner_payload(text: str) -> dict[str, Any] | None:
    sanitized = review_swarm.sanitize_text(text, limit=120000)
    payload = review_swarm._extract_json_payload(sanitized)
    if not isinstance(payload, dict):
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    return payload


def _ai_plan_fix_tasks(
    review_job: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    inventory: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Run the AI planner with bounded retries on no-plan/empty-output.

    The Codex CLI is stochastic — given the exact same prompt it sometimes
    drifts toward hallucinated tasks with no findingIds, sometimes returns a
    well-formed plan. We retry up to `CLADEX_FIX_PLANNER_RETRIES` (default 2
    additional attempts) before letting the deterministic fallback take over.
    """
    if not findings:
        return None
    try:
        max_retries = int(os.environ.get("CLADEX_FIX_PLANNER_RETRIES", "2"))
    except ValueError:
        max_retries = 2
    max_retries = max(0, min(max_retries, 5))
    last_result: tuple[list[dict[str, Any]], dict[str, Any]] | None = None
    for attempt in range(max_retries + 1):
        last_result = _ai_plan_fix_tasks_once(review_job, findings, inventory=inventory)
        if last_result is not None:
            return last_result
    return last_result


def _ai_plan_fix_tasks_once(
    review_job: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    inventory: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Single AI planner attempt — extracted so the public wrapper can retry."""
    provider = review_swarm.validate_provider(str(review_job.get("provider") or "codex"))
    account_home = str(review_job.get("accountHome") or "").strip() or None
    prompt = _planner_prompt(review_job, findings, inventory)
    if provider == "claude":
        plan = _run_claude_planner(prompt, account_home)
    else:
        plan = _run_codex_planner(prompt, account_home)
    if plan is None:
        return None
    raw_tasks = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
    findings_by_id = {str(item.get("id") or ""): item for item in findings if isinstance(item, dict)}
    # Build a path → finding-id index so we can salvage planner tasks that
    # forgot the `findingIds` field but did pin specific files. Without this
    # rescue path, a single hallucinated/empty `findingIds` from the planner
    # collapses the whole plan to None and the deterministic 1:1 fallback
    # takes over — which loses the planner's grouping/provider intelligence.
    findings_by_path: dict[str, list[str]] = {}
    for fid, finding in findings_by_id.items():
        path_norm = _finding_path(finding.get("path"))
        if path_norm and path_norm != ".":
            findings_by_path.setdefault(path_norm, []).append(fid)
    tasks: list[dict[str, Any]] = []
    seen_findings: set[str] = set()
    # Remap the planner's task IDs → CLADEX-canonical `task-NNNN`. The planner
    # is free to use any naming for its own ids; we rewrite every dependsOn
    # reference so the dependency graph stays consistent after rename.
    raw_id_to_task_id: dict[str, str] = {}
    raw_depends: list[list[str]] = []
    for index, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict):
            continue
        finding_ids = [str(fid) for fid in (raw.get("findingIds") or []) if str(fid).strip()]
        finding_ids = [fid for fid in finding_ids if fid in findings_by_id]
        # Lenient salvage: if the planner emitted a task with files but no
        # findingIds, map files back onto findings whose path matches. Only
        # rescues findings the orchestrator hasn't already claimed elsewhere
        # so we don't double-assign work.
        if not finding_ids:
            files_raw = raw.get("files") or []
            for raw_file in files_raw:
                path_norm = _finding_path(raw_file)
                for candidate in findings_by_path.get(path_norm, []):
                    if candidate not in seen_findings and candidate not in finding_ids:
                        finding_ids.append(candidate)
        if not finding_ids:
            continue
        worker = str(raw.get("provider") or provider).strip().lower()
        if worker not in {"codex", "claude"}:
            worker = provider
        files_raw = raw.get("files") or []
        files = [str(item).strip() for item in files_raw if str(item).strip()]
        # Always seed with the first finding's path if the planner forgot.
        if not files:
            primary = findings_by_id.get(finding_ids[0]) or {}
            primary_path = _finding_path(primary.get("path"))
            files = [primary_path] if primary_path != "." else []
        phase = int(raw.get("phase") or _severity_phase(str(raw.get("severity") or "")))
        if phase < 1 or phase > 3:
            phase = 2
        primary_finding = findings_by_id.get(finding_ids[0]) or {}
        title = str(raw.get("title") or primary_finding.get("title") or "Fix task").strip()[:180]
        category = str(raw.get("category") or primary_finding.get("category") or "review").strip()[:80]
        severity = str(raw.get("severity") or primary_finding.get("severity") or "medium").strip().lower()
        recommendation = str(raw.get("recommendation") or primary_finding.get("recommendation") or "Apply a targeted fix.").strip()
        detail_bits = [str(findings_by_id.get(fid, {}).get("detail") or "").strip() for fid in finding_ids]
        detail = "\n\n".join(bit for bit in detail_bits if bit)
        rationale = str(raw.get("rationale") or "").strip()
        depends_on_raw = [str(item).strip() for item in (raw.get("dependsOn") or []) if str(item).strip()]
        reasoning_effort = str(raw.get("reasoningEffort") or "").strip().lower()
        if reasoning_effort and reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            reasoning_effort = ""
        canonical_id = f"task-{len(tasks) + 1:04d}"
        raw_id = str(raw.get("id") or "").strip()
        if raw_id:
            raw_id_to_task_id[raw_id] = canonical_id
        # Also let the planner reference a task by its (1-based) index in the
        # raw list — useful when it omits its own ids.
        raw_id_to_task_id[str(index)] = canonical_id
        raw_id_to_task_id[canonical_id] = canonical_id
        tasks.append(
            {
                "id": canonical_id,
                "findingId": finding_ids[0],
                "findingIds": finding_ids,
                "phase": phase,
                "provider": worker,
                "status": "queued",
                "title": title,
                "severity": severity,
                "category": category,
                "files": files,
                "recommendation": recommendation,
                "detail": detail,
                "rationale": rationale,
                "reasoningEffort": reasoning_effort,
                "dependsOn": [],  # filled in second pass
                "startedAt": "",
                "finishedAt": "",
                "attempts": 0,
                "error": "",
                "outputPath": "",
            }
        )
        raw_depends.append(depends_on_raw)
        seen_findings.update(finding_ids)
    # Second pass: remap planner-named dependencies onto canonical task IDs.
    for task, raw_deps in zip(tasks, raw_depends, strict=False):
        remapped: list[str] = []
        for dep in raw_deps:
            mapped = raw_id_to_task_id.get(dep)
            if mapped and mapped != task["id"] and mapped not in remapped:
                remapped.append(mapped)
        task["dependsOn"] = remapped
    if not tasks:
        return None
    # Catch-all task for findings the AI silently dropped so nothing is missed.
    missed = [fid for fid in findings_by_id if fid not in seen_findings]
    if missed:
        next_index = len(tasks) + 1
        primary = findings_by_id.get(missed[0]) or {}
        files = [_finding_path(primary.get("path"))] if _finding_path(primary.get("path")) != "." else []
        tasks.append(
            {
                "id": f"task-{next_index:04d}",
                "findingId": missed[0],
                "findingIds": missed,
                "phase": 3,
                "provider": provider,
                "status": "queued",
                "title": f"Catch-all: address {len(missed)} planner-skipped finding(s)",
                "severity": "low",
                "category": "planner-residual",
                "files": files,
                "recommendation": "Apply targeted fixes for the findings the orchestrator skipped, one by one.",
                "detail": "Planner did not assign these findings; CLADEX added a residual task so they are not silently dropped.",
                "rationale": "Residual safety net so every finding from the review reaches a fix worker.",
                "reasoningEffort": "",
                "dependsOn": [],
                "startedAt": "",
                "finishedAt": "",
                "attempts": 0,
                "error": "",
                "outputPath": "",
            }
        )
    tasks.sort(key=lambda item: (int(item.get("phase", 3)), str(item.get("id", ""))))
    summary = str(plan.get("summary") or "").strip()
    rationale = str(plan.get("rationale") or "").strip()
    recommended_count = plan.get("recommendedAgentCount")
    try:
        recommended_count = max(1, min(MAX_FIX_AGENTS, int(recommended_count)))
    except (TypeError, ValueError):
        recommended_count = 1
    metadata = {
        "source": "ai",
        "provider": provider,
        "summary": summary,
        "rationale": rationale,
        "recommendedAgentCount": recommended_count,
        "taskCount": len(tasks),
    }
    return tasks, metadata


def _deterministic_fix_tasks(review_job: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                "findingIds": [finding_id],
                "phase": _severity_phase(str(finding.get("severity") or "")),
                "provider": provider,
                "status": "queued",
                "title": str(finding.get("title") or "Review finding").strip()[:180],
                "severity": str(finding.get("severity") or "medium").strip().lower(),
                "category": str(finding.get("category") or "review").strip()[:80],
                "files": files,
                "recommendation": str(finding.get("recommendation") or "Apply a targeted fix.").strip(),
                "detail": str(finding.get("detail") or "").strip(),
                "rationale": "",
                "reasoningEffort": "",
                "dependsOn": [],
                "startedAt": "",
                "finishedAt": "",
                "attempts": 0,
                "error": "",
                "outputPath": "",
            }
        )
    tasks.sort(key=lambda item: (int(item.get("phase", 3)), str(item.get("id", ""))))
    return tasks


def _build_tasks(
    review_job: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    workspace: Path | None = None,
    use_ai_planner: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (tasks, plan_metadata).

    Tries the AI planner first when `use_ai_planner` is true and a workspace
    is provided. Falls back to a deterministic 1:1 mapping if the AI call
    fails, so a missing/broken provider never blocks Fix Review.
    """
    if use_ai_planner and workspace is not None and findings:
        try:
            inventory = _project_inventory(workspace)
            ai = _ai_plan_fix_tasks(review_job, findings, inventory=inventory)
            if ai is not None:
                tasks, metadata = ai
                return tasks, metadata
        except Exception as exc:
            return _deterministic_fix_tasks(review_job, findings), {
                "source": "deterministic",
                "fallbackReason": f"AI planner raised: {exc}",
                "taskCount": len(findings),
                "recommendedAgentCount": 1,
            }
    deterministic = _deterministic_fix_tasks(review_job, findings)
    return deterministic, {
        "source": "deterministic",
        "fallbackReason": "AI planner disabled or returned no plan",
        "taskCount": len(deterministic),
        "recommendedAgentCount": 1,
    }


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
    plan = run.get("plan") or {}
    lines = [
        f"# CLADEX Fix Run - {run.get('title') or run.get('id')}",
        "",
        f"- Run: `{run.get('id')}`",
        f"- Review: `{run.get('reviewId')}`",
        f"- Workspace: `{run.get('workspace')}`",
        f"- Status: `{run.get('status')}`",
        f"- Source backup: `{(run.get('sourceBackup') or {}).get('id', 'not created')}`",
        f"- Max fix agents: `{run.get('maxAgents')}`{' (operator requested ' + str(run.get('requestedMaxAgents')) + ')' if run.get('requestedMaxAgents') and run.get('requestedMaxAgents') != run.get('maxAgents') else ''}",
        f"- Planner source: `{plan.get('source', 'unknown')}`",
        "",
    ]
    if plan.get("source") == "ai":
        lines.extend([
            "## Orchestrator Plan",
            "",
            f"- Recommended concurrent agents: `{plan.get('recommendedAgentCount', 1)}`",
            f"- Planner provider: `{plan.get('provider', 'codex')}`",
        ])
        if plan.get("summary"):
            lines.extend(["", str(plan.get("summary"))])
        if plan.get("rationale"):
            lines.extend(["", "Rationale:", "", str(plan.get("rationale"))])
        lines.append("")
    elif plan.get("fallbackReason"):
        lines.extend([
            "## Planner Fallback",
            "",
            f"- Reason: `{plan.get('fallbackReason')}`",
            "- A deterministic 1-task-per-finding plan is being used so Fix Review still runs.",
            "",
        ])
    lines.extend([
        "## Progress",
        "",
        f"- Queued: `{progress.get('queued', 0)}/{progress.get('total', 0)}`",
        f"- Running: `{progress.get('running', 0)}/{progress.get('total', 0)}`",
        f"- Done: `{progress.get('done', 0)}/{progress.get('total', 0)}`",
        f"- Failed: `{progress.get('failed', 0)}/{progress.get('total', 0)}`",
        f"- Cancelled: `{progress.get('cancelled', 0)}/{progress.get('total', 0)}`",
        "",
    ])
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
        # AI orchestrator picks per-task provider + agent count; falls back to
        # a deterministic 1:1 plan if the planner is unreachable so Fix Review
        # never blocks on a missing or rate-limited provider.
        tasks, plan_metadata = _build_tasks(
            review_job,
            findings,
            workspace=workspace,
            use_ai_planner=os.environ.get("CLADEX_FIX_PLANNER_DISABLE", "").strip().lower() not in {"1", "true", "yes"},
        )
        recommended_agents = plan_metadata.get("recommendedAgentCount") or 1
        try:
            recommended_agents = max(1, min(MAX_FIX_AGENTS, int(recommended_agents)))
        except (TypeError, ValueError):
            recommended_agents = 1
        # Honor the smaller of operator-requested max_agents and planner-recommended.
        effective_max_agents = min(max_agent_count, recommended_agents)
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
            "maxAgents": effective_max_agents,
            "requestedMaxAgents": max_agent_count,
            "plan": plan_metadata,
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
        "Do not use `git stash`, `git reset`, or checkout commands to hide unrelated local changes; validate against the workspace as given.\n"
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


def _stable_scope_check(
    workspace: Path,
    before: dict[str, Any],
    assigned_files: list[Any],
    *,
    attempts: int = 3,
    delay_seconds: float = 0.25,
) -> tuple[set[str], list[str]]:
    """Return changed files and outside-scope paths after transient state settles."""
    attempts = max(1, attempts)
    changed_files: set[str] = set()
    outside_assigned: list[str] = []
    for attempt in range(attempts):
        after = _workspace_change_snapshot(workspace)
        next_changed = _workspace_touched_files(before, after)
        next_outside = _changed_outside_assigned(next_changed, assigned_files)
        changed_files = next_changed
        outside_assigned = next_outside
        if not outside_assigned:
            break
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    return changed_files, outside_assigned


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
        # `--allowedTools` is the canonical Claude Code flag (the deprecated
        # `--tools` was being silently ignored, which combined with
        # `--permission-mode dontAsk` denied every write tool and turned
        # every Claude fix task into a no-op).
        # `bypassPermissions` is required so Edit/Write/Bash run without
        # prompting; the operator already opted in to write access via the
        # `--allow-cladex-self-fix`-gated Fix Review backup.
        command = [
            claude_relay.claude_code_bin(),
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--allowedTools",
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
    changed_files, outside_assigned = _stable_scope_check(
        workspace,
        before,
        list(task.get("files") or ["."]),
    )
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
