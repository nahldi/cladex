# AGENTS

- Discord messages are transport, not source of truth.
- Before editing files or answering factual repo questions, read `memory/STATUS.md`, `memory/TASKS.json`, `memory/DECISIONS.md`, `memory/HANDOFF.md`, and the relevant code/tests.
- Verify claims from other agents against files, git diff, tests, or docs before accepting them.
- Claim a task before editing files.
- Do not edit files owned by another fresh lease.
- For medium or large tasks, plan first in `memory/PLAN.md`, then implement, validate, repair, and update memory files.
- After every milestone, run validation and fix failures before proceeding.
- Before ending a turn, update STATUS, TASKS, DECISIONS if changed, and HANDOFF.
- If another agent drifted, correct it with evidence and log it in `memory/DRIFT_LOG.md`.
- Success claims require evidence.
