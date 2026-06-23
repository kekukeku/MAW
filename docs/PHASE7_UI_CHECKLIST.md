# Phase 7 UI Manual Regression Checklist

> Run against `MAW_MOCK_MODE=1 ./start.sh` or `MAW.command`.
> Target: any real small project or ctx_smoke fixture.
> Fill [x] for pass, [ ] for fail with note.
> Fix only if a checklist item fails; no redesign.

**Date**: ________
**Target**: ctx_smoke fixture
**Viewport**: 1024px+

## Desktop viewport (>= 1024px)

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| C1 | Context bar | Shows L0/L1+ status; Scout hint when previewed; Explorer hint when enabled | [ ] |
| C2 | File selector chips | Manual files show as user-selected; Scout auto files visually distinct | [ ] |
| C3 | Gate #1 audit card | Shows Highest Level, Decision, Risk Flags chips (not raw codes only) | [ ] |
| C4 | Provenance details | Manual table + Scout table separated (`static/index.html:1460-1474`) | [ ] |
| C5 | Explorer block | Shows "research brief -- not source of truth" (`:1366`) | [ ] |
| C6 | Auto-approve blocked | With scout auto + default policy, Decision shows Blocked + `blocked_scout_auto_selected` translation | [ ] |

## Narrow viewport (<= 768px)

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| C7 | Context bar | No horizontal overflow; link `[預覽 Context]` still clickable | [ ] |
| C8 | Gate #1 audit card | Risk flag chips wrap; readable without horizontal scroll | [ ] |
| C9 | Provenance `<details>` | Expands without layout break | [ ] |

## Browser reload (Gate #1)

| # | Check | Pass criteria | Status |
|---|-------|---------------|--------|
| C10 | Reload at Gate #1 | Explorer brief block **still visible** (6g reload bug does not regress) | [ ] |
| C11 | Audit card | `context_audit` decision still shown after reload | [ ] |

## Notes

- C1: Context bar line `:1090-1099`.
- C3: Decision format `:1468-1480`; risk chips `:1211-1219` with translations.
- C5: Explorer heading `:1364-1366` must include "research brief -- not source of truth".
- C6: Default policy has `allow_scout_auto_approve=False`.
- C10: 6g reload bug fixed in `resolveContextPreview()` (`:1156` area).
