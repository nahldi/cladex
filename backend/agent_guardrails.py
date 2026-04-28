from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


CLADEX_REPO_ROOT = Path(__file__).resolve().parents[1]

RULE_FILE_NAMES = (
    "AGENTS.md",
    "AGENT.md",
    "CLAUDE.md",
    "agents.md",
    "agent.md",
)
ROADMAP_FILE_NAMES = (
    "UNIFIED_ROADMAP.md",
    "ROADMAP.md",
    "roadmap.md",
    "roadmap-pt1.md",
    "roadmap-pt2.md",
)
CODEX_SKILL_PATTERNS = (
    ".codex/skills/*/SKILL.md",
    ".codex/skills/*/*/SKILL.md",
    ".agents/skills/*/SKILL.md",
    ".agents/skills/*/*/SKILL.md",
)
CLAUDE_AGENT_PATTERN = ".claude/agents/*.md"
CLAUDE_COMMAND_PATTERN = ".claude/commands/**/*.md"


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def path_key(path: Path) -> str:
    resolved = path.expanduser().resolve()
    text = str(resolved)
    return text.lower() if os.name == "nt" else text


def is_subpath_or_same(candidate: Path, root: Path) -> bool:
    try:
        candidate_key = path_key(candidate)
        root_key = path_key(root)
    except OSError:
        candidate_key = str(candidate.expanduser().absolute())
        root_key = str(root.expanduser().absolute())
        if os.name == "nt":
            candidate_key = candidate_key.lower()
            root_key = root_key.lower()
    if candidate_key == root_key:
        return True
    separator = "\\" if os.name == "nt" else "/"
    return candidate_key.startswith(root_key.rstrip("\\/") + separator)


def paths_overlap(left: Path, right: Path) -> bool:
    return is_subpath_or_same(left, right) or is_subpath_or_same(right, left)


def protected_roots_from_env() -> list[Path]:
    roots = [CLADEX_REPO_ROOT]
    raw = ";".join(
        value
        for value in (
            os.environ.get("CLADEX_PROTECTED_ROOT", ""),
            os.environ.get("CLADEX_PROTECTED_ROOTS", ""),
        )
        if value
    )
    for item in re.split(r"[;,]", raw):
        text = item.strip()
        if text:
            roots.append(Path(text).expanduser())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = path_key(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root.expanduser().resolve())
    return unique


def workspace_protection_override_enabled(env: dict[str, str] | None = None) -> bool:
    source = os.environ.copy()
    if env:
        source.update(env)
    return truthy(source.get("CLADEX_ALLOW_CLADEX_WORKSPACE")) or truthy(source.get("CLADEX_ALLOW_SELF_WORKSPACE"))


def workspace_protection_violation(
    workspace: Path | str,
    *,
    env: dict[str, str] | None = None,
    protected_roots: list[Path] | None = None,
) -> str:
    if workspace_protection_override_enabled(env):
        return ""
    target = Path(workspace).expanduser().resolve()
    for root in protected_roots or protected_roots_from_env():
        if paths_overlap(target, root):
            return (
                f"Workspace `{target}` overlaps protected CLADEX/runtime root `{root}`. "
                "Use a project workspace outside CLADEX, or set CLADEX_ALLOW_CLADEX_WORKSPACE=1 only for deliberate CLADEX development."
            )
    return ""


def assert_workspace_allowed(
    workspace: Path | str,
    *,
    env: dict[str, str] | None = None,
    protected_roots: list[Path] | None = None,
) -> None:
    violation = workspace_protection_violation(workspace, env=env, protected_roots=protected_roots)
    if violation:
        raise ValueError(violation)


def project_root_for_guidance(workdir: Path) -> Path:
    current = workdir.expanduser().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def ancestor_paths_within_project(workdir: Path) -> list[Path]:
    current = workdir.expanduser().resolve()
    root = project_root_for_guidance(current)
    paths: list[Path] = []
    candidate = current
    while True:
        paths.append(candidate)
        if path_key(candidate) == path_key(root) or candidate.parent == candidate:
            break
        candidate = candidate.parent
    return paths


def find_upward_file(workdir: Path, names: tuple[str, ...]) -> Path | None:
    lowered = {name.lower() for name in names}
    for root in ancestor_paths_within_project(workdir):
        try:
            for entry in root.iterdir():
                if entry.is_file() and entry.name.lower() in lowered:
                    return entry
        except OSError:
            continue
    return None


def find_upward_files(workdir: Path, names: tuple[str, ...]) -> list[Path]:
    lowered = {name.lower() for name in names}
    found: list[Path] = []
    seen: set[str] = set()
    for root in ancestor_paths_within_project(workdir):
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file() or entry.name.lower() not in lowered:
                continue
            key = path_key(entry)
            if key in seen:
                continue
            seen.add(key)
            found.append(entry)
    return found


def _shorten(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _highlight_lines(path: Path, *, limit: int = 4) -> list[str]:
    patterns = (
        "must",
        "do not",
        "before editing",
        "before answering",
        "claim",
        "plan",
        "validate",
        "skill",
        "agent",
        "permission",
        "current",
        "next",
    )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    highlights: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-*># ").strip()
        if not line:
            continue
        lowered = line.lower()
        if any(pattern in lowered for pattern in patterns):
            shortened = _shorten(line, 220)
            if shortened not in highlights:
                highlights.append(shortened)
        if len(highlights) >= limit:
            break
    return highlights


def _collect_glob(root: Path, pattern: str, *, max_items: int = 16) -> list[Path]:
    try:
        items = sorted(path for path in root.glob(pattern) if path.is_file())
    except OSError:
        return []
    return items[:max_items]


def _skill_name(path: Path) -> str:
    if path.name.lower() == "skill.md":
        return path.parent.name
    return path.stem


def discover_workspace_guidance(workdir: Path | str, *, agent_name: str = "") -> dict[str, Any]:
    workspace = Path(workdir).expanduser().resolve()
    root = project_root_for_guidance(workspace)
    rule_files: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for name_group in (RULE_FILE_NAMES, ROADMAP_FILE_NAMES):
        for found in find_upward_files(workspace, name_group):
            key = path_key(found)
            if key in seen_rules:
                continue
            seen_rules.add(key)
            rule_files.append(
                {
                    "path": str(found),
                    "name": found.name,
                    "highlights": _highlight_lines(found, limit=5),
                }
            )

    codex_skills: list[dict[str, str]] = []
    seen_skills: set[str] = set()
    for pattern in CODEX_SKILL_PATTERNS:
        for path in _collect_glob(root, pattern):
            key = path_key(path)
            if key in seen_skills:
                continue
            seen_skills.add(key)
            codex_skills.append({"name": _skill_name(path), "path": str(path)})

    claude_agents = [
        {"name": path.stem, "path": str(path)}
        for path in _collect_glob(root, CLAUDE_AGENT_PATTERN)
    ]
    claude_commands = [
        {"name": path.relative_to(root / ".claude" / "commands").with_suffix("").as_posix(), "path": str(path)}
        for path in _collect_glob(root, CLAUDE_COMMAND_PATTERN)
    ]

    return {
        "agentName": agent_name,
        "workspace": str(workspace),
        "projectRoot": str(root),
        "ruleFiles": rule_files,
        "codexSkills": codex_skills,
        "claudeAgents": claude_agents,
        "claudeCommands": claude_commands,
    }


def format_workspace_guidance(workdir: Path | str, *, agent_name: str = "", max_chars: int = 1800) -> str:
    guidance = discover_workspace_guidance(workdir, agent_name=agent_name)
    lines = [
        "Workspace-local rules and skills.",
        f"Workspace: {guidance['workspace']}",
        f"Project root: {guidance['projectRoot']}",
        f"Protected CLADEX root: {CLADEX_REPO_ROOT}",
        "Boundary: edit only the active workspace/worktree unless the user explicitly assigns another allowed workspace. Do not edit CLADEX itself from a managed relay profile.",
    ]
    if guidance["ruleFiles"]:
        lines.append("Rule files to follow:")
        for item in guidance["ruleFiles"][:4]:
            lines.append(f"- {item['name']}: {item['path']}")
            for highlight in item.get("highlights", [])[:3]:
                lines.append(f"  - {highlight}")
    else:
        lines.append("Rule files to follow: none discovered.")
    if guidance["codexSkills"]:
        names = ", ".join(item["name"] for item in guidance["codexSkills"][:12])
        lines.append(f"Codex project skills discovered: {names}. Use a matching skill when the task calls for it.")
    if guidance["claudeAgents"]:
        names = ", ".join(item["name"] for item in guidance["claudeAgents"][:12])
        lines.append(f"Claude project subagents discovered: {names}. Use/delegate to a matching subagent when appropriate.")
    if guidance["claudeCommands"]:
        names = ", ".join(item["name"] for item in guidance["claudeCommands"][:12])
        lines.append(f"Claude project slash commands discovered: {names}. Use them when they match the requested workflow.")
    lines.append("Keep this discovery as compact guidance; do not dump full rule or skill files into every message.")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max(max_chars - 18, 0)].rstrip() + "\n...[truncated]"
