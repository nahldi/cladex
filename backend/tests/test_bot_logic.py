from __future__ import annotations

import importlib
import os
import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def _load_bot_module():
    temp_root = tempfile.mkdtemp(prefix="relay-bot-test-")
    os.environ["HOME"] = temp_root
    os.environ["XDG_CONFIG_HOME"] = temp_root
    os.environ["XDG_DATA_HOME"] = temp_root
    os.environ["ENV_FILE"] = str(Path(temp_root) / "test.env")
    os.environ["DISCORD_BOT_TOKEN"] = "test-token"
    os.environ["CODEX_WORKDIR"] = os.getcwd()
    os.environ["STATE_NAMESPACE"] = "test-bot-logic"
    sys.modules.pop("bot", None)
    sys.modules.pop("relay_common", None)
    if "bot" in sys.modules:
        return importlib.reload(sys.modules["bot"])
    return importlib.import_module("bot")


def _message(*, channel_id: int, author_id: int = 1, content: str = "", mentions=None, guild=True, reference=None):
    return SimpleNamespace(
        guild=object() if guild else None,
        author=SimpleNamespace(id=author_id, bot=False, name="user", display_name="user", global_name=None),
        channel=SimpleNamespace(id=channel_id),
        content=content,
        mentions=list(mentions or []),
        reference=reference,
        attachments=[],
        embeds=[],
        stickers=[],
    )


def test_allowed_channel_does_not_bypass_mention_mode() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = set()
    bot.CONFIG.trigger_mode = "mention_or_dm"

    plain_message = _message(channel_id=42, content="hello there")
    mention_message = _message(channel_id=42, content="<@999> hello", mentions=[relay_user])

    assert bot._message_targets_bot(plain_message, relay_user) is False
    assert bot._message_targets_bot(mention_message, relay_user) is True


def test_load_config_defaults_codex_model_to_gpt_5_4(tmp_path) -> None:
    bot = _load_bot_module()
    env_path = tmp_path / "relay.env"
    env_path.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=test-token",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-default-model",
                "CODEX_MODEL=",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    original_env_file = os.environ.get("ENV_FILE")
    try:
        os.environ["ENV_FILE"] = str(env_path)
        config = bot._load_config()
    finally:
        if original_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = original_env_file

    assert config.codex_model == bot.DEFAULT_CODEX_MODEL


def test_load_config_disables_visible_terminal_on_stdio(tmp_path) -> None:
    bot = _load_bot_module()
    env_path = tmp_path / "relay.env"
    env_path.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=test-token",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-stdio-terminal",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "OPEN_VISIBLE_TERMINAL=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    original_env_file = os.environ.get("ENV_FILE")
    try:
        os.environ["ENV_FILE"] = str(env_path)
        config = bot._load_config()
    finally:
        if original_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = original_env_file

    assert config.open_visible_terminal is False


def test_durable_context_budget_trims_lightweight_coordination_turns() -> None:
    bot = _load_bot_module()

    assert (
        bot._durable_context_budget(
            directive=bot.RelayDirective(kind="lightweight_ping", authoritative=False, reply_required=False, reason=""),
            cleaned_text="ping",
            new_thread=False,
        )
        == 900
    )
    assert (
        bot._durable_context_budget(
            directive=bot.RelayDirective(kind="teammate_question", authoritative=True, reply_required=True, reason=""),
            cleaned_text="yes or no?",
            new_thread=False,
        )
        == 1200
    )
    assert (
        bot._durable_context_budget(
            directive=bot.RelayDirective(kind="teammate_handoff", authoritative=True, reply_required=False, reason=""),
            cleaned_text="take over the audit and verify the restart logs",
            new_thread=False,
        )
        == 1400
    )
    assert (
        bot._durable_context_budget(
            directive=bot.RelayDirective(kind="teammate_question", authoritative=True, reply_required=True, reason=""),
            cleaned_text="yes or no?",
            new_thread=True,
        )
        == 3200
    )


def test_channel_turn_input_prioritizes_latest_human_instruction() -> None:
    bot = _load_bot_module()
    bot.client = SimpleNamespace(user=SimpleNamespace(id=999))
    bot.DURABLE_RUNTIME = SimpleNamespace(build_context_bundle=lambda *args, **kwargs: "Durable runtime context.")

    message = SimpleNamespace(
        id=42,
        created_at=datetime.now(timezone.utc),
        content="status?",
        attachments=[],
        embeds=[],
        mentions=[],
        stickers=[],
        webhook_id=None,
        reference=None,
        author=SimpleNamespace(id=123, bot=True, name="Forge", display_name="Forge", global_name=None),
        channel=SimpleNamespace(id=77),
        guild=object(),
    )
    bootstrap_prompt = asyncio.run(
        bot._channel_turn_input(
            message,
            new_thread=True,
            directive=bot.RelayDirective(kind="teammate_question", authoritative=True, reply_required=True, reason=""),
            latest_authoritative_instruction="Only answer yes or no.",
        )
    )
    update_prompt = asyncio.run(
        bot._channel_turn_input(
            message,
            new_thread=False,
            directive=bot.RelayDirective(kind="teammate_question", authoritative=True, reply_required=True, reason=""),
            latest_authoritative_instruction="Only answer yes or no.",
        )
    )

    assert "The latest authoritative human instruction is the task to execute now." in bootstrap_prompt
    assert "Use this update only insofar as it helps satisfy the latest authoritative human instruction." in update_prompt
    assert "continuing the same underlying work" not in bootstrap_prompt
    assert "continuing the same underlying work" not in update_prompt


def test_open_visible_terminal_uses_dangerous_resume_flag(monkeypatch, tmp_path) -> None:
    bot = _load_bot_module()
    session = bot.CodexSession("channel-visible-terminal")
    session.thread_id = "thread-123"
    session.visible_terminal_opened = False
    bot.CONFIG.open_visible_terminal = True
    bot.CONFIG.codex_workdir = tmp_path
    monkeypatch.setattr(bot, "APP_SERVER", SimpleNamespace(ws_url="ws://127.0.0.1:4040/codex"))
    bot.CODEX_BIN = "codex.exe"
    monkeypatch.setattr(bot, "_uses_websocket_transport", lambda: True)
    monkeypatch.setattr(bot, "best_windows_shell", lambda: "powershell.exe")
    monkeypatch.setattr(shutil, "which", lambda name: None if name in {"wt.exe", "wt"} else None)
    monkeypatch.setattr(bot.os, "name", "nt")

    calls: list[tuple[str, ...]] = []

    class _FakeProcess:
        async def wait(self):
            return 0

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls.append(tuple(str(part) for part in args))
        return _FakeProcess()

    monkeypatch.setattr(bot.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    asyncio.run(session._open_visible_terminal())

    assert calls
    joined = " ".join(calls[0])
    assert "--dangerously-bypass-approvals-and-sandbox resume" in joined


def test_reader_loop_ignores_non_json_stdio_stdout_and_continues(tmp_path) -> None:
    bot = _load_bot_module()
    original_log_path = bot.APP_SERVER_LOG_PATH
    bot.APP_SERVER_LOG_PATH = tmp_path / "app-server.log"

    class _FakeStdout:
        def __init__(self, lines):
            self._lines = [line.encode("utf-8") for line in lines]

        async def readline(self):
            if self._lines:
                return self._lines.pop(0)
            return b""

    session = bot.CodexSession("channel-test-reader")
    handled: list[dict] = []

    async def _fake_handle(payload):
        handled.append(payload)

    session._handle_payload = _fake_handle
    session.app_server_process = SimpleNamespace(
        stdout=_FakeStdout(
            [
                "not-json\n",
                json.dumps({"method": "ping", "params": {}}) + "\n",
            ]
        ),
        returncode=None,
        pid=123,
    )
    try:
        asyncio.run(session._reader_loop())
    finally:
        bot.APP_SERVER_LOG_PATH = original_log_path

    assert handled == [{"method": "ping", "params": {}}]
    assert "STDOUT non-JSON payload ignored: not-json" in (tmp_path / "app-server.log").read_text(encoding="utf-8")


def test_reader_loop_ignores_payload_handler_errors_and_continues(tmp_path) -> None:
    bot = _load_bot_module()
    original_log_path = bot.APP_SERVER_LOG_PATH
    bot.APP_SERVER_LOG_PATH = tmp_path / "app-server.log"

    class _FakeStdout:
        def __init__(self, lines):
            self._lines = [line.encode("utf-8") for line in lines]

        async def readline(self):
            if self._lines:
                return self._lines.pop(0)
            return b""

    session = bot.CodexSession("channel-test-reader-errors")
    handled: list[dict] = []

    async def _fake_handle(payload):
        if payload.get("method") == "bad":
            raise RuntimeError("boom")
        handled.append(payload)

    session._handle_payload = _fake_handle
    session.app_server_process = SimpleNamespace(
        stdout=_FakeStdout(
            [
                json.dumps({"method": "bad", "params": {}}) + "\n",
                json.dumps({"method": "good", "params": {"ok": True}}) + "\n",
            ]
        ),
        returncode=None,
        pid=456,
    )
    try:
        asyncio.run(session._reader_loop())
    finally:
        bot.APP_SERVER_LOG_PATH = original_log_path

    assert handled == [{"method": "good", "params": {"ok": True}}]
    assert "Protocol handler error ignored" in (tmp_path / "app-server.log").read_text(encoding="utf-8")


def test_allowed_channel_respects_author_allowlist() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.trigger_mode = "mention_or_dm"

    blocked = _message(channel_id=42, author_id=3, content="<@999> hi", mentions=[relay_user])
    allowed = _message(channel_id=42, author_id=7, content="<@999> hi", mentions=[relay_user])

    assert bot._message_targets_bot(blocked, relay_user) is False
    assert bot._message_targets_bot(allowed, relay_user) is True


def test_trigger_mode_all_allows_allowed_channel_messages() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = set()
    bot.CONFIG.trigger_mode = "all"

    plain_message = _message(channel_id=42, content="ship it")

    assert bot._message_targets_bot(plain_message, relay_user) is True


def test_allowed_channel_message_is_observable_without_trigger() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.channel_no_mention_author_ids = set()
    bot.CONFIG.trigger_mode = "mention_or_dm"

    plain_message = _message(channel_id=42, author_id=7, content="build update")

    assert bot._message_is_observable_by_relay(plain_message, relay_user) is True
    assert bot._message_targets_bot(plain_message, relay_user) is False


def test_channel_no_mention_author_can_trigger_without_mention() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.channel_no_mention_author_ids = {7}
    bot.CONFIG.trigger_mode = "mention_or_dm"

    plain_message = _message(channel_id=42, author_id=7, content="status")

    assert bot._message_targets_bot(plain_message, relay_user) is True


def test_unmentioned_allowed_channel_message_is_context_only() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.channel_no_mention_author_ids = set()
    bot.CONFIG.trigger_mode = "mention_or_dm"

    directive = bot._classify_relay_message(
        _message(channel_id=42, author_id=7, content="ship the logging fix"),
        relay_user,
    )

    assert directive.kind == "channel_context"
    assert directive.authoritative is False
    assert directive.reply_required is False


def test_targeted_teammate_bot_message_becomes_actionable_handoff() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.channel_no_mention_author_ids = set()
    bot.CONFIG.trigger_mode = "mention_or_dm"

    message = _message(channel_id=42, author_id=7, content="<@999> take phase 4b frontend now", mentions=[relay_user])
    message.author.bot = True

    directive = bot._classify_relay_message(message, relay_user)

    assert directive.kind == "teammate_handoff"
    assert directive.authoritative is True
    assert directive.reply_required is False


def test_targeted_teammate_bot_question_requires_reply() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.channel_no_mention_author_ids = set()
    bot.CONFIG.trigger_mode = "mention_or_dm"

    message = _message(channel_id=42, author_id=7, content="<@999> are you landing the files now or not?", mentions=[relay_user])
    message.author.bot = True

    directive = bot._classify_relay_message(message, relay_user)

    assert directive.kind == "teammate_question"
    assert directive.authoritative is True
    assert directive.reply_required is True


def test_silence_instruction_detection() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    message = _message(channel_id=42, author_id=7, content="literally stop answering fully dont even acknowledge this just dont answer")
    assert bot._is_silence_instruction(message, relay_user) is True


def test_developer_instructions_warn_against_bot_loops() -> None:
    bot = _load_bot_module()
    instructions = bot._developer_instructions()
    assert "other allowed bots are teammates" in instructions
    assert "reply loops" in instructions
    assert "still waiting" in instructions
    assert "caveman mode" in instructions


def test_reply_to_known_relay_message_targets_bot_without_resolved_reference() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.trigger_mode = "mention_or_dm"
    bot._remember_relay_message_id(555)

    reply_message = _message(
        channel_id=42,
        author_id=7,
        content="keep going",
        reference=SimpleNamespace(message_id=555, resolved=None),
    )

    assert bot._message_targets_bot(reply_message, relay_user) is True


def test_expected_active_turn_mismatch_counts_as_stale_steer_error() -> None:
    bot = _load_bot_module()
    exc = bot.JsonRpcError(
        "expected active turn id 019d6591-700e-7cb0-a5af-7735952a8c76 but found 019d658f-5985-79b1-8f20-a561baf8874a"
    )
    assert bot._is_stale_steer_error(exc) is True


def test_typing_indicator_expires_after_cap() -> None:
    bot = _load_bot_module()
    turn = SimpleNamespace(started_at=100.0)
    assert bot._typing_indicator_expired(turn, now=100.0 + bot.TYPING_INDICATOR_MAX_SECONDS - 1) is False
    assert bot._typing_indicator_expired(turn, now=100.0 + bot.TYPING_INDICATOR_MAX_SECONDS) is True


def test_turn_is_stalled_after_activity_timeout() -> None:
    bot = _load_bot_module()
    turn = SimpleNamespace(last_activity_at=200.0)
    assert bot._turn_is_stalled(turn, now=200.0 + bot.TURN_STALL_TIMEOUT_SECONDS - 1) is False
    assert bot._turn_is_stalled(turn, now=200.0 + bot.TURN_STALL_TIMEOUT_SECONDS) is True


def test_stalled_turn_error_detection() -> None:
    bot = _load_bot_module()
    assert bot._is_stalled_turn_error("Codex turn stalled without activity.") is True


def test_status_only_reply_catches_commitment_without_progress() -> None:
    bot = _load_bot_module()
    assert bot._is_status_only_reply("Phase 4B frontend completion is the next thing you should see from me.") is True
    assert bot._is_status_only_reply("Phase 4.5 QA is already moving.") is True
    assert bot._is_status_only_reply("I'm still closing Phase 4B frontend first.") is True
    assert bot._is_status_only_reply("No new Discord reply sent.") is True
    assert bot._is_status_only_reply("Still silent. Waiting on Tyson's backend seam.") is True
    assert bot._is_status_only_reply("Copy.") is True
    assert bot._is_status_only_reply("Holding.") is True
    assert bot._is_status_only_reply("No blocker.") is True
    assert bot._is_status_only_reply("்") is True


def test_project_context_block_pulls_role_and_roadmap(tmp_path) -> None:
    bot = _load_bot_module()
    project_root = tmp_path / "teamspace"
    workspace = project_root / "agent-ui"
    workspace.mkdir(parents=True)
    (project_root / "AGENTS.md").write_text(
        "\n".join(
            [
                "# Agents",
                "| Agent | Model | Role | What They Own |",
                "|-------|-------|------|---------------|",
                "| **agent-ui** | Codex | Frontend Execution | All `frontend/` and `desktop/`. |",
                "Current state: Phase 4B frontend is active.",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / "UNIFIED_ROADMAP.md").write_text(
        "\n".join(
            [
                "# Roadmap",
                "**Current version:** v5.7.2",
                "Current local status: Phase 4B active.",
                "Phase 4.5 is next after 4B passes.",
            ]
        ),
        encoding="utf-8",
    )

    block = bot._load_project_context_block(workspace, "agent-ui")

    assert "Project role file:" in block
    assert "Declared role/ownership:" in block
    assert "Frontend Execution" in block
    assert "Project roadmap file:" in block
    assert "Phase 4.5 is next" in block


def test_parse_verification_claim_supports_extended_claim_types() -> None:
    bot = _load_bot_module()

    assert bot._parse_verification_claim("file:src/app.py") == ("file_exists", "src/app.py")
    assert bot._parse_verification_claim("branch:feature/runtime") == ("branch_exists", "feature/runtime")
    assert bot._parse_verification_claim("tests:python -m pytest tests/test_runtime.py -q -> pass") == (
        "tests_passed",
        "python -m pytest tests/test_runtime.py -q -> pass",
    )
    assert bot._parse_verification_claim("unknown:value") is None


def test_ensure_thread_locked_rebinds_discoverable_thread() -> None:
    bot = _load_bot_module()
    session = bot.CodexSession("channel-909")
    workdir = session._runtime_workdir()

    class _FakeBackend:
        async def resume_thread(self, thread_id: str):
            raise RuntimeError("stale saved thread")

        async def list_threads(self, project_id: str):
            return [SimpleNamespace(thread_id="thread-rebound", metadata={"cwd": str(workdir)})]

        async def read_thread(self, thread_id: str):
            return {"thread": {"id": thread_id, "cwd": str(workdir)}}

        async def create_thread(self, binding):
            raise AssertionError("create_thread should not be called when a discoverable thread exists")

        async def set_thread_name(self, thread_id: str, name: str):
            return {}

    async def _noop_connection() -> None:
        return None

    session.backend = _FakeBackend()
    session._ensure_connection_locked = _noop_connection  # type: ignore[assignment]
    bot._save_thread_id(session.key, "thread-stale")

    created_new = asyncio.run(session._ensure_thread_locked())

    assert created_new is False
    assert session.thread_id == "thread-rebound"
    assert bot.DURABLE_RUNTIME.active_thread_id(session.key) == "thread-rebound"


def test_ensure_thread_locked_skips_resumed_thread_with_wrong_cwd() -> None:
    bot = _load_bot_module()
    session = bot.CodexSession("channel-910")
    workdir = session._runtime_workdir()

    class _FakeBackend:
        async def resume_thread(self, thread_id: str):
            return SimpleNamespace(thread_id="thread-stale", metadata={"cwd": "B:/"})

        async def read_thread(self, thread_id: str):
            if thread_id == "thread-rebound":
                return {"thread": {"id": thread_id, "cwd": str(workdir)}}
            return {"thread": {"id": thread_id, "cwd": "B:/"}}

        async def list_threads(self, project_id: str):
            return [SimpleNamespace(thread_id="thread-rebound", metadata={"cwd": str(workdir)})]

        async def create_thread(self, binding):
            raise AssertionError("create_thread should not be called when a matching thread exists")

        async def set_thread_name(self, thread_id: str, name: str):
            return {}

    async def _noop_connection() -> None:
        return None

    session.backend = _FakeBackend()
    session._ensure_connection_locked = _noop_connection  # type: ignore[assignment]
    bot._save_thread_id(session.key, "thread-stale")

    created_new = asyncio.run(session._ensure_thread_locked())

    assert created_new is False
    assert session.thread_id == "thread-rebound"
    assert bot.DURABLE_RUNTIME.active_thread_id(session.key) == "thread-rebound"


def test_build_channel_history_scans_past_initial_raw_slice_for_relevant_messages() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.client = SimpleNamespace(user=relay_user)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7, 8}
    bot.CONFIG.channel_history_limit = 2
    bot.CONFIG.trigger_mode = "mention_or_dm"

    def _history_message(index: int, *, author_id: int, content: str) -> SimpleNamespace:
        return SimpleNamespace(
            guild=object(),
            author=SimpleNamespace(id=author_id, bot=False, name=f"user-{author_id}", display_name=f"user-{author_id}", global_name=None),
            channel=SimpleNamespace(id=42),
            content=content,
            mentions=[],
            reference=None,
            attachments=[],
            embeds=[],
            stickers=[],
            created_at=SimpleNamespace(isoformat=lambda: f"2026-04-08T00:00:{index:02d}"),
        )

    raw_messages = [_history_message(index, author_id=3, content=f"irrelevant {index}") for index in range(120, 0, -1)]
    raw_messages.extend(
        [
            _history_message(121, author_id=8, content="older relevant 2"),
            _history_message(122, author_id=7, content="older relevant 1"),
        ]
    )

    class FakeChannel:
        async def history(self, limit=None, oldest_first=False):
            assert oldest_first is False
            for item in raw_messages:
                yield item

    history = asyncio.run(bot._build_channel_history_for_channel(FakeChannel()))

    assert [item.content for item in history] == ["older relevant 1", "older relevant 2"]


def test_build_channel_history_supports_unlimited_relevant_scan() -> None:
    bot = _load_bot_module()
    relay_user = SimpleNamespace(id=999)
    bot.client = SimpleNamespace(user=relay_user)
    bot.CONFIG.allowed_channel_ids = {42}
    bot.CONFIG.allowed_channel_author_ids = {7}
    bot.CONFIG.channel_history_limit = 0
    bot.CONFIG.trigger_mode = "mention_or_dm"

    messages = [
        SimpleNamespace(
            guild=object(),
            author=SimpleNamespace(id=author_id, bot=False, name=f"user-{author_id}", display_name=f"user-{author_id}", global_name=None),
            channel=SimpleNamespace(id=42),
            content=content,
            mentions=[],
            reference=None,
            attachments=[],
            embeds=[],
            stickers=[],
            created_at=SimpleNamespace(isoformat=lambda content=content: f"2026-04-08T00:00:{len(content):02d}"),
        )
        for author_id, content in [
            (3, "ignore newest"),
            (7, "relevant newest"),
            (3, "ignore older"),
            (7, "relevant oldest"),
        ]
    ]

    class FakeChannel:
        async def history(self, limit=None, oldest_first=False):
            assert limit is None
            assert oldest_first is False
            for item in messages:
                yield item

    history = asyncio.run(bot._build_channel_history_for_channel(FakeChannel()))

    assert [item.content for item in history] == ["relevant oldest", "relevant newest"]


def test_auth_failure_text_detection() -> None:
    bot = _load_bot_module()
    assert bot._is_auth_failure_text('Auth(TokenRefreshFailed("Server returned error response: invalid_grant: Invalid refresh token"))') is True


def test_stdio_stream_limit_is_large_enough_for_heavy_sessions() -> None:
    bot = _load_bot_module()
    assert bot.STDIO_STREAM_LIMIT_BYTES >= 64 * 1024 * 1024


def test_auth_failure_marker_is_written() -> None:
    bot = _load_bot_module()
    bot._record_auth_failure_marker("auth broke")
    written = bot.AUTH_FAILURE_MARKER_PATH.read_text(encoding="utf-8")
    assert "auth broke" in written


def test_complete_tracked_turn_clears_auth_failure_marker() -> None:
    bot = _load_bot_module()

    async def _run() -> None:
        bot._record_auth_failure_marker("auth broke")
        session = bot.CodexSession("channel-42")
        turn = bot.ActiveTurn(
            turn_id="turn-1",
            started_at=0.0,
            last_activity_at=0.0,
            latest_message=_message(channel_id=42, author_id=7, content="hey"),
            completion=asyncio.get_running_loop().create_future(),
        )
        session.tracked_turns[turn.turn_id] = turn
        session.active_turn = turn
        session._complete_tracked_turn(turn, result="done")
        if session.idle_disconnect_task is not None:
            session.idle_disconnect_task.cancel()
            try:
                await session.idle_disconnect_task
            except asyncio.CancelledError:
                pass
        assert not bot.AUTH_FAILURE_MARKER_PATH.exists()

    asyncio.run(_run())


def test_developer_instructions_include_soul() -> None:
    bot = _load_bot_module()
    instructions = bot._developer_instructions()
    assert "SOUL.md" in instructions
    assert "Do not grovel." in instructions
    assert "Just help like someone with a brain and a spine." in instructions
    assert "codex-discord restart" in instructions
    assert "cmd /c codex" in instructions


def test_finalize_turn_after_grace_uses_missing_reply_sentinel() -> None:
    bot = _load_bot_module()

    async def _run() -> None:
        session = bot.CodexSession("channel-42")
        loop = asyncio.get_running_loop()
        turn = bot.ActiveTurn(
            turn_id="turn-1",
            started_at=0.0,
            last_activity_at=0.0,
            latest_message=_message(channel_id=42, author_id=7, content="<@999> status"),
            completion=loop.create_future(),
        )
        session.tracked_turns[turn.turn_id] = turn
        session.active_turn = turn
        captured: dict[str, object] = {}
        original_complete = session._complete_tracked_turn
        original_best = session._best_turn_text
        session._complete_tracked_turn = lambda turn, result=None, exc=None: captured.update({"result": result, "exc": exc})
        session._best_turn_text = lambda turn: ""
        try:
            await session._finalize_turn_after_grace(turn.turn_id, delay_seconds=0)
        finally:
            session._complete_tracked_turn = original_complete
            session._best_turn_text = original_best
        assert captured["result"] == bot.MISSING_REPLY_SENTINEL
        assert captured["exc"] is None

    asyncio.run(_run())


def test_finalize_turn_after_grace_uses_no_reply_needed_for_no_reply_turns() -> None:
    bot = _load_bot_module()

    async def _run() -> None:
        session = bot.CodexSession("channel-42")
        loop = asyncio.get_running_loop()
        turn = bot.ActiveTurn(
            turn_id="turn-1",
            started_at=0.0,
            last_activity_at=0.0,
            latest_message=_message(channel_id=42, author_id=7, content="<@999> take over the audit", mentions=[SimpleNamespace(id=999)]),
            completion=loop.create_future(),
            directive_kind="teammate_handoff",
            reply_required=False,
        )
        session.tracked_turns[turn.turn_id] = turn
        session.active_turn = turn
        captured: dict[str, object] = {}
        original_complete = session._complete_tracked_turn
        original_best = session._best_turn_text
        session._complete_tracked_turn = lambda turn, result=None, exc=None: captured.update({"result": result, "exc": exc})
        session._best_turn_text = lambda turn: ""
        try:
            await session._finalize_turn_after_grace(turn.turn_id, delay_seconds=0)
        finally:
            session._complete_tracked_turn = original_complete
            session._best_turn_text = original_best
        assert captured["result"] == bot.NO_REPLY_NEEDED_SENTINEL
        assert captured["exc"] is None

    asyncio.run(_run())


def test_best_turn_text_prefers_substantive_reply_over_no_reply_sentinel_when_reply_required() -> None:
    bot = _load_bot_module()
    session = bot.CodexSession("channel-42")
    loop = asyncio.new_event_loop()
    try:
        turn = bot.ActiveTurn(
            turn_id="turn-1",
            started_at=0.0,
            last_activity_at=0.0,
            latest_message=_message(channel_id=42, author_id=7, content="why are you only saying yes"),
            completion=loop.create_future(),
            reply_required=True,
        )
        turn.agent_item_text["item-1"] = "Because your last few messages were just `sage` and `sage?`, so I treated them as presence checks."
        turn.agent_item_text["item-2"] = bot.NO_REPLY_NEEDED_SENTINEL
        turn.final_item_id = "item-2"
        turn.final_text = bot.NO_REPLY_NEEDED_SENTINEL
        turn.fallback_text = turn.agent_item_text["item-1"]

        assert session._best_turn_text(turn) == turn.agent_item_text["item-1"]
    finally:
        loop.close()


def test_best_turn_text_keeps_no_reply_sentinel_for_no_reply_turns() -> None:
    bot = _load_bot_module()
    session = bot.CodexSession("channel-42")
    loop = asyncio.new_event_loop()
    try:
        turn = bot.ActiveTurn(
            turn_id="turn-1",
            started_at=0.0,
            last_activity_at=0.0,
            latest_message=_message(channel_id=42, author_id=8, content="<@999> take over", mentions=[SimpleNamespace(id=999)]),
            completion=loop.create_future(),
            reply_required=False,
        )
        turn.agent_item_text["item-1"] = "Taking over the task now."
        turn.agent_item_text["item-2"] = bot.NO_REPLY_NEEDED_SENTINEL
        turn.final_item_id = "item-2"
        turn.final_text = bot.NO_REPLY_NEEDED_SENTINEL
        turn.fallback_text = turn.agent_item_text["item-1"]

        assert session._best_turn_text(turn) == bot.NO_REPLY_NEEDED_SENTINEL
    finally:
        loop.close()


def test_bot_handoff_does_not_overwrite_latest_human_instruction() -> None:
    bot = _load_bot_module()

    async def _run() -> None:
        session = bot.CodexSession("channel-42")
        human_message = _message(channel_id=42, author_id=7, content="<@999> ship phase 4b", mentions=[SimpleNamespace(id=999)])
        teammate_message = _message(channel_id=42, author_id=8, content="<@999> backend is ready", mentions=[SimpleNamespace(id=999)])
        teammate_message.author.bot = True

        session._remember_message(human_message, authoritative=True)
        session.latest_authoritative_instruction = bot._authoritative_instruction_text(human_message, None)
        session._remember_message(teammate_message, authoritative=True)

        assert session.memory.latest_authoritative_instruction == bot._authoritative_instruction_text(human_message, None)
        assert any("backend is ready" in item for item in session.memory.recent_teammate_messages)

    asyncio.run(_run())


def test_soul_markdown_matches_repo_file() -> None:
    bot = _load_bot_module()
    expected = (bot.Path(bot.__file__).with_name("SOUL.md")).read_text(encoding="utf-8").strip()
    assert bot.SOUL_MARKDOWN == expected


def test_load_config_tolerates_utf8_bom_env_file(tmp_path) -> None:
    bot = _load_bot_module()
    env_file = tmp_path / "relay.env"
    env_file.write_bytes(
        (
            "\ufeffDISCORD_BOT_TOKEN=bom-token\n"
            f"CODEX_WORKDIR={tmp_path}\n"
            "STATE_NAMESPACE=bom-test\n"
        ).encode("utf-8")
    )
    tracked_keys = ["ENV_FILE", "DISCORD_BOT_TOKEN", "CODEX_WORKDIR", "STATE_NAMESPACE"]
    original = {key: os.environ.get(key) for key in tracked_keys}
    try:
        os.environ["ENV_FILE"] = str(env_file)
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        os.environ.pop("CODEX_WORKDIR", None)
        os.environ.pop("STATE_NAMESPACE", None)
        config = bot._load_config()
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert config.discord_bot_token == "bom-token"
    assert config.codex_workdir == tmp_path.resolve()


def test_startup_notice_marker_suppresses_repeat_notifications() -> None:
    bot = _load_bot_module()
    bot.STARTUP_NOTICE_MARKER_PATH.unlink(missing_ok=True)

    assert bot._should_send_startup_notice() is True

    bot._record_startup_notice()

    assert bot._should_send_startup_notice() is False


def test_run_preflights_before_discord_login() -> None:
    bot = _load_bot_module()
    events: list[str] = []

    async def fake_preflight() -> None:
        events.append("preflight")

    class FakeClient:
        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("exit")
            return False

        async def start(self, token: str) -> None:
            events.append(f"start:{token}")

    bot._startup_preflight = fake_preflight
    bot.client = FakeClient()

    asyncio.run(bot._run())

    assert events == ["preflight", "enter", "start:test-token", "exit"]


def test_automatic_reset_preserves_memory() -> None:
    bot = _load_bot_module()
    session = bot.CodexSession("channel-42")
    session.memory.latest_authoritative_instruction = "keep working on release hardening"
    session.memory.recent_user_messages = ["user: keep going"]

    asyncio.run(session.reset(clear_memory=False))

    assert session.memory.latest_authoritative_instruction == "keep working on release hardening"
    assert session.memory.recent_user_messages == ["user: keep going"]
