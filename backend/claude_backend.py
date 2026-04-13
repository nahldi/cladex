#!/usr/bin/env python3
"""
Discord Claude Relay - Backend

Runs Claude Code through a persistent subprocess in streaming JSON mode.

Why this shape:
- One persistent Claude CLI subprocess per relay channel/worktree
- Uses stream-json input/output for bidirectional communication
- Subprocess runs with CREATE_NO_WINDOW on Windows to prevent terminal spam
- Each Discord turn is sent into the persistent session for that channel
- Durable continuity comes from session persistence and resume flags
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

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

# Lightweight context for simple coordination messages
LIGHTWEIGHT_CONTEXT_FILES: tuple[tuple[str, int], ...] = (
    ("memory/STATUS.md", 800),
)

# Patterns that indicate a lightweight coordination message (yes/no, ack, status check)
LIGHTWEIGHT_MESSAGE_PATTERNS: tuple[str, ...] = (
    "yes",
    "no",
    "ok",
    "okay",
    "ack",
    "acknowledged",
    "confirmed",
    "done",
    "ready",
    "waiting",
    "here",
    "present",
    "understood",
    "got it",
    "will do",
    "on it",
    "working",
    "ping",
    "pong",
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
    """Structured result from one Claude transport turn."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    used_resume: bool


def _windows_hidden_subprocess_kwargs() -> dict[str, object]:
    """Return subprocess kwargs to hide the window on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


@dataclass
class PersistentClaudeProcess:
    """Per-channel persistent Claude subprocess bound to one worktree."""

    session: ClaudeSession
    worktree: Path
    process: asyncio.subprocess.Process | None = None
    reader_task: asyncio.Task | None = None
    loop_id: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    response_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    closing: bool = False


class ClaudeSession:
    """
    Persists the Claude session identifier for the relay.

    Each inbound Discord turn is sent into this same Claude session id until the
    relay explicitly resets it for recovery.
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
    Executes relay turns through a persistent Claude subprocess per channel.
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
        self._persistent_processes: dict[str, PersistentClaudeProcess] = {}
        self._seen_channels: set[str] = set()
        self._last_session_id: str | None = None
        self._last_channel_id: str | None = None
        self._last_worktree: Path = workspace
        self._last_effort: str = self.reasoning_effort_default

        self._running = False
        self._active_channel_id: str | None = None

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
        self.runtime.record_restart_event(reason="process-startup")
        self._running = True
        # Check for restart churn
        if self.runtime.is_restart_churn():
            restart_count = self.runtime.count_recent_restarts()
            logger.warning("Restart churn detected: %d restarts in last 5 minutes", restart_count)
            self.on_status(f"WARNING: Restart churn detected ({restart_count} restarts in 5min). Check relay logs.")
        else:
            self.on_status("Claude ready (persistent subprocess mode). Durable memory and session recovery are active.")
        return True

    async def stop(self) -> None:
        self._running = False
        await self.interrupt()
        for persistent in self._persistent_processes.values():
            await self._terminate_process(persistent)
        for channel_key in sorted(self._seen_channels):
            try:
                self.runtime.record_shutdown(channel_key, reason="Claude relay stopped.")
            except Exception:
                logger.exception("Failed to record Claude shutdown for %s", channel_key)
        self.on_status("Claude stopped")

    async def interrupt(self) -> None:
        active = self._persistent_processes.get(self._active_channel_id or "")
        if not active or active.process is None:
            return
        # Send interrupt signal via stdin if process is active
        try:
            if active.process.stdin and active.process.returncode is None:
                # Send a cancel message in stream-json format
                cancel_msg = json.dumps({"type": "cancel"}) + "\n"
                active.process.stdin.write(cancel_msg.encode("utf-8"))
                await active.process.stdin.drain()
        except Exception:
            logger.exception("Failed to interrupt persistent Claude process")

    async def process_message(self, msg: InboundMessage) -> bool:
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
        persistent = await self._persistent_process_for_channel(msg.channel_id, binding.worktree_path)
        session = persistent.session
        self.runtime.bind_thread(
            msg.channel_id,
            thread_id=session.session_id or "",
            backend="claude-subprocess",
            status="active",
        )
        prompt = self._format_prompt(msg, binding.worktree_path, self.runtime.build_context_bundle(msg.channel_id))
        self._last_channel_id = msg.channel_id
        self._last_worktree = binding.worktree_path
        self._last_session_id = session.session_id
        before_changes = self._git_status(binding.worktree_path)
        started_at = _now_iso()
        self._active_channel_id = msg.channel_id
        try:
            self.on_status(
                f"Claude working on {msg.channel_type.value} message from {msg.sender_name} in {binding.worktree_path.name} (effort: {self._effort_for_message(msg.content)})."
            )
            result = await self._run_turn(prompt, cwd=binding.worktree_path, persistent=persistent)

            if self._should_retry_fresh_session(result):
                logger.warning(
                    "Claude session transport failed for session %s; creating a fresh session",
                    session.session_id,
                )
                self.on_status("Claude session was stale. Recreating session.")
                session.reset()
                await self._terminate_process(persistent)
                self.runtime.bind_thread(
                    msg.channel_id,
                    thread_id=session.session_id or "",
                    backend="claude-subprocess",
                    status="rebound",
                )
                result = await self._run_turn(prompt, cwd=binding.worktree_path, persistent=persistent)

            after_changes = self._git_status(binding.worktree_path)
            changed_files = sorted(set(after_changes) | set(before_changes))
            content = self._extract_response_text(result.stdout) if result.returncode == 0 else ""

            if self._should_retry_empty_response(result, content=content, before_changes=before_changes, after_changes=after_changes):
                logger.warning(
                    "Claude returned no text for session %s with no observed workspace changes; retrying once with a fresh session",
                    session.session_id,
                )
                self.on_status("Claude returned no text. Retrying once with a fresh session.")
                session.reset()
                await self._terminate_process(persistent)
                self.runtime.bind_thread(
                    msg.channel_id,
                    thread_id=session.session_id or "",
                    backend="claude-subprocess",
                    status="rebound",
                )
                result = await self._run_turn(prompt, cwd=binding.worktree_path, persistent=persistent)
                after_changes = self._git_status(binding.worktree_path)
                changed_files = sorted(set(after_changes) | set(before_changes))
                content = self._extract_response_text(result.stdout) if result.returncode == 0 else ""

            if result.returncode != 0:
                failure_message = self._failure_text(result)
                self.runtime.record_turn_result(
                    channel_key=msg.channel_id,
                    thread_id=session.session_id or "claude-missing-session",
                    turn_id=f"claude-error-{msg.message_id or int(time.time() * 1000)}",
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
                    backend="claude-subprocess",
                    degraded=False,
                )
                self._report_failure(result)
                return False

            if not content.strip():
                self.runtime.record_turn_result(
                    channel_key=msg.channel_id,
                    thread_id=session.session_id or "claude-missing-session",
                    turn_id=f"claude-empty-{msg.message_id or int(time.time() * 1000)}",
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
                    backend="claude-subprocess",
                    degraded=False,
                )
                self._report_failure(result, default_message="Claude returned no text.")
                return False

            session.mark_success()
            self.runtime.bind_thread(
                msg.channel_id,
                thread_id=session.session_id or "",
                backend="claude-subprocess",
                status="active",
            )
            self._last_session_id = session.session_id
            validations = self._extract_validation_lines(content)
            commands_run = [self._display_command(result.args), *self._extract_command_lines(content)]
            summary = self._summarize_response(content)
            next_step = _extract_next_step(content) or "Continue from STATUS.md and the latest handoff."
            blocker = _extract_blocker(content)
            turn_id = f"claude-{msg.message_id or int(time.time() * 1000)}"
            turn_recorded = self.runtime.record_turn_result(
                channel_key=msg.channel_id,
                thread_id=session.session_id or "claude-missing-session",
                turn_id=turn_id,
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
                backend="claude-subprocess",
                degraded=False,
            )
            if not turn_recorded:
                logger.info("Duplicate turn %s detected, skipping response", turn_id)
                self.on_status("Claude turn was duplicate (already processed).")
                return True
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
        finally:
            self._active_channel_id = None

    def claim_inbound_discord_message(self, channel_key: str, message_id: str | int | None) -> bool:
        return self.runtime.claim_inbound_discord_message(channel_key, message_id)

    def claim_outbound_discord_reply(
        self,
        channel_key: str,
        source_message_id: str | int | None,
        content: str,
        *,
        force: bool = False,
    ) -> bool:
        return self.runtime.claim_outbound_discord_reply(
            channel_key,
            source_message_id,
            content,
            force=force,
        )

    def _format_prompt(self, msg: InboundMessage, prompt_workspace: Path, durable_bundle: str) -> str:
        effort = self._effort_for_message(msg.content)
        self._last_effort = effort
        is_lightweight = self._is_lightweight_message(msg.content)

        if is_lightweight:
            # Lightweight fast path for simple coordination messages
            parts = [
                (
                    "You are Claude in CLADEX relay. This is a lightweight coordination message.\n"
                    "Rules: Be brief. No filler. Respond directly to the message.\n"
                    f"Effort: {effort}."
                )
            ]
            # Minimal context for lightweight messages
            lightweight_context = self._durable_context(prompt_workspace, lightweight=True)
            if lightweight_context:
                parts.append(f"Status:\n{lightweight_context}")
            parts.append(f"Sender: {msg.sender_name}\nMessage: {msg.content.strip()}")
            return "\n\n".join(part for part in parts if part)

        # Full context path for substantive messages
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
        durable_context = self._durable_context(prompt_workspace, lightweight=False)
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

    def _durable_context(self, prompt_workspace: Path, *, lightweight: bool = False) -> str:
        sections: list[str] = []
        context_files = LIGHTWEIGHT_CONTEXT_FILES if lightweight else PROMPT_CONTEXT_FILES
        for relative_path, limit in context_files:
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

    def _build_command(self, *, cwd: Path) -> list[str]:
        cmd = [
            claude_code_bin(),
            "--output-format",
            "stream-json",
            "--verbose",
            "--input-format",
            "stream-json",
            "--model",
            self.model,
            "--permission-mode",
            "bypassPermissions",
        ]
        return cmd

    async def _persistent_process_for_channel(self, channel_key: str, prompt_workspace: Path) -> PersistentClaudeProcess:
        """Get or create a persistent Claude subprocess for a channel."""
        session = self._session_for_channel(channel_key, prompt_workspace)
        persistent = self._persistent_processes.get(channel_key)
        current_loop_id = id(asyncio.get_running_loop())
        if persistent is None:
            persistent = PersistentClaudeProcess(session=session, worktree=prompt_workspace)
            self._persistent_processes[channel_key] = persistent
            return persistent
        persistent.session = session
        if persistent.worktree != prompt_workspace:
            await self._terminate_process(persistent)
            persistent.worktree = prompt_workspace
        if persistent.process is not None and persistent.loop_id is not None and persistent.loop_id != current_loop_id:
            await self._terminate_process(persistent)
        return persistent

    async def _start_process(self, persistent: PersistentClaudeProcess) -> asyncio.subprocess.Process:
        """Start a new Claude subprocess with stream-json format."""
        current_loop_id = id(asyncio.get_running_loop())
        if persistent.process is not None and persistent.process.returncode is None and persistent.loop_id == current_loop_id:
            return persistent.process
        if persistent.process is not None:
            await self._terminate_process(persistent)

        cmd = self._build_command(cwd=persistent.worktree)
        # Add --print flag for non-interactive mode with stream-json
        cmd.append("--print")
        # Add session resume if we have a session
        if persistent.session.initialized and persistent.session.session_id:
            cmd.extend(["--resume", persistent.session.session_id])

        env = os.environ.copy()
        env["CLADEX_ACTIVE_WORKTREE"] = str(persistent.worktree)
        env["CLAUDE_CODE_ENTRYPOINT"] = "cladex-relay"

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(persistent.worktree),
            env=env,
            **_windows_hidden_subprocess_kwargs(),
        )
        persistent.process = process
        persistent.loop_id = current_loop_id
        persistent.closing = False
        return process

    async def _terminate_process(self, persistent: PersistentClaudeProcess) -> None:
        """Terminate a Claude subprocess and clean up."""
        persistent.closing = True
        process = persistent.process
        persistent.process = None
        persistent.loop_id = None

        if persistent.reader_task is not None:
            persistent.reader_task.cancel()
            try:
                await persistent.reader_task
            except asyncio.CancelledError:
                pass
            persistent.reader_task = None

        if process is None:
            return

        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
                await process.stdin.wait_closed()
        except Exception:
            pass

        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            except Exception:
                logger.exception("Failed to terminate Claude subprocess")

    async def _run_turn(self, prompt: str, *, cwd: Path, persistent: PersistentClaudeProcess) -> CommandResult:
        """Run a single turn by starting a new Claude process for each message.

        Unlike SDK mode, we start a fresh process per turn using --print mode
        with stream-json format. This avoids terminal spam by using
        CREATE_NO_WINDOW on Windows.
        """
        cmd = self._build_command(cwd=cwd)
        used_resume = persistent.session.initialized
        async with persistent.lock:
            persistent.worktree = cwd

            # Build command for this turn
            turn_cmd = list(cmd)
            turn_cmd.append("--print")
            if persistent.session.initialized and persistent.session.session_id:
                turn_cmd.extend(["--resume", persistent.session.session_id])
            # Add the prompt as the final argument
            turn_cmd.extend(["--", prompt])

            env = os.environ.copy()
            env["CLADEX_ACTIVE_WORKTREE"] = str(cwd)
            env["CLAUDE_CODE_ENTRYPOINT"] = "cladex-relay"

            try:
                process = await asyncio.create_subprocess_exec(
                    *turn_cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(cwd),
                    env=env,
                    **_windows_hidden_subprocess_kwargs(),
                )

                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(),
                    timeout=600.0,  # 10 minute timeout
                )
                stdout = stdout_data.decode("utf-8", errors="replace")
                stderr = stderr_data.decode("utf-8", errors="replace")
                returncode = process.returncode or 0

                # Extract session ID from output if present
                final_session_id = self._extract_session_id_from_output(stdout)
                if final_session_id and final_session_id != persistent.session.session_id:
                    persistent.session.adopt(final_session_id, initialized=returncode == 0)

                return CommandResult(
                    args=turn_cmd,
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    used_resume=used_resume,
                )
            except asyncio.TimeoutError:
                return CommandResult(
                    args=turn_cmd,
                    returncode=1,
                    stdout="",
                    stderr="Claude turn timed out after 10 minutes",
                    used_resume=used_resume,
                )
            except Exception as exc:
                return CommandResult(
                    args=turn_cmd,
                    returncode=1,
                    stdout="",
                    stderr=str(exc),
                    used_resume=used_resume,
                )

    def _extract_session_id_from_output(self, stdout: str) -> str | None:
        """Extract session ID from Claude output if present."""
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                # Look for session_id in result events
                if event.get("type") == "result":
                    session_id = event.get("session_id")
                    if session_id:
                        return str(session_id)
            except json.JSONDecodeError:
                continue
        return None

    def _extract_response_text(self, stdout: str) -> str:
        delta_parts: list[str] = []
        result_text: str = ""
        assistant_text: str = ""
        error_text: str = ""
        parsed_json = False

        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            parsed_json = True

            event_type = event.get("type", "")

            # Collect streaming deltas separately
            if event_type == "content_block_delta":
                delta = event.get("delta", {})
                text = delta.get("text", "")
                if text:
                    delta_parts.append(text)
            # Keep only the LAST of each final event type (don't concatenate)
            elif event_type == "result":
                text = self._extract_text_from_event(event)
                if text:
                    result_text = text  # Overwrite, don't append
            elif event_type == "assistant":
                text = self._extract_text_from_event(event)
                if text:
                    assistant_text = text  # Overwrite, don't append
            elif event_type == "error":
                text = self._extract_text_from_event(event)
                if text:
                    error_text = text

        # Priority: deltas > result > assistant > error
        if delta_parts:
            return "".join(delta_parts).strip()
        if result_text:
            return result_text.strip()
        if assistant_text:
            return assistant_text.strip()
        if error_text:
            return error_text.strip()
        if not parsed_json:
            return stdout.strip()
        return ""

    def _extract_text_from_event(self, event: dict) -> str:
        event_type = event.get("type", "")

        # Handle streaming deltas
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            return delta.get("text", "")

        # Handle assistant message (only if no deltas were found)
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
            return ""

        # Handle result type
        if event_type == "result":
            return event.get("result", "") or event.get("text", "")

        if event_type == "error":
            error = event.get("error", {})
            if isinstance(error, dict):
                return error.get("message", "")
            return str(error)

        return ""

    def _should_retry_fresh_session(self, result: CommandResult) -> bool:
        if result.returncode == 0:
            return False
        haystack = f"{result.stdout}\n{result.stderr}".lower()
        if self._is_session_id_in_use_error(haystack):
            return True
        if not result.used_resume:
            return False
        needles = [
            "session not found",
            "could not find session",
            "unknown session",
            "invalid session",
            "no conversation found",
            "resume",
        ]
        return any(needle in haystack for needle in needles)

    @staticmethod
    def _is_session_id_in_use_error(haystack: str) -> bool:
        normalized = haystack.lower()
        return "session" in normalized and "already in use" in normalized

    def _should_retry_empty_response(
        self,
        result: CommandResult,
        *,
        content: str,
        before_changes: list[str],
        after_changes: list[str],
    ) -> bool:
        if result.returncode != 0:
            return False
        if content.strip():
            return False
        return sorted(before_changes) == sorted(after_changes)

    def _report_failure(self, result: CommandResult, *, default_message: str | None = None) -> None:
        message = default_message or self._failure_text(result)
        self.on_status(f"ERROR: {message}")
        logger.error(
            "Claude transport failed (rc=%s, resume=%s): %s",
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
        return stderr or stdout or f"Claude transport failed with exit code {result.returncode}"

    def _display_command(self, args: list[str]) -> str:
        if not args:
            return "claude"
        if len(args) <= 7:
            return " ".join(args)
        visible = [*args[:7], "..."]
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

    def _is_lightweight_message(self, text: str) -> bool:
        """Check if message is a simple coordination message that needs minimal context."""
        normalized = text.strip().lower()
        # Very short messages (under 30 chars) that match coordination patterns
        if len(normalized) > 50:
            return False
        # Check for exact or near-exact matches to lightweight patterns
        words = set(normalized.replace(",", " ").replace(".", " ").replace("!", " ").replace("?", " ").split())
        if not words:
            return False
        # If message is just 1-3 words and matches lightweight patterns
        if len(words) <= 3:
            for pattern in LIGHTWEIGHT_MESSAGE_PATTERNS:
                if pattern in normalized or normalized in pattern:
                    return True
        return False

    def _effort_for_message(self, text: str) -> str:
        normalized = text.strip().lower()
        # Lightweight messages get quick effort
        if self._is_lightweight_message(text):
            return self.reasoning_effort_quick
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
        on_discord_response: Callable[[str, str, str], None],
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
            self._on_discord(msg.channel_id, msg.content, msg.reply_to)
            return
        future = self._pending_local.get(msg.reply_to)
        if future and not future.done():
            future.set_result(msg.content)

    def claim_inbound_discord_message(self, channel_id: str, message_id: str | int | None) -> bool:
        return self._claude.claim_inbound_discord_message(channel_id, message_id)

    def claim_outbound_discord_reply(
        self,
        channel_id: str,
        source_message_id: str | int | None,
        content: str,
        *,
        force: bool = False,
    ) -> bool:
        return self._claude.claim_outbound_discord_reply(
            channel_id,
            source_message_id,
            content,
            force=force,
        )

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
        await self._claude.stop()

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
                ok = await self._claude.process_message(msg)
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
