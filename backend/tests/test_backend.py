from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from relay_backend import (
    AppServerCodexBackend,
    AppServerProtocolError,
    CliResumeCodexBackend,
    build_thread_list_params,
    build_thread_resume_params,
    build_thread_start_params,
    build_turn_interrupt_params,
    build_turn_start_params,
    build_turn_steer_params,
)


class _MissingMethodError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("missing method")
        self.code = -32601


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.config = SimpleNamespace(codex_full_access=True)

    async def _request(self, method: str, params: dict | None) -> dict:
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/list":
            return {"data": [{"id": "thread-1", "status": "active", "cwd": str(self._runtime_workdir())}]}
        if method == "review/start":
            return {"turn": {"id": "review-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        return {}

    def _configured_model(self) -> str:
        return "gpt-explicit"

    def _approval_policy(self) -> str:
        return "never"

    def _sandbox_mode(self) -> str:
        return "danger-full-access"

    def _developer_instructions(self) -> str:
        return "developer"

    def _runtime_workdir(self) -> Path:
        return Path("C:/relay-worktree")

    def _turn_effort(self, kind: str, prompt_text: str = "") -> str:
        return "high"


def _schema_summary() -> dict:
    path = Path(__file__).parent / "fixtures" / "codex_app_server_schema_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_schema_compatible(method: str, params: dict) -> None:
    method_schema = _schema_summary()["methods"][method]
    allowed = set(method_schema["allowedProperties"])
    required = set(method_schema["required"])
    assert set(params).issubset(allowed)
    assert required.issubset(params)


def test_codex_app_server_payload_builders_match_schema_summary() -> None:
    session = _FakeSession()

    payloads = {
        "thread/start": build_thread_start_params(session, cwd=Path("C:/repo")),
        "thread/resume": build_thread_resume_params(session, thread_id="thread-1", cwd=Path("C:/repo")),
        "thread/list": build_thread_list_params(session),
        "turn/start": build_turn_start_params(
            session,
            thread_id="thread-1",
            prompt="do the work",
            injected_context="context",
        ),
        "turn/steer": build_turn_steer_params(
            thread_id="thread-1",
            prompt="new context",
            expected_turn_id="turn-1",
        ),
        "turn/interrupt": build_turn_interrupt_params(thread_id="thread-1", turn_id="turn-1"),
    }

    for method, params in payloads.items():
        _assert_schema_compatible(method, params)

    assert payloads["turn/start"]["input"][0]["type"] == "text"
    assert payloads["turn/steer"]["input"][0]["type"] == "text"


def test_codex_permission_profile_payloads_do_not_mix_legacy_sandbox_fields() -> None:
    session = _FakeSession()
    session._permission_profile = lambda: {"profileName": "workspace-write"}  # type: ignore[attr-defined]

    thread_payload = build_thread_start_params(session, cwd=Path("C:/repo"))
    turn_payload = build_turn_start_params(
        session,
        thread_id="thread-1",
        prompt="do it",
        injected_context="context",
    )

    assert thread_payload["permissionProfile"] == {"profileName": "workspace-write"}
    assert "sandbox" not in thread_payload
    assert "approvalPolicy" not in thread_payload
    assert turn_payload["permissionProfile"] == {"profileName": "workspace-write"}
    assert "sandboxPolicy" not in turn_payload
    assert "approvalPolicy" not in turn_payload


def test_app_server_backend_uses_current_protocol_shapes() -> None:
    session = _FakeSession()
    backend = AppServerCodexBackend(session)

    asyncio.run(backend.read_thread("thread-1"))
    asyncio.run(backend.list_threads("project-1"))
    asyncio.run(backend.compact_thread("thread-1"))
    asyncio.run(backend.start_review("thread-1"))
    asyncio.run(backend.set_thread_name("thread-1", "relay thread"))

    assert ("thread/read", {"threadId": "thread-1", "includeTurns": True}) in session.calls
    assert (
        "thread/list",
        {
            "cwd": "C:\\relay-worktree",
            "limit": 50,
            "archived": False,
            "sourceKinds": [],
            "modelProviders": [],
            "searchTerm": None,
            "sortKey": "updated_at",
        },
    ) in session.calls
    assert ("thread/compact/start", {"threadId": "thread-1"}) in session.calls
    assert (
        "review/start",
        {
            "threadId": "thread-1",
            "target": {"type": "uncommittedChanges"},
            "delivery": "inline",
        },
    ) in session.calls
    assert ("thread/name/set", {"threadId": "thread-1", "name": "relay thread"}) in session.calls


def test_app_server_backend_interrupt_uses_active_turn_when_available() -> None:
    session = _FakeSession()
    session.active_turn = SimpleNamespace(turn_id="turn-1")
    backend = AppServerCodexBackend(session)

    asyncio.run(backend.interrupt_turn("thread-1"))

    assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}) in session.calls


def test_app_server_backend_steer_uses_active_turn_precondition() -> None:
    session = _FakeSession()
    session.active_turn = SimpleNamespace(turn_id="turn-1")
    backend = AppServerCodexBackend(session)

    asyncio.run(backend.steer_turn("thread-1", "fold this in"))

    assert (
        "turn/steer",
        {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "fold this in", "text_elements": []}],
            "expectedTurnId": "turn-1",
        },
    ) in session.calls


def test_app_server_backend_raises_clear_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()

    async def _missing_method(method: str, params: dict | None) -> dict:
        raise _MissingMethodError()

    session._request = _missing_method  # type: ignore[assignment]
    monkeypatch.setattr("relay_backend.codex_cli_version", lambda: "codex-cli 0.118.0")
    backend = AppServerCodexBackend(session)

    with pytest.raises(AppServerProtocolError) as exc:
        asyncio.run(backend.compact_thread("thread-1"))

    assert "thread/compact/start" in str(exc.value)
    assert "0.118.0" in str(exc.value)


def test_cli_resume_backend_builds_real_exec_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SimpleNamespace(
        config=SimpleNamespace(codex_full_access=True),
        _configured_model=lambda: "gpt-explicit",
        _runtime_workdir=lambda: Path("C:/relay-worktree"),
    )
    backend = CliResumeCodexBackend(session, state_store=SimpleNamespace(list_threads_for_project=lambda project_id: []))
    monkeypatch.setattr("relay_backend.resolve_codex_bin", lambda: "codex.exe")
    backend.codex_bin = "codex.exe"

    fresh = backend._base_command(resume_thread_id=None)
    resumed = backend._base_command(resume_thread_id="thread-1")
    review = backend._base_command(resume_thread_id="thread-1", review=True)

    assert fresh[:2] == ["codex.exe", "exec"]
    assert resumed[:4] == ["codex.exe", "exec", "resume", "thread-1"]
    assert review[:2] == ["codex.exe", "review"]
    assert "--json" in fresh
    assert "--dangerously-bypass-approvals-and-sandbox" in fresh
