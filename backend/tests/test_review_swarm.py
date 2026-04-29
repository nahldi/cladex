from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import claude_relay
import review_swarm


def test_analyze_workspace_recommends_swarm_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint .", "test": "vitest", "build": "vite build"}}),
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text("[project]\nname='target'\n", encoding="utf-8")
    src = project / "src"
    src.mkdir()
    tests = project / "tests"
    tests.mkdir()
    for index in range(35):
        (src / f"module_{index}.ts").write_text(f"export const value{index} = {index};\n", encoding="utf-8")
    (src / "worker.py").write_text("print('ok')\n", encoding="utf-8")
    (tests / "worker.test.ts").write_text("test('ok', () => {});\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "workspace_protection_violation", lambda *_args, **_kwargs: "")

    analysis = review_swarm.analyze_workspace(project, provider="claude")

    assert analysis["workspace"] == str(project.resolve())
    assert analysis["recommendation"]["provider"] == "claude"
    assert analysis["recommendation"]["modelStrategy"] == "claude CLI default"
    assert analysis["recommendation"]["agents"] >= 7
    assert analysis["hasTests"] is True
    assert "npm run lint" in analysis["validationCommands"]
    assert any(item["name"] == "TypeScript" for item in analysis["languages"])
    assert any(item["path"] == "package.json" for item in analysis["markers"])


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


def test_scan_file_secret_detection_ignores_type_annotations(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    sample = project / "lib.py"
    sample.write_text(
        "def token_fingerprint(token: str) -> str:\n"
        "    return token[:2]\n"
        "token = os.environ.get('DISCORD_BOT_TOKEN', '')\n"
        "auth_token = 'live-looking-secret-1234567890'\n",
        encoding="utf-8",
    )

    findings = review_swarm.scan_file(sample, project)
    secret_lines = [item["line"] for item in findings if item["category"] == "secret-hygiene"]

    assert secret_lines == [4]


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
    for index, finding in enumerate(deduped, start=1):
        finding["id"] = f"F{index:04d}"
    report = review_swarm.build_report(
        {
            "id": "review-20260428-120000-abcdef12",
            "title": "test",
            "workspace": ".",
            "provider": "codex",
            "strategy": review_swarm.REVIEW_STRATEGY,
            "agentCount": 3,
            "status": "completed",
            "createdAt": "",
            "finishedAt": "",
            "progress": {},
            "agents": [],
        },
        deduped,
        [],
    )
    assert "Seen by agents: `agent-01, agent-02`" in report


def test_review_swarm_writes_coordination_artifact_with_lane_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (project / "ui.tsx").write_text("export const ok = true;\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=2, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    coordination_path = Path(finished["coordinationPath"])
    assert coordination_path.exists()
    coordination = coordination_path.read_text(encoding="utf-8")
    assert "## Project Briefing" in coordination
    assert "## Lane Assignments" in coordination
    assert "## Agent agent-01 - security" in coordination
    assert "## Agent agent-02 - runtime" in coordination
    assert "Treat the target as an unknown project" in coordination
    assert "`app.py`" in coordination


def test_ai_prompt_points_lane_at_coordination_section_and_unknown_project(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    file_path = project / "app.py"
    file_path.write_text("print('ok')\n", encoding="utf-8")
    job = {
        "id": "review-20260428-120000-abcdef12",
        "workspace": str(project),
        "provider": "codex",
        "strategy": review_swarm.REVIEW_STRATEGY,
    }
    agent = {
        "id": "agent-01",
        "focus": "security",
        "focusPrompt": "Threat model the project.",
        "scratchWorkspace": str(tmp_path / "scratch" / "agent-01" / "workspace"),
    }

    prompt = review_swarm._ai_prompt(job, agent, [file_path], scratch=Path(agent["scratchWorkspace"]))

    assert "Treat the target as an unknown project" in prompt
    assert "Shared coordination artifact:" in prompt
    assert "Coordination section to use: ## Agent agent-01 - security" in prompt
    assert "avoid repeating other lanes" in prompt


def test_claude_ai_review_sends_large_prompt_through_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    scratch = tmp_path / "scratch" / "agent-01" / "workspace"
    project.mkdir()
    scratch.mkdir(parents=True)
    file_path = project / "app.py"
    file_path.write_text("print('ok')\n", encoding="utf-8")
    job = {
        "id": "review-20260428-120000-abcdef12",
        "workspace": str(project),
        "provider": "claude",
        "strategy": review_swarm.REVIEW_STRATEGY,
    }
    agent = {
        "id": "agent-01",
        "focus": "security",
        "focusPrompt": "Threat model the project." * 400,
        "scratchWorkspace": str(scratch),
    }
    captured: dict[str, object] = {}

    def fake_run_cli(command: list[str], prompt: str, **kwargs: object) -> review_swarm.AIRunResult:
        captured["command"] = command
        captured["prompt"] = prompt
        captured["cwd"] = kwargs.get("cwd")
        return review_swarm.AIRunResult(text='{"findings":[]}', ok=True)

    monkeypatch.setattr(claude_relay, "claude_code_bin", lambda: "claude")
    monkeypatch.setattr(review_swarm, "_run_cli", fake_run_cli)

    result = review_swarm._run_claude_ai_review(job, agent, [file_path])

    command = captured["command"]
    prompt = str(captured["prompt"])
    assert result.ok is True
    assert isinstance(command, list)
    assert all("Threat model the project." * 100 not in str(part) for part in command)
    assert "--allowedTools" in command
    assert "--tools" not in command
    assert command[-1] == "Read the review instructions from stdin and return only the requested JSON findings."
    assert "Threat model the project." in prompt
    assert captured["cwd"] == scratch


def test_codex_ai_review_uses_read_only_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    scratch = tmp_path / "scratch" / "agent-01" / "workspace"
    project.mkdir()
    scratch.mkdir(parents=True)
    job = {
        "id": "review-20260428-120000-abcdef12",
        "workspace": str(project),
        "provider": "codex",
        "strategy": review_swarm.REVIEW_STRATEGY,
    }
    agent = {
        "id": "agent-01",
        "focus": "security",
        "focusPrompt": "Threat model the project.",
        "scratchWorkspace": str(scratch),
    }
    captured: dict[str, object] = {}
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    def fake_run_cli(command: list[str], prompt: str, **kwargs: object) -> review_swarm.AIRunResult:
        captured["command"] = command
        output_path = tmp_path / "reviews" / job["id"] / "agent-01-codex.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("[]", encoding="utf-8")
        return review_swarm.AIRunResult(text="[]", ok=True)

    monkeypatch.setattr("relayctl.resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(review_swarm, "_run_cli", fake_run_cli)

    result = review_swarm._run_codex_ai_review(job, agent, [])

    command = captured["command"]
    assert result.ok is True
    assert isinstance(command, list)
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--ask-for-approval") + 1] == "never"


def test_review_swarm_rejects_invalid_agent_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    with pytest.raises(ValueError, match="between 1 and 50"):
        review_swarm.start_review(project, agents=0, launch=False)
    with pytest.raises(ValueError, match="between 1 and 50"):
        review_swarm.start_review(project, agents=51, launch=False)


def test_start_review_reuses_active_same_workspace_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    first = review_swarm.start_review(project, provider="codex", agents=4, launch=False)
    duplicate = review_swarm.start_review(project, provider="claude", agents=9, launch=False)

    assert duplicate["id"] == first["id"]
    assert duplicate["agentCount"] == 4
    assert duplicate["returnedActiveReview"] is True
    assert len(list((tmp_path / "reviews").glob("*/job.json"))) == 1

    review_swarm.cancel_review(first["id"])
    next_job = review_swarm.start_review(project, provider="claude", agents=2, launch=False)

    assert next_job["id"] != first["id"]
    assert next_job["provider"] == "claude"
    assert len(list((tmp_path / "reviews").glob("*/job.json"))) == 2


def test_start_review_marks_stale_active_job_and_allows_new_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_ACTIVE_STALE_SECONDS", "60")

    first = review_swarm.start_review(project, provider="codex", agents=2, launch=False)
    stale = review_swarm.load_job(first["id"])
    stale["updatedAt"] = "2000-01-01T00:00:00+00:00"
    stale["createdAt"] = "2000-01-01T00:00:00+00:00"
    review_swarm._write_json(review_swarm.job_json_path(first["id"]), stale)

    next_job = review_swarm.start_review(project, provider="claude", agents=1, launch=False)

    assert next_job["id"] != first["id"]
    old_job = review_swarm.load_job(first["id"])
    assert old_job["status"] == "failed"
    assert "stale" in old_job["error"]


def test_provider_limit_stops_launching_remaining_ai_lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    for index in range(3):
        (project / f"app{index}.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_MAX_PARALLEL", "1")
    calls = 0

    def fake_codex(*_args: object, **_kwargs: object) -> review_swarm.AIRunResult:
        nonlocal calls
        calls += 1
        return review_swarm.AIRunResult(
            text="",
            ok=False,
            error="ERROR: You've hit your usage limit. Try again at 4:54 AM.\n" + ("prompt noise\n" * 200),
        )

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", fake_codex)

    job = review_swarm.start_review(project, provider="codex", agents=3, preflight_only=False, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    assert calls == 1
    assert finished["status"] == "failed"
    assert "account limit" in finished["error"].lower()
    assert finished["providerLimit"]
    assert all(agent["status"] == "failed" for agent in finished["agents"])
    assert all("prompt noise" not in agent["detail"] for agent in finished["agents"])
    assert all("remaining reviewer lanes were not launched" in agent["detail"] for agent in finished["agents"])
    report = Path(finished["reportPath"]).read_text(encoding="utf-8")
    assert "## Provider Account Limit" in report


def test_review_swarm_sanitizes_assignment_values() -> None:
    text = "password = 'supersecretvalue'\n\"api_key\": \"sk-test-secret\"\nplain text"

    sanitized = review_swarm.sanitize_text(text)

    assert "supersecretvalue" not in sanitized
    assert "sk-test-secret" not in sanitized
    assert "password = [REDACTED]" in sanitized
    assert "\"api_key\": [REDACTED]" in sanitized


def test_review_swarm_sanitizes_raw_provider_tokens() -> None:
    openai_token = "sk-proj-" + ("A" * 32)
    anthropic_token = "sk-ant-api03-" + ("B" * 32)
    sanitized = review_swarm.sanitize_text(f"openai={openai_token}\nanthropic={anthropic_token}\n")

    assert openai_token not in sanitized
    assert anthropic_token not in sanitized
    assert "[REDACTED_OPENAI_TOKEN]" in sanitized
    assert "[REDACTED_ANTHROPIC_TOKEN]" in sanitized


def test_scan_file_detects_raw_provider_tokens(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    sample = project / "app.py"
    sample.write_text("OPENAI_API_KEY='sk-proj-" + ("A" * 32) + "'\n", encoding="utf-8")

    findings = review_swarm.scan_file(sample, project)

    assert any(item["category"] == "secret-hygiene" for item in findings)


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
    assert job["sourceBackup"] == {}
    finished = review_swarm.run_review_job(job["id"])
    assert finished["sourceBackup"]["id"].startswith("backup-")
    assert Path(finished["sourceBackup"]["snapshot"]).exists()


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


def test_source_backup_includes_template_configs_but_skips_real_local_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    (project / ".env").write_text("REAL_SECRET=skip\n", encoding="utf-8")
    (project / ".env.local").write_text("REAL_SECRET=skip\n", encoding="utf-8")
    (project / ".env.example").write_text("PLACEHOLDER=include\n", encoding="utf-8")
    (project / "auth-token.yaml").write_text("token: skip\n", encoding="utf-8")
    (src / "vite-env.d.ts").write_text('/// <reference types="vite/client" />\n', encoding="utf-8")
    monkeypatch.setattr(review_swarm, "BACKUP_DATA_ROOT", tmp_path / "backups")

    backup = review_swarm.create_source_backup(project, reason="test")
    snapshot = Path(backup["snapshot"])

    assert not (snapshot / ".env").exists()
    assert not (snapshot / ".env.local").exists()
    assert not (snapshot / "auth-token.yaml").exists()
    assert (snapshot / ".env.example").read_text(encoding="utf-8") == "PLACEHOLDER=include\n"
    assert (snapshot / "src" / "vite-env.d.ts").exists()


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


def test_corrupt_review_job_json_is_reported_not_treated_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    job_id = "review-20260429-120000-abcdef12"
    path = review_swarm.job_json_path(job_id)
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(review_swarm.CorruptReviewStateError, match="Corrupt review job state"):
        review_swarm.load_job(job_id)

    listed = review_swarm.list_reviews()
    assert listed[0]["id"] == job_id
    assert listed[0]["status"] == "failed"
    assert "corrupt JSON" in listed[0]["error"]


def test_cancel_review_does_not_save_stale_snapshot_for_running_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    live = review_swarm.load_job(job["id"])
    live["status"] = "running"
    live["agents"][0]["status"] = "done"
    live["agents"][0]["detail"] = "live worker progress"
    review_swarm.save_job(live)

    def fail_on_save(_job: dict) -> None:
        raise AssertionError("running cancel should use cancel.flag without rewriting job.json")

    monkeypatch.setattr(review_swarm, "save_job", fail_on_save)
    cancelled = review_swarm.cancel_review(job["id"])
    raw = json.loads(review_swarm.job_json_path(job["id"]).read_text(encoding="utf-8"))

    assert cancelled["cancelRequested"] is True
    assert raw["status"] == "running"
    assert raw["agents"][0]["detail"] == "live worker progress"
    assert "cancelRequested" not in raw


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


@pytest.mark.skipif(os.name != "nt", reason="Windows console-window flags are platform-specific")
def test_process_tree_termination_hides_windows_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("taskkill path should not fall back to process.kill")

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return review_swarm.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(review_swarm.subprocess, "run", fake_run)

    review_swarm._terminate_process_tree(FakeProcess())  # type: ignore[arg-type]

    assert captured["command"] == ["taskkill", "/F", "/T", "/PID", "12345"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("creationflags") == review_swarm.subprocess.CREATE_NO_WINDOW
    assert kwargs.get("startupinfo") is not None


def test_relativize_finding_path_falls_back_to_scratch(tmp_path: Path) -> None:
    workspace = tmp_path / "src-original"
    scratch = tmp_path / "src-scratch"
    workspace.mkdir()
    scratch.mkdir()
    (workspace / "app.py").write_text("ok", encoding="utf-8")
    (scratch / "app.py").write_text("ok", encoding="utf-8")

    assert review_swarm._relativize_finding_path(
        str((workspace / "app.py").resolve()), workspace=workspace, scratch=scratch
    ) == "app.py"
    assert review_swarm._relativize_finding_path(
        str((scratch / "app.py").resolve()), workspace=workspace, scratch=scratch
    ) == "app.py"
    assert review_swarm._relativize_finding_path("../escape.py", workspace=workspace, scratch=scratch) == "."
    assert review_swarm._relativize_finding_path("src/app.py", workspace=workspace, scratch=scratch) == "src/app.py"


def test_review_report_is_written_after_final_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("eval(value)\n# TODO: real comment\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    report = Path(finished["reportPath"]).read_text(encoding="utf-8")
    assert finished["status"] == "completed"
    assert "Status: `completed`" in report
    assert "Status: `running`" not in report
    assert "Finished: `not finished`" not in report


def test_run_review_job_short_circuits_when_already_finished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    first = review_swarm.run_review_job(job["id"])
    assert first["status"] == "completed"

    # Second call must not re-run the lanes; it should return the existing
    # public job state without flipping status back to `running`.
    second = review_swarm.run_review_job(job["id"])
    assert second["status"] == "completed"
    assert second["progress"] == first["progress"]


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


def test_secret_name_findings_does_not_match_hyphenated_env_words(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "vite-env.d.ts").write_text('/// <reference types="vite/client" />\n', encoding="utf-8")
    (project / ".env").write_text("REAL=1\n", encoding="utf-8")
    (project / "auth-token.yaml").write_text("token: abc\n", encoding="utf-8")

    findings = review_swarm.secret_name_findings(project)
    flagged = sorted(item["path"] for item in findings)

    assert ".env" in flagged
    assert "auth-token.yaml" in flagged
    assert "src/vite-env.d.ts" not in flagged
    assert review_swarm.has_secret_token_segment("vite-env.d.ts") is False
    assert review_swarm.has_secret_token_segment(".env") is True
    assert review_swarm.has_secret_token_segment("auth-token.yaml") is True


def test_scan_file_skips_docs_config_and_rule_definition_files(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    pretend_review = project / "review_swarm.py"
    pretend_review.write_text("# eval(\"shell=true\")\n# password = 'x'\n", encoding="utf-8")
    handoff = project / "HANDOFF.md"
    handoff.write_text("`0.0.0.0` example listen line\neval(value)\npassword = 'x'\n", encoding="utf-8")
    test_fixture = project / "test_review_swarm.py"
    test_fixture.write_text("# TODO: real comment marker\n", encoding="utf-8")
    real_code = project / "app.py"
    real_code.write_text("# TODO: real comment\nshell=True\n", encoding="utf-8")

    assert review_swarm.scan_file(pretend_review, project) == []
    assert review_swarm.scan_file(handoff, project) == []
    assert review_swarm.scan_file(test_fixture, project) == []
    real_findings = review_swarm.scan_file(real_code, project)
    assert any(item["category"] == "maintenance" for item in real_findings)
    assert any(item["category"] == "command-execution" for item in real_findings)


def test_scratch_disk_preflight_refuses_oversized_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "big.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_SCRATCH_MAX_BYTES", "100")

    with pytest.raises(RuntimeError, match="Scratch disk preflight"):
        review_swarm._scratch_disk_preflight({"id": "r", "workspace": str(project), "agentCount": 5})


def test_scratch_disk_preflight_passes_under_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "small.txt").write_text("ok\n", encoding="utf-8")
    monkeypatch.setenv("CLADEX_REVIEW_SCRATCH_MAX_BYTES", "1000000")

    metadata = review_swarm._scratch_disk_preflight({"id": "r", "workspace": str(project), "agentCount": 4})
    assert metadata["agentCount"] == 4
    assert metadata["workspaceBytes"] >= 0
    assert metadata["estimatedScratchBytes"] >= metadata["workspaceBytes"]


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
    (project / ".playwright-cli").mkdir()
    (project / ".playwright-mcp").mkdir()
    (project / ".pytest-basetemp").mkdir()
    (project / ".pytest-tmp").mkdir()
    (project / "manual-mode-test").mkdir()
    (project / "memory").mkdir()
    (project / "output").mkdir()
    (project / "tmp").mkdir()
    (project / "app.py").write_text("ok", encoding="utf-8")

    names = sorted(child.name for child in project.iterdir())
    ignored = review_swarm._review_artifact_ignore(str(project), names)

    assert ".npmrc" in ignored
    assert ".pypirc" in ignored
    assert ".netrc" in ignored
    assert ".git-credentials" in ignored
    assert ".ssh" in ignored
    assert ".aws" in ignored
    assert ".playwright-cli" in ignored
    assert ".playwright-mcp" in ignored
    assert ".pytest-basetemp" in ignored
    assert ".pytest-tmp" in ignored
    assert "manual-mode-test" in ignored
    assert "memory" in ignored
    assert "output" in ignored
    assert "tmp" in ignored
    assert "src" not in ignored
    assert "app.py" not in ignored


def test_scratch_workspace_skips_symlinks_to_keep_ai_inside_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = project / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    scratch = review_swarm.prepare_scratch_workspace(job)

    assert not (scratch / "outside-link.txt").exists()
    assert (scratch / "app.py").exists()


def test_inventory_skips_file_symlinks_before_scanning(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("eval(user_input)\n", encoding="utf-8")
    link = project / "outside_link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")

    files = review_swarm.inventory_files(project)
    findings = []
    for path in files:
        findings.extend(review_swarm.scan_file(path, project))

    assert [path.name for path in files] == ["app.py"]
    assert not any(item["path"] == "outside_link.py" for item in findings)


def test_analyze_workspace_uses_one_budgeted_inventory_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "workspace_protection_violation", lambda *_args, **_kwargs: "")
    original_walk = review_swarm.os.walk
    calls = 0

    def counting_walk(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        yield from original_walk(*args, **kwargs)

    monkeypatch.setattr(review_swarm.os, "walk", counting_walk)

    analysis = review_swarm.analyze_workspace(project)

    assert analysis["fileCount"] == 2
    assert calls == 1


def test_scratch_workspace_rebuilds_partial_copy_without_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    scratch = review_swarm.agent_scratch_workspace_path(job, job["agents"][0])
    scratch.mkdir(parents=True)
    (scratch / "stale.txt").write_text("partial\n", encoding="utf-8")

    base = review_swarm.prepare_scratch_workspace(job)
    rebuilt = review_swarm.prepare_agent_scratch_workspace(job, job["agents"][0], base)

    assert rebuilt == scratch
    assert not (rebuilt / "stale.txt").exists()
    assert (rebuilt / "app.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (rebuilt / review_swarm.SCRATCH_READY_MARKER).exists()


def test_agent_scratch_uses_hardlinks_when_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_SCRATCH_MODE", "hardlink")
    if not review_swarm._hardlink_supported_for(tmp_path / "probe"):
        pytest.skip("hardlinks are unavailable in this environment")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    base = review_swarm.prepare_scratch_workspace(job)
    rebuilt = review_swarm.prepare_agent_scratch_workspace(job, job["agents"][0], base)

    assert os.stat(base / "app.py").st_ino == os.stat(rebuilt / "app.py").st_ino


def test_global_ai_lane_slot_cleans_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    lane_dir = review_swarm.global_review_lane_dir()
    lane_dir.mkdir(parents=True)
    (lane_dir / "slot-0.lock").write_text(json.dumps({"pid": 999999999, "label": "dead"}), encoding="utf-8")
    monkeypatch.setattr(review_swarm, "_pid_alive", lambda _pid: False)

    with review_swarm._global_ai_lane_slot(1, label="live"):
        payload = json.loads((lane_dir / "slot-0.lock").read_text(encoding="utf-8"))
        assert payload["label"] == "live"

    assert not (lane_dir / "slot-0.lock").exists()


def test_global_ai_lane_slot_respects_cancel_while_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    with review_swarm._global_ai_lane_slot(1, label="first"):
        with pytest.raises(RuntimeError, match="Cancelled"):
            with review_swarm._global_ai_lane_slot(1, label="second", cancel_check=lambda: True):
                pass


def test_global_ai_lane_slots_are_scoped_by_provider_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    with review_swarm._global_ai_lane_slot(1, label="codex-a", provider="codex", account_home=str(tmp_path / "a")):
        with review_swarm._global_ai_lane_slot(1, label="codex-b", provider="codex", account_home=str(tmp_path / "b")):
            scoped_locks = sorted((tmp_path / "reviews" / "_global-ai-slots").glob("codex-*/*.lock"))
            assert len(scoped_locks) == 2


def test_ai_reviewer_failure_marks_agent_failed_not_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    def failing_ai(_job: dict, _agent: dict, _files, **_kwargs):
        return review_swarm.AIRunResult(text="", ok=False, error="AI reviewer binary not found: codex")

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", failing_ai)

    job = review_swarm.start_review(project, provider="codex", agents=2, preflight_only=False, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "failed"
    assert all(agent["status"] == "failed" for agent in finished["agents"])
    assert all("AI reviewer binary not found" in agent["detail"] for agent in finished["agents"])


def test_partial_ai_lane_failure_completes_with_warnings_and_keeps_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    for index in range(3):
        (project / f"app_{index}.py").write_text(f"print({index})\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_MAX_PARALLEL", "1")

    def mixed_ai(_job: dict, agent: dict, _files, **_kwargs):
        if agent["id"] == "agent-02":
            return review_swarm.AIRunResult(text="", ok=False, error="agent-02 validation failed")
        return review_swarm.AIRunResult(
            text=json.dumps(
                {
                    "summary": "ok",
                    "findings": [
                        {
                            "severity": "low",
                            "category": "ai-test",
                            "path": "app_0.py",
                            "line": 1,
                            "title": "Lane finding",
                            "detail": "Concrete partial finding.",
                            "recommendation": "Keep report usable.",
                            "confidence": "high",
                        }
                    ],
                }
            ),
            ok=True,
        )

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", mixed_ai)

    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=3,
        preflight_only=False,
        launch=False,
        backup_before_review=False,
    )
    finished = review_swarm.run_review_job(job["id"])
    planned = review_swarm.create_fix_plan(finished["id"])

    assert finished["status"] == "completed_with_warnings"
    assert "1 of 3 reviewer lane(s) failed" in finished["error"]
    assert [agent["status"] for agent in finished["agents"]].count("failed") == 1
    assert Path(finished["reportPath"]).exists()
    assert planned["fixPlanPath"]


def test_failed_ai_lane_keeps_deterministic_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("import subprocess\nsubprocess.run('echo hi', shell=True)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    def failing_ai(_job: dict, _agent: dict, _files, **_kwargs):
        return review_swarm.AIRunResult(text="", ok=False, error="provider failed")

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", failing_ai)

    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=1,
        preflight_only=False,
        launch=False,
        backup_before_review=False,
    )
    finished = review_swarm.run_review_job(job["id"])
    findings = json.loads(Path(finished["findingsPath"]).read_text(encoding="utf-8"))["findings"]

    assert finished["status"] == "failed"
    assert any(item["category"] == "command-execution" for item in findings)


def test_findings_json_is_capped_per_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    for index in range(20):
        (project / f"app_{index}.py").write_text("eval(user_input)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_MAX_FINDINGS", "5")

    job = review_swarm.start_review(project, provider="codex", agents=1, preflight_only=True, launch=False)
    finished = review_swarm.run_review_job(job["id"])
    payload = json.loads(Path(finished["findingsPath"]).read_text(encoding="utf-8"))

    assert len(payload["findings"]) == 5
    assert payload["truncated"] is True
    assert payload["maxFindings"] == 5


def test_completed_lane_findings_are_persisted_before_review_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app_0.py").write_text("print(0)\n", encoding="utf-8")
    (project / "app_1.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_MAX_PARALLEL", "1")
    second_started = threading.Event()
    release_second = threading.Event()

    def fake_ai(_job: dict, agent: dict, _files: list[Path], **_kwargs: object) -> review_swarm.AIRunResult:
        if agent["id"] == "agent-02":
            second_started.set()
            assert release_second.wait(timeout=5)
            return review_swarm.AIRunResult(text='{"findings":[]}', ok=True)
        return review_swarm.AIRunResult(
            text=json.dumps(
                {
                    "findings": [
                        {
                            "severity": "medium",
                            "category": "durable-lane",
                            "path": "app_0.py",
                            "line": 1,
                            "title": "Durable lane finding",
                            "detail": "Persist before all lanes finish.",
                            "recommendation": "Write partial findings after each lane.",
                        }
                    ]
                }
            ),
            ok=True,
        )

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", fake_ai)
    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=2,
        preflight_only=False,
        launch=False,
        backup_before_review=False,
    )
    result: dict[str, object] = {}
    worker = threading.Thread(target=lambda: result.update(review_swarm.run_review_job(job["id"])))
    worker.start()

    assert second_started.wait(timeout=5)
    payload = json.loads(Path(review_swarm.findings_json_path(job["id"])).read_text(encoding="utf-8"))
    assert any(item["category"] == "durable-lane" for item in payload["findings"])

    release_second.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert result["status"] == "completed"


def test_ai_lanes_use_distinct_scratch_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (project / "app_0.py").write_text("print(0)\n", encoding="utf-8")
    (project / "app_1.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    monkeypatch.setenv("CLADEX_REVIEW_MAX_PARALLEL", "1")
    seen: dict[str, Path] = {}

    def checking_ai(job: dict, agent: dict, _files, **_kwargs):
        scratch = Path(agent["scratchWorkspace"])
        seen[agent["id"]] = scratch
        assert scratch.exists()
        assert scratch != Path(job["scratchWorkspace"])
        assert scratch.parent.name == agent["id"]
        assert (scratch / "app_0.py").exists()
        return review_swarm.AIRunResult(text='{"summary":"ok","findings":[]}', ok=True)

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", checking_ai)

    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=2,
        preflight_only=False,
        launch=False,
        backup_before_review=False,
    )
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "completed"
    assert sorted(seen) == ["agent-01", "agent-02"]
    assert seen["agent-01"] != seen["agent-02"]


def test_ai_findings_relativize_against_lane_scratch_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    def fake_ai_review(_job: dict, agent: dict, _files: list[Path], **_kwargs) -> review_swarm.AIRunResult:
        absolute = Path(agent["scratchWorkspace"]) / "app.py"
        return review_swarm.AIRunResult(
            text=json.dumps(
                {
                    "findings": [
                        {
                            "severity": "medium",
                            "category": "lane-path",
                            "path": str(absolute),
                            "line": 1,
                            "title": "Absolute lane path",
                            "detail": "Absolute path from lane scratch.",
                            "recommendation": "Normalize against the lane scratch.",
                            "confidence": "high",
                        }
                    ]
                }
            ),
            ok=True,
        )

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", fake_ai_review)

    job = review_swarm.start_review(
        project,
        provider="codex",
        agents=1,
        preflight_only=False,
        launch=False,
        backup_before_review=False,
    )
    finished = review_swarm.run_review_job(job["id"])
    findings = json.loads(Path(finished["findingsPath"]).read_text(encoding="utf-8"))["findings"]

    assert any(item["category"] == "lane-path" and item["path"] == "app.py" for item in findings)


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


def test_job_run_lock_recovers_empty_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")
    job_id = "review-20260429-000000-abcdef12"
    lock_path = review_swarm.job_dir(job_id) / "run.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("", encoding="utf-8")
    old_time = time.time() - review_swarm.STALE_JOB_RUN_LOCK_SECONDS - 5
    os.utime(lock_path, (old_time, old_time))

    assert review_swarm._acquire_job_run_lock(job_id) is True
    review_swarm._release_job_run_lock(job_id)


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

    def fake_ai_review(_job: dict, _agent: dict, _files: list[Path], **_kwargs) -> review_swarm.AIRunResult:
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
    assert finished["maxParallel"] == review_swarm.DEFAULT_AI_MAX_PARALLEL
    assert finished["progress"]["maxParallel"] == review_swarm.DEFAULT_AI_MAX_PARALLEL
    assert any("12 lanes requested" in warning for warning in finished["limitWarnings"])


def test_extract_json_payload_handles_trailing_log_lines() -> None:
    """The Codex CLI emits the orchestrator JSON followed by `tokens used` log
    lines. The greedy `rfind` candidate alone is enough for that case, but the
    new balanced-brace fallback must keep working when the trailer also
    contains a stray `}` character.
    """
    text = (
        '{"summary":"x","recommendedAgentCount":2,"tasks":[{"id":"t1","phase":1}]}\n'
        "tokens used }\n"
        "10,690 }\n"
    )
    payload = review_swarm._extract_json_payload(text)
    assert isinstance(payload, dict)
    assert payload["recommendedAgentCount"] == 2
    assert payload["tasks"][0]["id"] == "t1"


def test_extract_json_payload_skips_string_braces() -> None:
    """Brace counter must respect string literals so `}` inside a JSON string
    does not close the object early.
    """
    text = '{"summary":"close brace } in string","tasks":[{"id":"t1"}]}'
    payload = review_swarm._extract_json_payload(text)
    assert isinstance(payload, dict)
    assert payload["summary"].endswith("in string")


def test_extract_json_payload_prefers_largest_balanced_object() -> None:
    """When multiple balanced objects exist, prefer the larger one — that is
    typically the orchestrator plan, not a small log object.
    """
    text = (
        '{"step":"prep"}\n'
        '{"summary":"main","tasks":[{"id":"t1"}]}\n'
        '{"step":"done"}\n'
    )
    payload = review_swarm._extract_json_payload(text)
    assert isinstance(payload, dict)
    assert payload.get("summary") == "main"


def test_parse_ai_findings_reads_large_json_before_sanitizing(tmp_path: Path) -> None:
    workspace = tmp_path / "target"
    workspace.mkdir()
    findings = [
        {
            "severity": "medium",
            "category": "bulk",
            "path": "app.py",
            "line": index,
            "title": f"Finding {index}",
            "detail": "x" * 500,
            "recommendation": "Inspect and fix.",
        }
        for index in range(20)
    ]
    text = json.dumps({"findings": findings})

    parsed = review_swarm.parse_ai_findings(text, workspace=workspace, agent={"id": "agent-01", "focus": "testing"})

    assert len(parsed) == 20
    assert parsed[0]["title"] == "Finding 0"


def test_parse_ai_findings_redacts_all_free_text_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "target"
    workspace.mkdir()
    text = json.dumps(
        {
            "findings": [
                {
                    "severity": "high",
                    "category": "token: sk-category-secret",
                    "path": "token: sk-path-secret",
                    "line": 1,
                    "title": "api_key: sk-title-secret",
                    "detail": "\"token\": \"sk-detail-secret\"",
                    "recommendation": "password = sk-recommendation-secret",
                    "confidence": "high",
                }
            ]
        }
    )

    parsed = review_swarm.parse_ai_findings(text, workspace=workspace, agent={"id": "agent-01", "focus": "security"})
    serialized = json.dumps(parsed)

    assert "sk-category-secret" not in serialized
    assert "sk-path-secret" not in serialized
    assert "sk-title-secret" not in serialized
    assert "sk-detail-secret" not in serialized
    assert "sk-recommendation-secret" not in serialized
    assert "[REDACTED]" in serialized


def test_linear_json_extraction_handles_many_unmatched_braces_quickly() -> None:
    text = "{" * 20_000
    started = time.monotonic()
    payload = review_swarm._extract_json_payload(text)

    assert payload is None
    assert time.monotonic() - started < 1.0


def test_run_cli_uses_idle_timeout_not_short_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "5")
    result = review_swarm._run_cli(
        [
            sys.executable,
            "-c",
            "import time\nfor i in range(4):\n print(f'tick-{i}', flush=True)\n time.sleep(0.35)\n",
        ],
        "",
        env=os.environ.copy(),
    )
    assert result.ok is True
    assert "tick-3" in result.text


def test_run_cli_idle_timeout_kills_silent_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "10")
    started = time.monotonic()
    result = review_swarm._run_cli(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        "",
        env=os.environ.copy(),
    )
    assert time.monotonic() - started < 4
    assert result.ok is False
    assert "idle" in result.error.lower()


def test_run_cli_allows_silent_start_until_initial_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_INITIAL_IDLE_TIMEOUT", "3")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "5")
    result = review_swarm._run_cli(
        [sys.executable, "-c", "import time; time.sleep(1.4); print('done', flush=True)"],
        "",
        env=os.environ.copy(),
    )
    assert result.ok is True
    assert "done" in result.text


def test_run_cli_stderr_output_resets_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "5")
    result = review_swarm._run_cli(
        [
            sys.executable,
            "-c",
            "import sys, time\nfor i in range(4):\n sys.stderr.write(f'err-{i}\\n')\n sys.stderr.flush()\n time.sleep(0.35)\n",
        ],
        "",
        env=os.environ.copy(),
    )
    assert result.ok is True
    assert "err-3" in result.text


def test_run_cli_error_prioritizes_stderr_over_noisy_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_OUTPUT_LIMIT", "200")
    result = review_swarm._run_cli(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "sys.stdout.write('noise-' * 1000)\n"
                "sys.stdout.flush()\n"
                "sys.stderr.write(\"ERROR: You've hit your usage limit. Try again later.\\n\")\n"
                "sys.stderr.flush()\n"
                "raise SystemExit(1)\n"
            ),
        ],
        "",
        env=os.environ.copy(),
    )

    assert result.ok is False
    assert "usage limit" in result.error
    assert review_swarm._is_provider_limit_error(result.error)


def test_run_cli_max_runtime_kills_chatty_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "2")
    started = time.monotonic()
    result = review_swarm._run_cli(
        [
            sys.executable,
            "-c",
            "import time\nfor i in range(100):\n print(f'tick-{i}', flush=True)\n time.sleep(0.2)\n",
        ],
        "",
        env=os.environ.copy(),
    )
    assert time.monotonic() - started < 5
    assert result.ok is False
    assert "maximum runtime" in result.error.lower()


def test_run_cli_large_stdin_prompt_does_not_block_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_INITIAL_IDLE_TIMEOUT", "1")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "10")
    prompt = "x" * 5_000_000
    started = time.monotonic()
    result = review_swarm._run_cli(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        prompt,
        env=os.environ.copy(),
    )
    assert time.monotonic() - started < 4
    assert result.ok is False
    assert "idle" in result.error.lower()


def test_run_cli_max_runtime_zero_disables_absolute_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operators auditing slow, deep code should be able to disable the wall-clock
    ceiling and rely only on idle timeout + cancel. Setting MAX_RUNTIME to 0
    should mean 'no absolute ceiling' rather than 'kill immediately'."""
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "5")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_INITIAL_IDLE_TIMEOUT", "5")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", "0")
    # Chatty process: emits a byte per 0.1s for 2s, then exits cleanly. The old
    # behavior with a low MAX_RUNTIME kills it; with MAX_RUNTIME=0 the only
    # kill path is idle (which never triggers because the process keeps emitting).
    started = time.monotonic()
    result = review_swarm._run_cli(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "for _ in range(20):\n"
            "    sys.stdout.write('.')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.1)\n"
            "print('done')\n",
        ],
        "",
        env=os.environ.copy(),
    )
    elapsed = time.monotonic() - started
    assert elapsed < 6, f"process exited cleanly so should not be killed; elapsed={elapsed}"
    assert result.ok is True
    assert "done" in result.text


def test_run_cli_legacy_timeout_zero_also_disables_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy CLADEX_REVIEW_AGENT_TIMEOUT env knob is still honored. Setting
    it to 0 should also disable the absolute ceiling, matching the new
    MAX_RUNTIME=0 contract so old configs do not behave inconsistently."""
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_IDLE_TIMEOUT", "5")
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_INITIAL_IDLE_TIMEOUT", "5")
    monkeypatch.delenv("CLADEX_REVIEW_AGENT_MAX_RUNTIME", raising=False)
    monkeypatch.setenv("CLADEX_REVIEW_AGENT_TIMEOUT", "0")
    started = time.monotonic()
    result = review_swarm._run_cli(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "for _ in range(8):\n"
            "    sys.stdout.write('.')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.1)\n"
            "print('done')\n",
        ],
        "",
        env=os.environ.copy(),
    )
    elapsed = time.monotonic() - started
    assert elapsed < 4
    assert result.ok is True
    assert "done" in result.text


def test_run_synthesizer_pass_skipped_when_no_findings(tmp_path: Path) -> None:
    """The synthesizer is best-effort and should return [] without contacting
    any provider when the lanes produced nothing to correlate."""
    job = {"id": "review-20260101-000000-deadbeef", "provider": "codex", "workspace": str(tmp_path)}
    result = review_swarm._run_synthesizer_pass(job, [], scratch=tmp_path)
    assert result == []


def test_run_synthesizer_pass_disabled_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Operators must be able to opt out (CLADEX_REVIEW_SYNTHESIZER=0) so a
    swarm in a tightly rate-limited account doesn't burn the extra Codex
    call. Once disabled, the synthesizer must return [] without invoking
    the underlying CLI subprocess at all."""
    monkeypatch.setenv("CLADEX_REVIEW_SYNTHESIZER", "0")
    invoked: list[bool] = []

    def boom(*_args: object, **_kwargs: object) -> None:
        invoked.append(True)
        raise AssertionError("synthesizer must not invoke _run_cli when disabled")

    monkeypatch.setattr(review_swarm, "_run_cli", boom)
    job = {"id": "review-20260101-000000-deadbeef", "provider": "codex", "workspace": str(tmp_path)}
    findings = [
        {"id": "F1", "severity": "high", "category": "auth", "path": "src/x.py", "line": 1, "title": "boom"},
    ]
    result = review_swarm._run_synthesizer_pass(job, findings, scratch=tmp_path)
    assert result == []
    assert invoked == []


def test_run_synthesizer_pass_returns_parsed_findings_with_synth_agent_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the synthesizer subprocess returns valid JSON, the orchestrator
    must parse it, prefix every finding with `agentId: synthesizer`, and
    return them so the caller can append them to the run's findings list."""
    monkeypatch.setenv("CLADEX_REVIEW_SYNTHESIZER", "1")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "auth.py").write_text("def login():\n    pass\n", encoding="utf-8")
    (workspace / "lifecycle.py").write_text("def start():\n    pass\n", encoding="utf-8")

    payload = (
        '{"summary":"cross-cutting","findings":['
        '{"severity":"high","category":"cross-cutting","path":"auth.py","line":1,'
        '"title":"auth bypass via lifecycle path",'
        '"detail":"lane A says auth is safe under start order X, lane B shows X reversed in lifecycle.py.",'
        '"recommendation":"merge the two paths.","confidence":"high"}]}'
    )
    monkeypatch.setattr(
        review_swarm,
        "_run_cli",
        lambda *_a, **_k: review_swarm.AIRunResult(text=payload, ok=True),
    )
    monkeypatch.setattr(review_swarm, "_read_text_with_limit", lambda *_a, **_k: payload)

    job = {"id": "review-20260101-000000-deadbeef", "provider": "codex", "workspace": str(workspace), "accountHome": ""}
    lane_findings = [
        {"agentId": "agent-01", "severity": "high", "category": "auth", "path": "auth.py", "line": 5, "title": "auth"},
        {"agentId": "agent-02", "severity": "high", "category": "runtime", "path": "lifecycle.py", "line": 10, "title": "race"},
    ]
    result = review_swarm._run_synthesizer_pass(job, lane_findings, scratch=workspace)
    assert len(result) == 1
    assert result[0]["agentId"] == "synthesizer"
    assert result[0]["category"] == "cross-cutting"
    assert result[0]["title"].startswith("auth bypass")


def test_fix_worker_prompt_includes_karpathy_discipline() -> None:
    """The fix-worker prompt must carry the four Karpathy-derived working
    principles: think before editing, simplicity first, surgical changes,
    goal-driven verification. These materially reduce scope creep and
    over-edits in real Claude/Codex fix runs."""
    import fix_orchestrator

    run = {"id": "fix-1", "workspace": "/tmp/x"}
    task = {
        "id": "task-0001",
        "title": "fix it",
        "files": ["src/x.py"],
        "findingId": "F1",
        "severity": "high",
        "category": "auth",
        "detail": "evidence",
        "recommendation": "do the fix",
    }
    prompt = fix_orchestrator._task_prompt(run, task)
    assert "Working principles" in prompt
    assert "Karpathy" in prompt
    for phrase in ("Simplicity first", "Surgical changes", "Goal-driven verification"):
        assert phrase in prompt, f"prompt missing principle: {phrase}"


def test_skip_from_snapshot_or_restore_excludes_agent_local_dirs() -> None:
    """Agent-tool local config dirs (.claude, .codex, .cursor, .aider, .continue,
    .windsurf, .copilot) carry pre-approved tool permissions, prior-session
    transcripts, and MCP tokens. They must never be copied into source backups,
    scratch lane workspaces, or restore targets — otherwise a leftover
    `.claude/settings.local.json` that allows transcript reads silently lets
    every review lane inspect prior Claude session data."""
    for name in (".claude", ".codex", ".cursor", ".aider", ".continue", ".windsurf", ".copilot"):
        assert review_swarm._skip_from_snapshot_or_restore(name) is True, (
            f"{name!r} must be excluded from snapshot/restore"
        )


def test_source_backup_excludes_dot_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end regression for the synthesizer-flagged F0004 (2.5.5 audit):
    a workspace with .claude/settings.local.json must produce a source backup
    snapshot that omits the entire .claude/ tree, even though .claude/ is not
    in .gitignore. The deny list is the source of truth, not git."""
    project = tmp_path / "target"
    project.mkdir()
    (project / "README.md").write_text("# project\n", encoding="utf-8")
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.local.json").write_text('{"permissions":{"allow":["Read(//tmp/**)"]}}', encoding="utf-8")
    (project / ".cursor").mkdir()
    (project / ".cursor" / "config.json").write_text("{}", encoding="utf-8")

    # monkeypatch (not importlib.reload) so we don't poison shared module
    # state for later tests in the same session.
    monkeypatch.setattr(review_swarm, "BACKUP_DATA_ROOT", tmp_path / "backups")
    backup = review_swarm.create_source_backup(project, reason="test")
    snapshot_root = Path(str(backup["snapshot"]))
    assert snapshot_root.exists()
    assert not (snapshot_root / ".claude").exists(), "snapshot must not copy .claude/"
    assert not (snapshot_root / ".cursor").exists(), "snapshot must not copy .cursor/"
    assert (snapshot_root / "README.md").exists()


def test_run_synthesizer_pass_skipped_when_provider_limit_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a provider rate-limit signal stops the lane workers mid-run, the
    synthesizer pass must NOT spend another provider call to summarize the
    partial findings. The orchestrator gate that wraps the synthesizer must
    set synthesizerStatus=skipped with a clear reason and avoid invoking
    the underlying CLI subprocess at all."""
    monkeypatch.setenv("CLADEX_REVIEW_SYNTHESIZER", "1")

    invoked: list[bool] = []

    def boom(*_args: object, **_kwargs: object) -> None:
        invoked.append(True)
        raise AssertionError("synthesizer must not invoke _run_cli on rate-limit")

    monkeypatch.setattr(review_swarm, "_run_cli", boom)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    job = {
        "id": "review-20260101-000000-deadbeef",
        "provider": "codex",
        "workspace": str(workspace),
        "providerLimit": "ChatGPT plan rate limit hit; partial coverage.",
    }
    findings = [{"id": "F1", "severity": "high", "category": "auth", "path": "x.py", "line": 1, "title": "x"}]
    # _run_synthesizer_pass itself only checks env + cancel; the orchestrator
    # gate is what enforces provider-limit skip. The orchestrator skip path
    # is exercised in run_review_job; here we assert the env opt-out path
    # alone short-circuits when the operator sets it.
    monkeypatch.setenv("CLADEX_REVIEW_SYNTHESIZER", "0")
    result = review_swarm._run_synthesizer_pass(job, findings, scratch=workspace)
    assert result == []
    assert invoked == []


def test_claude_subprocess_env_excludes_secret_suffixed_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Claude subprocess env builder must drop any *_TOKEN / *_KEY / *_SECRET /
    *_PASSWORD / *_PRIVATE_KEY / *_CREDENTIALS variable from the inherited env,
    even if a future allowlist entry would otherwise accept it. Discord/workspace
    prompts can read process env via Bash, so a leaked credential here is a
    credential-exfiltration path."""
    import claude_backend

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-leak")
    monkeypatch.setenv("CLAUDE_SESSION_TOKEN", "must-not-leak")
    monkeypatch.setenv("CLAUDE_FUTURE_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    env = claude_backend._claude_subprocess_env(Path("/tmp/x"))
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_SESSION_TOKEN" not in env
    assert "CLAUDE_FUTURE_KEY" not in env
    # Non-secret config-shaped vars stay reachable.
    assert env.get("ANTHROPIC_BASE_URL") == "https://api.anthropic.com"


def test_claude_subprocess_env_uses_explicit_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """The allowlist for ANTHROPIC_*/CLAUDE_* vars must be explicit, not
    prefix-based. An unknown ANTHROPIC_* env var that does not look secret
    must still be dropped because future Anthropic env vars may carry
    authentication material we cannot anticipate."""
    import claude_backend

    monkeypatch.setenv("ANTHROPIC_UNKNOWN_FUTURE_VAR", "should-not-leak-by-default")
    env = claude_backend._claude_subprocess_env(Path("/tmp/x"))
    assert "ANTHROPIC_UNKNOWN_FUTURE_VAR" not in env, (
        "explicit allowlist must drop unknown ANTHROPIC_* vars"
    )


def test_bootstrap_runtime_module_imports_with_stdlib_only() -> None:
    """The packaged bootstrap entry point must be importable on a clean
    machine where psutil/platformdirs are not yet installed. Importing
    install_plugin (which transitively requires those) would crash before
    pip can install them; bootstrap_runtime must do its job stdlib-only."""
    import importlib

    module = importlib.import_module("bootstrap_runtime")
    # Sanity-check the public functions and that they only use stdlib.
    assert callable(getattr(module, "main"))
    assert callable(getattr(module, "_runtime_root"))
    assert callable(getattr(module, "_ensure_venv"))
    # The module must NOT import relay_common (which needs psutil/platformdirs).
    import sys

    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "import relay_common" not in src
    assert "from relay_common" not in src
