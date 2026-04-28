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
