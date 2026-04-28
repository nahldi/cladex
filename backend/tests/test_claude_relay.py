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
