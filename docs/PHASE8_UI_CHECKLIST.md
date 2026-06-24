# Phase 8 UI Manual Regression Checklist (Entry Gate)

> **Blocks Phase 8 implementation** until all items pass or have documented minimal fixes.
> Prerequisite: `docs/PHASE7_UI_CHECKLIST.md` all pass (context / Gate #1 — no regression).
> Run against a **real** target project (`MAW_MOCK_MODE=0` unless noted).
> Fill [x] for pass, [ ] for fail with note.

**Date**: _pending_
**Target project**: _pending_
**Operator**: _pending_

## Panel flow (real project, MAW_MOCK_MODE=0)

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| U1 | Panel 0 setup | LLM keys, project health, scaffold, adapter install sections load | [ ] |
| U2 | Panel 1 council start | Prompt, models, policy, context bar, file selector, Scout/Explorer toggles; council starts | [ ] |
| U3 | Panel 2 Gate #1 | Stage 3 readable; approve/reject work; audit card present (Phase 7 regression) | [ ] |
| U4 | Panel 3 pipeline | State badges advance council → executor → reviewer | [ ] |
| U5 | Panel 4 logs | WebSocket logs stream; task subscribe on task switch | [ ] |
| U6 | Panel 5 pre-commit | Report modal; commit gate enforced when `ALLOW_AUTO_COMMIT=false` | [ ] |

## Resilience & switching

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| U7 | Workflow resume | Restart MAW mid-workflow; UI shows correct resumed state | [ ] |
| U8 | Target switch | Changing target updates context preview and export path | [ ] |

## Layout & reload

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| U9 | Narrow viewport | Panels 0–2 usable at ≤768px; primary actions reachable | [ ] |
| U10 | Browser reload | Mid-workflow reload restores panel and pending gate | [ ] |

## Phase 7 regression (quick verify)

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| R1 | Context / Gate #1 | Re-run key Phase 7 items (C3, C5, C10, C11) — no regression | [ ] |

## Notes

- See `CONTEXT_RELIABILITY_PLAN.md` §1 for full criteria.
- Optional: repeat U4–U6 with `MAW_MOCK_MODE=1` for faster log/stream validation.
- Record blockers here before starting 8B–8E implementation.