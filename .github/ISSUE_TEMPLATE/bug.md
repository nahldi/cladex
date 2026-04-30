---
name: Bug report
about: Something is broken or behaves unexpectedly
title: "[bug] "
labels: bug
---

## What happened
A clear, concise description of the bug.

## Expected
What you thought would happen.

## Reproduction steps
1. ...
2. ...
3. ...

## Environment
- CLADEX version: (`cladex --version` or the `.exe` filename)
- OS: (Windows 11 / macOS 14 / Ubuntu 24.04 / etc.)
- Python: (`python --version`)
- Node: (`node --version`)
- Codex CLI version: (`codex --version`)
- Claude Code CLI version: (`claude --version`)

## Diagnostics
Please attach the JSON output of:
```
python backend/cladex.py doctor --json
```
and the relevant section of `relay.log` (`%LOCALAPPDATA%\discord-codex-relay\state\<namespace>\relay.log` on Windows).

**Redact any bot tokens or other secrets before posting.**
