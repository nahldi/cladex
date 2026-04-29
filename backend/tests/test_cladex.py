from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import api_runner
import cladex


CLAUDE_SAFE_ENV = {
    "DISCORD_BOT_TOKEN": "test",
    "ALLOWED_CHANNEL_IDS": "123",
    "ALLOW_DMS": "false",
}


def test_claude_state_dir_rejects_path_traversal_namespace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", tmp_path / "claude-data")

    valid = cladex._claude_state_dir({"state_namespace": "forge-ce0eef1b"})

    assert valid == (tmp_path / "claude-data" / "state" / "forge-ce0eef1b").resolve()
    for namespace in ("../outside", "nested/path", "nested\\path", "", ".", ".."):
        try:
            cladex._claude_state_dir({"state_namespace": namespace})
        except ValueError:
            pass
        else:
            raise AssertionError(f"namespace should be rejected: {namespace!r}")


def test_api_runner_bounded_capture_marks_truncation() -> None:
    capture = api_runner.BoundedTextCapture(10)

    assert capture.write("0123456789abcdef") == 16

    value = capture.getvalue()
    assert value.startswith("0123456789")
    assert "abcdef" not in value
    assert "truncated by CLADEX" in value


def test_api_runner_preserves_systemexit_message(monkeypatch) -> None:
    def boom() -> None:
        raise SystemExit("specific failure")

    monkeypatch.setitem(api_runner.MODULES, "boom.py", SimpleNamespace(main=boom))

    payload = api_runner._run_module("boom.py", [])

    assert payload["code"] == 1
    assert "specific failure" in payload["stderr"]


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


def test_start_codex_profile_delegates_to_relayctl(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(cladex.relayctl, "_run_profile", lambda profile: calls.append(profile) or 0)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    profile = {"name": "codex-one", "_relay_type": "codex", "workspace": str(workspace)}
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
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: dict(CLAUDE_SAFE_ENV))
    monkeypatch.setattr(cladex, "_wait_for_claude_worker_startup", lambda profile, *, pid: None)
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
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: dict(CLAUDE_SAFE_ENV))
    monkeypatch.setattr(cladex, "_wait_for_claude_worker_startup", lambda profile, *, pid: None)
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


def test_start_claude_profile_returns_when_already_running(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "forge.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    workspace = tmp_path / "forge"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    launches: list[list[str]] = []
    runtime_checks: list[str] = []
    runtime_bootstraps: list[bool] = []

    monkeypatch.setattr(cladex, "_claude_state_dir", lambda profile: state_dir)
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: dict(CLAUDE_SAFE_ENV))
    monkeypatch.setattr(
        cladex,
        "_claude_profile_runtime_state",
        lambda profile: runtime_checks.append(profile["name"]) or {"running": True, "ready": True},
    )
    monkeypatch.setattr(cladex, "_ensure_claude_background_runtime", lambda: runtime_bootstraps.append(True))
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

    assert runtime_checks == ["forge-ce0eef1b-09de"]
    assert launches == []
    assert runtime_bootstraps == []


def test_start_claude_profile_waits_when_existing_worker_not_ready(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "forge.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    workspace = tmp_path / "forge"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    waited: list[int] = []

    monkeypatch.setattr(cladex, "_claude_state_dir", lambda profile: state_dir)
    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: dict(CLAUDE_SAFE_ENV))
    monkeypatch.setattr(cladex, "_claude_profile_runtime_state", lambda profile: {"running": True, "ready": False, "pid": 987})
    monkeypatch.setattr(cladex, "_wait_for_claude_worker_startup", lambda profile, *, pid: waited.append(pid))
    monkeypatch.setattr(cladex, "_ensure_claude_background_runtime", lambda: (_ for _ in ()).throw(AssertionError("no bootstrap")))
    monkeypatch.setattr(cladex.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no launch")))

    profile = {
        "name": "forge-ce0eef1b-09de",
        "_relay_type": "claude",
        "workspace": str(workspace),
        "env_file": str(env_file),
        "state_namespace": "forge-ce0eef1b",
    }

    cladex.start_profile(profile)

    assert waited == [987]


def test_wait_for_claude_worker_startup_requires_ready_state(monkeypatch) -> None:
    states = iter(
        [
            {"running": True, "ready": False, "raw_status": "starting", "status_message": "connecting"},
            {"running": True, "ready": True, "raw_status": "ready", "status_message": "ready"},
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(cladex, "_claude_profile_runtime_state", lambda profile: next(states))
    monkeypatch.setattr(cladex.time, "sleep", lambda seconds: sleeps.append(seconds))

    cladex._wait_for_claude_worker_startup({"name": "claude-one"}, pid=123, timeout_seconds=1.0)

    assert sleeps == [0.25]


def test_wait_for_claude_worker_startup_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(
        cladex,
        "_claude_profile_runtime_state",
        lambda profile: {"running": True, "ready": False, "raw_status": "error", "status_message": "bad token"},
    )

    try:
        cladex._wait_for_claude_worker_startup({"name": "claude-one"}, pid=123, timeout_seconds=1.0)
    except RuntimeError as exc:
        assert "bad token" in str(exc)
    else:
        raise AssertionError("expected startup error")


def test_start_claude_profile_rejects_existing_empty_allowlists(tmp_path: Path, monkeypatch) -> None:
    import pytest

    env_file = tmp_path / "forge.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    workspace = tmp_path / "forge"
    workspace.mkdir()

    monkeypatch.setattr(cladex, "_load_claude_env", lambda profile: {"DISCORD_BOT_TOKEN": "test"})

    profile = {
        "name": "forge-ce0eef1b-09de",
        "_relay_type": "claude",
        "workspace": str(workspace),
        "env_file": str(env_file),
        "state_namespace": "forge-ce0eef1b",
    }

    with pytest.raises(ValueError, match="Claude profiles require"):
        cladex.start_profile(profile)


def test_start_claude_profile_rejects_non_directory_workspace(tmp_path: Path, monkeypatch) -> None:
    import pytest

    env_file = tmp_path / "forge.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\nALLOWED_CHANNEL_IDS=1234567890\n", encoding="utf-8")
    workspace_file = tmp_path / "forge.txt"
    workspace_file.write_text("not a directory", encoding="utf-8")
    launches: list[list[str]] = []

    monkeypatch.setattr(
        cladex,
        "_load_claude_env",
        lambda profile: {"DISCORD_BOT_TOKEN": "test", "ALLOWED_CHANNEL_IDS": "1234567890"},
    )
    monkeypatch.setattr(
        cladex.subprocess,
        "Popen",
        lambda command, **kwargs: launches.append(command) or SimpleNamespace(pid=321),
    )

    profile = {
        "name": "forge-ce0eef1b-09de",
        "_relay_type": "claude",
        "workspace": str(workspace_file),
        "env_file": str(env_file),
        "state_namespace": "forge-ce0eef1b",
    }

    with pytest.raises(ValueError) as excinfo:
        cladex.start_profile(profile)

    assert "workspace does not exist or is not a directory" in str(excinfo.value)
    assert launches == []


def test_claude_runtime_state_cleans_duplicate_namespace_workers(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    state_dir = data_root / "state" / "ns-dup"
    state_dir.mkdir(parents=True)
    (state_dir / "relay.pid").write_text("222", encoding="utf-8")
    (state_dir / "status.json").write_text(json.dumps({"status": "ready"}), encoding="utf-8")
    live_pids = {111, 222}
    killed: list[int] = []

    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", data_root)
    monkeypatch.setattr(cladex, "_discovered_claude_bot_pids", lambda profile: [111, 222])
    monkeypatch.setattr(cladex, "_pid_matches_claude_profile", lambda profile, pid: pid in live_pids)
    monkeypatch.setattr(cladex.psutil, "pid_exists", lambda pid: pid in live_pids)

    def fake_terminate(pid: int) -> bool:
        killed.append(pid)
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(cladex.relayctl, "terminate_process_tree", fake_terminate)
    monkeypatch.setattr(cladex.relayctl, "_wait_for_process_exit", lambda pid, timeout_seconds=5.0: None)

    state = cladex._claude_profile_runtime_state({"state_namespace": "ns-dup"})

    assert killed == [111]
    assert state["running"] is True
    assert state["ready"] is True
    assert state["pid"] == 222
    assert state["pids"] == [222]
    assert (state_dir / "relay.pid").read_text(encoding="utf-8") == "222"



def test_stop_codex_profile_delegates_to_relayctl(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(cladex.relayctl, "_stop_profile", lambda profile: calls.append(profile) or 0)

    profile = {"name": "codex-one", "_relay_type": "codex"}
    cladex.stop_profile(profile)

    assert calls == [profile]


def test_doctor_codex_account_falls_back_to_warning_when_binary_missing(monkeypatch) -> None:
    """Doctor surfaces account/rate-limit info as a warning when codex is
    absent or the app-server can't be reached, never as a hard failure."""
    monkeypatch.setattr(cladex.relayctl, "resolve_codex_bin", lambda: "")
    result = cladex._doctor_codex_account()
    assert result["name"] == "codex-account"
    assert result["ok"] is True
    assert result["warning"] is True
    assert result["account"] == {}
    assert result["rateLimits"] == {}


def test_doctor_codex_account_parses_app_server_responses(monkeypatch) -> None:
    """Doctor pings codex app-server with initialize + getAccount +
    getAccountRateLimits and parses the responses into the warning entry."""
    sequence = iter([
        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "result": {"accountType": "ChatGPT", "planType": "Plus"}},
        {"jsonrpc": "2.0", "id": 3, "result": {"primary": {"used": 12, "limit": 100, "windowMinutes": 60}}},
    ])

    class FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.stdin = SimpleNamespace(write=lambda data: None, flush=lambda: None, close=lambda: None, closed=False)
            self.stdout = SimpleNamespace(readline=lambda: (json.dumps(next(sequence)) + "\n").encode("utf-8"))
            self.stderr = SimpleNamespace(read=lambda: b"")

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(cladex.relayctl, "resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(cladex.subprocess, "Popen", FakePopen)

    result = cladex._doctor_codex_account()
    assert result["name"] == "codex-account"
    assert result["ok"] is True
    assert result["warning"] is False
    assert result["account"]["accountType"] == "ChatGPT"
    assert result["account"]["planType"] == "Plus"
    assert result["rateLimits"]["primary"]["limit"] == 100
    assert "plan=Plus" in result["detail"]


def test_doctor_required_version_fails_below_declared_floor(monkeypatch) -> None:
    monkeypatch.setattr(
        cladex,
        "_doctor_command",
        lambda command: {"ok": True, "output": "v20.11.0\n"},
    )

    result = cladex._doctor_required_version("node", ["node", "--version"], minimum="22.12.0")

    assert result["ok"] is False
    assert result["requiredVersion"] == ">=22.12.0"
    assert "below required" in result["detail"]


def test_doctor_python_version_uses_declared_floor() -> None:
    result = cladex._doctor_runtime_version("python", "3.9.18", "python.exe", minimum="3.10")

    assert result["ok"] is False
    assert result["requiredVersion"] == ">=3.10"


def test_update_claude_profile_persists_via_local_save(tmp_path: Path, monkeypatch) -> None:
    """`_update_claude_profile` historically called a bare `_save_registry`
    that was undefined in `cladex.py`, so the path crashed at runtime with
    `NameError`. This regression test exercises a successful Claude profile
    edit end-to-end against tmp_path so any future drop of `_save_claude_registry`
    fails the suite instead of the user."""
    config_root = tmp_path / "config"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env_file = profiles_dir / "claude-one.env"
    env_file.write_text(
        "DISCORD_BOT_TOKEN=existing-token\n"
        f"CLAUDE_WORKDIR={workspace}\n"
        "ALLOWED_CHANNEL_IDS=42\n"
        "ALLOWED_USER_IDS=7\n"
        "BOT_TRIGGER_MODE=mention_or_dm\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cladex, "CLAUDE_CONFIG_ROOT", config_root)
    monkeypatch.setattr(cladex, "CLAUDE_REGISTRY_PATH", config_root / "workspaces.json")
    monkeypatch.setattr(cladex, "_load_claude_registry", lambda: {"profiles": [{"name": "claude-one", "env_file": str(env_file)}], "projects": []})
    monkeypatch.setattr(cladex.claude_relay, "PROFILES_DIR", profiles_dir)

    profile = {"name": "claude-one", "env_file": str(env_file)}
    cladex._update_claude_profile(profile, bot_name="Renamed", trigger_mode="mention_or_dm")

    saved = json.loads((config_root / "workspaces.json").read_text(encoding="utf-8"))
    assert any(p.get("bot_name") == "Renamed" for p in saved["profiles"])


def test_remove_claude_profile_uses_atomic_registry_writer(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "claude.env"
    env_file.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    saved: list[dict] = []

    monkeypatch.setattr(cladex, "stop_profile", lambda profile: None)
    monkeypatch.setattr(
        cladex,
        "_load_claude_registry",
        lambda: {"profiles": [{"name": "claude-one"}, {"name": "claude-two"}], "projects": []},
    )
    monkeypatch.setattr(cladex, "_save_claude_registry", lambda registry: saved.append(registry))

    cladex._remove_claude_profile({"name": "claude-one", "env_file": str(env_file)})

    assert saved == [{"profiles": [{"name": "claude-two"}], "projects": []}]
    assert not env_file.exists()


def test_claude_running_state_uses_relay_pid(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "config"
    data_root = tmp_path / "data"
    state_dir = data_root / "state" / "ns-one"
    state_dir.mkdir(parents=True)
    (state_dir / "relay.pid").write_text("1234", encoding="utf-8")
    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", data_root)
    monkeypatch.setattr(cladex, "_pid_matches_claude_profile", lambda profile, pid: pid == 1234)
    monkeypatch.setattr(cladex.psutil, "pid_exists", lambda pid: pid == 1234)

    state = cladex._claude_profile_runtime_state({"state_namespace": "ns-one"})

    # PID alone now means "process spawned but not yet ready"; readiness is
    # confirmed once claude_bot writes its first status.json.
    assert state["running"] is True
    assert state["ready"] is False
    assert state["pid"] == 1234


def test_claude_ready_requires_status_json_after_pid_appears(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    state_dir = data_root / "state" / "ns-ready"
    state_dir.mkdir(parents=True)
    (state_dir / "relay.pid").write_text("9999", encoding="utf-8")
    (state_dir / "status.json").write_text(
        json.dumps({"status": "ready", "detail": "Claude ready for next turn"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", data_root)
    monkeypatch.setattr(cladex, "_pid_matches_claude_profile", lambda profile, pid: pid == 9999)
    monkeypatch.setattr(cladex.psutil, "pid_exists", lambda pid: pid == 9999)

    state = cladex._claude_profile_runtime_state({"state_namespace": "ns-ready"})

    assert state["running"] is True
    assert state["ready"] is True


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
    monkeypatch.setattr(cladex, "_pid_matches_claude_profile", lambda profile, pid: pid == 555)
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


def test_claude_runtime_state_discards_stale_relay_pid(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    state_dir = data_root / "state" / "ns-stale"
    state_dir.mkdir(parents=True)
    pid_file = state_dir / "relay.pid"
    pid_file.write_text("777", encoding="utf-8")
    (state_dir / "status.json").write_text(json.dumps({"status": "ready"}), encoding="utf-8")
    killed: list[int] = []

    monkeypatch.setattr(cladex, "CLAUDE_DATA_ROOT", data_root)
    monkeypatch.setattr(cladex, "_pid_matches_claude_profile", lambda profile, pid: False)
    monkeypatch.setattr(cladex, "_discovered_claude_bot_pids", lambda profile: [])
    monkeypatch.setattr(cladex.psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(cladex.relayctl, "terminate_process_tree", lambda pid: killed.append(pid) or True)

    state = cladex._claude_profile_runtime_state({"state_namespace": "ns-stale"})

    assert state["running"] is False
    assert state["ready"] is False
    assert state["pid"] is None
    assert state["pids"] == []
    assert not pid_file.exists()
    assert killed == []


def test_stop_claude_profile_terminates_pid(monkeypatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr(cladex, "_claude_profile_runtime_state", lambda profile: {"pid": 4321})
    monkeypatch.setattr(cladex.relayctl, "terminate_process_tree", lambda pid: killed.append(pid) or True)

    profile = {"name": "claude-one", "_relay_type": "claude", "workspace": "C:/claude", "state_namespace": "ns"}
    cladex.stop_profile(profile)

    assert killed == [4321]


def test_stop_claude_profile_terminates_duplicate_pids(tmp_path: Path, monkeypatch) -> None:
    killed: list[int] = []
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "relay.pid").write_text("222", encoding="utf-8")
    monkeypatch.setattr(cladex, "_claude_state_dir", lambda profile: state_dir)
    monkeypatch.setattr(cladex, "_claude_profile_runtime_state", lambda profile: {"pid": 222, "pids": [111, 222]})
    monkeypatch.setattr(cladex.relayctl, "terminate_process_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(cladex.relayctl, "_wait_for_process_exit", lambda pid, timeout_seconds=5.0: None)

    profile = {"name": "claude-one", "_relay_type": "claude", "workspace": "C:/claude", "state_namespace": "ns"}
    cladex.stop_profile(profile)

    assert killed == [111, 222]
    assert not (state_dir / "relay.pid").exists()


def test_load_claude_registry_is_tolerant(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "workspaces.json"
    path.write_text(json.dumps({"profiles": [{"name": "x"}]}), encoding="utf-8")
    monkeypatch.setattr(cladex, "CLAUDE_REGISTRY_PATH", path)

    payload = cladex._load_claude_registry()

    assert payload["profiles"][0]["name"] == "x"
    assert payload["projects"] == []


def test_load_json_file_quarantines_corrupt_state(tmp_path: Path) -> None:
    import pytest

    path = tmp_path / "workspaces.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON state file"):
        cladex._load_json_file(path, default={"profiles": [], "projects": []})

    assert list(tmp_path.glob("workspaces.json.corrupt-*"))


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
        "_doctor_required_version",
        lambda name, command, minimum: {
            "name": name,
            "ok": True,
            "version": "test",
            "requiredVersion": f">={minimum}",
            "detail": "",
        },
    )
    monkeypatch.setattr(
        cladex,
        "_doctor_runtime_version",
        lambda name, version, detail, minimum: {
            "name": name,
            "ok": True,
            "version": version,
            "requiredVersion": f">={minimum}",
            "detail": detail,
        },
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


def test_cmd_update_json_returns_renamed_profile(monkeypatch, capsys) -> None:
    old_profile = {"name": "old", "_relay_type": "claude"}
    new_profile = {
        "name": "new",
        "_relay_type": "claude",
        "_running": False,
        "_ready": False,
        "_provider": "claude-code",
        "workspace": "C:/repo",
    }
    updated = False

    def fake_filter(name=None, relay_type=None):
        if not updated:
            return [old_profile]
        if name == "new":
            return [new_profile]
        return []

    def fake_update(profile, **kwargs):
        nonlocal updated
        updated = True
        return {"name": "new", "_relay_type": "claude"}

    monkeypatch.setattr(cladex, "_filter_profiles", fake_filter)
    monkeypatch.setattr(cladex, "update_profile", fake_update)

    rc = cladex.cmd_update(
        SimpleNamespace(
            name="old",
            type="claude",
            workspace=None,
            discord_bot_token=None,
            discord_bot_token_env=None,
            bot_name="New",
            model=None,
            codex_home=None,
            claude_config_dir=None,
            trigger_mode=None,
            allow_dms=False,
            deny_dms=False,
            operator_ids=None,
            allowed_user_ids=None,
            allowed_bot_ids=None,
            allowed_channel_id=None,
            allowed_channel_author_ids=None,
            channel_no_mention_author_ids=None,
            channel_history_limit=None,
            startup_dm_user_ids=None,
            startup_dm_text=None,
            startup_channel_text=None,
            json=True,
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "new"
    assert payload["relayType"] == "claude"


def test_update_profile_rejects_non_numeric_user_ids(monkeypatch) -> None:
    """F0004: PATCH /api/profiles forwards the raw allowed-user CSV; the
    update path must reject non-numeric IDs rather than silently dropping
    them via _parse_csv_ids and leaving an empty allowlist."""
    profile = {
        "name": "codex-one",
        "_relay_type": "codex",
        "workspace": "C:/repo",
        "env_file": "C:/repo/.env",
    }

    import pytest

    with pytest.raises(ValueError) as excinfo:
        cladex.update_profile(profile, allowed_user_ids="not-a-discord-id")
    assert "numeric Discord IDs" in str(excinfo.value)


def test_update_profile_rejects_non_numeric_channel_id(monkeypatch) -> None:
    profile = {
        "name": "claude-one",
        "_relay_type": "claude",
        "workspace": "C:/repo",
        "env_file": "C:/repo/.env",
    }

    import pytest

    with pytest.raises(ValueError) as excinfo:
        cladex.update_profile(profile, allowed_channel_id="general")
    assert "numeric Discord IDs" in str(excinfo.value)


def test_update_claude_profile_rejects_unsupported_startup_notice_fields() -> None:
    profile = {
        "name": "claude-one",
        "_relay_type": "claude",
        "workspace": "C:/repo",
        "env_file": "C:/repo/.env",
    }

    import pytest

    with pytest.raises(ValueError, match="Codex profiles only"):
        cladex.update_profile(profile, startup_dm_text="online")


def test_update_profile_rejects_invalid_channel_history_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env_file = tmp_path / "codex.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={workspace}",
                "ALLOWED_CHANNEL_IDS=1234567890",
                "STATE_NAMESPACE=test-history-limit",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"name": "codex-one", "_relay_type": "codex", "workspace": str(workspace), "env_file": str(env_file)}

    import pytest

    with pytest.raises(ValueError) as excinfo:
        cladex.update_profile(profile, channel_history_limit="many")
    assert "channelHistoryLimit" in str(excinfo.value)


def test_update_profile_rejects_missing_workspace_before_persist(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_workspace = tmp_path / "missing"
    env_file = tmp_path / "codex.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={workspace}",
                "ALLOWED_CHANNEL_IDS=1234567890",
                "STATE_NAMESPACE=test-missing-workspace",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"name": "codex-one", "_relay_type": "codex", "workspace": str(workspace), "env_file": str(env_file)}
    persisted: list[dict] = []
    monkeypatch.setattr(cladex.relayctl, "_replace_profile_registration", lambda previous, new: persisted.append(new))

    import pytest

    with pytest.raises(ValueError) as excinfo:
        cladex.update_profile(profile, workspace=str(missing_workspace))

    assert "workspace does not exist or is not a directory" in str(excinfo.value)
    assert persisted == []


def test_cmd_update_returns_error_for_invalid_id(monkeypatch, capsys) -> None:
    """F0004: Invalid Discord IDs in cmd_update should surface as a clear
    JSON error (status 2) rather than a stack trace, so the API can map them
    to a 4xx response."""
    profile = {"name": "codex-one", "_relay_type": "codex", "workspace": "C:/repo", "env_file": "C:/repo/.env"}
    monkeypatch.setattr(cladex, "_filter_profiles", lambda name=None, relay_type=None: [profile])

    rc = cladex.cmd_update(
        SimpleNamespace(
            name="codex-one",
            type="codex",
            allow_dms=True,
            deny_dms=False,
            allowed_user_ids="not-a-discord-id",
            allowed_channel_id=None,
            json=True,
        )
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert "numeric Discord IDs" in payload["error"]


def test_workspace_relay_skill_avoids_command_line_token_examples() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    skill_path = backend_root / "discord_codex_relay_plugin" / "bundle" / "skills" / "workspace-discord-relay" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    assert "--discord-bot-token <token>" not in text
    assert "CLADEX_REGISTER_DISCORD_BOT_TOKEN" in text


def test_workspace_relay_bootstrap_default_is_pinned_to_project_version() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    pyproject = (backend_root / "pyproject.toml").read_text(encoding="utf-8")
    bootstrap = (
        backend_root
        / "discord_codex_relay_plugin"
        / "bundle"
        / "skills"
        / "workspace-discord-relay"
        / "scripts"
        / "bootstrap.py"
    ).read_text(encoding="utf-8")

    version = re.search(r'^version = "([^"]+)"', pyproject, flags=re.MULTILINE)
    assert version is not None
    assert f'PACKAGE_VERSION = "{version.group(1)}"' in bootstrap
    assert "DEFAULT_PACKAGE_SPEC" in bootstrap


def test_backend_wheel_includes_constraints_and_workspace_relay_skill(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    src = tmp_path / "backend-src"
    ignore = shutil.ignore_patterns("build", "*.egg-info", "__pycache__", ".pytest_cache")
    shutil.copytree(backend_root, src, ignore=ignore)
    out_dir = tmp_path / "dist"
    out_dir.mkdir()

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out_dir), str(src)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    wheel = next(out_dir.glob("discord_codex_relay-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert any(name.endswith(".data/data/share/discord-codex-relay/constraints.txt") for name in names)
    assert "discord_codex_relay_plugin/bundle/skills/workspace-discord-relay/SKILL.md" in names
    assert "discord_codex_relay_plugin/bundle/skills/workspace-discord-relay/scripts/bootstrap.py" in names


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


def test_cmd_chat_reads_message_file(tmp_path: Path, monkeypatch, capsys) -> None:
    message_file = tmp_path / "message.txt"
    message_file.write_text("hello from a file\n", encoding="utf-8")
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [{"name": "codex-one", "_relay_type": "codex"}],
    )

    def fake_chat(profile, *, message, channel_id, sender_name, sender_id):
        captured["message"] = message
        return {"ok": True, "reply": "done"}

    monkeypatch.setattr(cladex, "_chat_with_profile", fake_chat)

    rc = cladex.cmd_chat(
        SimpleNamespace(
            name="codex-one",
            type="codex",
            message="",
            message_file=str(message_file),
            channel_id=None,
            sender_name="Operator",
            sender_id="0",
            json=True,
        )
    )

    assert rc == 0
    assert captured["message"] == "hello from a file"
    assert json.loads(capsys.readouterr().out)["reply"] == "done"


def test_cmd_chat_missing_message_file_returns_json_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cladex,
        "_filter_profiles",
        lambda name=None, relay_type=None: [{"name": "codex-one", "_relay_type": "codex"}],
    )

    rc = cladex.cmd_chat(
        SimpleNamespace(
            name="codex-one",
            type="codex",
            message="",
            message_file=str(tmp_path / "missing.txt"),
            channel_id=None,
            sender_name="Operator",
            sender_id="0",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert "Could not read message file" in payload["error"]


def test_cmd_review_findings_missing_job_is_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cladex.review_swarm, "REVIEW_DATA_ROOT", tmp_path / "reviews")

    rc = cladex.cmd_review_findings(SimpleNamespace(id="review-20260429-010203-abcdef12"))

    assert rc == 1
    assert "No review job found" in capsys.readouterr().err
