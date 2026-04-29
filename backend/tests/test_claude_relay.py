import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import claude_relay


def test_cmd_gui_delegates_to_cladex(monkeypatch) -> None:
    launches: list[tuple[list[str], dict]] = []

    monkeypatch.setattr(claude_relay.shutil, "which", lambda name: "C:\\tools\\cladex.exe" if name == "cladex" else None)

    def fake_popen(command, **kwargs):
        launches.append((command, kwargs))

        class DummyProcess:
            pass

        return DummyProcess()

    monkeypatch.setattr(claude_relay.subprocess, "Popen", fake_popen)

    rc = claude_relay.cmd_gui(argparse.Namespace())

    assert rc == 0
    assert launches
    assert launches[0][0][:2] == ["C:\\tools\\cladex.exe", "gui"]


def test_cmd_gui_errors_when_cladex_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(claude_relay.shutil, "which", lambda name: None)

    rc = claude_relay.cmd_gui(argparse.Namespace())

    assert rc == 1
    assert "cladex is not installed" in capsys.readouterr().out.lower()


def _register_args(workspace: Path, **overrides) -> SimpleNamespace:
    base = dict(
        workspace=str(workspace),
        discord_bot_token="discord-token",
        bot_name="Test",
        operator_ids="",
        allowed_user_ids="",
        allowed_bot_ids="",
        allow_dms=False,
        trigger_mode="mention_or_dm",
        allowed_channel_id="",
        channel_history_limit=20,
        model="",
        claude_config_dir="",
        allow_cladex_workspace=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cmd_register_rejects_empty_allowlists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(claude_relay, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(claude_relay, "REGISTRY_PATH", tmp_path / "workspaces.json")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    rc = claude_relay.cmd_register(_register_args(workspace))

    captured = capsys.readouterr()
    assert rc == 2
    assert "empty allowlists" in captured.err.lower()


def test_cmd_register_rejects_allow_dms_without_user_allowlist(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(claude_relay, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(claude_relay, "REGISTRY_PATH", tmp_path / "workspaces.json")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    rc = claude_relay.cmd_register(_register_args(workspace, allow_dms=True, allowed_channel_id="555"))

    captured = capsys.readouterr()
    assert rc == 2
    assert "allow-dms requires" in captured.err.lower()


def test_cmd_register_accepts_channel_allowlist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_relay, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(claude_relay, "REGISTRY_PATH", tmp_path / "workspaces.json")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    rc = claude_relay.cmd_register(_register_args(workspace, allowed_channel_id="555"))

    assert rc == 0


def test_cmd_register_rejects_non_numeric_user_id(tmp_path: Path, monkeypatch, capsys) -> None:
    """F0004: A non-numeric --allowed-user-ids would be filtered out by
    `_parse_csv_ids`, leaving the relay with an empty DM allowlist while
    `--allow-dms` is enabled. cmd_register must reject the value loudly."""
    monkeypatch.setattr(claude_relay, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(claude_relay, "REGISTRY_PATH", tmp_path / "workspaces.json")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(SystemExit):
        claude_relay.cmd_register(
            _register_args(workspace, allow_dms=True, allowed_user_ids="not-a-discord-id")
        )

    captured = capsys.readouterr()
    assert "numeric Discord IDs" in captured.err


def test_write_env_file_rejects_newline_injection(tmp_path: Path) -> None:
    """F0004: The Claude .env writer must refuse CR/LF in values so a
    user-controlled bot-name or startup text cannot inject ALLOW_DMS=true on
    the next profile load."""
    env_path = tmp_path / "profile.env"
    with pytest.raises(ValueError) as excinfo:
        claude_relay._write_env_file(
            env_path,
            {
                "DISCORD_BOT_TOKEN": "token",
                "RELAY_BOT_NAME": "Hi\nALLOW_DMS=true",
                "CLAUDE_WORKDIR": str(tmp_path),
            },
        )
    assert "RELAY_BOT_NAME" in str(excinfo.value)
    assert not env_path.exists()


def test_claude_bot_run_bot_does_not_unlink_newer_pid(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    import importlib

    claude_bot = importlib.import_module("claude_bot")

    class FakeBot:
        def __init__(self, config) -> None:
            self.state_dir = tmp_path

        async def start(self, token: str) -> None:
            (tmp_path / "relay.pid").write_text("999999", encoding="utf-8")

    monkeypatch.setattr(claude_bot, "ClaudeRelayBot", FakeBot)

    asyncio.run(claude_bot.run_bot(SimpleNamespace(token="token")))

    assert (tmp_path / "relay.pid").read_text(encoding="utf-8") == "999999"


def test_claude_bot_should_respond_accepts_dm_with_channel_allowlist(monkeypatch) -> None:
    """F0060: a profile with `--allow-dms` AND a guild-channel allowlist must
    still accept DMs from allowed users. The DM channel id is private and
    will never appear in the guild-channel allowlist, so the channel-allowlist
    gate has to skip DM channels."""
    import importlib

    # claude_bot opens a discord.Client at import time; load lazily and stub.
    import discord

    claude_bot = importlib.import_module("claude_bot")

    bot_user = SimpleNamespace(id=999)

    class FakeBot:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                allow_dms=True,
                allowed_user_ids={"7"},
                allowed_bot_ids=set(),
                allowed_channel_ids={"42"},
                operator_ids=set(),
                trigger_mode="mention_or_dm",
                prefix="!cladex",
            )
            self.user = bot_user

    fake = FakeBot()
    dm_message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        channel=discord.DMChannel.__new__(discord.DMChannel),
        content="hi",
    )
    guild_message_in = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        channel=SimpleNamespace(id=42),
        content=f"<@{bot_user.id}> hi",
        mentions=[bot_user],
    )
    guild_message_out = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        channel=SimpleNamespace(id=999),
        content="hi",
        mentions=[],
    )

    # Make discord.DMChannel introspection match the fake.
    monkeypatch.setattr(discord, "DMChannel", type(dm_message.channel))
    bot_user.mentioned_in = lambda message: any(getattr(m, "id", None) == bot_user.id for m in getattr(message, "mentions", []))

    bound_should_respond = claude_bot.ClaudeRelayBot._should_respond.__get__(fake)
    assert bound_should_respond(dm_message) is True
    assert bound_should_respond(guild_message_in) is True
    assert bound_should_respond(guild_message_out) is False


def test_claude_bot_should_respond_fails_closed_with_empty_allowlists(monkeypatch) -> None:
    import importlib
    import discord

    claude_bot = importlib.import_module("claude_bot")
    bot_user = SimpleNamespace(id=999)

    fake = SimpleNamespace(
        config=SimpleNamespace(
            allow_dms=True,
            allowed_user_ids=set(),
            allowed_bot_ids=set(),
            allowed_channel_ids=set(),
            operator_ids=set(),
            trigger_mode="always",
            prefix="!cladex",
        ),
        user=bot_user,
    )
    dm_message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        channel=discord.DMChannel.__new__(discord.DMChannel),
        content="hi",
        mentions=[],
    )
    guild_message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        channel=SimpleNamespace(id=42),
        content=f"<@{bot_user.id}> hi",
        mentions=[bot_user],
    )

    monkeypatch.setattr(discord, "DMChannel", type(dm_message.channel))
    bot_user.mentioned_in = lambda message: True

    bound_should_respond = claude_bot.ClaudeRelayBot._should_respond.__get__(fake)
    assert bound_should_respond(dm_message) is False
    assert bound_should_respond(guild_message) is False


def test_claude_bot_should_respond_allows_dm_for_operator_id(monkeypatch) -> None:
    import importlib
    import discord

    claude_bot = importlib.import_module("claude_bot")
    bot_user = SimpleNamespace(id=999)
    fake = SimpleNamespace(
        config=SimpleNamespace(
            allow_dms=True,
            allowed_user_ids=set(),
            allowed_bot_ids=set(),
            allowed_channel_ids=set(),
            operator_ids={"7"},
            trigger_mode="mention_or_dm",
            prefix="!cladex",
        ),
        user=bot_user,
    )
    dm_message = SimpleNamespace(
        author=SimpleNamespace(id=7, bot=False),
        channel=discord.DMChannel.__new__(discord.DMChannel),
        content="hi",
        mentions=[],
    )

    monkeypatch.setattr(discord, "DMChannel", type(dm_message.channel))
    bot_user.mentioned_in = lambda message: False

    bound_should_respond = claude_bot.ClaudeRelayBot._should_respond.__get__(fake)
    assert bound_should_respond(dm_message) is True


def test_profile_from_env_marks_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(claude_relay, "PROFILES_DIR", tmp_path / "profiles")
    env = {
        "DISCORD_BOT_TOKEN": "abc",
        "RELAY_BOT_NAME": "Claude",
        "CLAUDE_WORKDIR": str(tmp_path / "workspace"),
        "ALLOW_DMS": "true",
        "BOT_TRIGGER_MODE": "mention_or_dm",
        "OPERATOR_IDS": "123",
        "ALLOWED_USER_IDS": "123",
        "ALLOWED_CHANNEL_IDS": "456",
        "CHANNEL_HISTORY_LIMIT": "20",
    }

    profile = claude_relay._profile_from_env(env)

    assert profile["backend"] == "claude-code"
    assert Path(profile["env_file"]).exists()


def test_claude_relay_run_rejects_existing_empty_allowlists(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token",
                f"CLAUDE_WORKDIR={workspace}",
                "ALLOW_DMS=true",
                "ALLOWED_USER_IDS=",
                "ALLOWED_CHANNEL_IDS=",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {
        "name": "legacy-open",
        "workspace": str(workspace),
        "env_file": str(env_file),
        "state_namespace": "legacy-open",
    }
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(claude_relay, "_get_profile_for_workspace", lambda _workspace: profile)

    with pytest.raises(SystemExit) as excinfo:
        claude_relay.cmd_run(SimpleNamespace())

    assert excinfo.value.code
