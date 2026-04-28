from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import review_swarm


def test_review_swarm_preflight_writes_report_and_redacts_secret_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (project / ".env").write_text("DISCORD_TOKEN=abc.def.ghi\n", encoding="utf-8")
    (project / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8")
    src = project / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "password = 'supersecretvalue'\n"
        "eval(user_input)\n"
        "# TODO: add tests\n",
        encoding="utf-8",
    )
    vendor = project / "node_modules"
    vendor.mkdir()
    (vendor / "ignored.js").write_text("eval(should_not_scan)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=3, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "completed"
    assert finished["progress"]["done"] == 3
    report = Path(finished["reportPath"]).read_text(encoding="utf-8")
    findings = json.loads(Path(finished["findingsPath"]).read_text(encoding="utf-8"))["findings"]
    assert "supersecretvalue" not in report
    assert "ignored.js" not in report
    assert any(item["category"] == "secret-hygiene" for item in findings)
    assert any(item["category"] == "unsafe-execution" for item in findings)
    assert any(item["category"] == "maintenance" for item in findings)


def test_secret_name_findings_allowlists_env_template_files(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / ".env").write_text("REAL=1\n", encoding="utf-8")
    (project / ".env.example").write_text("PLACEHOLDER=value\n", encoding="utf-8")
    (project / ".env.template").write_text("PLACEHOLDER=value\n", encoding="utf-8")
    (project / ".env.sample").write_text("PLACEHOLDER=value\n", encoding="utf-8")
    (project / "secrets.example.json").write_text("{}\n", encoding="utf-8")
    (project / "secrets.json").write_text("{}\n", encoding="utf-8")

    findings = review_swarm.secret_name_findings(project)
    flagged = sorted(item["path"] for item in findings)

    assert flagged == [".env", "secrets.json"]
    assert review_swarm.is_template_secret_filename(".env.example") is True
    assert review_swarm.is_template_secret_filename("secrets.example.json") is True
    assert review_swarm.is_template_secret_filename(".env") is False
    assert review_swarm.is_template_secret_filename("secrets.json") is False


def test_scan_file_todo_marker_requires_word_boundary_and_comment_context(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    sample = project / "lib.py"
    sample.write_text(
        '"""Module that mentions todo without a comment."""\n'
        'PODCAST_TITLE = "todo list maker"\n'
        'def shack(): return 1\n'
        '# TODO: real comment marker\n'
        '# fixme: lowercase comment marker\n'
        '/* HACK: fix later */\n',
        encoding="utf-8",
    )

    findings = review_swarm.scan_file(sample, project)
    maintenance = [item for item in findings if item["category"] == "maintenance"]

    assert {item["line"] for item in maintenance} == {4, 5, 6}
    assert all("TODO" in item["detail"] or "FIXME" in item["detail"] or "HACK" in item["detail"] for item in maintenance)


def test_dedup_findings_merges_duplicate_lanes_and_keeps_highest_severity() -> None:
    raw = [
        {"category": "maintenance", "path": "src/app.py", "line": 12, "title": "Marker", "severity": "low", "agentId": "agent-01"},
        {"category": "maintenance", "path": "src/app.py", "line": 12, "title": "Marker", "severity": "medium", "agentId": "agent-02"},
        {"category": "maintenance", "path": "src/app.py", "line": 13, "title": "Marker", "severity": "low", "agentId": "agent-03"},
        {"category": "secret-hygiene", "path": "src/app.py", "line": 0, "title": "Hit", "severity": "high", "agentId": "agent-01"},
    ]

    deduped = review_swarm.dedup_findings(raw)

    by_key = {(item["path"], item["line"], item["title"]): item for item in deduped}
    merged = by_key[("src/app.py", 12, "Marker")]
    assert merged["severity"] == "medium"
    assert sorted(merged["seenByAgents"]) == ["agent-01", "agent-02"]
    assert by_key[("src/app.py", 13, "Marker")]["seenByAgents"] == ["agent-03"]
    assert by_key[("src/app.py", 0, "Hit")]["severity"] == "high"


def test_review_swarm_rejects_invalid_agent_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    with pytest.raises(ValueError, match="between 1 and 50"):
        review_swarm.start_review(project, agents=0, launch=False)
    with pytest.raises(ValueError, match="between 1 and 50"):
        review_swarm.start_review(project, agents=51, launch=False)


def test_review_swarm_sanitizes_assignment_values() -> None:
    text = "password = 'supersecretvalue'\napi_key: sk-test-secret\nplain text"

    sanitized = review_swarm.sanitize_text(text)

    assert "supersecretvalue" not in sanitized
    assert "sk-test-secret" not in sanitized
    assert "password=[REDACTED]" in sanitized
    assert "api_key=[REDACTED]" in sanitized


def test_review_swarm_fix_plan_is_separate_from_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("exec(user_input)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="claude", agents=1, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])
    planned = review_swarm.create_fix_plan(finished["id"])

    assert planned["fixPlanPath"]
    assert Path(planned["fixPlanPath"]).exists()
    plan = Path(planned["fixPlanPath"]).read_text(encoding="utf-8")
    assert "No fixes have been applied." in plan
    assert "Phase 1 - Stop Shipping Risks" in plan


def test_review_swarm_self_review_requires_explicit_allow_and_creates_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "cladex"
    project.mkdir()
    (project / "backend.py").write_text("# TODO: review self\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(review_swarm, "BACKUP_DATA_ROOT", tmp_path / "backups")
    monkeypatch.setattr(review_swarm, "workspace_protection_violation", lambda workspace, **_kwargs: "protected")

    with pytest.raises(ValueError, match="self-review"):
        review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)

    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=1,
        preflight_only=True,
        allow_self_review=True,
        launch=False,
    )

    assert job["selfReview"] is True
    assert job["sourceBackup"]["id"].startswith("backup-")
    assert Path(job["sourceBackup"]["snapshot"]).exists()


def test_backup_restore_requires_confirmation_and_preserves_skip_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('good')\n", encoding="utf-8")
    src = project / "src"
    src.mkdir()
    (src / "kept.py").write_text("print('kept')\n", encoding="utf-8")
    node_modules = project / "node_modules"
    node_modules.mkdir()
    (node_modules / "cache.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "BACKUP_DATA_ROOT", tmp_path / "backups")

    backup = review_swarm.create_source_backup(project, reason="test")
    (project / "app.py").write_text("print('broken')\n", encoding="utf-8")
    (project / "extra.py").write_text("remove me\n", encoding="utf-8")
    (src / "extra.py").write_text("remove me too\n", encoding="utf-8")
    (project / ".env").write_text("LOCAL_SECRET=keep\n", encoding="utf-8")

    with pytest.raises(ValueError, match="confirm"):
        review_swarm.restore_backup(backup["id"], confirm="")

    restored = review_swarm.restore_backup(backup["id"], confirm=backup["id"])

    assert restored["preRestoreBackupId"].startswith("backup-")
    assert (project / "app.py").read_text(encoding="utf-8") == "print('good')\n"
    assert not (project / "extra.py").exists()
    assert not (src / "extra.py").exists()
    assert (src / "kept.py").read_text(encoding="utf-8") == "print('kept')\n"
    assert (project / ".env").read_text(encoding="utf-8") == "LOCAL_SECRET=keep\n"
    assert (node_modules / "cache.txt").read_text(encoding="utf-8") == "keep\n"


def test_cancel_review_marks_queued_job_cancelled_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=2, preflight_only=True, launch=False)
    cancelled = review_swarm.cancel_review(job["id"])

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelRequested"] is True
    assert cancelled.get("error")


def test_cancel_review_during_run_stops_subsequent_lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    for index in range(8):
        (project / f"file_{index}.py").write_text(f"print({index})\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=4, preflight_only=True, launch=False)
    review_swarm.cancel_review(job["id"])
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "cancelled"
    assert all(agent["status"] == "cancelled" for agent in finished["agents"])
    assert finished["progress"]["cancelled"] == 4


def test_completed_review_cancel_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])
    assert finished["status"] == "completed"

    after = review_swarm.cancel_review(job["id"])
    assert after["status"] == "completed"


def test_show_review_includes_severity_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("eval(user_input)\n# TODO: revisit\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    counts = finished.get("severityCounts")
    assert counts is not None
    assert counts["high"] >= 1
    assert counts["low"] >= 1


def test_review_artifact_ignore_skips_local_credential_files_and_dirs(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "src").mkdir()
    (project / ".npmrc").write_text("", encoding="utf-8")
    (project / ".pypirc").write_text("", encoding="utf-8")
    (project / ".netrc").write_text("", encoding="utf-8")
    (project / ".git-credentials").write_text("", encoding="utf-8")
    (project / ".ssh").mkdir()
    (project / ".aws").mkdir()
    (project / "app.py").write_text("ok", encoding="utf-8")

    names = sorted(child.name for child in project.iterdir())
    ignored = review_swarm._review_artifact_ignore(str(project), names)

    assert ".npmrc" in ignored
    assert ".pypirc" in ignored
    assert ".netrc" in ignored
    assert ".git-credentials" in ignored
    assert ".ssh" in ignored
    assert ".aws" in ignored
    assert "src" not in ignored
    assert "app.py" not in ignored


def test_ai_reviewer_failure_marks_agent_failed_not_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    def failing_ai(_job: dict, _agent: dict, _files):
        return review_swarm.AIRunResult(text="", ok=False, error="AI reviewer binary not found: codex")

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", failing_ai)

    job = review_swarm.start_review(project, provider="codex", agents=2, preflight_only=False, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "failed"
    assert all(agent["status"] == "failed" for agent in finished["agents"])
    assert all("AI reviewer binary not found" in agent["detail"] for agent in finished["agents"])


def test_minimal_reviewer_env_strips_inherited_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    monkeypatch.setenv("CLADEX_REMOTE_ACCESS_TOKEN", "should-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = review_swarm._minimal_reviewer_env(account_home={"CODEX_HOME": "/tmp/account"})

    assert "DISCORD_BOT_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "CLADEX_REMOTE_ACCESS_TOKEN" not in env
    assert env.get("PATH") == "/usr/bin"
    assert env.get("CODEX_HOME") == "/tmp/account"
    assert env.get("CLADEX_REVIEW_LANE") == "1"


def test_review_and_backup_ids_reject_path_traversal() -> None:
    with pytest.raises(ValueError, match="invalid review id"):
        review_swarm.load_job("..\\outside")
    with pytest.raises(ValueError, match="invalid backup id"):
        review_swarm.load_backup("../outside")


def test_ai_review_defaults_to_bounded_parallelism(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    for index in range(12):
        (project / f"file_{index}.py").write_text(f"print({index})\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.delenv("CLADEX_REVIEW_MAX_PARALLEL", raising=False)

    lock = threading.Lock()
    running = 0
    max_running = 0

    def fake_ai_review(_job: dict, _agent: dict, _files: list[Path]) -> review_swarm.AIRunResult:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.02)
        with lock:
            running -= 1
        return review_swarm.AIRunResult(text='{"summary":"ok","findings":[]}', ok=True)

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", fake_ai_review)

    job = review_swarm.start_review(project, provider="codex", agents=12, preflight_only=False, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "completed"
    assert max_running <= review_swarm.DEFAULT_AI_MAX_PARALLEL
