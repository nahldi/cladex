#!/usr/bin/env python3
"""
Discord Claude Relay - Backend

Runs Claude Code in one-shot print mode per turn.

Why this shape:
- `claude -p` is a one-shot command that prints a response and exits
- durable continuity comes from explicit session creation + later `--resume`
- Discord relay and GUI chat are separate sessions unless they share the same
  persisted Claude session ID on purpose
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from claude_common import claude_code_bin, claude_code_version, atomic_write_text

logger = logging.getLogger(__name__)


class ChannelType(Enum):
    DISCORD = "discord"
    GUI = "gui"
    TERMINAL = "terminal"


@dataclass
class InboundMessage:
    """Message coming into the relay from any channel."""

    channel_type: ChannelType
    channel_id: str
    sender_id: str
    sender_name: str
    content: str
    message_id: str = ""
    reply_to: str = ""
    attachments: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class OutboundMessage:
    """Response going out from Claude to a channel."""

    channel_type: ChannelType
    channel_id: str
    content: str
    reply_to: str = ""
    is_final: bool = True


@dataclass
class CommandResult:
    """Structured result from a one-shot Claude CLI invocation."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    used_resume: bool


class ClaudeSession:
    """
    Persists the Claude session identifier for the relay.

    The first successful turn uses `--session-id <uuid>`.
    Later turns use `--resume <session_id>`.
    """

    def __init__(self, state_dir: Path, workspace: Path):
        self.state_dir = state_dir
        self.workspace = workspace
        self.session_file = state_dir / "claude_session.json"
        self.session_id: str | None = None
        self.initialized = False
        self.created_at: str | None = None
        self.last_success_at: str | None = None
        self._load_session()

    def _load_session(self) -> None:
        if self.session_file.exists():
            try:
                data = json.loads(self.session_file.read_text(encoding="utf-8"))
                self.session_id = data.get("session_id")
                self.initialized = bool(data.get("initialized", False))
                self.created_at = data.get("created_at")
                self.last_success_at = data.get("last_success_at")
            except Exception as exc:
                logger.warning("Failed to load Claude session file: %s", exc)
                self.session_id = None
                self.initialized = False

        if not self.session_id:
            self.reset()

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "initialized": self.initialized,
            "created_at": self.created_at,
            "last_success_at": self.last_success_at,
        }
        atomic_write_text(self.session_file, json.dumps(payload, indent=2))

    def reset(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.initialized = False
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.last_success_at = None
        self._save()

    def mark_success(self) -> None:
        self.initialized = True
        self.last_success_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save()


class ClaudeBackend:
    """
    Executes one Claude CLI turn per inbound relay message.

    This backend does not try to keep a `claude -p` subprocess alive, because
    print mode is explicitly one-shot.
    """

    def __init__(
        self,
        workspace: Path,
        state_dir: Path,
        on_response: Callable[[OutboundMessage], None],
        on_status: Callable[[str], None] | None = None,
    ):
        self.workspace = workspace
        self.state_dir = state_dir
        self.on_response = on_response
        self.on_status = on_status or (lambda s: None)
        self.session = ClaudeSession(state_dir, workspace)

        self._running = False
        self._process_lock = threading.Lock()
        self._active_process: subprocess.Popen | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True
        version = claude_code_version()
        if "unknown" in version.lower():
            logger.error("Claude Code CLI not found")
            self.on_status("ERROR: Claude Code CLI not found")
            return False
        self._running = True
        self.on_status(f"Claude ready (session: {self.session.session_id[:8]}...)")
        return True

    def stop(self) -> None:
        self._running = False
        self.interrupt()
        self.on_status("Claude stopped")

    def interrupt(self) -> None:
        with self._process_lock:
            process = self._active_process
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception:
                pass

    def process_message(self, msg: InboundMessage) -> bool:
        if not self.start():
            return False

        prompt = self._format_prompt(msg)
        result = self._run_turn(prompt, use_resume=self.session.initialized)

        if self._should_retry_fresh_session(result):
            logger.warning(
                "Claude resume failed for session %s; creating a fresh session",
                self.session.session_id,
            )
            self.on_status("Claude session was stale. Recreating session.")
            self.session.reset()
            result = self._run_turn(prompt, use_resume=False)

        if result.returncode != 0:
            self._report_failure(result)
            return False

        content = self._extract_response_text(result.stdout)
        if not content.strip():
            self._report_failure(result, default_message="Claude returned no text.")
            return False

        self.session.mark_success()
        self.on_response(
            OutboundMessage(
                channel_type=msg.channel_type,
                channel_id=msg.channel_id,
                content=content,
                is_final=True,
            )
        )
        return True

    def _format_prompt(self, msg: InboundMessage) -> str:
        parts = [msg.content.strip()]
        if msg.attachments:
            parts.append(f"(Attachments: {len(msg.attachments)} files)")
        return "\n\n".join(part for part in parts if part)

    def _build_command(self, prompt: str, *, use_resume: bool) -> list[str]:
        cmd = [
            claude_code_bin(),
            "-p",
            "--output-format",
            "stream-json",
        ]
        if use_resume:
            cmd.extend(["--resume", self.session.session_id])
        else:
            cmd.extend(["--session-id", self.session.session_id])
        cmd.append(prompt)
        return cmd

    def _run_turn(self, prompt: str, *, use_resume: bool) -> CommandResult:
        cmd = self._build_command(prompt, use_resume=use_resume)
        env = os.environ.copy()
        env["CLAUDE_CODE_ENTRYPOINT"] = "discord-claude-relay"

        kwargs = {
            "cwd": str(self.workspace),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(cmd, **kwargs)
        with self._process_lock:
            self._active_process = process
        try:
            stdout, stderr = process.communicate()
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None

        return CommandResult(
            args=cmd,
            returncode=process.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            used_resume=use_resume,
        )

    def _extract_response_text(self, stdout: str) -> str:
        text_parts: list[str] = []
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                text_parts.append(line)
                continue
            text = self._extract_text_from_event(event)
            if text:
                text_parts.append(text)

        collapsed = "".join(text_parts).strip()
        if collapsed:
            return collapsed
        return stdout.strip()

    def _extract_text_from_event(self, event: dict) -> str:
        event_type = event.get("type", "")

        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            return delta.get("text", "")

        if event_type == "message":
            parts: list[str] = []
            for block in event.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "".join(parts)

        if event_type == "assistant":
            message = event.get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
            return event.get("content", "")

        if event_type == "result":
            return event.get("result", "") or event.get("text", "")

        if event_type == "error":
            error = event.get("error", {})
            if isinstance(error, dict):
                return error.get("message", "")
            return str(error)

        return ""

    def _should_retry_fresh_session(self, result: CommandResult) -> bool:
        if result.returncode == 0 or not result.used_resume:
            return False
        haystack = f"{result.stdout}\n{result.stderr}".lower()
        needles = [
            "session not found",
            "could not find session",
            "unknown session",
            "invalid session",
            "resume",
        ]
        return any(needle in haystack for needle in needles)

    def _report_failure(self, result: CommandResult, *, default_message: str | None = None) -> None:
        message = default_message
        if not message:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            message = stderr or stdout or f"Claude command failed with exit code {result.returncode}"
        self.on_status(f"ERROR: {message}")
        logger.error(
            "Claude command failed (rc=%s, resume=%s): %s",
            result.returncode,
            result.used_resume,
            " ".join(result.args),
        )


class RelayBackend:
    """
    High-level relay backend for Discord.

    GUI chat is intentionally out of scope for this repo; the canonical manager
    lives in discord-codex-relay.
    """

    def __init__(
        self,
        workspace: Path,
        state_dir: Path,
        on_discord_response: Callable[[str, str], None],
        on_status: Callable[[str], None] | None = None,
    ):
        self.workspace = workspace
        self.state_dir = state_dir
        self._on_discord = on_discord_response
        self._on_status = on_status or (lambda s: None)

        self._claude = ClaudeBackend(
            workspace=workspace,
            state_dir=state_dir,
            on_response=self._route_response,
            on_status=self._on_status,
        )

        self._message_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    @property
    def session_id(self) -> str | None:
        return self._claude.session.session_id

    def _route_response(self, msg: OutboundMessage) -> None:
        if msg.channel_type == ChannelType.DISCORD:
            self._on_discord(msg.channel_id, msg.content)

    async def start(self) -> bool:
        if not self._claude.start():
            return False
        self._worker_task = asyncio.create_task(self._process_messages())
        return True

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._claude.stop()

    async def send_discord_message(
        self,
        channel_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        message_id: str = "",
    ) -> None:
        await self._message_queue.put(
            InboundMessage(
                channel_type=ChannelType.DISCORD,
                channel_id=channel_id,
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                message_id=message_id,
            )
        )

    async def _process_messages(self) -> None:
        while True:
            try:
                msg = await self._message_queue.get()
                await asyncio.to_thread(self._claude.process_message, msg)
                self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error processing message")
                self._on_status(f"ERROR: {exc}")
