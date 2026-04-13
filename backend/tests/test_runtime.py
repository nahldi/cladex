from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pytest

from relay_runtime import DurableRuntime, TaskLeaseConflictError


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "relay@example.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Relay"], cwd=path, check=True, capture_output=True, text=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_runtime_binding_creates_worktree_and_memory_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.ensure_binding("channel-123")

    assert binding.worktree_path.exists()
    assert (binding.worktree_path / "AGENTS.md").exists()
    assert (binding.worktree_path / "memory" / "STATUS.md").exists()
    assert (state / "durable-runtime.sqlite3").exists()


def test_runtime_persists_primary_thread_mapping(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.bind_thread("channel-55", thread_id="thread-abc", backend="codex-app-server", status="active")

    resumed = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    assert resumed.active_thread_id("channel-55") == "thread-abc"


def test_runtime_persists_inbound_discord_message_dedup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")

    assert runtime.claim_inbound_discord_message("channel-77", "12345") is True
    assert runtime.claim_inbound_discord_message("channel-77", "12345") is False

    resumed = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    assert resumed.claim_inbound_discord_message("channel-77", "12345") is False


def test_runtime_persists_outbound_discord_reply_dedup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")

    assert runtime.claim_outbound_discord_reply("channel-88", "222", "done") is True
    assert runtime.claim_outbound_discord_reply("channel-88", "222", "done") is False
    assert runtime.claim_outbound_discord_reply("channel-88", "222", "done", force=True) is True

    resumed = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    assert resumed.claim_outbound_discord_reply("channel-88", "222", "done") is False


def test_runtime_rejects_overlapping_fresh_leases(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.claim_task(
        channel_key="channel-1",
        title="edit auth",
        owner_agent="codex-a",
        target_files=["src/auth/**"],
        validation=["pytest tests/auth -q"],
    )

    with pytest.raises(TaskLeaseConflictError):
        runtime.claim_task(
            channel_key="channel-2",
            title="edit login",
            owner_agent="codex-b",
            target_files=["src/**"],
            validation=["pytest tests/login -q"],
        )


def test_runtime_logs_false_external_claims_to_drift_log(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.observe_incoming_message(
        channel_key="channel-3",
        author_name="teammate",
        author_id=7,
        author_is_bot=True,
        text="Files on disk:\nMissingThing.py",
    )

    drift_text = (binding.worktree_path / "memory" / "DRIFT_LOG.md").read_text(encoding="utf-8")
    assert "MissingThing.py" in drift_text
    assert "Verdict: false" in drift_text


def test_runtime_updates_status_and_handoff_after_turn_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.observe_incoming_message(
        channel_key="channel-4",
        author_name="Finn",
        author_id=1,
        author_is_bot=False,
        text="Implement durable memory runtime.",
    )
    runtime.bind_thread("channel-4", thread_id="thread-444", backend="codex-app-server", status="active")
    runtime.record_turn_result(
        channel_key="channel-4",
        thread_id="thread-444",
        turn_id="turn-1",
        summary="Implemented runtime store.",
        files_changed=["relay_runtime.py", "tests/test_runtime.py"],
        commands_run=["pytest tests/test_runtime.py -q -> pass"],
        validations=["pytest tests/test_runtime.py -q -> pass"],
        next_step="Wire the runtime into bot.py.",
    )

    binding = runtime.ensure_binding("channel-4")
    status_text = (binding.worktree_path / "memory" / "STATUS.md").read_text(encoding="utf-8")
    handoff_text = (binding.worktree_path / "memory" / "HANDOFF.md").read_text(encoding="utf-8")
    tasks = (binding.worktree_path / "memory" / "TASKS.json").read_text(encoding="utf-8")

    assert "Implement durable memory runtime." in status_text
    assert "Wire the runtime into bot.py." in status_text
    assert "relay_runtime.py" in handoff_text
    assert "task-" in tasks


def test_runtime_new_human_message_supersedes_existing_active_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.observe_incoming_message(
        channel_key="channel-redirect",
        author_name="Finn",
        author_id=1,
        author_is_bot=False,
        text="Keep working on the tunnel task.",
    )
    runtime.observe_incoming_message(
        channel_key="channel-redirect",
        author_name="Finn",
        author_id=1,
        author_is_bot=False,
        text="Only answer yes or no.",
    )

    binding = runtime.ensure_binding("channel-redirect")
    active = runtime.store.active_task("channel-redirect")
    tasks = json.loads((binding.worktree_path / "memory" / "TASKS.json").read_text(encoding="utf-8"))

    assert active is not None
    assert active["title"] == "Only answer yes or no."
    assert tasks["tasks"][0]["title"] == "Only answer yes or no."
    assert any(task["status"] == "released" for task in tasks["tasks"])


def test_runtime_context_bundle_uses_latest_distinct_handoff_highlights(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="claude")
    runtime.bind_thread("channel-handoff", thread_id="thread-1", backend="claude-print-resume", status="active")
    runtime.record_turn_result(
        channel_key="channel-handoff",
        thread_id="thread-1",
        turn_id="turn-old",
        summary="Old stale reply.",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="Old stale reply.",
    )
    runtime.record_turn_result(
        channel_key="channel-handoff",
        thread_id="thread-1",
        turn_id="turn-new",
        summary="Yes.",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="Yes.",
    )

    binding = runtime.ensure_binding("channel-handoff")
    handoff_text = (binding.worktree_path / "memory" / "HANDOFF.md").read_text(encoding="utf-8")
    bundle = runtime.build_context_bundle("channel-handoff")

    assert "- result: Yes." in handoff_text
    assert "- exact next step: Continue from STATUS.md." in handoff_text
    assert "Old stale reply." in handoff_text
    assert "- result: Yes." in bundle
    assert "Old stale reply." not in bundle
    assert "- exact next step: Continue from STATUS.md." not in bundle


def test_runtime_records_compaction_and_preserves_continuity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.observe_incoming_message(
        channel_key="channel-5",
        author_name="Finn",
        author_id=1,
        author_is_bot=False,
        text="Keep the same objective after compaction.",
    )
    runtime.bind_thread("channel-5", thread_id="thread-555", backend="codex-app-server", status="active")
    runtime.record_compaction_event("channel-5", thread_id="thread-555", event_type="thread/compact/start")

    binding = runtime.ensure_binding("channel-5")
    status_text = (binding.worktree_path / "memory" / "STATUS.md").read_text(encoding="utf-8")
    handoff_text = (binding.worktree_path / "memory" / "HANDOFF.md").read_text(encoding="utf-8")

    assert "Rehydrate from durable memory" in status_text
    assert "Compaction event recorded" in handoff_text


def test_runtime_writes_turn_artifact_jsonl(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.bind_thread("channel-6", thread_id="thread-666", backend="codex-app-server", status="active")
    runtime.record_turn_result(
        channel_key="channel-6",
        thread_id="thread-666",
        turn_id="turn-6",
        summary="Implemented the durable rebind path.",
        files_changed=["bot.py", "relay_runtime.py"],
        commands_run=["pytest tests/test_runtime.py -q"],
        validations=["pytest tests/test_runtime.py -q -> pass"],
        next_step="Update README and status output.",
        command_exit_codes=[0],
        cwd=str(runtime.ensure_binding("channel-6").worktree_path),
        approvals=["command approval"],
        blocker="",
        error_category="",
        started_at="2026-04-11T00:00:00Z",
        completed_at="2026-04-11T00:00:10Z",
        backend="codex-app-server",
        degraded=False,
    )

    artifact_path = state / "turn-artifacts" / f"{runtime.project_id}.jsonl"
    lines = artifact_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])

    assert payload["thread_id"] == "thread-666"
    assert payload["turn_id"] == "turn-6"
    assert payload["files_changed"] == ["bot.py", "relay_runtime.py"]
    assert payload["command_exit_codes"] == [0]


def test_runtime_tracks_restart_churn(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="claude")

    # Initially no churn
    assert runtime.is_restart_churn(threshold=5) is False
    assert runtime.count_recent_restarts() == 0

    # Record some restarts
    for _ in range(4):
        runtime.record_restart_event(reason="test")

    # Not yet churn (threshold is 5)
    assert runtime.is_restart_churn(threshold=5) is False
    assert runtime.count_recent_restarts() == 4

    # One more triggers churn
    runtime.record_restart_event(reason="test")
    assert runtime.is_restart_churn(threshold=5) is True
    assert runtime.count_recent_restarts() == 5

    # Persists across runtime instances
    resumed = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="claude")
    assert resumed.is_restart_churn(threshold=5) is True
    assert resumed.count_recent_restarts() == 5


def test_runtime_channel_startup_marker_does_not_increment_restart_churn(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="claude")
    runtime.record_startup("channel-99")

    assert runtime.count_recent_restarts() == 0
    binding = runtime.ensure_binding("channel-99")
    status_text = (binding.worktree_path / "memory" / "STATUS.md").read_text(encoding="utf-8")
    assert "Resume the same thread and continue without asking for a recap." in status_text
