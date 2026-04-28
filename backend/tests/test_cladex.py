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
    monkeypatch.setattr(cladex.relayctl, "_load_env_file", lambda path: {"CODEX_MODEL": "gpt-explicit"})
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

    profile = {"name": "codex-one", "_relay_type": "codex", "workspace": "C:/repo"}
    cladex.start_profile(profile)

    assert calls == [profile]


def test_start_claude_profile_uses_existing_runtime(tmp_path: Path, monkeypatch) -> None:
    """Verify Claude profile starts using existing runtime without SDK checks."""
    env_file = tmp_path / "forge.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    workspace = tmp_path / "forge"
    workspace.mkdir()
    runtime_python = tmp_path / "runtime" / "Scripts" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    state_dir = tmp_path / "state"
    launches: list[list[str]] = []

    monkeypatch.setattr(cladex.relayctl.install_plugin, "runtime_python_path", lambda: runtime_python)
    monkeypatch.setattr(cladex, "_claude_state_dir", lambda profile: state_dir)
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: {"DISCORD_BOT_TOKEN": "test"})
    monkeypatch.setattr(cladex.relayctl, "_background_python_windowless_executable", lambda: "pythonw.exe")
    monkeypatch.setattr(
        cladex.subprocess,
        "Popen",
        lambda command, **kwargs: launches.append(command) or SimpleNamespace(pid=321),
    )

    profile = {
        "name": "forge-ce0eef1b-09de",
        "_relay_type": "claude",
        "workspace": str(workspace),
        "env_file": str(env_file),
        "state_namespace": "forge-ce0eef1b",
    }

    cladex.start_profile(profile)

    # No SDK install should happen - backend now uses subprocess directly
    assert launches == [["pythonw.exe", cladex._backend_script_path("claude_bot.py")]]
    assert (state_dir / "relay.pid").read_text(encoding="utf-8") == "321"


def test_start_claude_profile_bootstraps_missing_runtime(tmp_path: Path, monkeypatch) -> None:
    """Verify Claude profile bootstraps runtime when missing."""
    env_file = tmp_path / "forge.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    workspace = tmp_path / "forge"
    workspace.mkdir()
    runtime_python = tmp_path / "runtime" / "Scripts" / "python.exe"
    state_dir = tmp_path / "state"
    launches: list[list[str]] = []
    ensure_runtime_calls: list[str] = []

    def fake_ensure_runtime(source=None):
        ensure_runtime_calls.append(source or "default")
        runtime_python.parent.mkdir(parents=True, exist_ok=True)
        runtime_python.write_text("", encoding="utf-8")
        return runtime_python

    monkeypatch.setattr(cladex.relayctl.install_plugin, "runtime_python_path", lambda: runtime_python)
    monkeypatch.setattr(cladex.relayctl.install_plugin, "_install_source", lambda: "local")
    monkeypatch.setattr(cladex.relayctl.install_plugin, "_ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr(cladex, "_claude_state_dir", lambda profile: state_dir)
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: {"DISCORD_BOT_TOKEN": "test"})
    monkeypatch.setattr(cladex.relayctl, "_background_python_windowless_executable", lambda: "pythonw.exe")
    monkeypatch.setattr(
        cladex.subprocess,
        "Popen",
        lambda command, **kwargs: launches.append(command) or SimpleNamespace(pid=321),
    )

    profile = {
        "name": "forge-ce0eef1b-09de",
        "_relay_type": "claude",
        "workspace": str(workspace),
        "env_file": str(env_file),
        "state_namespace": "forge-ce0eef1b",
    }

    cladex.start_profile(profile)

    # Should have called _ensure_runtime since runtime was missing
    assert ensure_runtime_calls == ["local"]
    assert launches == [["pythonw.exe", cladex._backend_script_path("claude_bot.py")]]



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
        json.dumps(
            {
                "status": "working",
                "detail": "Claude working on discord message",
                "session_id": "sess-123",
                "active_worktree": "C:/repo/worktree",
                "active_channel": "456",
                "model": "claude-explicit",
                "effort": "high",
            }
        ),
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
    assert state["active_worktree"] == "C:/repo/worktree"
    assert state["active_channel"] == "456"
    assert state["model"] == "claude-explicit"
    assert state["effort"] == "high"


def test_python_supports_module_uses_windowless_run(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        cladex,
        "_windowless_run",
        lambda command, cwd=None: commands.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert cladex._python_supports_module("python.exe", "discord") is True
    assert commands == [["python.exe", "-c", "import discord"]]


def test_stop_claude_profile_terminates_pid(monkeypatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr(cladex, "_claude_profile_runtime_state", lambda profile: {"pid": 4321})
    monkeypatch.setattr(cladex.relayctl, "terminate_process_tree", lambda pid: killed.append(pid) or True)

    profile = {"name": "claude-one", "_relay_type": "claude", "workspace": "C:/claude", "state_namespace": "ns"}
    cladex.stop_profile(profile)

    assert killed == [4321]


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
                "_model": "gpt-explicit",
                "_codex_home": "C:/accounts/codex-one",
                "_trigger_mode": "mention_or_dm",
                "_effort": "high",
                "_bot_name": "Kurt",
                "_allow_dms": True,
                "_state_namespace": "codex-ns",
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
    assert payload[0]["effort"] == "high"
    assert payload[0]["botName"] == "Kurt"
    assert payload[0]["codexHome"] == "C:/accounts/codex-one"
    assert payload[0]["allowDms"] is True
    assert payload[0]["stateNamespace"] == "codex-ns"
    assert payload[0]["displayName"] == "Kurt"
    assert payload[0]["workspaceLabel"] == "repo"


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
                "_claude_config_dir": "C:/accounts/claude-one",
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
    assert payload["profiles"][0]["claudeConfigDir"] == "C:/accounts/claude-one"


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


def test_cmd_doctor_json_reports_profile_port_collisions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cladex,
        "_doctor_version",
        lambda name, command: {"name": name, "ok": True, "version": "test", "detail": ""},
    )
    monkeypatch.setattr(
        cladex,
        "_doctor_profiles",
        lambda: {
            "count": 2,
            "codex": 2,
            "claude": 0,
            "running": [],
            "duplicateCodexPorts": {"18000": ["one", "two"]},
            "unsafeWorkspaces": [],
        },
    )
    monkeypatch.setattr(
        cladex,
        "_doctor_codex_app_server_schema",
        lambda: {"name": "codex-app-server-schema", "ok": True, "version": "test", "detail": "", "schemas": ["schema.json"]},
    )
    monkeypatch.setattr(
        cladex,
        "_doctor_windows_powershell_shim",
        lambda name: {"name": f"{name}-powershell-shim", "ok": True, "warning": False, "detail": ""},
    )

    rc = cladex.cmd_doctor(SimpleNamespace(json=True))

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["profiles"]["duplicateCodexPorts"]["18000"] == ["one", "two"]


def test_doctor_profiles_reports_unsafe_workspaces_and_mixed_100_scale(tmp_path: Path, monkeypatch) -> None:
    profiles: list[dict] = []
    env_by_path: dict[str, dict[str, str]] = {}
    for index in range(50):
        workspace = tmp_path / "workspaces" / f"codex-{index:02d}"
        account_home = tmp_path / "accounts" / f"codex-{index:02d}"
        workspace.mkdir(parents=True)
        env_path = tmp_path / "env" / f"codex-{index:02d}.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_by_path[str(env_path)] = {
            "DISCORD_BOT_TOKEN": f"codex-token-{index}",
            "CODEX_WORKDIR": str(workspace),
            "CODEX_HOME": str(account_home),
            "CODEX_APP_SERVER_PORT": str(18_000 + index),
            "ALLOWED_CHANNEL_IDS": str(10_000 + index),
        }
        profiles.append(
            {
                "name": f"codex-{index:02d}",
                "_relay_type": "codex",
                "workspace": str(workspace),
                "env_file": str(env_path),
                "_running": False,
            }
        )
    for index in range(50):
        workspace = tmp_path / "workspaces" / f"claude-{index:02d}"
        config_dir = tmp_path / "accounts" / f"claude-{index:02d}"
        workspace.mkdir(parents=True)
        env_path = tmp_path / "env" / f"claude-{index:02d}.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_by_path[str(env_path)] = {
            "DISCORD_BOT_TOKEN": f"claude-token-{index}",
            "CLAUDE_WORKDIR": str(workspace),
            "CLAUDE_CONFIG_DIR": str(config_dir),
        }
        profiles.append(
            {
                "name": f"claude-{index:02d}",
                "_relay_type": "claude",
                "workspace": str(workspace),
                "env_file": str(env_path),
                "_running": False,
            }
        )
    unsafe_workspace = Path(cladex.__file__).resolve().parents[1]
    unsafe_env = tmp_path / "env" / "unsafe.env"
    env_by_path[str(unsafe_env)] = {
        "DISCORD_BOT_TOKEN": "unsafe-token",
        "CODEX_WORKDIR": str(unsafe_workspace),
        "CODEX_APP_SERVER_PORT": "19999",
    }
    profiles.append(
        {
            "name": "unsafe-codex",
            "_relay_type": "codex",
            "workspace": str(unsafe_workspace),
            "env_file": str(unsafe_env),
            "_running": False,
        }
    )

    monkeypatch.setattr(cladex, "get_all_profiles", lambda: profiles)
    monkeypatch.setattr(cladex.relayctl, "_load_env_file", lambda path: env_by_path[str(path)])
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: env_by_path[str(profile["env_file"])])

    result = cladex._doctor_profiles()

    assert result["count"] == 101
    assert result["codex"] == 51
    assert result["claude"] == 50
    assert result["duplicateCodexPorts"] == {}
    assert result["accountHomes"] == {"codex": 51, "claude": 50}
    assert result["sharedAccountHomes"] == {"codex": {}, "claude": {}}
    assert result["unsafeWorkspaces"][0]["name"] == "unsafe-codex"
    assert "overlaps protected CLADEX/runtime root" in result["unsafeWorkspaces"][0]["reason"]


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


def test_cmd_update_passes_fields_to_update_profile(monkeypatch, capsys) -> None:
    updated: list[tuple[dict, dict]] = []
    profile = {"name": "codex-one", "_relay_type": "codex", "workspace": "C:/repo", "_bot_name": "Tyson"}
    monkeypatch.setattr(cladex, "_filter_profiles", lambda name=None, relay_type=None: [profile])
    monkeypatch.setattr(
        cladex,
        "update_profile",
        lambda selected, **kwargs: updated.append((selected, kwargs)),
    )

    rc = cladex.cmd_update(
        SimpleNamespace(
            name="codex-one",
            type="codex",
            bot_name="Tyson",
            model="gpt-explicit",
            trigger_mode="mention_or_dm",
            allow_dms=True,
            deny_dms=False,
            allowed_user_ids="1,2",
            allowed_channel_id="3",
            json=False,
        )
    )

    assert rc == 0
    assert updated[0][1]["bot_name"] == "Tyson"
    assert updated[0][1]["allow_dms"] is True
    assert "Updated codex-one [codex]." in capsys.readouterr().out


def test_project_list_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cladex,
        "_load_cladex_projects",
        lambda: {"projects": [{"name": "core", "members": [{"name": "codex-one", "relayType": "codex"}]}]},
    )
    monkeypatch.setattr(
        cladex,
        "_resolve_project_members",
        lambda project: ([{"name": "codex-one", "_relay_type": "codex", "workspace": "C:/repo", "_bot_name": "Tyson"}], []),
    )

    rc = cladex.cmd_project_list(SimpleNamespace(json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "core"
    assert payload[0]["members"][0]["displayName"] == "Tyson"


def test_load_cladex_projects_migrates_legacy_codex_projects(monkeypatch) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(cladex, "CLADEX_PROJECTS_PATH", Path("C:/missing/projects.json"))
    monkeypatch.setattr(
        cladex.relayctl,
        "_load_registry",
        lambda: {"projects": [{"name": "gl", "profiles": ["kurt-0cc9b99f-66db"]}]},
    )
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [
            {"name": "kurt-0cc9b99f-66db", "_relay_type": "codex", "workspace": "C:/workspace/kurt"}
        ] if name == "kurt-0cc9b99f-66db" else [],
    )
    monkeypatch.setattr(cladex, "_save_cladex_projects", lambda payload: saved.append(payload))

    payload = cladex._load_cladex_projects()

    assert payload["projects"][0]["name"] == "gl"
    assert payload["projects"][0]["members"][0]["name"] == "kurt-0cc9b99f-66db"
    assert saved[0]["projects"][0]["name"] == "gl"


def test_cmd_chat_history_returns_operator_messages(monkeypatch, capsys) -> None:
    profile = {"name": "codex-one", "_relay_type": "codex"}
    monkeypatch.setattr(cladex, "_filter_profiles", lambda name=None, relay_type=None: [profile])
    monkeypatch.setattr(
        cladex,
        "_read_operator_history",
        lambda selected: [{"id": "m1", "role": "assistant", "content": "Ready.", "channelId": "123"}],
    )

    rc = cladex.cmd_chat_history(SimpleNamespace(name="codex-one", type="codex", json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["messages"][0]["content"] == "Ready."
