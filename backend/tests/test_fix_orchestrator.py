from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

import claude_relay
import fix_orchestrator
import review_swarm


def _review_with_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("eval(user_input)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(review_swarm, "BACKUP_DATA_ROOT", tmp_path / "backups")
    monkeypatch.setattr(fix_orchestrator, "FIX_DATA_ROOT", tmp_path / "fix-runs")
    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=1,
        preflight_only=True,
        launch=False,
        backup_before_review=False,
    )
    finished = review_swarm.run_review_job(job["id"])
    findings = [
        {
            "id": "F0001",
            "severity": "high",
            "category": "unsafe-execution",
            "path": "app.py",
            "line": 1,
            "title": "Unsafe eval",
            "detail": "eval(user_input) executes arbitrary input.",
            "recommendation": "Replace eval with a safe parser.",
            "confidence": "high",
        }
    ]
    review_swarm._write_json(review_swarm.findings_json_path(finished["id"]), {"jobId": finished["id"], "findings": findings})
    return review_swarm.show_review(finished["id"])


def test_start_fix_run_requires_completed_review_and_creates_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)

    run = fix_orchestrator.start_fix_run(review["id"], launch=False)

    assert run["status"] == "queued"
    assert run["reviewId"] == review["id"]
    assert run["sourceBackup"]["id"].startswith("backup-")
    assert run["progress"]["total"] == 1
    assert run["tasks"][0]["findingId"] == "F0001"
    assert Path(run["reportPath"]).exists()


def test_start_fix_run_returns_existing_active_run_for_duplicate_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)

    first = fix_orchestrator.start_fix_run(review["id"], launch=False)
    second = fix_orchestrator.start_fix_run(review["id"], launch=False)

    assert second["id"] == first["id"]
    backups = review_swarm.list_backups()
    assert len(backups) == 1


def test_start_fix_run_requires_self_review_for_protected_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)

    def fake_violation(_workspace: Path, *, env: dict[str, str] | None = None, **_kwargs: object) -> str:
        return "" if (env or {}).get("CLADEX_ALLOW_CLADEX_WORKSPACE") == "1" else "protected workspace"

    monkeypatch.setattr(fix_orchestrator, "workspace_protection_violation", fake_violation)

    with pytest.raises(ValueError, match="self-review"):
        fix_orchestrator.start_fix_run(review["id"], launch=False)

    job = review_swarm.load_job(review["id"])
    job["selfReview"] = True
    review_swarm._write_json(review_swarm.job_json_path(review["id"]), job)

    with pytest.raises(ValueError, match="self-fix requires explicit"):
        fix_orchestrator.start_fix_run(review["id"], launch=False)

    run = fix_orchestrator.start_fix_run(review["id"], allow_self_fix=True, launch=False)

    assert run["selfReview"] is True
    assert run["selfFix"] is True


def test_run_fix_run_completes_tasks_without_live_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)

    def fake_worker(_run: dict, _task: dict) -> review_swarm.AIRunResult:
        return review_swarm.AIRunResult(text="changed app.py", ok=True)

    monkeypatch.setattr(fix_orchestrator, "_run_provider_fix_task", fake_worker)
    monkeypatch.setattr(fix_orchestrator, "_run_validation_commands", lambda _run, **_kwargs: [])

    run = fix_orchestrator.start_fix_run(review["id"], launch=False)
    finished = fix_orchestrator.run_fix_run(run["id"])

    assert finished["status"] == "completed"
    assert finished["progress"]["done"] == 1
    assert finished["tasks"][0]["status"] == "done"
    assert Path(finished["tasks"][0]["outputPath"]).read_text(encoding="utf-8") == "changed app.py"


def test_fix_task_success_is_rejected_when_worker_edits_outside_assigned_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)

    def fake_worker(run: dict, _task: dict) -> review_swarm.AIRunResult:
        Path(run["workspace"], "other.py").write_text("unexpected = True\n", encoding="utf-8")
        return review_swarm.AIRunResult(text="changed other.py", ok=True)

    monkeypatch.setattr(fix_orchestrator, "_run_provider_fix_task", fake_worker)

    run = fix_orchestrator.start_fix_run(review["id"], launch=False)
    result = fix_orchestrator.run_fix_task_once(run["id"], run["tasks"][0]["id"])

    task = result["tasks"][0]
    assert task["status"] == "failed"
    assert "outside assigned task scope" in task["error"]
    assert task["changedFiles"] == ["other.py"]


def test_ai_planner_groups_findings_and_picks_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator AI step must take findings + project shape and return
    a structured plan: per-task provider choice, agent-count recommendation,
    and at least one finding id per task."""
    monkeypatch.delenv("CLADEX_FIX_PLANNER_DISABLE", raising=False)
    project = tmp_path / "target"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("eval(user)\n", encoding="utf-8")
    (project / "src" / "lib.py").write_text("# TODO\n", encoding="utf-8")
    (project / "package.json").write_text('{"scripts":{"lint":"echo"}}', encoding="utf-8")

    findings = [
        {"id": "F0001", "severity": "high", "category": "unsafe-execution", "path": "src/app.py", "line": 1, "title": "Dynamic eval", "recommendation": "Replace eval", "detail": ""},
        {"id": "F0002", "severity": "low", "category": "maintenance", "path": "src/lib.py", "line": 1, "title": "TODO", "recommendation": "Resolve", "detail": ""},
    ]
    plan_payload = {
        "summary": "Replace eval and resolve the TODO.",
        "rationale": "Group code-grounded refactors on Codex; keep doc-style cleanup on Claude.",
        "recommendedAgentCount": 2,
        "tasks": [
            {
                "title": "Replace eval with safe parser",
                "provider": "codex",
                "reasoningEffort": "high",
                "findingIds": ["F0001"],
                "files": ["src/app.py"],
                "phase": 1,
                "category": "unsafe-execution",
                "severity": "high",
                "recommendation": "Use ast.parse + safe ops",
                "rationale": "Surgical local change with shell validation",
            },
            {
                "title": "Resolve TODO marker",
                "provider": "claude",
                "reasoningEffort": "medium",
                "findingIds": ["F0002"],
                "files": ["src/lib.py"],
                "phase": 3,
                "category": "maintenance",
                "severity": "low",
                "recommendation": "Replace TODO with explicit follow-up issue",
                "rationale": "Doc-style cleanup",
                "dependsOn": ["task-0001"],
            },
        ],
    }

    captured: dict[str, str] = {}

    def fake_codex_planner(prompt: str, account_home):
        captured["prompt_len"] = len(prompt)
        captured["account_home"] = account_home or ""
        return plan_payload

    def fake_claude_planner(prompt: str, account_home):
        # Should NOT be called when review provider is codex.
        captured["claude_called"] = "yes"
        return None

    monkeypatch.setattr(fix_orchestrator, "_run_codex_planner", fake_codex_planner)
    monkeypatch.setattr(fix_orchestrator, "_run_claude_planner", fake_claude_planner)

    review_job = {"id": "review-x", "workspace": str(project), "provider": "codex", "accountHome": ""}
    tasks, metadata = fix_orchestrator._build_tasks(review_job, findings, workspace=project, use_ai_planner=True)

    assert "claude_called" not in captured
    assert metadata["source"] == "ai"
    assert metadata["recommendedAgentCount"] == 2
    assert len(tasks) == 2
    assert tasks[0]["provider"] == "codex"
    assert tasks[0]["reasoningEffort"] == "high"
    assert tasks[0]["findingIds"] == ["F0001"]
    assert tasks[1]["provider"] == "claude"
    assert tasks[1]["dependsOn"] == ["task-0001"]


def test_ai_planner_falls_back_to_deterministic_when_provider_returns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the planner subprocess fails, returns garbage, or gets cancelled,
    Fix Review must NEVER block. It falls back to the deterministic
    1-task-per-finding plan and records the fallback reason."""
    monkeypatch.delenv("CLADEX_FIX_PLANNER_DISABLE", raising=False)
    project = tmp_path / "target"
    project.mkdir()
    findings = [
        {"id": "F0001", "severity": "high", "path": "src/app.py", "title": "boom", "recommendation": "fix"},
    ]
    monkeypatch.setattr(fix_orchestrator, "_run_codex_planner", lambda prompt, home: None)
    monkeypatch.setattr(fix_orchestrator, "_run_claude_planner", lambda prompt, home: None)

    review_job = {"id": "review-x", "workspace": str(project), "provider": "codex"}
    tasks, metadata = fix_orchestrator._build_tasks(review_job, findings, workspace=project, use_ai_planner=True)

    assert metadata["source"] == "deterministic"
    assert "fallbackReason" in metadata
    assert len(tasks) == 1
    assert tasks[0]["provider"] == "codex"


def test_ai_planner_adds_residual_task_for_skipped_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the AI planner silently drops some findings, the orchestrator must
    add a catch-all task so nothing reaches production unfixed."""
    monkeypatch.delenv("CLADEX_FIX_PLANNER_DISABLE", raising=False)
    project = tmp_path / "target"
    project.mkdir()
    findings = [
        {"id": "F0001", "severity": "high", "path": "a.py", "title": "a", "recommendation": "fix a"},
        {"id": "F0002", "severity": "high", "path": "b.py", "title": "b", "recommendation": "fix b"},
        {"id": "F0003", "severity": "low", "path": "c.py", "title": "c", "recommendation": "fix c"},
    ]
    plan_payload = {
        "summary": "skip last",
        "recommendedAgentCount": 1,
        "tasks": [
            {"title": "fix a+b", "provider": "codex", "findingIds": ["F0001", "F0002"], "files": ["a.py", "b.py"], "phase": 1},
        ],
    }
    monkeypatch.setattr(fix_orchestrator, "_run_codex_planner", lambda prompt, home: plan_payload)

    review_job = {"id": "review-x", "workspace": str(project), "provider": "codex"}
    tasks, metadata = fix_orchestrator._build_tasks(review_job, findings, workspace=project, use_ai_planner=True)

    assert metadata["source"] == "ai"
    assert any(t.get("category") == "planner-residual" for t in tasks)
    residual = next(t for t in tasks if t.get("category") == "planner-residual")
    assert "F0003" in residual["findingIds"]


def test_ai_planner_remaps_planner_named_dependencies_to_canonical_task_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planner is allowed to invent its own task ids (e.g. `fix-001`,
    `update-readme`). CLADEX rewrites every task id to the canonical
    `task-NNNN` shape, so any `dependsOn` referencing the planner's own
    naming must be remapped onto the canonical IDs — otherwise the dep
    graph silently breaks and phase-2 tasks would launch before phase-1.
    """
    monkeypatch.delenv("CLADEX_FIX_PLANNER_DISABLE", raising=False)
    project = tmp_path / "target"
    project.mkdir()
    findings = [
        {"id": "F1", "severity": "high", "path": "a.py", "title": "a", "recommendation": "fix a"},
        {"id": "F2", "severity": "low", "path": "README.md", "title": "docs", "recommendation": "doc"},
    ]
    plan_payload = {
        "summary": "Two-step plan",
        "recommendedAgentCount": 1,
        "tasks": [
            {
                "id": "fix-the-code",  # planner-invented id
                "title": "Fix code",
                "provider": "codex",
                "findingIds": ["F1"],
                "files": ["a.py"],
                "phase": 1,
            },
            {
                "id": "update-readme",
                "title": "Update README",
                "provider": "claude",
                "findingIds": ["F2"],
                "files": ["README.md"],
                "phase": 3,
                # dependsOn references the planner's own id, not task-0001
                "dependsOn": ["fix-the-code"],
            },
        ],
    }
    monkeypatch.setattr(fix_orchestrator, "_run_codex_planner", lambda prompt, home: plan_payload)

    review_job = {"id": "review-x", "workspace": str(project), "provider": "codex"}
    tasks, metadata = fix_orchestrator._build_tasks(
        review_job, findings, workspace=project, use_ai_planner=True
    )

    assert metadata["source"] == "ai"
    code_task = next(t for t in tasks if t["findingIds"] == ["F1"])
    docs_task = next(t for t in tasks if t["findingIds"] == ["F2"])
    assert code_task["id"] == "task-0001"
    assert docs_task["id"] == "task-0002"
    # The planner's `dependsOn: ["fix-the-code"]` must be rewritten to
    # `["task-0001"]` so Fix Review's phase scheduler waits for the code
    # task before launching the docs task.
    assert docs_task["dependsOn"] == ["task-0001"]


def test_workspace_touched_detects_edits_to_already_dirty_files(tmp_path: Path) -> None:
    """F0013/F0014: a worker editing an already-dirty file outside its
    assignment must still be detected. Pure path-set diff misses these
    edits because the path stays in `git status` before and after."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess = __import__("subprocess")
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=workspace, check=True, capture_output=True)
    (workspace / "tracked.py").write_text("v=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True)
    # Make `tracked.py` already dirty before the snapshot.
    (workspace / "tracked.py").write_text("v=2\n", encoding="utf-8")

    before = fix_orchestrator._workspace_change_snapshot(workspace)
    # A worker (incorrectly) edits the already-dirty unrelated file.
    (workspace / "tracked.py").write_text("v=3 # worker drift\n", encoding="utf-8")
    after = fix_orchestrator._workspace_change_snapshot(workspace)

    touched = fix_orchestrator._workspace_touched_files(before, after)
    assert "tracked.py" in touched, "edits to already-dirty paths must be detected via content hash"


def test_claude_fix_task_sends_large_prompt_through_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)
    run = fix_orchestrator.start_fix_run(review["id"], launch=False)
    run["provider"] = "claude"
    run["tasks"][0]["provider"] = "claude"
    run["tasks"][0]["detail"] = "long evidence " * 2000
    captured: dict[str, object] = {}

    def fake_run_cli(command: list[str], prompt: str, **kwargs: object) -> review_swarm.AIRunResult:
        captured["command"] = command
        captured["prompt"] = prompt
        captured["cwd"] = kwargs.get("cwd")
        return review_swarm.AIRunResult(text="changed", ok=True)

    monkeypatch.setattr(claude_relay, "claude_code_bin", lambda: "claude")
    monkeypatch.setattr(fix_orchestrator, "_run_cli", fake_run_cli)

    result = fix_orchestrator._run_provider_fix_task(run, run["tasks"][0])

    command = captured["command"]
    prompt = str(captured["prompt"])
    assert result.ok is True
    assert isinstance(command, list)
    assert all("long evidence " * 100 not in str(part) for part in command)
    assert command[-1] == "Read the fix task from stdin, apply the targeted change, and summarize the result."
    assert "long evidence" in prompt
    assert captured["cwd"] == Path(run["workspace"])


def test_fix_run_failed_validation_stops_with_restore_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fix_orchestrator,
        "_run_provider_fix_task",
        lambda _run, _task: review_swarm.AIRunResult(text="changed", ok=True),
    )
    monkeypatch.setattr(
        fix_orchestrator,
        "_run_validation_commands",
        lambda _run, **_kwargs: [{"command": ["pytest"], "status": "failed", "returncode": 1, "output": "boom"}],
    )

    run = fix_orchestrator.start_fix_run(review["id"], launch=False)
    finished = fix_orchestrator.run_fix_run(run["id"])

    assert finished["status"] == "failed"
    assert "Validation failed" in finished["error"]
    assert finished["sourceBackup"]["id"] in finished["error"]
    assert finished["restoreCommand"] == f"cladex backup restore {finished['sourceBackup']['id']} --confirm {finished['sourceBackup']['id']}"
    assert finished["restoreCommand"] in finished["error"]


def test_cancel_fix_run_marks_queued_tasks_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review_with_findings(tmp_path, monkeypatch)
    run = fix_orchestrator.start_fix_run(review["id"], launch=False)

    cancelled = fix_orchestrator.cancel_fix_run(run["id"])

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelRequested"] is True
    assert cancelled["progress"]["cancelled"] == 1


def test_validation_command_cancel_terminates_promptly(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    cancelled = False

    def flip_cancel() -> None:
        nonlocal cancelled
        cancelled = True

    timer = threading.Timer(0.2, flip_cancel)
    timer.start()
    started = time.monotonic()
    try:
        results = fix_orchestrator._run_validation_commands(
            {
                "workspace": str(project),
                "validationCommands": [[sys.executable, "-c", "import time; time.sleep(30)"]],
            },
            cancel_check=lambda: cancelled,
        )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 5
    assert results[0]["status"] == "cancelled"


def test_discover_validation_commands_uses_existing_project_scripts(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint .", "build": "vite build"}}), encoding="utf-8")
    (project / "tests").mkdir()

    commands = fix_orchestrator.discover_validation_commands(project)
    joined = [" ".join(command) for command in commands]

    assert any("npm run lint" in item for item in joined)
    assert any("npm run build" in item for item in joined)
    assert any("pytest" in item for item in joined)
