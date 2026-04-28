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

    def fake_ai_review(_job: dict, _agent: dict, _files: list[Path]) -> str:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.02)
        with lock:
            running -= 1
        return '{"summary":"ok","findings":[]}'

    monkeypatch.setattr(review_swarm, "_run_codex_ai_review", fake_ai_review)

    job = review_swarm.start_review(project, provider="codex", agents=12, preflight_only=False, launch=False)
    finished = review_swarm.run_review_job(job["id"])

    assert finished["status"] == "completed"
    assert max_running <= review_swarm.DEFAULT_AI_MAX_PARALLEL
