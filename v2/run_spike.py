#!/usr/bin/env python3
"""Spike runner to verify real Antigravity agent adapter.

Can be run via:
    uv run python -m v2.run_spike --role planner
    uv run python -m v2.run_spike --role reviewer
"""

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from v2.schema import (
    build_roster,
    make_manifest,
    load_manifest,
    save_manifest,
    set_status,
    WorkflowStatus,
    proposal_path,
    review_path,
)
from v2.files import (
    scaffold_target,
    init_workflow,
    ensure_workflow_dirs,
    write_atomic,
    exists_nonempty,
)
from v2.watcher import Watcher


def run_planner_spike(target_path: str) -> None:
    print("\n=======================================================")
    print("RUNNING PLANNER SPIKE...")
    print("=======================================================")
    
    wf_id = "wf_spike_planner"
    roster = build_roster(
        chair="mock",
        planners=[{"seat": "planner_a", "agent": "antigravity"}],
        executor="mock",
        reviewer="mock",
    )
    manifest = make_manifest(wf_id, target_path, roster)
    
    # Initialize workflow
    request = "Add a dark-mode toggle switch to the application navbar."
    wf_dir = init_workflow(target_path, wf_id, manifest, request)
    ensure_workflow_dirs(wf_dir)
    
    # Fast-forward to PLANNING by manually creating the chair_brief.md
    brief_content = (
        "# Chair Brief\n"
        "- **Goal**: Add a dark-mode toggle switch to the navbar.\n"
        "- **Constraints**: Pure CSS/JS implementation, no external UI library.\n"
        "- **Non-Goals**: Persistent backend settings storage.\n"
        "- **Questions**: Where in the navbar should it be placed?\n"
    )
    write_atomic(wf_dir / "chair_brief.md", brief_content)
    
    # Set status to PLANNING
    manifest = load_manifest(str(wf_dir))
    set_status(manifest, WorkflowStatus.PLANNING)
    save_manifest(str(wf_dir), manifest)
    
    print(f"Workflow initialized in: {wf_dir}")
    print(f" Roster: planners = planner_a (antigravity)")
    print(f" Initial status: PLANNING")
    
    # Initialize Watcher
    watcher = Watcher(
        target_path=target_path,
        workflow_id=wf_id,
        poll_interval=1.0,
        agent_timeout=300,
        auto_run=True,
    )
    
    # Start loop
    start_time = time.time()
    expected_artifact = wf_dir / proposal_path("planner_a")
    invocation_file = expected_artifact.with_suffix(expected_artifact.suffix + ".invocation")
    
    invocation_id = "unknown"
    dispatched = False
    completed = False
    
    print("Starting Watcher tick loop...")
    while time.time() - start_time < 300:
        # Check if invocation ID has appeared
        if not dispatched and invocation_file.is_file():
            try:
                state = json.loads(invocation_file.read_text(encoding="utf-8"))
                invocation_id = state.get("conversation_id", "unknown")
                print(f"--> Real Antigravity Dispatched! Conversation ID: {invocation_id}")
                dispatched = True
            except Exception:
                pass
                
        watcher.run_once()
        
        # Check if target artifact was written
        if exists_nonempty(expected_artifact):
            print(f"--> Expected artifact detected: {expected_artifact.name}")
            completed = True
            break
            
        time.sleep(1.0)
        
    elapsed = time.time() - start_time
    manifest = load_manifest(str(wf_dir))
    
    print("\n-------------------------------------------------------")
    print("SPIKE RESULT:")
    print(f"- target path: {target_path}")
    print(f"- workflow ID: {wf_id}")
    print(f"- dispatch key: {wf_id}:proposal:planner_a:1")
    print(f"- invocation ID (Conversation ID): {invocation_id}")
    print(f"- expected artifact: {proposal_path('planner_a')}")
    print(f"- elapsed time: {elapsed:.1f} seconds")
    print(f"- final workflow status: {manifest['status']}")
    print(f"- success: {completed}")
    print("-------------------------------------------------------")
    
    if completed:
        print("\nArtifact Preview:")
        print("---------------------------------------")
        print(expected_artifact.read_text(encoding="utf-8")[:400] + "\n...")
        print("---------------------------------------")
    else:
        raise RuntimeError("Planner spike failed to produce expected artifact.")


def run_reviewer_spike(target_path: str) -> None:
    print("\n=======================================================")
    print("RUNNING REVIEWER SPIKE...")
    print("=======================================================")
    
    wf_id = "wf_spike_reviewer"
    roster = build_roster(
        chair="mock",
        planners=[{"seat": "planner_a", "agent": "mock"}],
        executor="mock",
        reviewer="antigravity",
    )
    manifest = make_manifest(wf_id, target_path, roster)
    
    # Initialize workflow
    request = "Add a glassmorphism button."
    wf_dir = init_workflow(target_path, wf_id, manifest, request)
    ensure_workflow_dirs(wf_dir)
    
    # Manually create required artifacts for review phase
    write_atomic(wf_dir / "chair_brief.md", "# Brief")
    write_atomic(wf_dir / proposal_path("planner_a"), "# Proposal")
    
    final_plan = (
        "# Final Plan\n"
        "## 1. Goal\nImplement a glassmorphism button.\n"
        "## 5. File-Level Change List\n- `index.html`\n"
        "## 9. Verification\nOpen index.html and verify styling.\n"
    )
    write_atomic(wf_dir / "final_plan.md", final_plan)
    
    walkthrough = (
        "# Walkthrough\n"
        "## Changes Made\n- Added glassmorphism button to index.html.\n"
        "## Tests\nVerified locally.\n"
    )
    write_atomic(wf_dir / "walkthroughs/walkthrough_001.md", walkthrough)
    
    # Set status to REVIEWING, review_iteration = 0
    manifest = load_manifest(str(wf_dir))
    set_status(manifest, WorkflowStatus.REVIEWING)
    manifest["review_iteration"] = 0
    save_manifest(str(wf_dir), manifest)
    
    print(f"Workflow initialized in: {wf_dir}")
    print(f" Roster: reviewer = antigravity")
    print(f" Initial status: REVIEWING (iteration 1)")
    
    # Initialize Watcher
    watcher = Watcher(
        target_path=target_path,
        workflow_id=wf_id,
        poll_interval=1.0,
        agent_timeout=300,
        auto_run=True,
    )
    
    # Start loop
    start_time = time.time()
    expected_artifact = wf_dir / review_path(1)
    invocation_file = expected_artifact.with_suffix(expected_artifact.suffix + ".invocation")
    
    invocation_id = "unknown"
    dispatched = False
    completed = False
    
    print("Starting Watcher tick loop...")
    while time.time() - start_time < 300:
        if not dispatched and invocation_file.is_file():
            try:
                state = json.loads(invocation_file.read_text(encoding="utf-8"))
                invocation_id = state.get("conversation_id", "unknown")
                print(f"--> Real Antigravity Dispatched! Conversation ID: {invocation_id}")
                dispatched = True
            except Exception:
                pass
                
        watcher.run_once()
        
        if exists_nonempty(expected_artifact):
            print(f"--> Expected artifact detected: {expected_artifact.name}")
            completed = True
            break
            
        time.sleep(1.0)
        
    elapsed = time.time() - start_time
    manifest = load_manifest(str(wf_dir))
    
    print("\n-------------------------------------------------------")
    print("SPIKE RESULT:")
    print(f"- target path: {target_path}")
    print(f"- workflow ID: {wf_id}")
    print(f"- dispatch key: {wf_id}:review:reviewer:1")
    print(f"- invocation ID (Conversation ID): {invocation_id}")
    print(f"- expected artifact: {review_path(1)}")
    print(f"- elapsed time: {elapsed:.1f} seconds")
    print(f"- final workflow status: {manifest['status']}")
    print(f"- success: {completed}")
    print("-------------------------------------------------------")
    
    if completed:
        print("\nArtifact Preview:")
        print("---------------------------------------")
        print(expected_artifact.read_text(encoding="utf-8")[:400] + "\n...")
        print("---------------------------------------")
    else:
        raise RuntimeError("Reviewer spike failed to produce expected artifact.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MAW v2 Real Antigravity Adapter Spike")
    parser.add_argument(
        "--role",
        choices=["planner", "reviewer"],
        default="planner",
        help="The role to spike (default: planner)",
    )
    args = parser.parse_args()
    
    target_path = tempfile.mkdtemp(prefix="maw_v2_spike_")
    try:
        scaffold_target(target_path)
        # Create dummy AGENTS.md / TEAM_RULES.md in target project
        Path(target_path).mkdir(parents=True, exist_ok=True)
        (Path(target_path) / "AGENTS.md").write_text("# Target AGENTS.md\n", encoding="utf-8")
        (Path(target_path) / "TEAM_RULES.md").write_text("# Target TEAM_RULES.md\n", encoding="utf-8")
        
        if args.role == "planner":
            run_planner_spike(target_path)
        else:
            run_reviewer_spike(target_path)
    finally:
        shutil.rmtree(target_path, ignore_errors=True)


if __name__ == "__main__":
    main()
