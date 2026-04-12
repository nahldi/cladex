from pathlib import Path

from claude_backend import ClaudeBackend, ClaudeSession, CommandResult, InboundMessage, ChannelType


def test_session_persists_initialized_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    session = ClaudeSession(state_dir, workspace)
    first_id = session.session_id
    assert first_id
    assert session.initialized is False

    session.mark_success()

    reloaded = ClaudeSession(state_dir, workspace)
    assert reloaded.session_id == first_id
    assert reloaded.initialized is True
    assert reloaded.last_success_at


def test_build_command_uses_session_id_then_resume(tmp_path: Path) -> None:
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )
    session = backend._session_for_channel("123", tmp_path)

    create_cmd = backend._build_command("hello", use_resume=False, session=session)
    assert "--session-id" in create_cmd
    assert "--resume" not in create_cmd
    assert "--dangerously-skip-permissions" in create_cmd

    resume_cmd = backend._build_command("hello again", use_resume=True, session=session)
    assert "--resume" in resume_cmd
    assert "--session-id" not in resume_cmd
    assert "--dangerously-skip-permissions" in resume_cmd
    assert "claude-opus-4-5-20251101" in resume_cmd


def test_process_message_retries_with_fresh_session_on_resume_failure(tmp_path: Path) -> None:
    responses: list[str] = []
    statuses: list[str] = []
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: responses.append(msg.content),
        on_status=statuses.append,
    )
    session = backend._session_for_channel("123", tmp_path)
    session.initialized = True
    session._save()

    calls: list[bool] = []

    def fake_run_turn(prompt: str, *, use_resume: bool, cwd: Path, session: ClaudeSession) -> CommandResult:
        calls.append(use_resume)
        if use_resume:
            return CommandResult(
                args=["claude", "--resume", session.session_id],
                returncode=1,
                stdout="",
                stderr="Session not found",
                used_resume=True,
            )
        return CommandResult(
            args=["claude", "--session-id", session.session_id],
            returncode=0,
            stdout='{"type":"result","result":"done"}\n',
            stderr="",
            used_resume=False,
        )

    backend._run_turn = fake_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = backend.process_message(
        InboundMessage(
            channel_type=ChannelType.DISCORD,
            channel_id="123",
            sender_id="u1",
            sender_name="user",
            content="fix it",
        )
    )

    assert ok is True
    assert calls == [True, False]
    assert responses == ["done"]
    assert any("stale" in status.lower() for status in statuses)


def test_format_prompt_includes_durable_context_and_caveman_rules(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Durable rules here", encoding="utf-8")
    (workspace / "memory" / "STATUS.md").write_text("Current objective: ship it", encoding="utf-8")

    backend = ClaudeBackend(
        workspace=workspace,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )
    durable_bundle = backend.runtime.build_context_bundle("123")

    prompt = backend._format_prompt(
        InboundMessage(
            channel_type=ChannelType.DISCORD,
            channel_id="123",
            sender_id="u1",
            sender_name="Finn",
            content="fix the relay",
        ),
        workspace,
        durable_bundle,
    )

    assert "caveman mode" in prompt
    assert "Discord is transport, not memory." in prompt
    assert "[AGENTS.md]" in prompt
    assert "[memory/STATUS.md]" in prompt
    assert "User message:\nfix the relay" in prompt
    assert "Current relay effort policy for this turn: high." in prompt


def test_process_message_writes_durable_memory_and_handoff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    responses: list[str] = []
    statuses: list[str] = []
    backend = ClaudeBackend(
        workspace=workspace,
        state_dir=tmp_path / "state",
        on_response=lambda msg: responses.append(msg.content),
        on_status=statuses.append,
    )

    def fake_run_turn(prompt: str, *, use_resume: bool, cwd: Path, session: ClaudeSession) -> CommandResult:
        assert "Durable runtime context:" in prompt
        assert "Current verified status:" in prompt
        assert cwd == workspace
        return CommandResult(
            args=["claude", "-p", "--session-id", session.session_id],
            returncode=0,
            stdout='{"type":"result","result":"done\\nNext step: run the validation pass"}\n',
            stderr="",
            used_resume=use_resume,
        )

    backend._run_turn = fake_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = backend.process_message(
        InboundMessage(
            channel_type=ChannelType.DISCORD,
            channel_id="456",
            sender_id="u1",
            sender_name="Finn",
            content="Implement durable relay memory.",
        )
    )

    assert ok is True
    assert responses == ["done\nNext step: run the validation pass"]
    status_md = (workspace / "memory" / "STATUS.md").read_text(encoding="utf-8")
    handoff_md = (workspace / "memory" / "HANDOFF.md").read_text(encoding="utf-8")
    tasks_json = (workspace / "memory" / "TASKS.json").read_text(encoding="utf-8")
    assert "Implement durable relay memory." in status_md
    assert "run the validation pass" in status_md
    assert "done" in handoff_md.lower()
    assert "Implement durable relay memory." in tasks_json
    assert any("working on discord message" in status.lower() for status in statuses)


def test_session_rebinds_from_runtime_thread_when_session_file_is_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = ClaudeBackend(
        workspace=workspace,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    backend.runtime.bind_thread("999", thread_id="session-rebound", backend="claude-print-resume", status="active")
    session = backend._session_for_channel("999", workspace)

    assert session.session_id == "session-rebound"
    assert session.initialized is True


def test_effort_policy_uses_quick_and_default_modes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_REASONING_EFFORT_QUICK", "medium")
    monkeypatch.setenv("CLAUDE_REASONING_EFFORT_DEFAULT", "high")
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    assert backend._effort_for_message("status?") == "medium"
    assert backend._effort_for_message("implement a durable restart-safe relay runtime") == "high"
