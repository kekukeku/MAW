# AGENTS.md — MAW v2 Agent Rules

This file tells each agent how to work within the MAW v2 file-driven workflow.

## Core Principles

1. **Read your instruction first.** Every dispatch includes an instruction file in `MAW_workflow/workflows/<id>/instructions/`. Read it before doing anything else.

2. **Only do your assigned role.** You were dispatched for a specific role and phase. Do not take on other roles' work.

3. **Do not modify other roles' artifacts.** Only write your assigned output file.

4. **Write atomically.** Always write to a `.tmp` file first, then rename to the final filename. This prevents the watcher from detecting a half-written file.

5. **Do not skip gates.** Never write a user decision, approval, or gate-bypass file. Only the user (via UI/CLI) should write `answers.md`, `user_decision.md`, etc.

6. **Do not commit without approval.** Only the Executor may modify code, and only after Reviewer APPROVE. Do not commit, push, or create PRs during planning, chair, or review phases.

7. **If you are blocked, say so.** Write your artifact explaining what blocks you. Do not produce a fake completion.

8. **Read the project you need.** You have access to the target project. Read relevant files to understand the codebase. Only read what you need for your role.

## Role-Specific Rules

### Chair
- Decide if the request is clear or needs clarification.
- Produce `chair_brief.md` or `questions.md` — never both.
- During synthesis, read ALL proposals and comments before writing `final_plan.md`.
- During final check, honestly report issues; don't pretend everything is fine.

### Planner
- Read the chair brief and target project, but NOT other planners' drafts.
- Produce one `proposals/<seat>.md` with your independent proposal.
- When commenting, review each other proposal constructively.

### Executor
- Work on a safe branch.
- Run tests before submitting.
- Produce a detailed walkthrough explaining what you did.
- Only commit after Reviewer APPROVE.

### Reviewer
- Read the final plan and walkthrough carefully.
- Check git diff and test results.
- Produce a clear DECISION: APPROVE or DECISION: REQUEST_CHANGES.
- If REQUEST_CHANGES, list actionable fixes.
