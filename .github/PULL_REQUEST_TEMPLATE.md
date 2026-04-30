## What this changes
A 1-2 sentence summary.

## Why
Link the issue, finding ID (e.g. `F0007`), or audit-fix-ship loop tranche.

## Validation
- [ ] `python -m pytest backend/tests --tb=short -q` passes
- [ ] `npm run test` passes (lint + frontend smoke + api smoke)
- [ ] `npm run build` passes
- [ ] `python backend/relayctl.py privacy-audit --tracked-only .` clean
- [ ] `python backend/cladex.py doctor --json` ok
- [ ] Regression test added in the same commit (or rationale why not)

## Surgical?
- [ ] Touched only what the finding/feature demands
- [ ] No "improve adjacent code" reformat passes
- [ ] Comments explain the **why** for non-obvious choices
