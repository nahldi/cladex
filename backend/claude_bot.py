#!/usr/bin/env python3
"""
Discord Claude Relay - Bot

Discord bot that relays messages to Claude Code CLI.
Uses explicit session IDs for durable conversation continuity.

NOTE: This bot serves Discord only. GUI chat in cladex spawns a separate
Claude subprocess with its own session. They are independent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

from claude_backend import RelayBackend, ChannelType
from claude_common import (
    state_dir_for_namespace,
    atomic_write_text,
)

logger = logging.getLogger(__name__)


@dataclass
class BotConfig:
    """Bot configuration."""
    token: str
    workspace: Path
    namespace: str
    operator_ids: list[str]
    allowed_user_ids: list[str]
    allowed_channel_ids: list[str]
    allow_dms: bool = True
    trigger_mode: str = "mention_or_dm"  # mention_or_dm, prefix, always
    prefix: str = "!"
    bot_name: str = "Claude"

    @classmethod
    def from_env(cls, workspace: Path, namespace: str) -> "BotConfig":
        """Load config from environment variables."""
        return cls(
            token=os.environ.get("DISCORD_BOT_TOKEN", ""),
            workspace=workspace,
            namespace=namespace,
            operator_ids=_parse_ids(os.environ.get("OPERATOR_IDS", "")),
            allowed_user_ids=_parse_ids(os.environ.get("ALLOWED_USER_IDS", "")),
            allowed_channel_ids=_parse_ids(os.environ.get("ALLOWED_CHANNEL_IDS", "")),
            allow_dms=os.environ.get("ALLOW_DMS", "true").lower() in ("1", "true", "yes"),
            trigger_mode=os.environ.get("BOT_TRIGGER_MODE", "mention_or_dm"),
            prefix=os.environ.get("BOT_PREFIX", "!"),
            bot_name=os.environ.get("RELAY_BOT_NAME", "Claude"),
        )


def _parse_ids(value: str) -> list[str]:
    """Parse comma-separated IDs."""
    return [x.strip() for x in value.split(",") if x.strip()]


class ClaudeRelayBot(commands.Bot):
    """
    Discord bot that relays messages to Claude Code.
    """

    def __init__(self, config: BotConfig):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        intents.guild_messages = True

        super().__init__(
            command_prefix=config.prefix,
            intents=intents,
            help_command=None,
        )

        self.config = config
        self.state_dir = state_dir_for_namespace(config.namespace)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self._backend: RelayBackend | None = None
        self._status = "starting"
        self._status_detail = ""

    async def setup_hook(self) -> None:
        """Called when bot is ready to start."""
        self._backend = RelayBackend(
            workspace=self.config.workspace,
            state_dir=self.state_dir,
            on_discord_response=self._queue_discord_response,
            on_status=self._on_status,
        )

        if not await self._backend.start():
            logger.error("Failed to start Claude backend")
            await self.close()
            return

        logger.info(f"Claude backend started (session: {self._backend.session_id})")

    async def close(self) -> None:
        """Cleanup on shutdown."""
        if self._backend:
            await self._backend.stop()
        await super().close()

    def _queue_discord_response(self, channel_id: str, content: str) -> None:
        """Queue a response for Discord (called from backend thread)."""
        asyncio.run_coroutine_threadsafe(
            self._send_discord_response(channel_id, content),
            self.loop
        )

    async def _send_discord_response(self, channel_id: str, content: str) -> None:
        """Send response to Discord channel."""
        try:
            channel = self.get_channel(int(channel_id))
            if not channel:
                channel = await self.fetch_channel(int(channel_id))

            if channel and hasattr(channel, "send"):
                for chunk in self._split_message(content):
                    await channel.send(chunk)

        except Exception as e:
            logger.exception(f"Failed to send Discord response: {e}")

    def _on_status(self, status: str) -> None:
        """Handle status updates from backend."""
        logger.info(f"[STATUS] {status}")
        normalized = "ready"
        lowered = status.lower()
        if lowered.startswith("error"):
            normalized = "error"
        elif "working" in lowered:
            normalized = "working"
        elif "stopped" in lowered:
            normalized = "stopped"
        self._write_status(normalized, status)

    def _split_message(self, content: str, max_length: int = 1900) -> list[str]:
        """Split message into chunks for Discord's character limit."""
        if len(content) <= max_length:
            return [content]

        chunks = []
        lines = content.split("\n")
        current: list[str] = []
        current_len = 0

        for line in lines:
            if current_len + len(line) + 1 > max_length:
                if current:
                    chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line) + 1

        if current:
            chunks.append("\n".join(current))

        return chunks

    def _should_respond(self, message: discord.Message) -> bool:
        """Check if bot should respond to this message."""
        # Never respond to self
        if message.author.id == self.user.id:
            return False

        # Never respond to other bots
        if message.author.bot:
            return False

        # Check user allowlist (if set)
        if self.config.allowed_user_ids:
            user_id = str(message.author.id)
            if user_id not in self.config.allowed_user_ids and user_id not in self.config.operator_ids:
                return False

        # Check channel allowlist (if set)
        if self.config.allowed_channel_ids:
            if str(message.channel.id) not in self.config.allowed_channel_ids:
                return False

        # Handle DMs
        if isinstance(message.channel, discord.DMChannel):
            if not self.config.allow_dms:
                return False
            return True

        # Check trigger mode
        if self.config.trigger_mode == "always":
            return True

        if self.config.trigger_mode == "prefix":
            return message.content.startswith(self.config.prefix)

        # Default: mention_or_dm
        return self.user.mentioned_in(message)

    def _clean_content(self, message: discord.Message) -> str:
        """Clean message content (remove mentions, etc.)."""
        content = message.content

        if self.user:
            content = content.replace(f"<@{self.user.id}>", "").strip()
            content = content.replace(f"<@!{self.user.id}>", "").strip()

        if content.startswith(self.config.prefix):
            content = content[len(self.config.prefix):].strip()

        return content

    async def on_ready(self) -> None:
        """Called when bot is connected and ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Workspace: {self.config.workspace}")
        logger.info(f"Operators: {self.config.operator_ids}")
        self._write_status("ready")

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        if not self._should_respond(message):
            return

        content = self._clean_content(message)
        if not content:
            return

        logger.info(f"[RECV] {message.author}: {content[:100]}...")
        self._write_status("working", f"Handling message from {message.author.display_name}")

        async with message.channel.typing():
            await self._backend.send_discord_message(
                channel_id=str(message.channel.id),
                sender_id=str(message.author.id),
                sender_name=message.author.display_name,
                content=content,
                message_id=str(message.id),
            )

    def _write_status(self, status: str, detail: str | None = None) -> None:
        """Write status to state file."""
        self._status = status
        self._status_detail = detail or ""
        status_file = self.state_dir / "status.json"
        data = {
            "status": status,
            "detail": self._status_detail,
            "workspace": str(self.config.workspace),
            "active_worktree": self._backend.current_worktree if self._backend else str(self.config.workspace),
            "active_channel": self._backend.current_channel if self._backend else None,
            "bot_user": str(self.user) if self.user else None,
            "session_id": self._backend.session_id if self._backend else None,
            "timestamp": datetime.utcnow().isoformat(),
        }
        atomic_write_text(status_file, json.dumps(data, indent=2))


async def run_bot(config: BotConfig) -> None:
    """Run the bot."""
    bot = ClaudeRelayBot(config)

    # Write PID file
    pid_file = bot.state_dir / "relay.pid"
    pid_file.write_text(str(os.getpid()))

    try:
        await bot.start(config.token)
    finally:
        pid_file.unlink(missing_ok=True)


def main() -> int:
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    workspace = Path(os.environ.get("CLAUDE_WORKDIR", os.getcwd())).resolve()
    namespace = os.environ.get("STATE_NAMESPACE", "default")

    config = BotConfig.from_env(workspace, namespace)

    if not config.token:
        print("ERROR: DISCORD_BOT_TOKEN not set")
        return 1

    try:
        asyncio.run(run_bot(config))
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
