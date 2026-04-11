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

    create_cmd = backend._build_command("hello", use_resume=False)
    assert "--session-id" in create_cmd
    assert "--resume" not in create_cmd

    resume_cmd = backend._build_command("hello again", use_resume=True)
    assert "--resume" in resume_cmd
    assert "--session-id" not in resume_cmd


def test_process_message_retries_with_fresh_session_on_resume_failure(tmp_path: Path) -> None:
    responses: list[str] = []
    statuses: list[str] = []
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: responses.append(msg.content),
        on_status=statuses.append,
    )
    backend.session.initialized = True
    backend.session._save()

    calls: list[bool] = []

    def fake_run_turn(prompt: str, *, use_resume: bool) -> CommandResult:
        calls.append(use_resume)
        if use_resume:
            return CommandResult(
                args=["claude", "--resume", backend.session.session_id],
                returncode=1,
                stdout="",
                stderr="Session not found",
                used_resume=True,
            )
        return CommandResult(
            args=["claude", "--session-id", backend.session.session_id],
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
