from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import claude_relay
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
    assert command[-1] == "Read the review instructions from stdin and return only the requested JSON findings."
    assert "Threat model the project." in prompt
    assert captured["cwd"] == scratch


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
