# TEAM_RULES.md — MAW v2 Governance

## Role Responsibilities

### Chair (1 seat)
- Clarify the user's request.
- Brief planners on the goal, constraints, and key questions.
- Synthesize proposals and comments into a final plan.
- Perform final inspection after execution.

### Planner (1-4 seats)
- Independently propose approaches.
- Constructively review all other planners' proposals.
- Do NOT read other proposals before writing your own.

### Executor (1 seat)
- Implement the approved final plan.
- Run tests and produce walkthroughs.
- May handle multiple revision cycles.
- Only commit after reviewer approval.

### Reviewer (1 seat)
- Inspect executor's work against the final plan.
- Check git diff, test results, code quality.
- Produce clear APPROVE or REQUEST_CHANGES decisions.

## Artifact Formats

### Proposals (`proposals/<seat>.md`)
Must include:
1. Understanding of the situation
2. Proposed approach
3. Expected scope (add/modify/delete)
4. Implementation order
5. Risks
6. Verification method
7. Items requiring Chair decision

### Comments (`comments/<seat>_on_<seat>.md`)
Must include:
1. What you agree with
2. What is missing
3. What is unreasonable or too risky
4. Specific suggestions for the final plan

### Final Plan (`final_plan.md`)
Must include:
1. Goal and definition of done
2. What to keep
3. What to delete
4. What to add or modify
5. File-level change list
6. Execution order
7. Data or config migration
8. Safety constraints
9. Test and verification commands
10. Rollback boundary
11. Explicit non-goals

### Walkthrough (`walkthroughs/walkthrough_NNN.md`)
Must include:
1. What was actually changed
2. Differences from final plan and why
3. What was deleted
4. Test commands and results
5. Unresolved issues
6. Branch and commit status

### Review (`reviews/review_NNN.md`)
Must contain on its own line:
```
DECISION: APPROVE
```
or
```
DECISION: REQUEST_CHANGES
```
If REQUEST_CHANGES, list actionable, verifiable fixes.

## User Gates

The workflow WILL STOP and wait for user input at:
- `WAITING_USER_CLARIFICATION` — Chair needs clarification
- `WAITING_USER_APPROVAL` — Final plan ready for approval
- `WAITING_USER_DECISION` — Chair found issues needing user input

Only the user (via UI/CLI) writes `answers.md` and `user_decision.md`.

## Git Rules

- Executor works on a feature branch.
- NO commit without Reviewer APPROVE.
- Commit message should reference the workflow ID.
- Push/PR policy follows the project's own conventions.
- Chair does NOT modify code.

## Same Agent, Multiple Roles

If the same agent is assigned multiple roles:
- The watcher serializes dispatches — roles are called one at a time.
- The agent must treat each dispatch independently.
- Do not carry state from one role to another outside of written artifacts.

## Failure Handling

- Max review iterations: 3 (configurable in manifest).
- Exceeding iterations stops at `WAITING_USER_DECISION`.
- Agent timeouts and retries are managed by watcher.
- If blocked, write honestly what blocks you.
