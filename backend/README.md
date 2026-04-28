# CLADEX Backend

Python backend package for the CLADEX desktop app and relay command-line tools.

The package exposes the `cladex`, `codex-discord`, and `claude-discord` console commands. It contains the relay runtime, review-swarm orchestration, local API helpers, and bundled Codex plugin assets used by the desktop application.

Development installs should use the pinned constraints file from the repository root:

```powershell
python -m pip install -e "backend[dev]" -c backend/constraints.txt
```

