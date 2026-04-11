from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cladex


def test_get_all_profiles_uses_codex_runtime_state(monkeypatch) -> None:
    codex_profile = {
        "name": "codex-one",
        "workspace": "C:/repo",
        "env_file": "C:/repo/.env",
    }
    monkeypatch.setattr(cladex.relayctl, "_all_registered_profiles", lambda: [codex_profile])
    monkeypatch.setattr(
        cladex.relayctl,
        "_profile_runtime_state",
        lambda profile: {"running": True, "ready": True, "degraded": False, "log_path": Path("C:/relay.log")},
    )
    monkeypatch.setattr(cladex.relayctl, "_load_env_file", lambda path: {"CODEX_MODEL": "gpt-5.4"})
    monkeypatch.setattr(cladex.relayctl, "_normalized_profile_env", lambda env: env)
    monkeypatch.setattr(cladex, "_load_claude_registry", lambda: {"profiles": [], "projects": []})

    profiles = cladex.get_all_profiles()

    assert len(profiles) == 1
    assert profiles[0]["_relay_type"] == "codex"
    assert profiles[0]["_running"] is True
    assert profiles[0]["_provider"] == "codex-app-server"


def test_start_codex_profile_delegates_to_relayctl(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(cladex.relayctl, "_run_profile", lambda profile: calls.append(profile) or 0)

    profile = {"name": "codex-one", "_relay_type": "codex"}
    cladex.start_profile(profile)

    assert calls == [profile]


def test_stop_codex_profile_delegates_to_relayctl(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(cladex.relayctl, "_stop_profile", lambda profile: calls.append(profile) or 0)

    profile = {"name": "codex-one", "_relay_type": "codex"}
    cladex.stop_profile(profile)

    assert calls == [profile]


def test_claude_running_state_uses_relay_pid(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "config"
    data_root = tmp_path / "data"
    state_dir = data_root / "state" / "ns-one"
    state_dir.mkdir(parents=True)
    (state_dir / "relay.pid").write_text("1234", encoding="utf-8")
    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", data_root)
    monkeypatch.setattr(cladex.psutil, "pid_exists", lambda pid: pid == 1234)

    state = cladex._claude_profile_runtime_state({"state_namespace": "ns-one"})

    assert state["running"] is True
    assert state["ready"] is True
    assert state["pid"] == 1234


def test_claude_runtime_state_reads_status_json(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    state_dir = data_root / "state" / "ns-two"
    state_dir.mkdir(parents=True)
    (state_dir / "relay.pid").write_text("555", encoding="utf-8")
    (state_dir / "status.json").write_text(
        json.dumps({"status": "working", "detail": "Claude working on discord message", "session_id": "sess-123"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", data_root)
    monkeypatch.setattr(cladex.psutil, "pid_exists", lambda pid: pid == 555)

    state = cladex._claude_profile_runtime_state({"state_namespace": "ns-two"})

    assert state["running"] is True
    assert state["ready"] is True
    assert state["state"] == "working"
    assert state["status_message"] == "Claude working on discord message"
    assert state["session_id"] == "sess-123"


def test_start_claude_profile_uses_windowless_launch(monkeypatch) -> None:
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(cladex, "_claude_discord_bin", lambda: "claude-discord.cmd")
    monkeypatch.setattr(cladex, "_windowless_popen", lambda command, cwd=None: calls.append((command, str(cwd))) or SimpleNamespace())

    profile = {"name": "claude-one", "_relay_type": "claude", "workspace": "C:/claude"}
    cladex.start_profile(profile)

    assert calls == [(["claude-discord.cmd", "run"], "C:/claude")]


def test_stop_claude_profile_prefers_cli_stop(monkeypatch) -> None:
    run_calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(cladex, "_claude_discord_bin", lambda: "claude-discord.cmd")
    monkeypatch.setattr(
        cladex,
        "_windowless_run",
        lambda command, cwd=None: run_calls.append((command, str(cwd))) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    profile = {"name": "claude-one", "_relay_type": "claude", "workspace": "C:/claude"}
    cladex.stop_profile(profile)

    assert run_calls == [(["claude-discord.cmd", "stop"], "C:/claude")]


def test_load_claude_registry_is_tolerant(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "workspaces.json"
    path.write_text(json.dumps({"profiles": [{"name": "x"}]}), encoding="utf-8")
    monkeypatch.setattr(cladex, "CLAUDE_REGISTRY_PATH", path)

    payload = cladex._load_claude_registry()

    assert payload["profiles"][0]["name"] == "x"
    assert payload["projects"] == []


def test_list_json_contains_runtime_fields(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [
            {
                "name": "codex-one",
                "_relay_type": "codex",
                "_running": True,
                "_ready": True,
                "_provider": "codex-app-server",
                "_model": "gpt-5.4",
                "_trigger_mode": "mention_or_dm",
                "workspace": "C:/repo",
                "_log_path": "C:/repo/relay.log",
                "attach_channel_id": "123",
            }
        ],
    )

    rc = cladex.cmd_list(SimpleNamespace(type=None, json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["relayType"] == "codex"
    assert payload[0]["running"] is True
    assert payload[0]["discordChannel"] == "123"


def test_status_json_returns_profiles_and_running(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [
            {
                "name": "claude-one",
                "_relay_type": "claude",
                "_running": True,
                "_ready": True,
                "_provider": "claude-code",
                "_model": "",
                "_trigger_mode": "mention_or_dm",
                "workspace": "C:/claude",
                "_log_path": "C:/claude/relay.log",
            }
        ],
    )

    rc = cladex.cmd_status(SimpleNamespace(name=None, type=None, json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] == ["claude-one"]
    assert payload["profiles"][0]["relayType"] == "claude"


def test_cmd_logs_json_reads_tail(monkeypatch, tmp_path: Path, capsys) -> None:
    log_path = tmp_path / "relay.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [
            {"name": "codex-one", "_relay_type": "codex", "_log_path": str(log_path)}
        ],
    )

    rc = cladex.cmd_logs(SimpleNamespace(name="codex-one", type="codex", lines=2, json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["logs"] == ["two", "three"]


def test_cmd_remove_codex_uses_registry_cleanup(monkeypatch, capsys) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [{"name": "codex-one", "_relay_type": "codex", "env_file": "C:/one.env"}],
    )
    monkeypatch.setattr(cladex, "_remove_codex_profile", lambda profile: removed.append(profile["name"]))

    rc = cladex.cmd_remove(SimpleNamespace(name="codex-one", type="codex"))

    assert rc == 0
    assert removed == ["codex-one"]
    assert "Removed codex-one [codex]." in capsys.readouterr().out
