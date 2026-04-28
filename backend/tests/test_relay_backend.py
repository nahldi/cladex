import asyncio
import os
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


def test_build_persistent_command_uses_stream_json(tmp_path: Path) -> None:
    """Verify _build_persistent_command produces correct flags for persistent mode."""
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    # Without session
    cmd = backend._build_persistent_command(cwd=tmp_path)
    assert "-p" in cmd
    assert "--input-format" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--model" not in cmd
    assert "--permission-mode" in cmd
    assert "default" in cmd
    assert "--resume" not in cmd

    # With session
    cmd_with_session = backend._build_persistent_command(cwd=tmp_path, session_id="test-session")
    assert "-p" in cmd_with_session
    assert "--resume" in cmd_with_session
    assert "test-session" in cmd_with_session


def test_run_turn_uses_persistent_stream_json(tmp_path: Path) -> None:
    """Verify _run_turn uses persistent stdin/stdout streaming."""
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    # Mock _run_turn to verify command structure
    captured_cmds: list[list[str]] = []

    async def mock_run_turn(prompt: str, *, cwd: Path, persistent) -> CommandResult:
        # Build the command as the real method would
        session_id = persistent.session.session_id if persistent.session.initialized else None
        cmd = backend._build_persistent_command(cwd=cwd, session_id=session_id)
        captured_cmds.append(cmd)
        # Return a proper response with assistant content
        return CommandResult(
            args=cmd,
            returncode=0,
            stdout='{"type":"assistant","message":{"role":"assistant","content":"done"}}\n{"type":"result","session_id":"test-123"}',
            stderr="",
            used_resume=persistent.session.initialized,
        )

    backend._run_turn = mock_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = asyncio.run(
        backend.process_message(
            InboundMessage(
                channel_type=ChannelType.DISCORD,
                channel_id="123",
                sender_id="u1",
                sender_name="user",
                content="fix it",
            )
        )
    )

    assert ok is True
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    # Verify persistent mode flags (not --print mode)
    assert "-p" in cmd
    assert "--input-format" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--model" not in cmd
    assert "--permission-mode" in cmd
    assert "default" in cmd


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
    original_session_id = session.session_id
    session.initialized = True
    session._save()

    calls: list[tuple[bool, str | None]] = []

    async def fake_run_turn(prompt: str, *, cwd: Path, persistent) -> CommandResult:
        used_resume = persistent.session.initialized
        calls.append((used_resume, persistent.session.session_id))
        if used_resume:
            return CommandResult(
                args=["claude", "--input-format", "stream-json"],
                returncode=1,
                stdout="",
                stderr="Session not found",
                used_resume=True,
            )
        return CommandResult(
            args=["claude", "--input-format", "stream-json"],
            returncode=0,
            stdout="done",
            stderr="",
            used_resume=False,
        )

    backend._run_turn = fake_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = asyncio.run(
        backend.process_message(
            InboundMessage(
                channel_type=ChannelType.DISCORD,
                channel_id="123",
                sender_id="u1",
                sender_name="user",
                content="fix it",
            )
        )
    )

    assert ok is True
    assert calls[0] == (True, original_session_id)
    assert calls[1][0] is False
    assert calls[1][1] != calls[0][1]
    assert responses == ["done"]
    assert any("stale" in status.lower() for status in statuses)


def test_backend_start_uses_launcher_restart_reason_from_env(tmp_path: Path, monkeypatch) -> None:
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )
    recorded: list[str] = []

    monkeypatch.setattr("claude_backend.claude_code_version", lambda: "1.0.0")
    monkeypatch.setattr(backend.runtime, "record_restart_event", lambda reason="normal": recorded.append(reason))
    monkeypatch.setattr(backend.runtime, "is_restart_churn", lambda threshold=5, window_seconds=300: False)

    previous = os.environ.get("CLADEX_START_REASON")
    os.environ["CLADEX_START_REASON"] = "operator-restart"
    try:
        assert backend.start() is True
    finally:
        if previous is None:
            os.environ.pop("CLADEX_START_REASON", None)
        else:
            os.environ["CLADEX_START_REASON"] = previous

    assert recorded == ["operator-restart"]


def test_process_message_retries_with_fresh_session_on_session_id_collision(tmp_path: Path) -> None:
    responses: list[str] = []
    statuses: list[str] = []
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: responses.append(msg.content),
        on_status=statuses.append,
    )
    session = backend._session_for_channel("123", tmp_path)
    original_session_id = session.session_id

    calls: list[tuple[bool, str | None]] = []

    async def fake_run_turn(prompt: str, *, cwd: Path, persistent) -> CommandResult:
        used_resume = persistent.session.initialized
        calls.append((used_resume, persistent.session.session_id))
        if len(calls) == 1:
            return CommandResult(
                args=["claude", "--input-format", "stream-json"],
                returncode=1,
                stdout="",
                stderr=f"Error: Session ID {persistent.session.session_id} is already in use.",
                used_resume=used_resume,
            )
        return CommandResult(
            args=["claude", "--input-format", "stream-json"],
            returncode=0,
            stdout="done after fresh session",
            stderr="",
            used_resume=used_resume,
        )

    backend._run_turn = fake_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = asyncio.run(
        backend.process_message(
            InboundMessage(
                channel_type=ChannelType.DISCORD,
                channel_id="123",
                sender_id="u1",
                sender_name="user",
                content="fix it",
            )
        )
    )

    assert ok is True
    assert calls[0] == (False, original_session_id)
    assert calls[1][0] is False
    assert calls[1][1] != original_session_id
    assert responses == ["done after fresh session"]
    assert any("recreating session" in status.lower() for status in statuses)


def test_process_message_retries_once_when_claude_returns_no_text(tmp_path: Path) -> None:
    responses: list[str] = []
    statuses: list[str] = []
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: responses.append(msg.content),
        on_status=statuses.append,
    )

    calls: list[tuple[bool, str | None]] = []

    async def fake_run_turn(prompt: str, *, cwd: Path, persistent) -> CommandResult:
        used_resume = persistent.session.initialized
        calls.append((used_resume, persistent.session.session_id))
        if len(calls) == 1:
            return CommandResult(
                args=["claude", "--input-format", "stream-json"],
                returncode=0,
                stdout="",
                stderr="",
                used_resume=used_resume,
            )
        return CommandResult(
            args=["claude", "--input-format", "stream-json"],
            returncode=0,
            stdout="done after retry",
            stderr="",
            used_resume=used_resume,
        )

    backend._run_turn = fake_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = asyncio.run(
        backend.process_message(
            InboundMessage(
                channel_type=ChannelType.DISCORD,
                channel_id="123",
                sender_id="u1",
                sender_name="user",
                content="fix it",
            )
        )
    )

    assert ok is True
    assert calls[0][0] is False
    assert calls[1][0] is False
    assert calls[1][1] != calls[0][1]
    assert responses == ["done after retry"]
    assert any("retrying once" in status.lower() for status in statuses)


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
    assert "For relay implementation, runtime, packaging, or audit questions" in prompt
    assert "[AGENTS.md]" in prompt
    assert "[memory/STATUS.md]" in prompt
    assert "User message:\nfix the relay" in prompt
    assert "Current relay effort policy for this turn: high." in prompt


def test_extract_response_text_accepts_plain_sdk_output(tmp_path: Path) -> None:
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    assert backend._extract_response_text("done") == "done"


def test_extract_response_text_ignores_system_and_rate_limit_events(tmp_path: Path) -> None:
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    stdout = "\n".join(
        [
            '{"type":"system","subtype":"init","session_id":"abc"}',
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"internal"},{"type":"text","text":"yes"}]}}',
            '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"},"session_id":"abc"}',
            '{"type":"result","subtype":"success","is_error":false,"result":"yes","session_id":"abc"}',
        ]
    )

    assert backend._extract_response_text(stdout) == "yes"


def test_start_records_process_restart_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("claude_backend.claude_code_version", lambda: "claude 1.0.0")

    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    assert backend.runtime.count_recent_restarts() == 0

    ok = backend.start()

    assert ok is True
    assert backend.runtime.count_recent_restarts() == 1


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

    async def fake_run_turn(prompt: str, *, cwd: Path, persistent) -> CommandResult:
        assert "Durable runtime context:" in prompt
        assert "Current verified status:" in prompt
        assert cwd == workspace
        return CommandResult(
            args=["claude", "--input-format", "stream-json"],
            returncode=0,
            stdout="done\nNext step: run the validation pass",
            stderr="",
            used_resume=persistent.session.initialized,
        )

    backend._run_turn = fake_run_turn  # type: ignore[method-assign]
    backend.start = lambda: True  # type: ignore[method-assign]

    ok = asyncio.run(
        backend.process_message(
            InboundMessage(
                channel_type=ChannelType.DISCORD,
                channel_id="456",
                sender_id="u1",
                sender_name="Finn",
                content="Implement durable relay memory.",
            )
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

    backend.runtime.bind_thread("999", thread_id="session-rebound", backend="claude-subprocess", status="active")
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


def test_lightweight_message_detection(tmp_path: Path) -> None:
    backend = ClaudeBackend(
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    assert backend._is_lightweight_message("yes") is True
    assert backend._is_lightweight_message("no") is True
    assert backend._is_lightweight_message("ok") is True
    assert backend._is_lightweight_message("done") is True
    assert backend._is_lightweight_message("ready") is True
    assert backend._is_lightweight_message("acknowledged") is True
    assert backend._is_lightweight_message("got it") is True
    assert backend._is_lightweight_message("Yes!") is True
    assert backend._is_lightweight_message("OK.") is True

    assert backend._is_lightweight_message("fix the relay dedup bug") is False
    assert backend._is_lightweight_message("implement durable restart tracking") is False
    assert backend._is_lightweight_message("what is the status of the project?") is False
    assert backend._is_lightweight_message("a" * 60) is False


def test_format_prompt_uses_lightweight_path_for_short_messages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory").mkdir(parents=True)
    (workspace / "memory" / "STATUS.md").write_text("## Current objective\nTest objective", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# AGENTS\nTest agents file", encoding="utf-8")

    backend = ClaudeBackend(
        workspace=workspace,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )

    lightweight_prompt = backend._format_prompt(
        InboundMessage(
            channel_type=ChannelType.DISCORD,
            channel_id="123",
            sender_id="u1",
            sender_name="Finn",
            content="yes",
        ),
        workspace,
        "context bundle",
    )
    assert "lightweight coordination message" in lightweight_prompt
    assert "AGENTS.md" not in lightweight_prompt
    assert "Be brief" in lightweight_prompt

    full_prompt = backend._format_prompt(
        InboundMessage(
            channel_type=ChannelType.DISCORD,
            channel_id="123",
            sender_id="u1",
            sender_name="Finn",
            content="implement durable restart tracking",
        ),
        workspace,
        "context bundle",
    )
    assert "lightweight coordination message" not in full_prompt
    assert "caveman mode" in full_prompt
    assert "AGENTS.md" in full_prompt or "Relevant repo documents:" in full_prompt


def test_full_prompt_does_not_embed_raw_handoff_or_decisions_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("# AGENTS\nDo not drift.", encoding="utf-8")
    (workspace / "memory" / "STATUS.md").write_text("## Current objective\nShip it", encoding="utf-8")
    (workspace / "memory" / "HANDOFF.md").write_text("# HANDOFF\n## 2026-01-01\n- result: noisy handoff", encoding="utf-8")
    (workspace / "memory" / "DECISIONS.md").write_text("# DECISIONS\n## 2026-01-01\n- Decision: noisy decision", encoding="utf-8")

    backend = ClaudeBackend(
        workspace=workspace,
        state_dir=tmp_path / "state",
        on_response=lambda msg: None,
    )
    prompt = backend._format_prompt(
        InboundMessage(
            channel_type=ChannelType.DISCORD,
            channel_id="123",
            sender_id="u1",
            sender_name="Finn",
            content="implement the relay fix",
        ),
        workspace,
        backend.runtime.build_context_bundle("123"),
    )

    assert "[memory/HANDOFF.md]" not in prompt
    assert "[memory/DECISIONS.md]" not in prompt
    assert "[memory/STATUS.md]" in prompt
