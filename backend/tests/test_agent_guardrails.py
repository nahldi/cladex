from __future__ import annotations

from pathlib import Path

from agent_guardrails import format_workspace_guidance, protected_roots_from_env, workspace_protection_violation


def test_workspace_protection_blocks_overlapping_roots(tmp_path: Path) -> None:
    protected = tmp_path / "cladex"
    workspace = protected / "nested-project"
    workspace.mkdir(parents=True)

    violation = workspace_protection_violation(workspace, protected_roots=[protected])

    assert "overlaps protected CLADEX/runtime root" in violation
    assert workspace_protection_violation(tmp_path, protected_roots=[protected])
    assert workspace_protection_violation(workspace, env={"CLADEX_ALLOW_CLADEX_WORKSPACE": "1"}, protected_roots=[protected]) == ""


def test_protected_roots_include_singular_and_plural_env_vars(tmp_path: Path, monkeypatch) -> None:
    singular = tmp_path / "single"
    plural_a = tmp_path / "plural-a"
    plural_b = tmp_path / "plural-b"
    for root in (singular, plural_a, plural_b):
        root.mkdir()

    monkeypatch.setenv("CLADEX_PROTECTED_ROOT", str(singular))
    monkeypatch.setenv("CLADEX_PROTECTED_ROOTS", f"{plural_a};{plural_b};{singular}")

    roots = {str(root) for root in protected_roots_from_env()}

    assert str(singular.resolve()) in roots
    assert str(plural_a.resolve()) in roots
    assert str(plural_b.resolve()) in roots


def test_workspace_guidance_discovers_rules_skills_agents_and_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workspace = project / "packages" / "app"
    workspace.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "AGENTS.md").write_text("Before editing, read memory and validate changes.\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text("Use the reviewer subagent when reviewing.\n", encoding="utf-8")
    (project / ".codex" / "skills" / "review").mkdir(parents=True)
    (project / ".codex" / "skills" / "review" / "SKILL.md").write_text(
        "FULL SKILL BODY SHOULD NOT BE IN PROMPT\n",
        encoding="utf-8",
    )
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "agents" / "reviewer.md").write_text("# Reviewer\n", encoding="utf-8")
    (project / ".claude" / "commands" / "review").mkdir(parents=True)
    (project / ".claude" / "commands" / "review" / "sweep.md").write_text("# Sweep\n", encoding="utf-8")

    guidance = format_workspace_guidance(workspace, agent_name="agent-review", max_chars=1800)

    assert "Workspace-local rules and skills." in guidance
    assert "AGENTS.md" in guidance
    assert "CLAUDE.md" in guidance
    assert "Codex project skills discovered: review." in guidance
    assert "Claude project subagents discovered: reviewer." in guidance
    assert "Claude project slash commands discovered: review/sweep." in guidance
    assert "FULL SKILL BODY SHOULD NOT BE IN PROMPT" not in guidance
