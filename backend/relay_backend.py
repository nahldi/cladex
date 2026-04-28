from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_common import codex_cli_version, relay_codex_env, resolve_codex_bin


@dataclass(slots=True)
class BackendThread:
    thread_id: str
    backend: str
    status: str = "active"
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class BackendTurn:
    turn_id: str
    status: str = "started"
    metadata: dict[str, Any] | None = None


class BackendUnavailableError(RuntimeError):
    pass


class AppServerProtocolError(BackendUnavailableError):
    pass


class CodexBackend(ABC):
    @abstractmethod
    async def create_thread(self, channel_binding) -> BackendThread:
        raise NotImplementedError

    @abstractmethod
    async def resume_thread(self, thread_id: str) -> BackendThread:
        raise NotImplementedError

    @abstractmethod
    async def fork_thread(self, thread_id: str) -> BackendThread:
        raise NotImplementedError

    @abstractmethod
    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def list_threads(self, project_id: str) -> list[BackendThread]:
        raise NotImplementedError

    @abstractmethod
    async def start_turn(self, thread_id: str, prompt: str, injected_context: str) -> BackendTurn:
        raise NotImplementedError

    @abstractmethod
    async def steer_turn(self, thread_id: str, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def interrupt_turn(self, thread_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def start_review(self, thread_id: str) -> BackendTurn:
        raise NotImplementedError

    @abstractmethod
    async def compact_thread(self, thread_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def set_thread_name(self, thread_id: str, name: str) -> dict[str, Any]:
        raise NotImplementedError


def codex_text_input(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "text_elements": []}]


def _session_permission_profile(session) -> dict[str, Any] | None:
    provider = getattr(session, "_permission_profile", None)
    if provider is None:
        return None
    profile = provider()
    return profile if isinstance(profile, dict) and profile else None


def _thread_permission_fields(session) -> dict[str, Any]:
    permission_profile = _session_permission_profile(session)
    if permission_profile is not None:
        return {"permissionProfile": permission_profile}
    return {
        "approvalPolicy": session._approval_policy(),
        "approvalsReviewer": None,
        "sandbox": session._sandbox_mode(),
    }


def _turn_permission_fields(session) -> dict[str, Any]:
    permission_profile = _session_permission_profile(session)
    if permission_profile is not None:
        return {"permissionProfile": permission_profile}
    return {
        "approvalPolicy": None,
        "approvalsReviewer": None,
        "sandboxPolicy": None,
    }


def build_thread_start_params(session, *, cwd: Path) -> dict[str, Any]:
    params = {
        "model": session._configured_model(),
        "modelProvider": None,
        "serviceTier": None,
        "cwd": str(cwd),
        "config": None,
        "serviceName": "discord-codex-relay",
        "baseInstructions": None,
        "developerInstructions": session._developer_instructions(),
        "personality": None,
        "ephemeral": False,
        "experimentalRawEvents": False,
        "persistExtendedHistory": True,
    }
    params.update(_thread_permission_fields(session))
    return params


def build_thread_resume_params(session, *, thread_id: str, cwd: Path) -> dict[str, Any]:
    params = {
        "threadId": thread_id,
        "history": None,
        "path": None,
        "model": session._configured_model(),
        "modelProvider": None,
        "serviceTier": None,
        "cwd": str(cwd),
        "config": None,
        "baseInstructions": None,
        "developerInstructions": session._developer_instructions(),
        "personality": None,
        "persistExtendedHistory": True,
    }
    params.update(_thread_permission_fields(session))
    return params


def build_thread_list_params(session, *, limit: int = 50) -> dict[str, Any]:
    return {
        "cwd": str(session._runtime_workdir()),
        "limit": limit,
        "archived": False,
        "sourceKinds": [],
        "modelProviders": [],
        "searchTerm": None,
        "sortKey": "updated_at",
    }


def build_turn_start_params(
    session,
    *,
    thread_id: str,
    prompt: str,
    injected_context: str,
) -> dict[str, Any]:
    params = {
        "threadId": thread_id,
        "input": codex_text_input(f"{injected_context}\n\n{prompt}"),
        "cwd": None,
        "model": None,
        "serviceTier": None,
        "effort": session._turn_effort("implementation"),
        "summary": None,
        "personality": None,
        "outputSchema": None,
        "collaborationMode": None,
    }
    params.update(_turn_permission_fields(session))
    return params


def build_turn_steer_params(*, thread_id: str, prompt: str, expected_turn_id: str | None = None) -> dict[str, Any]:
    params = {"threadId": thread_id, "input": codex_text_input(prompt)}
    if expected_turn_id:
        params["expectedTurnId"] = expected_turn_id
    return params


def build_turn_interrupt_params(*, thread_id: str, turn_id: str | None = None) -> dict[str, Any]:
    if turn_id:
        return {"threadId": thread_id, "turnId": turn_id}
    return {"threadId": thread_id}


class AppServerCodexBackend(CodexBackend):
    def __init__(self, session) -> None:
        self.session = session

    async def _invoke(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return await self.session._request(method, params)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == -32601:
                raise AppServerProtocolError(
                    f"Local Codex app-server does not support `{method}`. Upgrade Codex CLI. "
                    f"Detected: {codex_cli_version()}."
                ) from exc
            raise

    async def create_thread(self, channel_binding) -> BackendThread:
        response = await self._invoke(
            "thread/start",
            build_thread_start_params(self.session, cwd=Path(channel_binding.worktree_path)),
        )
        thread = response.get("thread") or {}
        return BackendThread(
            thread_id=str(thread.get("id", "")),
            backend="codex-app-server",
            metadata=response,
        )

    async def resume_thread(self, thread_id: str) -> BackendThread:
        response = await self._invoke(
            "thread/resume",
            build_thread_resume_params(self.session, thread_id=thread_id, cwd=self.session._runtime_workdir()),
        )
        thread = response.get("thread") or {}
        return BackendThread(
            thread_id=str(thread.get("id", thread_id)),
            backend="codex-app-server",
            metadata=response,
        )

    async def fork_thread(self, thread_id: str) -> BackendThread:
        response = await self._invoke("thread/fork", {"threadId": thread_id})
        thread = response.get("thread") or {}
        return BackendThread(
            thread_id=str(thread.get("id", "")),
            backend="codex-app-server",
            metadata=response,
        )

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._invoke("thread/read", {"threadId": thread_id, "includeTurns": True})

    async def list_threads(self, project_id: str) -> list[BackendThread]:
        response = await self._invoke(
            "thread/list",
            build_thread_list_params(self.session),
        )
        items = response.get("data") or response.get("threads") or []
        return [
            BackendThread(
                thread_id=str(item.get("id", "")),
                backend="codex-app-server",
                status=str(item.get("status", "active")),
                metadata=item,
            )
            for item in items
            if item.get("id")
        ]

    async def start_turn(self, thread_id: str, prompt: str, injected_context: str) -> BackendTurn:
        response = await self._invoke(
            "turn/start",
            build_turn_start_params(
                self.session,
                thread_id=thread_id,
                prompt=prompt,
                injected_context=injected_context,
            ),
        )
        turn = response.get("turn") or {}
        return BackendTurn(turn_id=str(turn.get("id", "")), metadata=response)

    async def steer_turn(self, thread_id: str, prompt: str) -> dict[str, Any]:
        active_turn = getattr(self.session, "active_turn", None)
        expected_turn_id = getattr(active_turn, "turn_id", None)
        return await self._invoke(
            "turn/steer",
            build_turn_steer_params(thread_id=thread_id, prompt=prompt, expected_turn_id=expected_turn_id),
        )

    async def interrupt_turn(self, thread_id: str) -> dict[str, Any]:
        active_turn = getattr(self.session, "active_turn", None)
        turn_id = getattr(active_turn, "turn_id", None)
        return await self._invoke(
            "turn/interrupt",
            build_turn_interrupt_params(thread_id=thread_id, turn_id=turn_id),
        )

    async def start_review(self, thread_id: str) -> BackendTurn:
        response = await self._invoke(
            "review/start",
            {
                "threadId": thread_id,
                "target": {"type": "uncommittedChanges"},
                "delivery": "inline",
            },
        )
        turn = response.get("turn") or response.get("review") or {}
        return BackendTurn(turn_id=str(turn.get("id", "")), metadata=response)

    async def compact_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._invoke("thread/compact/start", {"threadId": thread_id})

    async def set_thread_name(self, thread_id: str, name: str) -> dict[str, Any]:
        return await self._invoke("thread/name/set", {"threadId": thread_id, "name": name})


class CliResumeCodexBackend(CodexBackend):
    def __init__(self, session, state_store) -> None:
        self.session = session
        self.state_store = state_store
        self.codex_bin = resolve_codex_bin()

    async def create_thread(self, channel_binding) -> BackendThread:
        thread_id = self.state_store.synthetic_thread_id(channel_binding.channel_id)
        return BackendThread(thread_id=thread_id, backend="codex-cli-resume", status="pending")

    async def resume_thread(self, thread_id: str) -> BackendThread:
        return BackendThread(thread_id=thread_id, backend="codex-cli-resume", status="pending")

    async def fork_thread(self, thread_id: str) -> BackendThread:
        fork_id = self.state_store.synthetic_thread_id(f"fork-{thread_id}")
        return BackendThread(thread_id=fork_id, backend="codex-cli-resume", status="pending")

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        return {"thread": {"id": thread_id, "backend": "codex-cli-resume", "status": "pending"}}

    async def list_threads(self, project_id: str) -> list[BackendThread]:
        rows = self.state_store.list_threads_for_project(project_id)
        return [
            BackendThread(
                thread_id=row["thread_id"],
                backend=row["backend"],
                status=row["status"],
                metadata=row,
            )
            for row in rows
        ]

    def _base_command(self, *, resume_thread_id: str | None, review: bool = False) -> list[str]:
        if review:
            command = [self.codex_bin, "review"]
        elif resume_thread_id:
            command = [self.codex_bin, "exec", "resume", resume_thread_id]
        else:
            command = [self.codex_bin, "exec"]
        if self.session._configured_model():
            command.extend(["--model", self.session._configured_model()])
        command.extend(["--cd", str(self.session._runtime_workdir()), "--json", "--skip-git-repo-check"])
        if self.session.config.codex_full_access:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])
        return command

    @staticmethod
    def _windows_hidden_subprocess_kwargs() -> dict[str, object]:
        if os.name != "nt":
            return {}
        return {"creationflags": subprocess.CREATE_NO_WINDOW}

    async def _run_cli_turn(
        self,
        *,
        prompt_text: str,
        resume_thread_id: str | None = None,
        review: bool = False,
    ) -> tuple[str, str, dict[str, Any]]:
        output_dir = self.session.config.state_dir / "fallback"
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = uuid.uuid4().hex[:10]
        last_message_path = output_dir / f"{suffix}-last.txt"
        events_path = output_dir / f"{suffix}-events.jsonl"
        command = self._base_command(resume_thread_id=resume_thread_id, review=review)
        command.extend(["-o", str(last_message_path), "-"])
        env = relay_codex_env(self.session._runtime_workdir(), os.environ.copy())
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.session._runtime_workdir()),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **self._windows_hidden_subprocess_kwargs(),
        )
        stdout, _ = await process.communicate(prompt_text.encode("utf-8"))
        raw_output = stdout.decode("utf-8", errors="replace")
        events_path.write_text(raw_output, encoding="utf-8")
        thread_id = resume_thread_id or ""
        events: list[dict[str, Any]] = []
        for raw_line in raw_output.splitlines():
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(payload)
            if payload.get("type") == "thread.started" and payload.get("thread_id"):
                thread_id = str(payload["thread_id"])
        if process.returncode not in {0, None}:
            raise BackendUnavailableError(
                f"Degraded Codex CLI fallback failed with exit code {process.returncode}."
            )
        last_message = last_message_path.read_text(encoding="utf-8").strip() if last_message_path.exists() else ""
        metadata = {
            "events": events,
            "raw_output": raw_output,
            "last_message_path": str(last_message_path),
            "events_path": str(events_path),
            "thread_id": thread_id,
            "degraded": True,
        }
        return thread_id, last_message, metadata

    async def start_turn(self, thread_id: str, prompt: str, injected_context: str) -> BackendTurn:
        next_thread_id, last_message, metadata = await self._run_cli_turn(
            prompt_text=f"{injected_context}\n\n{prompt}",
            resume_thread_id=thread_id if thread_id and not thread_id.startswith("cli-") else None,
        )
        metadata["reply_text"] = last_message
        return BackendTurn(
            turn_id=f"cli-turn-{uuid.uuid4().hex[:8]}",
            status="completed",
            metadata=metadata,
        )

    async def steer_turn(self, thread_id: str, prompt: str) -> dict[str, Any]:
        raise BackendUnavailableError("Degraded Codex CLI fallback cannot steer a live turn.")

    async def interrupt_turn(self, thread_id: str) -> dict[str, Any]:
        return {"threadId": thread_id, "interrupted": False, "backend": "codex-cli-resume"}

    async def start_review(self, thread_id: str) -> BackendTurn:
        next_thread_id, last_message, metadata = await self._run_cli_turn(
            prompt_text="Review the current uncommitted changes and report only the highest-signal findings.",
            resume_thread_id=thread_id if thread_id and not thread_id.startswith("cli-") else None,
            review=True,
        )
        metadata["reply_text"] = last_message
        metadata["thread_id"] = next_thread_id
        return BackendTurn(
            turn_id=f"cli-review-{uuid.uuid4().hex[:8]}",
            status="completed",
            metadata=metadata,
        )

    async def compact_thread(self, thread_id: str) -> dict[str, Any]:
        return {"threadId": thread_id, "event": "compaction-requested", "backend": "codex-cli-resume", "degraded": True}

    async def set_thread_name(self, thread_id: str, name: str) -> dict[str, Any]:
        return {"threadId": thread_id, "name": name, "backend": "codex-cli-resume", "degraded": True}
