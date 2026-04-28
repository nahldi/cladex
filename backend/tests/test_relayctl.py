from __future__ import annotations

import argparse
import concurrent.futures
import os
from pathlib import Path
import json
import threading
import time

import install_plugin
import relay_common
import relayctl


def _windows_project_path(*parts: str) -> str:
    return "\\".join(("C:", "Users", "exampleuser", *parts))


def test_state_dir_is_not_repo_local() -> None:
    state_dir = relay_common.state_dir_for_namespace("example")
    assert "state" in state_dir.parts
    assert Path(__file__).resolve().parents[1] not in state_dir.parents


def test_default_profile_port_allocator_avoids_registered_collisions(tmp_path: Path, monkeypatch) -> None:
    used_ports: set[int] = set()
    monkeypatch.setattr(relayctl, "_registered_profile_ports", lambda: set(used_ports))
    monkeypatch.setattr(relayctl, "_port_is_available", lambda port: port not in used_ports)

    for index in range(100):
        port = relayctl._default_app_server_port_for_profile(tmp_path / f"agent-{index}", token=f"token-{index}")
        assert port not in used_ports
        assert relay_common.DEFAULT_APP_SERVER_PORT_START <= port < (
            relay_common.DEFAULT_APP_SERVER_PORT_START + relay_common.DEFAULT_APP_SERVER_PORT_RANGE
        )
        used_ports.add(port)


def test_register_handles_100_isolated_codex_profiles_without_port_or_account_collisions(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_root = tmp_path / "config"
    profiles_dir = config_root / "profiles"
    registry_path = config_root / "workspaces.json"
    used_ports: set[int] = set()
    monkeypatch.setattr(relayctl, "PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(relayctl, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(relayctl, "_registered_profile_ports", lambda: set(used_ports))
    monkeypatch.setattr(relayctl, "_port_is_available", lambda port: port not in used_ports)
    parser = relayctl.build_parser()
    seen_env_files: set[Path] = set()

    for index in range(100):
        workspace = tmp_path / "workspaces" / f"agent-{index:03d}"
        codex_home = tmp_path / "accounts" / f"codex-{index:03d}"
        workspace.mkdir(parents=True)
        args = parser.parse_args(
            [
                "register",
                "--workspace",
                str(workspace),
                "--discord-bot-token",
                f"token-{index:03d}",
                "--bot-name",
                f"Agent {index:03d}",
                "--allowed-channel-id",
                str(10_000_000_000_000_000 + index),
                "--codex-home",
                str(codex_home),
            ]
        )
        assert relayctl.cmd_register(args) == 0
        new_env_files = [item for item in profiles_dir.glob("*.env") if item not in seen_env_files]
        assert len(new_env_files) == 1
        env_file = new_env_files[0]
        seen_env_files.add(env_file)
        env = relayctl._load_env_file(env_file)
        port = int(env["CODEX_APP_SERVER_PORT"])
        assert port not in used_ports
        used_ports.add(port)

    profiles = relayctl._all_registered_profiles()
    envs = [relayctl._load_env_file(Path(profile["env_file"])) for profile in profiles]
    names = {profile["name"] for profile in profiles}
    ports = {env["CODEX_APP_SERVER_PORT"] for env in envs}
    homes = {env["CODEX_HOME"] for env in envs}
    workspaces = {env["CODEX_WORKDIR"] for env in envs}

    assert len(profiles) == 100
    assert len(names) == 100
    assert len(ports) == 100
    assert len(homes) == 100
    assert len(workspaces) == 100
    assert all(env["CODEX_APP_SERVER_TRANSPORT"] == "stdio" for env in envs)
    assert all(env["CODEX_FULL_ACCESS"] == "false" for env in envs)
    assert all(env["CODEX_READ_ONLY"] == "false" for env in envs)
    assert "Registered" in capsys.readouterr().out


def test_register_reads_discord_token_from_env_and_clears_it(tmp_path: Path, monkeypatch) -> None:
    """F0083: tokens must not be visible in process command lines. The CLI
    accepts CLADEX_REGISTER_DISCORD_BOT_TOKEN as an alternative to
    --discord-bot-token, and the env var is unset after consumption so the
    value does not flow to grandchild processes."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(relayctl, "PROFILES_DIR", profiles_dir)
    monkeypatch.setenv("CLADEX_REGISTER_DISCORD_BOT_TOKEN", "secret-token-from-env")
    monkeypatch.setattr(relayctl, "_register_profile", lambda profile: None)
    parser = relayctl.build_parser()
    args = parser.parse_args(
        [
            "register",
            "--workspace",
            str(workspace),
            "--allowed-channel-id",
            "1234567890",
        ]
    )

    rc = relayctl.cmd_register(args)
    assert rc == 0
    assert os.environ.get("CLADEX_REGISTER_DISCORD_BOT_TOKEN") in (None, "")
    env_files = list(profiles_dir.glob("*.env"))
    assert env_files, "register should write a profile env file"
    contents = env_files[0].read_text(encoding="utf-8")
    assert "DISCORD_BOT_TOKEN=secret-token-from-env" in contents


def test_register_requires_token_via_arg_or_env(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(relayctl, "PROFILES_DIR", profiles_dir)
    monkeypatch.delenv("CLADEX_REGISTER_DISCORD_BOT_TOKEN", raising=False)
    parser = relayctl.build_parser()
    args = parser.parse_args(
        [
            "register",
            "--workspace",
            str(workspace),
            "--allowed-channel-id",
            "1234567890",
        ]
    )
    try:
        relayctl.cmd_register(args)
    except SystemExit as exc:
        assert "Discord bot token" in str(exc)
    else:
        raise AssertionError("cmd_register must require a token")


def test_register_rejects_allow_dms_without_user_allowlist(tmp_path: Path, monkeypatch) -> None:
    """F0034: `--allow-dms` without `--allowed-user-id` would expose the
    Codex relay to any DM sender. cmd_register must refuse this case."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    parser = relayctl.build_parser()
    args = parser.parse_args(
        [
            "register",
            "--workspace",
            str(workspace),
            "--discord-bot-token",
            "token",
            "--allow-dms",
        ]
    )
    monkeypatch.setattr(relayctl, "_register_profile", lambda profile: None)

    try:
        relayctl.cmd_register(args)
    except SystemExit as exc:
        message = str(exc)
        assert "--allow-dms" in message
        assert "--allowed-user-id" in message
    else:
        raise AssertionError("cmd_register must reject --allow-dms without an allowlist")


def test_register_rejects_protected_cladex_workspace(monkeypatch) -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(
        [
            "register",
            "--workspace",
            str(Path(relayctl.__file__).resolve().parents[1]),
            "--discord-bot-token",
            "token",
            "--allowed-channel-id",
            "1234567890",
        ]
    )
    monkeypatch.setattr(relayctl, "_register_profile", lambda profile: None)

    try:
        relayctl.cmd_register(args)
    except SystemExit as exc:
        assert "overlaps protected CLADEX/runtime root" in str(exc)
    else:
        raise AssertionError("cmd_register should reject protected CLADEX workspaces")


def test_prepare_relay_codex_home_copies_auth_without_personal_config(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "target-home"
    workspace = tmp_path / "workspace"
    source_home.mkdir()
    workspace.mkdir()
    (source_home / "auth.json").write_text('{"token":"value"}\n', encoding="utf-8")
    (source_home / "cap_sid").write_text("sid\n", encoding="utf-8")
    (source_home / "config.toml").write_text(
        '[mcp_servers.playwright]\ncommand = "npx"\nargs = ["@playwright/mcp@latest"]\n',
        encoding="utf-8",
    )

    relay_home = relay_common.prepare_relay_codex_home(workspace, source_home=source_home, target_home=target_home)

    assert relay_home == target_home.resolve()
    assert (relay_home / "auth.json").read_text(encoding="utf-8") == '{"token":"value"}\n'
    assert (relay_home / "cap_sid").read_text(encoding="utf-8") == "sid\n"
    config_text = (relay_home / "config.toml").read_text(encoding="utf-8")
    assert "mcp_servers" not in config_text
    assert "@playwright/mcp" not in config_text
    assert 'sandbox = "elevated"' in config_text
    assert f"[projects.{relay_common._toml_project_key(str(workspace.resolve()))}]" in config_text


def test_prepare_relay_codex_home_preserves_parallel_workspace_trusts(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "target-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"value"}\n', encoding="utf-8")
    workspaces = [tmp_path / "agent-a", tmp_path / "agent-b", tmp_path / "agent-c"]
    for workspace in workspaces:
        workspace.mkdir()
    start = threading.Barrier(len(workspaces))

    def _prepare(workspace: Path) -> None:
        start.wait()
        relay_common.prepare_relay_codex_home(workspace, source_home=source_home, target_home=target_home)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workspaces)) as executor:
        list(executor.map(_prepare, workspaces))

    config_text = (target_home / "config.toml").read_text(encoding="utf-8")
    for workspace in workspaces:
        assert f"[projects.{relay_common._toml_project_key(str(workspace.resolve()))}]" in config_text


def test_relay_codex_env_respects_explicit_codex_home(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    explicit_home = tmp_path / "account-home"
    default_home = tmp_path / "default-home"
    workspace.mkdir()
    default_home.mkdir()
    (default_home / "auth.json").write_text('{"token":"default"}\n', encoding="utf-8")

    env = relay_common.relay_codex_env(
        workspace,
        {"CODEX_HOME": str(explicit_home), "HOME": str(default_home), "USERPROFILE": str(default_home)},
    )

    assert env["CODEX_HOME"] == str(explicit_home.resolve())
    assert (explicit_home / "config.toml").exists()
    assert not (explicit_home / "auth.json").exists()


def test_auto_skill_preferences_can_disable_and_reenable(tmp_path: Path) -> None:
    original_path = install_plugin.EXTRAS_PREFS_PATH
    install_plugin.EXTRAS_PREFS_PATH = tmp_path / "extras.json"
    try:
        assert "playwright" in install_plugin.enabled_auto_skills()
        install_plugin.set_auto_skill_disabled("playwright", True)
        assert "playwright" not in install_plugin.enabled_auto_skills()
        prefs = install_plugin.load_extras_preferences()
        assert prefs["disabled"] == ["playwright"]
        install_plugin.set_auto_skill_disabled("playwright", False)
        assert "playwright" in install_plugin.enabled_auto_skills()
    finally:
        install_plugin.EXTRAS_PREFS_PATH = original_path


def test_terminate_process_tree_tolerates_child_lookup_race() -> None:
    class _FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def children(self, recursive: bool = False):
            raise relay_common.psutil.NoSuchProcess(self.pid)

        def terminate(self) -> None:
            return None

    original_process = relay_common.psutil.Process
    original_wait_procs = relay_common.psutil.wait_procs
    relay_common.psutil.Process = lambda pid: _FakeProcess(pid)
    relay_common.psutil.wait_procs = lambda processes, timeout=0: (processes, [])
    try:
        assert relay_common.terminate_process_tree(1234) is True
    finally:
        relay_common.psutil.Process = original_process
        relay_common.psutil.wait_procs = original_wait_procs


def test_profile_normalization_uses_publish_defaults(tmp_path: Path) -> None:
    env = relayctl._normalized_profile_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "ALLOWED_CHANNEL_IDS": "1234567890",
        }
    )
    assert env["CODEX_MODEL"] == ""
    assert env["CODEX_FULL_ACCESS"] == "false"
    assert env["CODEX_APP_SERVER_TRANSPORT"] == "stdio"
    assert env["BOT_TRIGGER_MODE"] == "mention_or_dm"
    assert env["CODEX_READ_ONLY"] == "false"
    assert env["OPEN_VISIBLE_TERMINAL"] == "false"
    assert env["RELAY_ATTACH_CHANNEL_ID"] == "1234567890"


def test_profile_normalization_preserves_optional_overrides(tmp_path: Path) -> None:
    env = relayctl._normalized_profile_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "BOT_TRIGGER_MODE": "all",
            "OPEN_VISIBLE_TERMINAL": "true",
            "ALLOWED_USER_IDS": "7",
            "ALLOWED_CHANNEL_AUTHOR_IDS": "1, 2, abc, 3",
            "CHANNEL_NO_MENTION_AUTHOR_IDS": "7, 9, nope",
        }
    )
    assert env["BOT_TRIGGER_MODE"] == "all"
    assert env["OPEN_VISIBLE_TERMINAL"] == "false"
    assert env["ALLOWED_CHANNEL_AUTHOR_IDS"] == "1,2,3,7,9"
    assert env["CHANNEL_NO_MENTION_AUTHOR_IDS"] == "7,9"


def test_profile_normalization_preserves_explicit_full_access(tmp_path: Path) -> None:
    env = relayctl._normalized_profile_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "CODEX_FULL_ACCESS": "true",
            "CODEX_MODEL": "gpt-explicit",
        }
    )
    assert env["CODEX_FULL_ACCESS"] == "true"
    assert env["CODEX_MODEL"] == "gpt-explicit"


def test_profile_normalization_preserves_explicit_codex_home(tmp_path: Path) -> None:
    account_home = tmp_path / "codex-account"
    env = relayctl._normalized_profile_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "CODEX_HOME": str(account_home),
        }
    )
    assert env["CODEX_HOME"] == str(account_home.resolve())


def test_profile_normalization_keeps_visible_terminal_only_for_websocket(tmp_path: Path) -> None:
    env = relayctl._normalized_profile_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "CODEX_APP_SERVER_TRANSPORT": "websocket",
            "OPEN_VISIBLE_TERMINAL": "true",
        }
    )
    assert env["OPEN_VISIBLE_TERMINAL"] == "true"


def test_profile_normalization_drops_stale_provider_keys(tmp_path: Path) -> None:
    env = relayctl._normalized_profile_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "RELAY_PROVIDER": "codex",
        }
    )
    assert "RELAY_PROVIDER" not in env


def test_quarantine_stale_session_bindings_uses_relay_codex_home(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    session_dir = state_dir / "sessions"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "channel-1.json"
    session_file.write_text('{"thread_id":"thread-live"}\n', encoding="utf-8")

    relay_home = tmp_path / "relay-home"
    live_sessions = relay_home / "sessions" / "2026" / "04" / "12"
    live_sessions.mkdir(parents=True)
    (live_sessions / "rollout-thread-live.jsonl").write_text("{}\n", encoding="utf-8")

    moved = relayctl._quarantine_stale_session_bindings(
        state_dir,
        tmp_path / "workspace",
        {"CODEX_HOME": str(relay_home)},
    )

    assert moved == 0
    assert session_file.exists()
    assert not (state_dir / "bad-sessions").exists()


def test_quarantine_stale_session_bindings_prunes_bad_session_history(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    session_dir = state_dir / "sessions"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "channel-1.json"
    session_file.write_text('{"thread_id":"thread-stale"}\n', encoding="utf-8")

    bad_dir = state_dir / "bad-sessions"
    bad_dir.mkdir(parents=True)
    for index in range(relayctl.BAD_SESSION_MAX_FILES + 5):
        path = bad_dir / f"old-{index}.json"
        path.write_text("{}", encoding="utf-8")
        stale_mtime = time.time() - index
        os.utime(path, (stale_mtime, stale_mtime))

    relay_home = tmp_path / "relay-home"
    (relay_home / "sessions").mkdir(parents=True)
    moved = relayctl._quarantine_stale_session_bindings(
        state_dir,
        tmp_path / "workspace",
        {"CODEX_HOME": str(relay_home)},
    )

    assert moved == 1
    assert len(list(bad_dir.glob("*.json"))) == relayctl.BAD_SESSION_MAX_FILES


def test_load_env_file_tolerates_utf8_bom(tmp_path: Path) -> None:
    env_path = tmp_path / "profile.env"
    env_path.write_bytes("\ufeffDISCORD_BOT_TOKEN=token-value\nALLOW_DMS=true\n".encode("utf-8"))
    env = relayctl._load_env_file(env_path)
    assert env["DISCORD_BOT_TOKEN"] == "token-value"
    assert env["ALLOW_DMS"] == "true"


def test_profile_from_env_merges_dm_and_channel_authors_for_setup(tmp_path: Path) -> None:
    original_profiles_dir = relayctl.PROFILES_DIR
    relayctl.PROFILES_DIR = tmp_path / "profiles"
    profile = relayctl._profile_from_env(
        {
            "DISCORD_BOT_TOKEN": "token-value",
            "CODEX_WORKDIR": str(tmp_path),
            "ALLOW_DMS": "true",
            "ALLOWED_USER_IDS": "111111111111111111",
            "ALLOWED_CHANNEL_AUTHOR_IDS": "222222222222222222",
            "CHANNEL_NO_MENTION_AUTHOR_IDS": "333333333333333333",
            "ALLOWED_CHANNEL_IDS": "333333333333333333",
        }
    )
    try:
        env = relayctl._load_env_file(Path(profile["env_file"]))
        assert env["ALLOWED_CHANNEL_AUTHOR_IDS"] == "111111111111111111,222222222222222222,333333333333333333"
        assert env["CHANNEL_NO_MENTION_AUTHOR_IDS"] == "333333333333333333"
    finally:
        relayctl.PROFILES_DIR = original_profiles_dir


def test_matching_profiles_do_not_cross_sibling_workspaces(tmp_path: Path) -> None:
    original_load_registry = relayctl._load_registry
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    relayctl._load_registry = lambda: {
        "profiles": [
            {"name": "alpha-profile", "workspace": str(alpha), "env_file": str(alpha / "alpha.env")},
            {"name": "beta-profile", "workspace": str(beta), "env_file": str(beta / "beta.env")},
        ]
    }
    try:
        matches = relayctl._matching_profiles_for_workspace(beta)
    finally:
        relayctl._load_registry = original_load_registry
    assert [profile["name"] for profile in matches] == ["beta-profile"]


def test_matching_profiles_prefers_deepest_parent_workspace(tmp_path: Path) -> None:
    original_load_registry = relayctl._load_registry
    parent = tmp_path / "team"
    child = parent / "agent-one"
    child.mkdir(parents=True)
    relayctl._load_registry = lambda: {
        "profiles": [
            {"name": "parent-profile", "workspace": str(parent), "env_file": str(parent / "parent.env")},
            {"name": "child-profile", "workspace": str(child), "env_file": str(child / "child.env")},
        ]
    }
    try:
        matches = relayctl._matching_profiles_for_workspace(child)
    finally:
        relayctl._load_registry = original_load_registry
    assert [profile["name"] for profile in matches] == ["child-profile", "parent-profile"]


def test_select_profile_for_workspace_prefers_exact_match_without_prompt(tmp_path: Path) -> None:
    child = tmp_path / "agent-one"
    child.mkdir(parents=True)
    profiles = [
        {"name": "child-profile", "workspace": str(child), "env_file": str(child / "child.env")},
        {"name": "parent-profile", "workspace": str(tmp_path), "env_file": str(tmp_path / "parent.env")},
    ]
    original_matching_profiles_for_workspace = relayctl._matching_profiles_for_workspace
    original_prompt = relayctl._prompt
    relayctl._matching_profiles_for_workspace = lambda workspace: profiles
    relayctl._prompt = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prompt should not be used"))
    try:
        selected = relayctl._select_profile_for_workspace(child)
    finally:
        relayctl._matching_profiles_for_workspace = original_matching_profiles_for_workspace
        relayctl._prompt = original_prompt
    assert selected["name"] == "child-profile"


def test_plugin_marketplace_entry_shape() -> None:
    marketplace = {"plugins": []}
    install_plugin._upsert_plugin_entry(marketplace)
    assert marketplace["plugins"] == [
        {
            "name": "discord-codex-relay",
            "source": {
                "source": "local",
                "path": "./plugins/discord-codex-relay",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ]


def test_runtime_python_path_matches_platform() -> None:
    path = install_plugin.runtime_python_path(Path("/tmp/relay-runtime"))
    if os.name == "nt":
        assert path == Path("/tmp/relay-runtime") / "Scripts" / "python.exe"
    else:
        assert path == Path("/tmp/relay-runtime") / "bin" / "python"


def test_runtime_site_packages_path_matches_platform() -> None:
    path = install_plugin.runtime_site_packages_path(Path("/tmp/relay-runtime"))
    if os.name == "nt":
        assert path == Path("/tmp/relay-runtime") / "Lib" / "site-packages"
    else:
        expected = Path("/tmp/relay-runtime") / "lib" / f"python{os.sys.version_info.major}.{os.sys.version_info.minor}" / "site-packages"
        assert path == expected


def test_ensure_runtime_retries_after_cleaning_stale_site_packages(tmp_path: Path) -> None:
    original_runtime_root = install_plugin.RUNTIME_ROOT
    original_runtime_python_path = install_plugin.runtime_python_path
    original_cleanup = install_plugin.cleanup_runtime_site_packages
    original_run = install_plugin.subprocess.run

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    python_path = runtime_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")

    calls: list[str] = []
    cleanup_calls = {"count": 0}
    seen_kwargs: list[dict] = []

    def _fake_cleanup(root=None):
        cleanup_calls["count"] += 1
        return [runtime_root / "Lib" / "site-packages" / "~sutil"] if cleanup_calls["count"] == 2 else []

    def _fake_run(*args, **kwargs):
        seen_kwargs.append(kwargs)
        calls.append("run")
        if len(calls) == 1:
            return argparse.Namespace(returncode=1, stdout="", stderr="access denied")
        return argparse.Namespace(returncode=0, stdout="", stderr="")

    install_plugin.RUNTIME_ROOT = runtime_root
    install_plugin.runtime_python_path = lambda root=None: python_path
    install_plugin.cleanup_runtime_site_packages = _fake_cleanup
    install_plugin.subprocess.run = _fake_run
    try:
        resolved = install_plugin._ensure_runtime(source=str(tmp_path))
    finally:
        install_plugin.RUNTIME_ROOT = original_runtime_root
        install_plugin.runtime_python_path = original_runtime_python_path
        install_plugin.cleanup_runtime_site_packages = original_cleanup
        install_plugin.subprocess.run = original_run

    assert resolved == python_path
    assert calls == ["run", "run"]
    assert cleanup_calls["count"] == 3
    if os.name == "nt":
        assert all("creationflags" in kwargs for kwargs in seen_kwargs)


def test_auto_install_enabled_skills_uses_windowless_subprocess_kwargs(tmp_path: Path) -> None:
    original_skill_installer_script = install_plugin._skill_installer_script
    original_skill_listing = install_plugin._skill_listing
    original_enabled_auto_skills = install_plugin.enabled_auto_skills
    original_subprocess_run = install_plugin.subprocess.run
    original_os_name = install_plugin.os.name
    seen_kwargs: list[dict] = []

    installer_script = tmp_path / "install-skill-from-github.py"
    installer_script.write_text("# stub\n", encoding="utf-8")
    install_plugin._skill_installer_script = lambda _name: installer_script
    install_plugin._skill_listing = lambda: {"playwright": False}
    install_plugin.enabled_auto_skills = lambda: ["playwright"]
    install_plugin.os.name = "nt"

    def _fake_run(*args, **kwargs):
        seen_kwargs.append(kwargs)
        return argparse.Namespace(returncode=0, stdout="", stderr="")

    install_plugin.subprocess.run = _fake_run
    try:
        installed, failed = install_plugin.auto_install_enabled_skills()
    finally:
        install_plugin._skill_installer_script = original_skill_installer_script
        install_plugin._skill_listing = original_skill_listing
        install_plugin.enabled_auto_skills = original_enabled_auto_skills
        install_plugin.subprocess.run = original_subprocess_run
        install_plugin.os.name = original_os_name

    assert installed == ["playwright"]
    assert failed == []
    assert seen_kwargs and "creationflags" in seen_kwargs[0]


def test_default_namespace_and_port_differ_for_sibling_workspaces(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    assert relay_common.default_namespace_for_workspace(alpha) != relay_common.default_namespace_for_workspace(beta)
    assert relay_common.default_port_for_workspace(alpha) != relay_common.default_port_for_workspace(beta)


def test_install_source_prefers_repo_root() -> None:
    assert install_plugin._install_source() == str(Path(__file__).resolve().parents[1])


def test_install_source_uses_direct_url_for_installed_local_source(tmp_path: Path) -> None:
    original_repo_root = install_plugin.REPO_ROOT
    original_distribution = install_plugin.importlib.metadata.distribution
    install_plugin.REPO_ROOT = tmp_path / "site-packages"
    dist_info = tmp_path / "discord_codex_relay-1.6.3.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": "file:///C:/Users/exampleuser/projects/discord-codex-relay"}),
        encoding="utf-8",
    )
    install_plugin.importlib.metadata.distribution = lambda _name: type("FakeDist", (), {"_path": dist_info})()
    try:
        assert install_plugin._install_source() == "C:\\Users\\exampleuser\\projects\\discord-codex-relay"
    finally:
        install_plugin.REPO_ROOT = original_repo_root
        install_plugin.importlib.metadata.distribution = original_distribution


def test_windows_candidate_shim_dirs_deduplicate() -> None:
    original_python = install_plugin._windows_python_scripts_dir
    original_user = install_plugin._windows_user_scripts_dir
    original_npm = install_plugin._windows_npm_dir
    install_plugin._windows_python_scripts_dir = lambda: Path("C:/tools")
    install_plugin._windows_user_scripts_dir = lambda: Path("C:/tools")
    install_plugin._windows_npm_dir = lambda: Path("C:/npm")
    try:
        assert install_plugin._windows_candidate_shim_dirs() == [Path("C:/tools"), Path("C:/npm")]
    finally:
        install_plugin._windows_python_scripts_dir = original_python
        install_plugin._windows_user_scripts_dir = original_user
        install_plugin._windows_npm_dir = original_npm


def test_setup_subcommand_exists() -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(["setup"])
    assert args.command == "setup"


def test_register_trigger_mode_defaults_to_none_for_inference() -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(["register", "--discord-bot-token", "token"])
    assert args.trigger_mode is None
    assert args.app_server_transport is None


def test_runtime_control_subcommands_exist() -> None:
    parser = relayctl.build_parser()
    assert parser.parse_args(["version"]).command == "version"
    assert parser.parse_args(["restart"]).command == "restart"
    assert parser.parse_args(["self-update"]).command == "self-update"
    assert parser.parse_args(["discord-smoke"]).command == "discord-smoke"
    assert parser.parse_args(["stop-all"]).command == "stop-all"
    assert parser.parse_args(["run", "--foreground"]).foreground is True


def test_background_python_executable_is_defined() -> None:
    path = relayctl._background_python_executable()
    assert isinstance(path, str)
    assert path


def test_background_python_executable_prefers_runtime(tmp_path: Path) -> None:
    original_runtime_python_path = install_plugin.runtime_python_path
    install_plugin.runtime_python_path = lambda root=None: tmp_path / "runtime-python.exe"
    (tmp_path / "runtime-python.exe").write_text("", encoding="utf-8")
    try:
        assert relayctl._background_python_executable() == str(tmp_path / "runtime-python.exe")
    finally:
        install_plugin.runtime_python_path = original_runtime_python_path


def test_background_python_windowless_executable_prefers_runtime_pythonw(tmp_path: Path) -> None:
    original_runtime_python_path = install_plugin.runtime_python_path
    original_os_name = relayctl.os.name
    install_plugin.runtime_python_path = lambda root=None: tmp_path / "python.exe"
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    relayctl.os.name = "nt"
    try:
        assert relayctl._background_python_windowless_executable() == str(tmp_path / "pythonw.exe")
    finally:
        install_plugin.runtime_python_path = original_runtime_python_path
        relayctl.os.name = original_os_name


def test_profile_runtime_state_reads_app_server_pid_file(tmp_path: Path) -> None:
    env_path = tmp_path / "profile.env"
    env_path.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=example",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "CODEX_APP_SERVER_PORT=8777",
                "ALLOWED_CHANNEL_IDS=123",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / ".app-server.pid").write_text('{"channel": 4321}\n', encoding="utf-8")
    profile = {"env_file": str(env_path), "workspace": str(tmp_path), "name": "relay"}

    original_state_dir_for_namespace = relayctl.state_dir_for_namespace
    original_pid_exists = relayctl.pid_exists
    original_discovered = relayctl._discovered_relay_process_pids
    original_listening_pids = relayctl.listening_pids
    relayctl.state_dir_for_namespace = lambda namespace: state_dir
    relayctl.pid_exists = lambda pid: pid == 4321
    relayctl._discovered_relay_process_pids = lambda profile, env: ([], [])
    relayctl.listening_pids = lambda port: []
    try:
        state = relayctl._profile_runtime_state(profile)
    finally:
        relayctl.state_dir_for_namespace = original_state_dir_for_namespace
        relayctl.pid_exists = original_pid_exists
        relayctl._discovered_relay_process_pids = original_discovered
        relayctl.listening_pids = original_listening_pids

    assert state["app_server_pids"] == [4321]
    assert state["running"] is True


def test_launch_bot_worker_uses_local_script_entrypoint(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text("DISCORD_BOT_TOKEN=token-value\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original_background_python_windowless_executable = relayctl._background_python_windowless_executable
    original_popen = relayctl.subprocess.Popen
    captured: dict[str, object] = {}

    class _FakeProcess:
        pid = 1234

    relayctl._background_python_windowless_executable = lambda: "python-bg"
    relayctl.subprocess.Popen = lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or _FakeProcess()
    try:
        process = relayctl._launch_bot_worker(env_file, workspace, start_reason="worker-restart")
    finally:
        relayctl._background_python_windowless_executable = original_background_python_windowless_executable
        relayctl.subprocess.Popen = original_popen

    assert process.pid == 1234
    assert captured["command"] == ["python-bg", "-u", relayctl._backend_script_path("bot.py")]
    assert captured["kwargs"]["env"]["CLADEX_START_REASON"] == "worker-restart"


def test_internal_serve_command_is_hidden_from_help() -> None:
    parser = relayctl.build_parser()
    help_text = parser.format_help()
    assert "self-update" in help_text
    assert "\n    serve" not in help_text


def test_cmd_run_defaults_to_background_supervisor() -> None:
    original_matching_profiles_for_workspace = relayctl._matching_profiles_for_workspace
    original_select_profile_for_workspace = relayctl._select_profile_for_workspace
    original_run_profile = relayctl._run_profile
    original_run_profile_foreground = relayctl._run_profile_foreground
    relayctl._matching_profiles_for_workspace = lambda workspace: [{"name": "relay", "workspace": str(workspace)}]
    relayctl._select_profile_for_workspace = lambda workspace: {"name": "relay", "workspace": str(workspace)}
    called: list[str] = []
    relayctl._run_profile = lambda profile: called.append("background") or 0
    relayctl._run_profile_foreground = lambda profile: called.append("foreground") or 0
    try:
        result = relayctl.cmd_run(argparse.Namespace())
    finally:
        relayctl._matching_profiles_for_workspace = original_matching_profiles_for_workspace
        relayctl._select_profile_for_workspace = original_select_profile_for_workspace
        relayctl._run_profile = original_run_profile
        relayctl._run_profile_foreground = original_run_profile_foreground
    assert result == 0
    assert called == ["background"]


def test_run_profile_launches_supervisor_via_local_script(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-run-profile",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "CODEX_APP_SERVER_PORT=9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"name": "relay", "workspace": str(tmp_path), "env_file": str(env_file)}
    state_dir = tmp_path / "state"
    log_path = state_dir / "logs" / "relay.log"
    app_server_log_path = state_dir / "logs" / "app-server.log"
    auth_failure_marker_path = state_dir / ".auth_failed"
    ready_marker_path = state_dir / ".ready"
    startup_notice_marker_path = state_dir / ".startup_notice"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    startup_notice_marker_path.write_text('{"sent_at": 1}\n', encoding="utf-8")

    original_profile_runtime_state = relayctl._profile_runtime_state
    original_ensure_codex_project_trusted = relayctl._ensure_codex_project_trusted
    original_codex_login_status = relayctl._codex_login_status
    original_background_python_windowless_executable = relayctl._background_python_windowless_executable
    original_truncate_file_tail = relayctl.truncate_file_tail
    original_wait_for_ready = relayctl._wait_for_ready
    original_popen = relayctl.subprocess.Popen
    captured: dict[str, object] = {}

    class _FakeProcess:
        pid = 4321

    relayctl._profile_runtime_state = lambda _profile: {
        "running": False,
        "log_path": log_path,
        "app_server_log_path": app_server_log_path,
        "auth_failure_marker_path": auth_failure_marker_path,
        "ready_marker_path": ready_marker_path,
        "startup_notice_marker_path": startup_notice_marker_path,
    }
    relayctl._ensure_codex_project_trusted = lambda workspace: None
    relayctl._codex_login_status = lambda workspace, profile_env=None: (True, "Logged in using ChatGPT")
    relayctl._background_python_windowless_executable = lambda: "python-bg"
    relayctl.truncate_file_tail = lambda *args, **kwargs: None
    relayctl._wait_for_ready = lambda *args, **kwargs: None
    relayctl.subprocess.Popen = lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or _FakeProcess()
    try:
        assert relayctl._run_profile(profile) == 0
    finally:
        relayctl._profile_runtime_state = original_profile_runtime_state
        relayctl._ensure_codex_project_trusted = original_ensure_codex_project_trusted
        relayctl._codex_login_status = original_codex_login_status
        relayctl._background_python_windowless_executable = original_background_python_windowless_executable
        relayctl.truncate_file_tail = original_truncate_file_tail
        relayctl._wait_for_ready = original_wait_for_ready
        relayctl.subprocess.Popen = original_popen

    assert captured["command"] == [
        "python-bg",
        relayctl._backend_script_path("relayctl.py"),
        "serve",
        "--env-file",
        str(env_file),
    ]
    assert startup_notice_marker_path.exists()


def test_skill_subcommands_exist() -> None:
    parser = relayctl.build_parser()
    list_args = parser.parse_args(["skill", "list"])
    install_args = parser.parse_args(["skill", "install", "--name", "example-skill"])
    assert list_args.command == "skill"
    assert list_args.skill_command == "list"
    assert install_args.command == "skill"
    assert install_args.skill_command == "install"
    assert install_args.names == ["example-skill"]


def test_privacy_audit_subcommand_exists() -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(["privacy-audit"])
    assert args.command == "privacy-audit"


def test_marketplace_has_plugin_detects_entry() -> None:
    marketplace = {
        "plugins": [
            {
                "name": "discord-codex-relay",
                "source": {"source": "local", "path": "./plugins/discord-codex-relay"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
        ]
    }
    original = install_plugin._load_marketplace
    install_plugin._load_marketplace = lambda: marketplace
    try:
        assert install_plugin.marketplace_has_plugin() is True
    finally:
        install_plugin._load_marketplace = original


def test_plugin_manifest_default_prompts_fit_spec() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prompts = manifest["interface"]["defaultPrompt"]
    assert len(prompts) <= 3
    assert all(len(prompt) <= 128 for prompt in prompts)


def test_plugin_manifest_version_matches_package_version() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = repo_root / ".codex-plugin" / "plugin.json"
    bundled_manifest_path = repo_root / "discord_codex_relay_plugin" / "bundle" / ".codex-plugin" / "plugin.json"
    pyproject_path = repo_root / "pyproject.toml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundled_manifest = json.loads(bundled_manifest_path.read_text(encoding="utf-8"))
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    match = relayctl.re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, flags=relayctl.re.MULTILINE)
    assert match is not None
    package_version = match.group(1)
    assert manifest["version"] == package_version
    assert bundled_manifest["version"] == package_version


def test_installed_plugin_completeness_check(tmp_path: Path) -> None:
    for relative_path in install_plugin.REQUIRED_PLUGIN_FILES:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    assert install_plugin.installed_plugin_is_complete(tmp_path) is True


def test_cleanup_runtime_site_packages_removes_tilde_leftovers(tmp_path: Path) -> None:
    site_packages = install_plugin.runtime_site_packages_path(tmp_path)
    site_packages.mkdir(parents=True, exist_ok=True)
    keep = site_packages / "psutil"
    keep.mkdir()
    remove_dir = site_packages / "~sutil"
    remove_dir.mkdir()
    remove_file = site_packages / "~bad.pth"
    remove_file.write_text("x", encoding="utf-8")

    removed = install_plugin.cleanup_runtime_site_packages(tmp_path)

    assert keep.exists()
    assert not remove_dir.exists()
    assert not remove_file.exists()
    assert set(removed) == {remove_dir, remove_file}


def test_auto_install_enabled_skills_installs_individually_and_keeps_going(tmp_path: Path) -> None:
    original_skill_installer_script = install_plugin._skill_installer_script
    original_skill_listing = install_plugin._skill_listing
    original_enabled_auto_skills = install_plugin.enabled_auto_skills
    original_subprocess_run = install_plugin.subprocess.run
    calls: list[str] = []

    installer_script = tmp_path / "install-skill-from-github.py"
    installer_script.write_text("# stub\n", encoding="utf-8")
    install_plugin._skill_installer_script = lambda _name: installer_script
    install_plugin._skill_listing = lambda: {
        "playwright": False,
        "screenshot": False,
        "pdf": True,
    }
    install_plugin.enabled_auto_skills = lambda: ["playwright", "screenshot", "missing-skill", "pdf"]

    def _fake_run(command, capture_output, text, check, **kwargs):
        target = command[-1].split("/")[-1]
        calls.append(target)
        return argparse.Namespace(returncode=0 if target == "playwright" else 1, stdout="", stderr="")

    install_plugin.subprocess.run = _fake_run
    try:
        installed, failed = install_plugin.auto_install_enabled_skills()
    finally:
        install_plugin._skill_installer_script = original_skill_installer_script
        install_plugin._skill_listing = original_skill_listing
        install_plugin.enabled_auto_skills = original_enabled_auto_skills
        install_plugin.subprocess.run = original_subprocess_run

    assert calls == ["playwright", "screenshot"]
    assert installed == ["playwright"]
    assert failed == ["missing-skill", "screenshot"]


def test_privacy_audit_flags_repo_local_env_and_secret_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_BOT_TOKEN=secret\nSAFE_VALUE=ok\n", encoding="utf-8")
    findings = relayctl._privacy_audit(tmp_path)
    assert any(item == "repo-local secret file: .env" for item in findings)
    assert any("DISCORD_BOT_TOKEN" in item for item in findings)


def test_privacy_audit_tracked_scans_only_git_tracked_files(tmp_path: Path, monkeypatch) -> None:
    tracked_env = tmp_path / ".env"
    ignored_env = tmp_path / "ignored.env"
    tracked_env.write_text("DISCORD_BOT_TOKEN=secret\n", encoding="utf-8")
    ignored_env.write_text("DISCORD_BOT_TOKEN=secret\n", encoding="utf-8")
    monkeypatch.setattr(relayctl, "_git_tracked_files", lambda root: [tracked_env])

    findings = relayctl._privacy_audit_tracked(tmp_path)

    assert "tracked secret file: .env" in findings
    assert all("ignored.env" not in item for item in findings)


def test_privacy_audit_allows_generic_user_path_patterns() -> None:
    findings = relayctl._privacy_audit(Path(__file__).resolve().parents[1])
    assert all(item != "user-specific path literal found in relayctl.py" for item in findings)


def test_ensure_codex_project_trusted_handles_windows_paths(tmp_path: Path) -> None:
    project_path = _windows_project_path("OneDrive", "Desktop", "projects", "teamspace")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[projects."{project_path}"]\ntrust_level = "read-only"\n',
        encoding="utf-8",
    )
    original = relayctl.codex_config_path
    relayctl.codex_config_path = lambda: config_path
    try:
        relayctl._ensure_codex_project_trusted(Path(project_path))
    finally:
        relayctl.codex_config_path = original
    updated = config_path.read_text(encoding="utf-8")
    expected_header = f"[projects.{relayctl._toml_project_key(project_path)}]"
    assert expected_header in updated
    assert 'trust_level = "trusted"' in updated


def test_normalize_codex_config_project_headers_repairs_legacy_windows_keys() -> None:
    teamspace_path = _windows_project_path("OneDrive", "Desktop", "projects", "teamspace")
    legacy_path = "\\\\?\\" + _windows_project_path("legacy-project")
    config_text = (
        f"[projects.'{teamspace_path}']\n"
        'trust_level = "trusted"\n\n'
        f'[projects."{legacy_path}"]\n'
        'trust_level = "trusted"\n'
    )
    normalized = relayctl._normalize_codex_config_project_headers(config_text)
    assert f"[projects.{relayctl._toml_project_key(teamspace_path)}]" in normalized
    legacy_fragment = "\\\\?\\\\" + _windows_project_path("legacy-project").replace("\\", "\\\\")
    assert legacy_fragment in normalized


def test_profile_runtime_state_requires_ready_marker_for_supervisor_only_state(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-runtime-state",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "CODEX_APP_SERVER_PORT=9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"env_file": str(env_file)}
    state_dir = tmp_path / "state"
    original_state_dir_for_profile = relayctl._state_dir_for_profile
    original_read_pid_file = relayctl._read_pid_file
    original_pid_exists = relayctl.pid_exists
    relayctl._state_dir_for_profile = lambda _profile, env=None: state_dir
    relayctl._read_pid_file = lambda path: 4321 if path.name == ".supervisor.pid" else 9876
    relayctl.pid_exists = lambda pid: pid == 4321
    try:
        state = relayctl._profile_runtime_state(profile)
    finally:
        relayctl._state_dir_for_profile = original_state_dir_for_profile
        relayctl._read_pid_file = original_read_pid_file
        relayctl.pid_exists = original_pid_exists
    assert state["running"] is True
    assert state["ready"] is False


def test_discovered_relay_process_pids_supports_module_launches(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-discovery",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"env_file": str(env_file)}
    env = relayctl._normalized_profile_env(relayctl._load_env_file(env_file))

    class _FakeProcess:
        def __init__(self, pid: int, cmdline: list[str], cwd: Path) -> None:
            self.info = {"pid": pid, "cmdline": cmdline}
            self._cwd = cwd

        def cwd(self) -> str:
            return str(self._cwd)

    original_process_iter = relayctl.psutil.process_iter
    relayctl.psutil.process_iter = lambda _fields: iter(
        [
            _FakeProcess(101, ["python.exe", "-m", "relayctl", "serve", "--env-file", str(env_file)], tmp_path),
            _FakeProcess(202, ["python.exe", "-u", "-m", "bot"], tmp_path),
            _FakeProcess(303, ["python.exe", "-u", "-m", "bot"], tmp_path / "other"),
        ]
    )
    try:
        supervisor_pids, relay_pids = relayctl._discovered_relay_process_pids(profile, env)
    finally:
        relayctl.psutil.process_iter = original_process_iter

    assert supervisor_pids == [101]
    assert relay_pids == [202]


def test_preferred_process_pids_prefers_background_runtime_executable() -> None:
    original_background_python_executable = relayctl._background_python_executable
    original_process = relayctl.psutil.Process

    class _FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def exe(self) -> str:
            return {
                101: "C:/global/python.exe",
                202: "C:/runtime/python.exe",
                303: "C:/runtime/python.exe",
            }[self.pid]

        def children(self, recursive: bool = False):
            return []

    relayctl._background_python_executable = lambda: "C:/runtime/python.exe"
    relayctl.psutil.Process = lambda pid: _FakeProcess(pid)
    try:
        preferred, extras = relayctl._preferred_process_pids([101, 202, 303])
    finally:
        relayctl._background_python_executable = original_background_python_executable
        relayctl.psutil.Process = original_process

    assert preferred == [202, 303]
    assert extras == [101]


def test_profile_runtime_state_clears_stale_ready_and_lock_files(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-stale-runtime",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "CODEX_APP_SERVER_PORT=9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"env_file": str(env_file)}
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / ".instance.lock").write_text("95508\n", encoding="utf-8")
    (state_dir / ".launch.lock").write_text("95508\n", encoding="utf-8")
    (state_dir / ".supervisor.lock").write_text("95508\n", encoding="utf-8")
    (state_dir / ".ready").write_text('{"ready_at": 1}\n', encoding="utf-8")
    original_state_dir_for_profile = relayctl._state_dir_for_profile
    original_read_pid_file = relayctl._read_pid_file
    original_pid_exists = relayctl.pid_exists
    relayctl._state_dir_for_profile = lambda _profile, env=None: state_dir
    relayctl._read_pid_file = lambda path: 95508 if path.name == ".instance.lock" else None
    relayctl.pid_exists = lambda pid: False
    try:
        state = relayctl._profile_runtime_state(profile)
    finally:
        relayctl._state_dir_for_profile = original_state_dir_for_profile
        relayctl._read_pid_file = original_read_pid_file
        relayctl.pid_exists = original_pid_exists
    assert state["running"] is False
    assert state["ready"] is False
    assert not (state_dir / ".instance.lock").exists()
    assert not (state_dir / ".launch.lock").exists()
    assert not (state_dir / ".supervisor.lock").exists()
    assert not (state_dir / ".ready").exists()


def test_profile_runtime_state_clears_stale_auth_failure_marker_when_login_is_healthy(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-stale-auth-marker",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "CODEX_APP_SERVER_PORT=9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"env_file": str(env_file), "workspace": str(tmp_path)}
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    auth_failed_path = state_dir / ".auth_failed"
    auth_failed_path.write_text('{"failed_at": 1, "message": "auth broke"}\n', encoding="utf-8")

    original_state_dir_for_profile = relayctl._state_dir_for_profile
    original_read_pid_file = relayctl._read_pid_file
    original_pid_exists = relayctl.pid_exists
    original_codex_login_status = relayctl._codex_login_status
    relayctl._state_dir_for_profile = lambda _profile, env=None: state_dir
    relayctl._read_pid_file = lambda path: None
    relayctl.pid_exists = lambda pid: False
    relayctl._codex_login_status = lambda workspace, profile_env=None: (True, "Logged in using ChatGPT")
    try:
        state = relayctl._profile_runtime_state(profile)
    finally:
        relayctl._state_dir_for_profile = original_state_dir_for_profile
        relayctl._read_pid_file = original_read_pid_file
        relayctl.pid_exists = original_pid_exists
        relayctl._codex_login_status = original_codex_login_status

    assert state["auth_failed"] is False
    assert not auth_failed_path.exists()


def test_run_profile_waits_for_existing_launch_when_lock_is_held(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-run-lock",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
                "CODEX_APP_SERVER_PORT=9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"name": "relay", "workspace": str(tmp_path), "env_file": str(env_file)}

    original_acquire_pid_lock = relayctl._acquire_pid_lock
    original_wait_for_inflight_launch = relayctl._wait_for_inflight_launch
    original_quarantine_stale_session_bindings = relayctl._quarantine_stale_session_bindings
    original_release_pid_lock = relayctl._release_pid_lock
    called: list[str] = []

    relayctl._acquire_pid_lock = lambda path: (_ for _ in ()).throw(OSError("busy"))
    relayctl._wait_for_inflight_launch = lambda item: called.append(item["name"]) or 0
    relayctl._quarantine_stale_session_bindings = lambda path, workspace=None, profile_env=None: 0
    relayctl._release_pid_lock = lambda handle: None
    try:
        result = relayctl._run_profile(profile)
    finally:
        relayctl._acquire_pid_lock = original_acquire_pid_lock
        relayctl._wait_for_inflight_launch = original_wait_for_inflight_launch
        relayctl._quarantine_stale_session_bindings = original_quarantine_stale_session_bindings
        relayctl._release_pid_lock = original_release_pid_lock

    assert result == 0
    assert called == ["relay"]


def test_register_infers_mention_or_dm_for_channel_profiles(tmp_path: Path) -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(
        [
            "register",
            "--workspace",
            str(tmp_path),
            "--discord-bot-token",
            "token-value",
            "--allowed-channel-id",
            "1234567890",
            "--channel-no-mention-author-id",
            "999",
        ]
    )
    captured: dict[str, dict[str, str]] = {}
    original_profile_from_env = relayctl._profile_from_env
    original_register_profile = relayctl._register_profile

    def _fake_profile_from_env(env: dict[str, str]) -> dict:
        captured["env"] = env
        return {"name": "relay", "workspace": str(tmp_path)}

    relayctl._profile_from_env = _fake_profile_from_env
    relayctl._register_profile = lambda profile: None
    try:
        result = relayctl.cmd_register(args)
    finally:
        relayctl._profile_from_env = original_profile_from_env
        relayctl._register_profile = original_register_profile
    assert result == 0
    assert captured["env"]["BOT_TRIGGER_MODE"] == "mention_or_dm"
    assert captured["env"]["CODEX_WORKDIR"] == str(tmp_path.resolve())
    assert captured["env"]["CODEX_HOME"] == ""
    assert captured["env"]["CHANNEL_NO_MENTION_AUTHOR_IDS"] == "999"


def test_rewrite_project_shortcut_args_maps_to_project_start() -> None:
    assert relayctl._rewrite_project_shortcut_args(["pj-team-alpha"]) == ["project", "start", "team-alpha"]
    assert relayctl._rewrite_project_shortcut_args(["pj-team-alpha", "--flag"]) == ["project", "start", "team-alpha", "--flag"]
    assert relayctl._rewrite_project_shortcut_args(["--gui"]) == ["gui"]
    assert relayctl._rewrite_project_shortcut_args(["status"]) == ["status"]


def test_build_parser_supports_gui_command() -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(["gui"])
    assert args.func is relayctl.cmd_gui


def test_cmd_gui_launches_detached_child_by_default(capsys) -> None:
    original_env = os.environ.get(relayctl.GUI_CHILD_ENV)
    original_popen = relayctl.subprocess.Popen
    original_gui_python = relayctl._gui_python_executable
    calls: list[tuple[list[str], dict]] = []

    def _fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return None

    relayctl.subprocess.Popen = _fake_popen
    relayctl._gui_python_executable = lambda: "pythonw.exe"
    os.environ.pop(relayctl.GUI_CHILD_ENV, None)
    try:
        result = relayctl.cmd_gui(argparse.Namespace(command="gui"))
    finally:
        relayctl.subprocess.Popen = original_popen
        relayctl._gui_python_executable = original_gui_python
        if original_env is None:
            os.environ.pop(relayctl.GUI_CHILD_ENV, None)
        else:
            os.environ[relayctl.GUI_CHILD_ENV] = original_env

    assert result == 0
    assert calls
    command, kwargs = calls[0]
    assert command == ["pythonw.exe", relayctl._backend_script_path("relayctl.py"), "gui"]
    assert kwargs["env"][relayctl.GUI_CHILD_ENV] == "1"
    assert "Opened relay manager GUI." in capsys.readouterr().out


def test_replace_profile_registration_updates_projects(tmp_path: Path) -> None:
    registry = {
        "profiles": [
            {"name": "old-name", "workspace": str(tmp_path / "old"), "env_file": str(tmp_path / "old.env")},
        ],
        "projects": [
            {"name": "team-alpha", "profiles": ["old-name", "other-name"]},
        ],
    }
    old_env = tmp_path / "old.env"
    old_env.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    new_env = tmp_path / "new.env"
    new_env.write_text("DISCORD_BOT_TOKEN=test\n", encoding="utf-8")
    original_load_registry = relayctl._load_registry
    original_save_registry = relayctl._save_registry
    saved: dict[str, object] = {}
    relayctl._load_registry = lambda: registry
    relayctl._save_registry = lambda value: saved.setdefault("registry", value)
    try:
        relayctl._replace_profile_registration(
            {"name": "old-name", "env_file": str(old_env)},
            {"name": "new-name", "workspace": str(tmp_path / "new"), "env_file": str(new_env)},
        )
    finally:
        relayctl._load_registry = original_load_registry
        relayctl._save_registry = original_save_registry
    assert saved["registry"]["projects"] == [{"name": "team-alpha", "profiles": ["new-name", "other-name"]}]
    assert old_env.exists() is False


def test_main_no_args_can_open_gui_from_prompt() -> None:
    original_argv = relayctl.sys.argv
    original_should_offer_gui_prompt = relayctl._should_offer_gui_prompt
    original_prompt_for_optional_gui = relayctl._prompt_for_optional_gui
    original_cmd_gui = relayctl.cmd_gui
    original_cmd_run = relayctl.cmd_run
    events: list[str] = []
    relayctl.sys.argv = ["codex-discord"]
    relayctl._should_offer_gui_prompt = lambda: True
    relayctl._prompt_for_optional_gui = lambda: True
    relayctl.cmd_gui = lambda args: events.append(f"gui:{args.command}") or 0
    relayctl.cmd_run = lambda args: events.append("run") or 0
    try:
        result = relayctl.main()
    finally:
        relayctl.sys.argv = original_argv
        relayctl._should_offer_gui_prompt = original_should_offer_gui_prompt
        relayctl._prompt_for_optional_gui = original_prompt_for_optional_gui
        relayctl.cmd_gui = original_cmd_gui
        relayctl.cmd_run = original_cmd_run
    assert result == 0
    assert events == ["gui:gui"]


def test_cmd_project_save_persists_explicit_profiles(tmp_path: Path) -> None:
    original_load_registry = relayctl._load_registry
    original_save_registry = relayctl._save_registry
    original_profile_by_name = relayctl._profile_by_name
    saved: dict[str, object] = {}
    relayctl._load_registry = lambda: {"profiles": [], "projects": []}
    relayctl._save_registry = lambda registry: saved.setdefault("registry", registry)
    relayctl._profile_by_name = lambda name: {"name": name, "workspace": str(tmp_path / name), "bot_name": name}
    try:
        result = relayctl.cmd_project_save(
            argparse.Namespace(
                name="team-alpha",
                profile_names=["agent-a-1", "agent-b-2", "agent-c-3"],
                workspace_root=None,
            )
        )
    finally:
        relayctl._load_registry = original_load_registry
        relayctl._save_registry = original_save_registry
        relayctl._profile_by_name = original_profile_by_name
    assert result == 0
    assert saved["registry"]["projects"] == [
        {"name": "team-alpha", "profiles": ["agent-a-1", "agent-b-2", "agent-c-3"]}
    ]


def test_cmd_project_start_runs_every_profile_in_project() -> None:
    original_project_by_name = relayctl._project_by_name
    original_profiles_for_project = relayctl._profiles_for_project
    original_run_profile = relayctl._run_profile
    events: list[str] = []
    relayctl._project_by_name = lambda name: {"name": name, "profiles": ["one", "two"]}
    relayctl._profiles_for_project = lambda project: [{"name": "one"}, {"name": "two"}]
    relayctl._run_profile = lambda profile: events.append(profile["name"]) or 0
    try:
        result = relayctl.cmd_project_start(argparse.Namespace(name="team-alpha"))
    finally:
        relayctl._project_by_name = original_project_by_name
        relayctl._profiles_for_project = original_profiles_for_project
        relayctl._run_profile = original_run_profile
    assert result == 0
    assert events == ["one", "two"]


def test_cmd_setup_restarts_running_profiles_around_shared_runtime_update() -> None:
    original_running_profiles = relayctl._shared_runtime_running_profiles
    original_stop_profile = relayctl._stop_profile
    original_restart_profiles = relayctl._restart_profiles
    original_install_main = install_plugin.main
    events: list[str] = []
    profiles = [{"name": "one"}, {"name": "two"}]
    relayctl._shared_runtime_running_profiles = lambda: profiles
    relayctl._stop_profile = lambda profile: events.append(f"stop:{profile['name']}") or 0
    relayctl._restart_profiles = lambda items: events.extend(f"restart:{profile['name']}" for profile in items)
    install_plugin.main = lambda source=None: events.append(f"install:{source}") or 0
    try:
        assert relayctl.cmd_setup(argparse.Namespace()) == 0
    finally:
        relayctl._shared_runtime_running_profiles = original_running_profiles
        relayctl._stop_profile = original_stop_profile
        relayctl._restart_profiles = original_restart_profiles
        install_plugin.main = original_install_main
    assert events == ["stop:one", "stop:two", "install:None", "restart:one", "restart:two"]


def test_cmd_self_update_restarts_all_running_profiles_after_success() -> None:
    update_source = _windows_project_path("Projects", "discord-codex-relay")
    original_profile_for_current_workspace = relayctl._profile_for_current_workspace
    original_running_profiles = relayctl._shared_runtime_running_profiles
    original_stop_profile = relayctl._stop_profile
    original_restart_profiles = relayctl._restart_profiles
    original_subprocess_run = relayctl.subprocess.run
    original_install_main = install_plugin.main
    original_package_version = relayctl._package_version
    events: list[str] = []
    profiles = [{"name": "one"}, {"name": "two"}]
    relayctl._profile_for_current_workspace = lambda: (_ for _ in ()).throw(SystemExit(1))
    relayctl._shared_runtime_running_profiles = lambda: profiles
    relayctl._stop_profile = lambda profile: events.append(f"stop:{profile['name']}") or 0
    relayctl._restart_profiles = lambda items: events.extend(f"restart:{profile['name']}" for profile in items)
    relayctl.subprocess.run = lambda *args, **kwargs: events.append("pip") or argparse.Namespace(returncode=0)
    install_plugin.main = lambda source=None: events.append(f"install:{source}") or 0
    relayctl._package_version = lambda: "1.6.3"
    try:
        result = relayctl.cmd_self_update(
            argparse.Namespace(
                source=update_source,
                force_reinstall=False,
                no_restart=False,
            )
        )
    finally:
        relayctl._profile_for_current_workspace = original_profile_for_current_workspace
        relayctl._shared_runtime_running_profiles = original_running_profiles
        relayctl._stop_profile = original_stop_profile
        relayctl._restart_profiles = original_restart_profiles
        relayctl.subprocess.run = original_subprocess_run
        install_plugin.main = original_install_main
        relayctl._package_version = original_package_version
    assert result == 0
    assert events == ["stop:one", "stop:two", "pip", f"install:{update_source}", "restart:one", "restart:two"]


def test_default_self_update_source_prefers_local_repo_tree(tmp_path: Path) -> None:
    repo_root = tmp_path / "discord-codex-relay"
    plugin_dir = repo_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='discord-codex-relay'\n", encoding="utf-8")
    (repo_root / "relayctl.py").write_text("# local source tree\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text("{}\n", encoding="utf-8")

    original_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        assert relayctl._default_self_update_source() == str(repo_root.resolve())
    finally:
        os.chdir(original_cwd)


def test_run_gui_self_update_uses_detected_source_and_forces_reinstall() -> None:
    original_default_self_update_source = relayctl._default_self_update_source
    original_cmd_self_update = relayctl.cmd_self_update
    original_package_version = relayctl._package_version
    calls: list[tuple[str | None, bool, bool]] = []

    relayctl._default_self_update_source = lambda: "C:\\relay-source"
    relayctl.cmd_self_update = lambda args: calls.append((args.source, args.force_reinstall, args.no_restart)) or 0
    relayctl._package_version = lambda: "1.6.3"
    try:
        lines = relayctl._run_gui_self_update()
    finally:
        relayctl._default_self_update_source = original_default_self_update_source
        relayctl.cmd_self_update = original_cmd_self_update
        relayctl._package_version = original_package_version

    assert calls == [("C:\\relay-source", True, False)]
    assert lines == [
        "Updated relay from `C:\\relay-source`.",
        "discord-codex-relay version: 1.6.3",
    ]


def test_cmd_self_update_uses_external_windows_installer_for_local_runtime_source(tmp_path: Path) -> None:
    repo_root = tmp_path / "discord-codex-relay"
    plugin_dir = repo_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='discord-codex-relay'\n", encoding="utf-8")
    (repo_root / "relayctl.py").write_text("# local source tree\n", encoding="utf-8")
    (repo_root / "install_plugin.py").write_text("# installer\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text("{}\n", encoding="utf-8")

    original_profile_for_current_workspace = relayctl._profile_for_current_workspace
    original_running_profiles = relayctl._shared_runtime_running_profiles
    original_stop_profile = relayctl._stop_profile
    original_restart_profiles = relayctl._restart_profiles
    original_subprocess_popen = relayctl.subprocess.Popen
    original_package_version = relayctl._package_version
    original_can_use_external = relayctl._can_use_external_windows_update
    original_launch_background = relayctl._launch_external_windows_update_background
    events: list[str] = []
    profiles = [{"name": "one"}]

    relayctl._profile_for_current_workspace = lambda: (_ for _ in ()).throw(SystemExit(1))
    relayctl._shared_runtime_running_profiles = lambda: profiles
    relayctl._stop_profile = lambda profile: events.append(f"stop:{profile['name']}") or 0
    relayctl._restart_profiles = lambda items: events.extend(f"restart:{profile['name']}" for profile in items)
    relayctl._can_use_external_windows_update = lambda update_target: True
    relayctl._launch_external_windows_update_background = lambda update_target, restarted_profiles: events.append(f"external:{Path(update_target).name}:{len(restarted_profiles)}")
    relayctl._package_version = lambda: "1.9.0"
    try:
        result = relayctl.cmd_self_update(
            argparse.Namespace(
                source=str(repo_root),
                force_reinstall=True,
                no_restart=False,
            )
        )
    finally:
        relayctl._profile_for_current_workspace = original_profile_for_current_workspace
        relayctl._shared_runtime_running_profiles = original_running_profiles
        relayctl._stop_profile = original_stop_profile
        relayctl._restart_profiles = original_restart_profiles
        relayctl.subprocess.Popen = original_subprocess_popen
        relayctl._package_version = original_package_version
        relayctl._can_use_external_windows_update = original_can_use_external
        relayctl._launch_external_windows_update_background = original_launch_background

    assert result == 0
    assert events == ["stop:one", "external:discord-codex-relay:1"]


def test_resolved_self_update_target_prefers_local_install_source(tmp_path: Path) -> None:
    repo_root = tmp_path / "discord-codex-relay"
    plugin_dir = repo_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='discord-codex-relay'\n", encoding="utf-8")
    (repo_root / "relayctl.py").write_text("# local source tree\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text("{}\n", encoding="utf-8")

    original_install_source = install_plugin._install_source
    install_plugin._install_source = lambda: str(repo_root)
    try:
        assert relayctl._resolved_self_update_target(None) == str(repo_root.resolve())
    finally:
        install_plugin._install_source = original_install_source


def test_cmd_self_update_without_source_uses_external_windows_installer_for_local_install(tmp_path: Path) -> None:
    repo_root = tmp_path / "discord-codex-relay"
    plugin_dir = repo_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='discord-codex-relay'\n", encoding="utf-8")
    (repo_root / "relayctl.py").write_text("# local source tree\n", encoding="utf-8")
    (repo_root / "install_plugin.py").write_text("# installer\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text("{}\n", encoding="utf-8")

    original_install_source = install_plugin._install_source
    original_profile_for_current_workspace = relayctl._profile_for_current_workspace
    original_running_profiles = relayctl._shared_runtime_running_profiles
    original_stop_profile = relayctl._stop_profile
    original_restart_profiles = relayctl._restart_profiles
    original_package_version = relayctl._package_version
    original_can_use_external = relayctl._can_use_external_windows_update
    original_launch_background = relayctl._launch_external_windows_update_background
    events: list[str] = []

    install_plugin._install_source = lambda: str(repo_root)
    relayctl._profile_for_current_workspace = lambda: (_ for _ in ()).throw(SystemExit(1))
    relayctl._shared_runtime_running_profiles = lambda: []
    relayctl._stop_profile = lambda profile: events.append(f"stop:{profile['name']}") or 0
    relayctl._restart_profiles = lambda items: events.extend(f"restart:{profile['name']}" for profile in items)
    relayctl._can_use_external_windows_update = lambda update_target: True
    relayctl._launch_external_windows_update_background = lambda update_target, restarted_profiles: events.append(f"external:{Path(update_target).name}:{len(restarted_profiles)}")
    relayctl._package_version = lambda: "1.9.1"
    try:
        result = relayctl.cmd_self_update(
            argparse.Namespace(
                source=None,
                force_reinstall=False,
                no_restart=False,
            )
        )
    finally:
        install_plugin._install_source = original_install_source
        relayctl._profile_for_current_workspace = original_profile_for_current_workspace
        relayctl._shared_runtime_running_profiles = original_running_profiles
        relayctl._stop_profile = original_stop_profile
        relayctl._restart_profiles = original_restart_profiles
        relayctl._package_version = original_package_version
        relayctl._can_use_external_windows_update = original_can_use_external
        relayctl._launch_external_windows_update_background = original_launch_background

    assert result == 0
    assert events == ["external:discord-codex-relay:0"]


def test_launch_external_windows_update_background_uses_windowless_python(tmp_path: Path) -> None:
    repo_root = tmp_path / "discord-codex-relay"
    plugin_dir = repo_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='discord-codex-relay'\n", encoding="utf-8")
    (repo_root / "relayctl.py").write_text("# local source tree\n", encoding="utf-8")
    (repo_root / "install_plugin.py").write_text("# installer\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text("{}\n", encoding="utf-8")
    helper_python = tmp_path / "pythonw.exe"
    helper_python.write_text("", encoding="utf-8")

    original_background_python = relayctl._background_python_windowless_executable
    original_popen = relayctl.subprocess.Popen
    original_os_name = relayctl.os.name
    captured: dict[str, object] = {}

    relayctl._background_python_windowless_executable = lambda: str(helper_python)
    relayctl.os.name = "nt"
    relayctl.subprocess.Popen = lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or argparse.Namespace()
    try:
        relayctl._launch_external_windows_update_background(str(repo_root), restarted_profiles=[])
    finally:
        relayctl._background_python_windowless_executable = original_background_python
        relayctl.subprocess.Popen = original_popen
        relayctl.os.name = original_os_name

    assert Path(captured["command"][0]).name.lower() == "pythonw.exe"
    assert captured["kwargs"]["stdin"] is relayctl.subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is relayctl.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is relayctl.subprocess.DEVNULL


def test_build_parser_supports_update_alias() -> None:
    parser = relayctl.build_parser()
    args = parser.parse_args(["update"])
    assert args.func is relayctl.cmd_self_update


def test_summarize_relay_log_text_returns_human_readable_events() -> None:
    summary = relayctl._summarize_relay_log_text(
        "\n".join(
            [
                "Supervisor starting relay worker for C:\\work\\sampleproject\\tyson",
                "Native Codex login: ok",
                "Logged in using ChatGPT",
                "Codex startup healthcheck: ok",
                "Discord relay connected as tyson#9111",
                "Relay error for channel-123: Codex completed the turn without a reply message.",
            ]
        )
    )
    assert summary == [
        "Started relay worker",
        "Codex login healthy",
        "Using ChatGPT login",
        "Codex healthcheck passed",
        "Discord connected as tyson#9111",
        "Relay error: Codex completed the turn without a reply message.",
    ]


def test_summarize_app_log_text_returns_terminal_style_status() -> None:
    summary = relayctl._summarize_app_log_text(
        "\n".join(
            [
                "=== app-server[channel-1475709381428645908] pid=32160 transport=stdio started_at=1775687649.388 ===",
                '+ Get-Content roadmap-pt1.md -TotalCount 120',
                "Get-Content : Cannot find path 'C:\\Users\\example\\roadmap-pt1.md' because it does not exist.",
                "\x1b[2m2026-04-08T22:32:45.114849Z\x1b[0m \x1b[31mERROR\x1b[0m \x1b[2mcodex_core::tools::router\x1b[0m: error=Exit code: 1",
                "rg: ..\\backend\\*.py: The filename, directory name, or volume label syntax is incorrect. (os error 123)",
            ]
        )
    )
    assert summary[:4] == [
        "Codex session active",
        "Reading roadmap-pt1.md -TotalCount 120",
        "Reading roadmap-pt1.md -TotalCount 120, but roadmap-pt1.md is missing",
        "Reading roadmap-pt1.md -TotalCount 120 failed",
    ]
    assert summary[4] == "Reading roadmap-pt1.md -TotalCount 120, but the search path failed"


def test_summarize_app_log_text_prefers_latest_session_and_redacts_user_paths() -> None:
    summary = relayctl._summarize_app_log_text(
        "\n".join(
            [
                "=== app-server[channel-old] pid=1 transport=stdio started_at=1 ===",
                "+ Get-Content C:\\Users\\exampleuser\\secret.txt",
                "=== app-server[channel-new] pid=2 transport=stdio started_at=2 ===",
                "Name  : PATH",
                "Value : C:\\Users\\exampleuser\\AppData\\Local\\Programs\\Python\\Python310",
                "+ cmd /c type C:\\Users\\exampleuser\\OneDrive\\Desktop\\projects\\sampleproject\\backend\\app.py",
            ]
        )
    )
    assert summary == [
        "Codex session active",
        "Reading app.py",
    ]


def test_summarize_app_log_text_surfaces_observer_events() -> None:
    summary = relayctl._summarize_app_log_text(
        "\n".join(
            [
                "=== app-server[channel-new] pid=2 transport=stdio started_at=2 ===",
                "OBSERVE input: channel_message | Finn in #123: fix the auth bug",
                "OBSERVE working: Working on the current Discord turn.",
                "OBSERVE output: tracing the login failure and checking the token refresh path",
                "OBSERVE reply: fixed the refresh path and reran the tests",
            ]
        )
    )
    assert summary == [
        "Codex session active",
        "input: channel_message | Finn in #123: fix the auth bug",
        "working: Working on the current Discord turn.",
        "output: tracing the login failure and checking the token refresh path",
        "reply: fixed the refresh path and reran the tests",
    ]


def test_profile_raw_terminal_lines_include_raw_app_and_relay_logs(tmp_path: Path) -> None:
    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            [
                "DISCORD_BOT_TOKEN=token-value",
                f"CODEX_WORKDIR={tmp_path}",
                "STATE_NAMESPACE=test-raw-live",
                "CODEX_APP_SERVER_TRANSPORT=stdio",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = {"name": "raw-live", "workspace": str(tmp_path), "env_file": str(env_file)}
    state_dir = tmp_path / "state"
    logs_dir = state_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "app-server.log").write_text("line one\nline two\n", encoding="utf-8")
    (logs_dir / "relay.log").write_text("relay one\n", encoding="utf-8")
    original_state_dir_for_profile = relayctl._state_dir_for_profile
    relayctl._state_dir_for_profile = lambda _profile, env=None: state_dir
    try:
        lines = relayctl._profile_raw_terminal_lines(profile)
    finally:
        relayctl._state_dir_for_profile = original_state_dir_for_profile

    rendered = "\n".join(lines)
    assert "[codex terminal]" in rendered
    assert "line one" in rendered
    assert "line two" in rendered
    assert "[relay supervisor]" in rendered
    assert "relay one" in rendered


def test_describe_command_normalizes_cmd_shell_prefixes() -> None:
    assert relayctl._describe_command("cmd /c type backend\\app.py") == "Reading app.py"
    assert relayctl._describe_command("cmd /c dir backend") == "Listing files"
    assert relayctl._describe_command("cmd /c findstr /s provider backend\\*.py") == "Searching text"


def test_cmd_stop_all_stops_profiles_kills_orphans_and_cleans_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True)
    lock_path = state_dir / ".instance.lock"
    supervisor_pid_path = state_dir / ".supervisor.pid"
    app_server_pid_path = state_dir / ".app-server.pid"
    launch_lock_path = state_dir / ".launch.lock"
    supervisor_lock_path = state_dir / ".supervisor.lock"
    ready_path = state_dir / ".ready"
    auth_failed_path = state_dir / ".auth_failed"
    lock_path.write_text("111\n", encoding="utf-8")
    supervisor_pid_path.write_text("222\n", encoding="utf-8")
    app_server_pid_path.write_text('{"session": 555}\n', encoding="utf-8")
    launch_lock_path.write_text("333\n", encoding="utf-8")
    supervisor_lock_path.write_text("444\n", encoding="utf-8")
    ready_path.write_text("", encoding="utf-8")
    auth_failed_path.write_text("", encoding="utf-8")

    profiles = [
        {"name": "one", "env_file": str(tmp_path / "one.env")},
        {"name": "two", "env_file": str(tmp_path / "two.env")},
    ]
    events: list[str] = []

    original_all_registered_profiles = relayctl._all_registered_profiles
    original_stop_profile = relayctl._stop_profile
    original_all_relay_process_pids = relayctl._all_relay_process_pids
    original_discovered_codex_app_server_pids = relayctl._discovered_codex_app_server_pids
    original_terminate_process_tree = relayctl.terminate_process_tree
    original_data_root = relayctl.DATA_ROOT
    original_read_pid_file = relayctl._read_pid_file
    original_pid_exists = relayctl.pid_exists

    relayctl._all_registered_profiles = lambda: profiles
    relayctl._stop_profile = lambda profile: events.append(f"stop:{profile['name']}") or 0
    relayctl._all_relay_process_pids = lambda: ([333], [444])
    relayctl._discovered_codex_app_server_pids = lambda workspace=None: []
    relayctl.terminate_process_tree = lambda pid: events.append(f"kill:{pid}") or True
    relayctl.DATA_ROOT = tmp_path
    relayctl._read_pid_file = (
        lambda path: 111
        if path == lock_path
        else 222
        if path == supervisor_pid_path
        else 333
        if path == launch_lock_path
        else 444
        if path == supervisor_lock_path
        else None
    )
    relayctl.pid_exists = lambda pid: pid == 555
    try:
        result = relayctl.cmd_stop_all(argparse.Namespace())
    finally:
        relayctl._all_registered_profiles = original_all_registered_profiles
        relayctl._stop_profile = original_stop_profile
        relayctl._all_relay_process_pids = original_all_relay_process_pids
        relayctl._discovered_codex_app_server_pids = original_discovered_codex_app_server_pids
        relayctl.terminate_process_tree = original_terminate_process_tree
        relayctl.DATA_ROOT = original_data_root
        relayctl._read_pid_file = original_read_pid_file
        relayctl.pid_exists = original_pid_exists

    assert result == 0
    assert events == ["stop:one", "stop:two", "kill:333", "kill:444", "kill:555"]
    assert not lock_path.exists()
    assert not supervisor_pid_path.exists()
    assert not app_server_pid_path.exists()
    assert not launch_lock_path.exists()
    assert not supervisor_lock_path.exists()
    assert not ready_path.exists()
    assert not auth_failed_path.exists()
