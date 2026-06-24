"""v2 app — CLI entry point for MAW v2 workflow management."""

import argparse
import json
import sys
from pathlib import Path

from v2.schema import (
    build_roster,
    make_manifest,
    load_manifest,
    WorkflowStatus,
    TERMINAL_STATES,
)
from v2.files import (
    scaffold_target,
    init_workflow,
    workflow_dir,
    workflows_dir,
    list_workflow_ids,
    set_active_workflow,
    write_atomic,
    read_file,
    read_file_optional,
    exists_nonempty,
    ARTIFACT_REQUEST,
    ARTIFACT_QUESTIONS,
    ARTIFACT_CHAIR_BRIEF,
    ARTIFACT_FINAL_PLAN,
    ARTIFACT_COMPLETION,
)
from v2.watcher import Watcher
from v2.dispatcher import list_adapters


def cmd_create(args):
    """Create a new workflow."""
    target_path = str(Path(args.target).resolve())

    # Scaffold if needed
    scaffold_target(target_path)

    # Build roster
    planners = []
    for i, agent in enumerate(args.planners):
        seat = f"planner_{chr(ord('a') + i)}"
        planners.append({"seat": seat, "agent": agent})

    roster = build_roster(
        chair=args.chair,
        planners=planners,
        executor=args.executor,
        reviewer=args.reviewer,
    )

    # Determine workflow ID
    existing = list_workflow_ids(target_path)
    next_num = len(existing) + 1
    workflow_id = args.id or f"workflow_{next_num:03d}"

    # Build manifest
    manifest = make_manifest(
        workflow_id=workflow_id,
        target_path=target_path,
        roster=roster,
        require_user_plan_approval=not args.skip_approval,
        max_review_iterations=args.max_reviews,
    )

    # Read request
    if args.request:
        request = args.request
    elif args.request_file:
        request = Path(args.request_file).read_text(encoding="utf-8")
    else:
        print("Enter your request (press Ctrl+D when done):")
        request = sys.stdin.read()

    wf_dir = init_workflow(target_path, workflow_id, manifest, request)
    set_active_workflow(target_path, workflow_id)

    print(f"Workflow created: {workflow_id}")
    print(f"  Target: {target_path}")
    print(f"  Dir: {wf_dir}")
    print(f"  Roster: chair={args.chair}, planners={len(planners)}, executor={args.executor}, reviewer={args.reviewer}")


def cmd_watch(args):
    """Start the watcher."""
    watcher = Watcher(
        target_path=args.target,
        workflow_id=args.workflow_id,
        poll_interval=args.poll,
        agent_timeout=args.timeout,
        auto_run=not args.no_auto,
    )

    if args.once:
        active = watcher.run_once()
        if not active:
            print("Workflow complete or inactive.")
    else:
        print(f"Watcher started. Press Ctrl+C to stop.")
        watcher.start()


def cmd_status(args):
    """Show workflow status."""
    wf_dir = workflow_dir(args.target, args.workflow_id) if args.workflow_id else None
    if not wf_dir or not wf_dir.is_dir():
        workflow_id = args.workflow_id or "none"
        print(f"No workflow found: {workflow_id}")
        return

    manifest = load_manifest(str(wf_dir))
    status = manifest["status"]
    roster = manifest["roster"]

    print(f"Workflow: {manifest['workflow_id']}")
    print(f"Status: {status}")
    print(f"Target: {manifest['target_path']}")
    print(f"Created: {manifest['created_at']}")

    if "last_transition_at" in manifest:
        print(f"Last transition: {manifest['last_transition_at']}")

    print(f"\nRoster:")
    print(f"  Chair: {roster['chair']}")
    for p in roster["planners"]:
        print(f"  Planner {p['seat']}: {p['agent']}")
    print(f"  Executor: {roster['executor']}")
    print(f"  Reviewer: {roster['reviewer']}")

    if args.verbose:
        print(f"\nArtifacts:")
        for name in [
            "request.md", "chair_brief.md", "questions.md", "answers.md",
            "final_plan.md", "user_decision.md", "commit.md", "completion.md",
        ]:
            path = wf_dir / name
            marker = " [EXISTS]" if exists_nonempty(path) else ""
            print(f"  {name}{marker}")

        proposals_dir = wf_dir / "proposals"
        if proposals_dir.is_dir():
            for p in sorted(proposals_dir.glob("*.md")):
                print(f"  proposals/{p.name} [EXISTS]")

        comments_dir = wf_dir / "comments"
        if comments_dir.is_dir():
            for c in sorted(comments_dir.glob("*.md")):
                print(f"  comments/{c.name} [EXISTS]")

        walkthroughs_dir = wf_dir / "walkthroughs"
        if walkthroughs_dir.is_dir():
            for w in sorted(walkthroughs_dir.glob("*.md")):
                print(f"  walkthroughs/{w.name} [EXISTS]")

        reviews_dir = wf_dir / "reviews"
        if reviews_dir.is_dir():
            for r in sorted(reviews_dir.glob("*.md")):
                print(f"  reviews/{r.name} [EXISTS]")


def cmd_answer(args):
    """Submit answer to chair questions."""
    wf_dir = workflow_dir(args.target, args.workflow_id)
    manifest = load_manifest(str(wf_dir))

    if args.text:
        answer = args.text
    elif args.file:
        answer = Path(args.file).read_text(encoding="utf-8")
    else:
        answer = sys.stdin.read()

    from v2.workflow import user_answer
    ok = user_answer(wf_dir, manifest, answer)
    if ok:
        print("Answer submitted.")
    else:
        print("Workflow is not waiting for clarification.")


def cmd_decide(args):
    """Submit approval/change-request/cancel decision."""
    wf_dir = workflow_dir(args.target, args.workflow_id)
    manifest = load_manifest(str(wf_dir))

    decision = args.decision.upper()
    from v2.workflow import user_decision, user_cancel

    if decision == "CANCEL":
        ok = user_cancel(wf_dir, manifest)
    else:
        ok = user_decision(wf_dir, manifest, decision)

    if ok:
        print(f"Decision '{decision}' recorded.")
    else:
        print("Could not record decision (invalid state or token).")


def cmd_list_workflows(args):
    """List workflows for a target."""
    ids = list_workflow_ids(args.target)
    if not ids:
        print("No workflows found.")
        return
    for wf_id in ids:
        wf_dir = workflow_dir(args.target, wf_id)
        manifest = load_manifest(str(wf_dir))
        status = manifest["status"]
        created = manifest.get("created_at", "?")
        print(f"  {wf_id}  {status:30s}  {created}")


def cmd_adapters(args):
    """List available adapters."""
    adapters = list_adapters()
    print("Available adapters:")
    for a in adapters:
        print(f"  {a}")


def cmd_read(args):
    """Read a workflow artifact."""
    wf_dir = workflow_dir(args.target, args.workflow_id)
    path = wf_dir / args.artifact
    if not path.is_file():
        print(f"Artifact not found: {args.artifact}")
        return
    content = read_file(path)
    if args.tail:
        lines = content.splitlines()
        for line in lines[-args.tail:]:
            print(line)
    else:
        print(content)


def main():
    parser = argparse.ArgumentParser(
        description="MAW v2 — File-driven multi-agent workflow coordinator"
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # create
    p_create = sub.add_parser("create", help="Create a new workflow")
    p_create.add_argument("--target", "-t", required=True, help="Target project path")
    p_create.add_argument("--id", help="Workflow ID (auto-generated if omitted)")
    p_create.add_argument("--chair", default="codex", help="Chair agent ID")
    p_create.add_argument("--planners", nargs="+", default=["codex"], help="Planner agent IDs (1-4)")
    p_create.add_argument("--executor", default="codex", help="Executor agent ID")
    p_create.add_argument("--reviewer", default="codex", help="Reviewer agent ID")
    p_create.add_argument("--request", "-r", help="Request text")
    p_create.add_argument("--request-file", help="Read request from file")
    p_create.add_argument("--skip-approval", action="store_true", help="Skip user plan approval gate")
    p_create.add_argument("--max-reviews", type=int, default=3, help="Max review iterations")

    # watch
    p_watch = sub.add_parser("watch", help="Start the watcher")
    p_watch.add_argument("--target", "-t", required=True, help="Target project path")
    p_watch.add_argument("--workflow-id", "-w", help="Workflow ID to watch")
    p_watch.add_argument("--poll", type=float, default=3.0, help="Poll interval")
    p_watch.add_argument("--timeout", type=int, default=600, help="Agent timeout")
    p_watch.add_argument("--once", action="store_true", help="Run one tick and exit")
    p_watch.add_argument("--no-auto", action="store_true", help="Don't auto-dispatch")

    # status
    p_status = sub.add_parser("status", help="Show workflow status")
    p_status.add_argument("--target", "-t", required=True, help="Target project path")
    p_status.add_argument("--workflow-id", "-w", help="Workflow ID")
    p_status.add_argument("--verbose", "-v", action="store_true", help="Show artifact details")

    # answer
    p_answer = sub.add_parser("answer", help="Answer chair questions")
    p_answer.add_argument("--target", "-t", required=True, help="Target project path")
    p_answer.add_argument("--workflow-id", "-w", required=True, help="Workflow ID")
    p_answer.add_argument("--text", help="Answer text")
    p_answer.add_argument("--file", help="Read answer from file")

    # decide
    p_decide = sub.add_parser("decide", help="Approve/reject/cancel plan")
    p_decide.add_argument("--target", "-t", required=True, help="Target project path")
    p_decide.add_argument("--workflow-id", "-w", required=True, help="Workflow ID")
    p_decide.add_argument("decision", choices=["APPROVE", "REQUEST_CHANGES", "CANCEL"], help="Decision")

    # list
    p_list = sub.add_parser("list", help="List workflows")
    p_list.add_argument("--target", "-t", required=True, help="Target project path")

    # adapters
    sub.add_parser("adapters", help="List available adapters")

    # read
    p_read = sub.add_parser("read", help="Read a workflow artifact")
    p_read.add_argument("--target", "-t", required=True, help="Target project path")
    p_read.add_argument("--workflow-id", "-w", required=True, help="Workflow ID")
    p_read.add_argument("artifact", help="Artifact name (e.g., request.md, final_plan.md)")
    p_read.add_argument("--tail", type=int, help="Show last N lines only")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "answer":
        cmd_answer(args)
    elif args.command == "decide":
        cmd_decide(args)
    elif args.command == "list":
        cmd_list_workflows(args)
    elif args.command == "adapters":
        cmd_adapters(args)
    elif args.command == "read":
        cmd_read(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
