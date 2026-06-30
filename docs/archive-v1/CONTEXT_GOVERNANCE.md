# MAW Context Governance Reference

> **Audience**: operators, reviewers, and gate auditors.
> **Version**: 7 (Phase 7 release hardening).
> **SSOT source**: `project_context.py:build_context_audit_summary()` and `loop_orchestrator.py:_can_auto_approve_council()`.

This doc is the single reference for reason codes, risk flags, auto-approve policy, and level derivation.
Do not copy-paste these rules into other files; link here from README, REFACTOR_PLAN, or UI docs.

---

## 1. Context pipeline (Phase 6-7 final)

```text
User prompt + targetKey
  -> build_context_pack()           [L0 blueprint always; L1 manual files; L2 scout_auto_selected if enabled]
  -> run_explorer_brief() optional  [L3 explorerBrief attached to context_pack; failure isolated]
  -> build_prompt_envelope()        [Council prompt injection; Explorer = NOT source of truth]
  -> run_council()                  [conversation.context_pack + conversation.context.preview saved]
  -> build_context_audit_summary()  [SSOT for highestLevel, riskFlags, status]
  -> _can_auto_approve_council()    [autoApprovePolicy decision object]
  -> conversation.context_audit     [persisted via save_conversation()]
  -> Gate #1 UI                     [resolveContextPreview() reads context_pack + context_audit]
  -> export_to_target()             [PLANNING/council_NNN.md + .json with contextAuditSummary + autoApprovePolicy]
```

**SSOT rules (do not break)**:

1. Level/risk decisions come **only** from `build_context_audit_summary()` (`project_context.py:1137`).
2. Auto-approve decisions come **only** from `_can_auto_approve_council()` (`loop_orchestrator.py:353`).
3. Export reads `conversation.context_audit` first; fallback uses `audit_unavailable` (`export.py`), never fabricates `allowed_policy_ok`.

---

## 2. Auto-approve reason codes

From `loop_orchestrator.py:353` (`_can_auto_approve_council`):

| reasonCode | allowed | Meaning | Default blocks auto-approve? |
|------------|---------|---------|------------------------------|
| `allowed_policy_ok` | true | All policy + safety checks passed | — |
| `blocked_policy_disabled` | false | `auto_approve_council` not enabled | yes |
| `blocked_no_context` | false | No context pack / status unavailable | yes |
| `blocked_context_failed` | false | Context status failed | yes |
| `blocked_fatal_access` | false | `permission_denied` in accessIssues | yes |
| `blocked_context_partial` | false | status partial AND `allow_partial_auto_approve=False` | only if policy false |
| `blocked_l0_only` | false | highestLevel L0 AND `allow_l0_auto_approve=False` | yes (default) |
| `blocked_scout_auto_selected` | false | scout_auto_selected present AND `allow_scout_auto_approve=False` | yes (default) |
| `blocked_prompt_file_missing` | false | Prompt references file not in context | yes |

**Export-only reason code** (legacy fallback, `export.py`):

| reasonCode | When used |
|------------|-----------|
| `audit_unavailable` | `context_pack` exists but `conversation.context_audit` missing (pre-6g export) |

---

## 3. Risk flags

From `build_context_audit_summary()` (`project_context.py:1244-1257` plus 6g.1):

| riskFlag | Emitted when |
|----------|--------------|
| `l0_only` | `highestLevel == "L0"` |
| `scout_auto_selected` | Any file `source == "scout_auto_selected"` |
| `explorer_timeout` | Explorer status timeout or `limits.hitTimeout` |
| `explorer_failed` | Explorer status failed |
| `access_issue` | Any `accessIssues` entry |
| `context_truncated` | `summary.truncated == true` |

---

## 4. highestLevel derivation (6g.1 final)

From `build_context_audit_summary()` (`project_context.py:1210-1222`):

```text
L3  if explorerBrief.status in ("ready", "partial")
L2  elif any scout_auto_selected file
L1  elif any user_selected file
L0  else
```

**Key rule (6g.1)**: a failed/timeout/skipped explorer brief does **not** promote to L3; the level falls back to L2/L1/L0. Risk flags (`explorer_timeout`, `explorer_failed`) are preserved regardless.

---

## 5. Formal design decisions

### 5.1 Truncation is audit-only

`context_truncated` and ordinary `accessIssues` (secret exclusion, scout skip, etc.) **do not block** auto-approve.
`blocked_context_truncated` is **not implemented**.
This is a formal design decision; future policy may add a blocking rule.

### 5.2 allow_partial_auto_approve defaults to True

When context `status == "partial"` (has accessIssues), auto-approve is still allowed unless the review policy explicitly sets `allow_partial_auto_approve: false` (`loop_orchestrator.py:404`).
The Gate #1 UI always shows all riskFlags for human audit.

### 5.3 Explorer failure isolation

Explorer failures never block Council. Orchestrator wraps Explorer execution in a `try/except` (line 482); failures produce `explorerBrief.status: "failed"|"timeout"|"skipped"`, recorded in `accessIssues` and logged as warning. Council proceeds normally.

### 5.4 Audit-unavailable for legacy exports

Missing `context_audit` on pre-6g conversations produces `audit_unavailable` in export, never `allowed_policy_ok`. This reason code is export-only; it does not appear in live auto-approve decisions.

---

## 6. Known pytest warnings (accepted)

```
ResourceWarning: unclosed transport in Explorer subprocess tests
Reason: daemon thread + bounded join pattern in explorer.py
Impact: none on production; tests pass
Revisit: if pytest --filterwarnings=error is adopted
```

---

## 7. Related docs

- `CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md` — full architecture plan (§13 Phase 6g + §17.1 6g.1).
- `CONTEXT_RELEASE_HARDENING_PLAN.md` — Phase 7 release hardening plan.
- `docs/PHASE7_UI_CHECKLIST.md` — manual UI regression checklist.
