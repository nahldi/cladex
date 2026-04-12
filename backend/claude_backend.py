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
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from claude_common import claude_code_bin, claude_code_version, atomic_write_text, slugify
from relay_runtime import DurableRuntime, _extract_blocker, _extract_next_step, _now_iso

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_MODEL = "claude-opus-4-5-20251101"

PROMPT_CONTEXT_FILES: tuple[tuple[str, int], ...] = (
    ("AGENTS.md", 3000),
    ("memory/STATUS.md", 2500),
    ("memory/HANDOFF.md", 2000),
    ("memory/DECISIONS.md", 2000),
)


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

    def adopt(self, session_id: str, *, initialized: bool = True) -> None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return
        self.session_id = normalized
        self.initialized = initialized
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.last_success_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if initialized else None
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
        self.runtime = DurableRuntime(
            state_dir=state_dir,
            repo_path=workspace,
            state_namespace=state_dir.name or "default",
            agent_name="claude",
        )
        self.model = (os.environ.get("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL).strip() or DEFAULT_CLAUDE_MODEL
        self.reasoning_effort_quick = (os.environ.get("CLAUDE_REASONING_EFFORT_QUICK", "medium").strip().lower() or "medium")
        self.reasoning_effort_default = (os.environ.get("CLAUDE_REASONING_EFFORT_DEFAULT", "high").strip().lower() or "high")
        self.reasoning_effort_allow_xhigh = os.environ.get("CLAUDE_REASONING_EFFORT_ALLOW_XHIGH", "false").strip().lower() in {"1", "true", "yes", "on"}
        self._sessions: dict[str, ClaudeSession] = {}
        self._seen_channels: set[str] = set()
        self._last_session_id: str | None = None
        self._last_channel_id: str | None = None
        self._last_worktree: Path = workspace
        self._last_effort: str = self.reasoning_effort_default

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
        self.on_status("Claude ready. Durable memory and session recovery are active.")
        return True

    def stop(self) -> None:
        self._running = False
        self.interrupt()
        for channel_key in sorted(self._seen_channels):
            try:
                self.runtime.record_shutdown(channel_key, reason="Claude relay stopped.")
            except Exception:
                logger.exception("Failed to record Claude shutdown for %s", channel_key)
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

        binding = self.runtime.observe_incoming_message(
            channel_key=msg.channel_id,
            author_name=msg.sender_name,
            author_id=int(msg.sender_id) if str(msg.sender_id).isdigit() else 0,
            author_is_bot=msg.channel_type != ChannelType.DISCORD,
            text=msg.content,
        )
        if msg.channel_id not in self._seen_channels:
            self.runtime.record_startup(msg.channel_id)
            self._seen_channels.add(msg.channel_id)
        session = self._session_for_channel(msg.channel_id, binding.worktree_path)
        self.runtime.bind_thread(
            msg.channel_id,
            thread_id=session.session_id or "",
            backend="claude-print-resume",
            status="active",
        )
        prompt = self._format_prompt(msg, binding.worktree_path, self.runtime.build_context_bundle(msg.channel_id))
        self._last_channel_id = msg.channel_id
        self._last_worktree = binding.worktree_path
        self._last_session_id = session.session_id
        before_changes = self._git_status(binding.worktree_path)
        started_at = _now_iso()

        self.on_status(
            f"Claude working on {msg.channel_type.value} message from {msg.sender_name} in {binding.worktree_path.name} (effort: {self._effort_for_message(msg.content)})."
        )
        result = self._run_turn(prompt, use_resume=session.initialized, cwd=binding.worktree_path, session=session)

        if self._should_retry_fresh_session(result):
            logger.warning(
                "Claude resume failed for session %s; creating a fresh session",
                session.session_id,
            )
            self.on_status("Claude session was stale. Recreating session.")
            session.reset()
            self.runtime.bind_thread(
                msg.channel_id,
                thread_id=session.session_id or "",
                backend="claude-print-resume",
                status="rebound",
            )
            result = self._run_turn(prompt, use_resume=False, cwd=binding.worktree_path, session=session)

        after_changes = self._git_status(binding.worktree_path)
        changed_files = sorted(set(after_changes) | set(before_changes))

        if result.returncode != 0:
            failure_message = self._failure_text(result)
            self.runtime.record_turn_result(
                channel_key=msg.channel_id,
                thread_id=session.session_id or "claude-missing-session",
                turn_id=f"claude-error-{int(time.time() * 1000)}",
                summary=f"Claude turn failed: {failure_message}",
                files_changed=changed_files,
                commands_run=[self._display_command(result.args)],
                validations=[],
                blocker=failure_message,
                next_step="Retry the same task from durable memory after fixing the Claude CLI failure.",
                command_exit_codes=[result.returncode],
                cwd=str(binding.worktree_path),
                approvals=[],
                error_category="claude-cli-error",
                started_at=started_at,
                completed_at=_now_iso(),
                backend="claude-print-resume",
                degraded=False,
            )
            self._report_failure(result)
            return False

        content = self._extract_response_text(result.stdout)
        if not content.strip():
            self.runtime.record_turn_result(
                channel_key=msg.channel_id,
                thread_id=session.session_id or "claude-missing-session",
                turn_id=f"claude-empty-{int(time.time() * 1000)}",
                summary="Claude returned no text.",
                files_changed=changed_files,
                commands_run=[self._display_command(result.args)],
                validations=[],
                blocker="Claude returned no text.",
                next_step="Retry the same task from durable memory and inspect the Claude CLI output.",
                command_exit_codes=[result.returncode],
                cwd=str(binding.worktree_path),
                approvals=[],
                error_category="empty-response",
                started_at=started_at,
                completed_at=_now_iso(),
                backend="claude-print-resume",
                degraded=False,
            )
            self._report_failure(result, default_message="Claude returned no text.")
            return False

        session.mark_success()
        self.runtime.bind_thread(
            msg.channel_id,
            thread_id=session.session_id or "",
            backend="claude-print-resume",
            status="active",
        )
        self._last_session_id = session.session_id
        validations = self._extract_validation_lines(content)
        commands_run = [self._display_command(result.args), *self._extract_command_lines(content)]
        summary = self._summarize_response(content)
        next_step = _extract_next_step(content) or "Continue from STATUS.md and the latest handoff."
        blocker = _extract_blocker(content)
        self.runtime.record_turn_result(
            channel_key=msg.channel_id,
            thread_id=session.session_id or "claude-missing-session",
            turn_id=f"claude-{int(time.time() * 1000)}",
            summary=summary,
            files_changed=changed_files,
            commands_run=commands_run[:8],
            validations=validations[:8],
            blocker=blocker,
            next_step=next_step,
            command_exit_codes=[result.returncode],
            cwd=str(binding.worktree_path),
            approvals=[],
            error_category="",
            started_at=started_at,
            completed_at=_now_iso(),
            backend="claude-print-resume",
            degraded=False,
        )
        self.on_response(
            OutboundMessage(
                channel_type=msg.channel_type,
                channel_id=msg.channel_id,
                content=content,
                reply_to=msg.message_id,
                is_final=True,
            )
        )
        self.on_status("Claude turn complete.")
        return True

    def _format_prompt(self, msg: InboundMessage, prompt_workspace: Path, durable_bundle: str) -> str:
        effort = self._effort_for_message(msg.content)
        self._last_effort = effort
        parts = [
            (
                "You are Claude running inside CLADEX as a durable coding relay.\n"
                "Rules:\n"
                "- Discord is transport, not memory.\n"
                "- Repo files, AGENTS.md, memory/*, code, tests, and git state are the source of truth.\n"
                "- Verify claims from other agents before repeating them as fact.\n"
                "- In shared team channels, default to caveman mode: facts, decisions, blockers, results.\n"
                "- No filler, no agreement-only replies, no loop chatter, no fake completion claims.\n"
                "- Use the lightest path that solves the task. Do not burn tools or context without reason.\n"
                "- Keep replies compact and operationally useful.\n"
                "- Before answering, check AGENTS.md, memory/*, code, tests, and git state in the current worktree.\n"
                "- After meaningful progress, make sure durable memory and handoff remain truthful.\n"
                "- If another AI made a claim, verify it before trusting it.\n"
                f"- Current relay effort policy for this turn: {effort}."
            )
        ]
        parts.append(f"Durable runtime context:\n{durable_bundle}")
        durable_context = self._durable_context(prompt_workspace)
        if durable_context:
            parts.append(f"Relevant repo documents:\n{durable_context}")
        parts.append(
            "\n".join(
                [
                    "Inbound message context:",
                    f"- channel_type: {msg.channel_type.value}",
                    f"- sender: {msg.sender_name} ({msg.sender_id})",
                    f"- relay workspace: {self.workspace}",
                    f"- active worktree: {prompt_workspace}",
                ]
            )
        )
        if msg.attachments:
            parts.append(f"Attachments: {len(msg.attachments)} files")
        parts.append(f"User message:\n{msg.content.strip()}")
        return "\n\n".join(part for part in parts if part)

    def _durable_context(self, prompt_workspace: Path) -> str:
        sections: list[str] = []
        for relative_path, limit in PROMPT_CONTEXT_FILES:
            path = prompt_workspace / relative_path
            if not path.exists() or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not content:
                continue
            if len(content) > limit:
                content = content[:limit].rstrip() + "\n...[truncated]"
            sections.append(f"[{relative_path}]\n{content}")
        return "\n\n".join(sections)

    def _build_command(self, prompt: str, *, use_resume: bool, session: ClaudeSession) -> list[str]:
        cmd = [
            claude_code_bin(),
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            self.model,
            "--dangerously-skip-permissions",
        ]
        if use_resume:
            cmd.extend(["--resume", session.session_id])
        else:
            cmd.extend(["--session-id", session.session_id])
        cmd.append(prompt)
        return cmd

    def _run_turn(self, prompt: str, *, use_resume: bool, cwd: Path, session: ClaudeSession) -> CommandResult:
        cmd = self._build_command(prompt, use_resume=use_resume, session=session)
        env = os.environ.copy()
        env["CLAUDE_CODE_ENTRYPOINT"] = "cladex"
        env["CLADEX_ACTIVE_WORKTREE"] = str(cwd)

        kwargs = {
            "cwd": str(cwd),
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
                # Skip non-JSON lines (verbose debug output)
                continue
            text = self._extract_text_from_event(event)
            if text:
                text_parts.append(text)

        collapsed = "".join(text_parts).strip()
        return collapsed

    def _extract_text_from_event(self, event: dict) -> str:
        event_type = event.get("type", "")

        # Only extract from streaming deltas to avoid duplication
        # Final message/assistant events duplicate the delta content
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            return delta.get("text", "")

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
            "no conversation found",
            "resume",
        ]
        return any(needle in haystack for needle in needles)

    def _report_failure(self, result: CommandResult, *, default_message: str | None = None) -> None:
        message = default_message or self._failure_text(result)
        self.on_status(f"ERROR: {message}")
        logger.error(
            "Claude command failed (rc=%s, resume=%s): %s",
            result.returncode,
            result.used_resume,
            " ".join(result.args),
        )

    def _session_for_channel(self, channel_key: str, prompt_workspace: Path) -> ClaudeSession:
        channel_id = slugify(channel_key)
        session = self._sessions.get(channel_key)
        if session is None:
            session = ClaudeSession(self.state_dir / "channels" / channel_id, prompt_workspace)
            rebound_thread = self.runtime.active_thread_id(channel_key)
            if rebound_thread and (not session.initialized or session.session_id != rebound_thread):
                session.adopt(rebound_thread, initialized=True)
            self._sessions[channel_key] = session
        return session

    def _git_status(self, cwd: Path) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), "status", "--short"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        paths: list[str] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[-1].strip()
            if path:
                paths.append(path.replace("\\", "/"))
        return sorted(dict.fromkeys(paths))

    def _failure_text(self, result: CommandResult) -> str:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        return stderr or stdout or f"Claude command failed with exit code {result.returncode}"

    def _display_command(self, args: list[str]) -> str:
        if not args:
            return "claude"
        if len(args) <= 7:
            return " ".join(args)
        visible = [*args[:7], "...<prompt>"]
        return " ".join(visible)

    def _extract_command_lines(self, text: str) -> list[str]:
        commands: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
                commands.append(stripped.strip("`"))
                continue
            if stripped.lower().startswith(("ran ", "run ", "command:")):
                commands.append(stripped)
        return list(dict.fromkeys(commands))

    def _extract_validation_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        pattern = re.compile(r"(pytest|test|tests|lint|build|tsc|vitest|passed|failed|green)", re.IGNORECASE)
        for line in text.splitlines():
            stripped = line.strip(" -*")
            if stripped and pattern.search(stripped):
                lines.append(stripped)
        return list(dict.fromkeys(lines))

    def _summarize_response(self, text: str) -> str:
        for chunk in re.split(r"\n\s*\n", text.strip()):
            normalized = " ".join(chunk.split())
            if normalized:
                return normalized[:280]
        return text.strip()[:280] or "Claude completed the turn."

    def _effort_for_message(self, text: str) -> str:
        normalized = text.strip().lower()
        quick_markers = (
            "status",
            "what changed",
            "what happened",
            "why",
            "explain",
            "list",
            "show",
        )
        hard_markers = (
            "implement",
            "fix",
            "repair",
            "refactor",
            "audit",
            "verify",
            "durable",
            "architecture",
            "multi-file",
            "long session",
            "restart",
            "compaction",
        )
        hardest_markers = (
            "full rearchitecture",
            "deep research",
            "full audit",
            "large refactor",
            "cross repo",
        )
        if any(marker in normalized for marker in quick_markers):
            return self.reasoning_effort_quick
        if self.reasoning_effort_allow_xhigh and any(marker in normalized for marker in hardest_markers):
            return "xhigh"
        if any(marker in normalized for marker in hard_markers) or len(normalized) > 500:
            return self.reasoning_effort_default
        return self.reasoning_effort_quick

    @property
    def session_id(self) -> str | None:
        return self._last_session_id

    @property
    def current_worktree(self) -> str:
        return str(self._last_worktree)

    @property
    def current_channel(self) -> str | None:
        return self._last_channel_id

    @property
    def effort(self) -> str:
        return self._last_effort

    @property
    def configured_model(self) -> str:
        return self.model


class RelayBackend:
    """
    High-level relay backend for Discord.

    GUI chat is intentionally out of scope for this repo; the canonical manager
    lives in CLADEX.
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
        self._pending_local: dict[str, asyncio.Future[str]] = {}

    @property
    def session_id(self) -> str | None:
        return self._claude.session_id

    @property
    def current_worktree(self) -> str:
        return self._claude.current_worktree

    @property
    def current_channel(self) -> str | None:
        return self._claude.current_channel

    @property
    def effort(self) -> str:
        return self._claude.effort

    @property
    def configured_model(self) -> str:
        return self._claude.configured_model

    def _route_response(self, msg: OutboundMessage) -> None:
        if msg.channel_type == ChannelType.DISCORD:
            self._on_discord(msg.channel_id, msg.content)
            return
        future = self._pending_local.get(msg.reply_to)
        if future and not future.done():
            future.set_result(msg.content)

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

    async def send_local_message(
        self,
        *,
        channel_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
    ) -> str:
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_local[request_id] = future
        await self._message_queue.put(
            InboundMessage(
                channel_type=ChannelType.GUI,
                channel_id=channel_id,
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                message_id=request_id,
            )
        )
        try:
            return await asyncio.wait_for(future, timeout=600)
        finally:
            self._pending_local.pop(request_id, None)

    async def _process_messages(self) -> None:
        while True:
            try:
                msg = await self._message_queue.get()
                ok = await asyncio.to_thread(self._claude.process_message, msg)
                if msg.channel_type == ChannelType.GUI:
                    future = self._pending_local.get(msg.message_id)
                    if future and not future.done() and not ok:
                        future.set_exception(RuntimeError("Claude local operator turn failed."))
                self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error processing message")
                self._on_status(f"ERROR: {exc}")
