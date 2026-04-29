from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import claude_common
import install_plugin
import relay_common


def test_install_subprocess_timeout_returns_bounded_output() -> None:
    result = install_plugin._run_limited_subprocess(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.write('x' * 10000); sys.stdout.flush(); time.sleep(5)",
        ],
        timeout_seconds=1,
        max_output_bytes=128,
    )

    assert result.returncode != 0
    assert "timed out" in result.stderr
    assert "stdout truncated" in result.stdout
    assert len(result.stdout.encode("utf-8")) < 256


def test_optional_skill_install_uses_per_child_timeout(tmp_path: Path, monkeypatch) -> None:
    installer_script = tmp_path / "install-skill-from-github.py"
    installer_script.write_text("# stub\n", encoding="utf-8")
    seen_timeouts: list[int] = []

    def fake_run_limited(command, *, timeout_seconds, max_output_bytes, env=None):
        seen_timeouts.append(timeout_seconds)
        return subprocess.CompletedProcess(command, 124, "", "timed out")

    monkeypatch.setattr(install_plugin, "_skill_installer_script", lambda _name: installer_script)
    monkeypatch.setattr(install_plugin, "_skill_listing", lambda: {"playwright": False})
    monkeypatch.setattr(install_plugin, "enabled_auto_skills", lambda: ["playwright"])
    monkeypatch.setattr(install_plugin, "_run_limited_subprocess", fake_run_limited)
    monkeypatch.setenv("CLADEX_AUTO_INSTALL_OPTIONAL_SKILLS", "1")

    installed, failed = install_plugin.auto_install_enabled_skills()

    assert installed == []
    assert failed == ["playwright"]
    assert seen_timeouts == [install_plugin.DEFAULT_OPTIONAL_SKILL_INSTALL_TIMEOUT_SECONDS]


def test_tail_lines_reads_bounded_tail_for_common_modules(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "relay.log"
    log_path.write_text("prefix-" + ("x" * 4096) + "\npenultimate\nlast\n", encoding="utf-8")
    monkeypatch.setenv("CLADEX_LOG_TAIL_MAX_BYTES", "128")

    assert relay_common.tail_lines(log_path, 2) == "penultimate\nlast\n"
    assert claude_common.tail_lines(log_path, 2) == "penultimate\nlast\n"
