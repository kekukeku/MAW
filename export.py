import os
import sys
import re
import json
import time
from datetime import datetime, timezone
import shutil

from maw_paths import WORKFLOW_DIR_NAME, get_project_root, get_workflow_root

# Config registry path
CONFIG_PATH = os.path.expanduser("~/.agent-cowork/targets.json")

def load_targets():
    """Load and return target configurations.

    If TARGET_PROJECT_PATH is set, inject a synthetic 'env' target so users
    can get started without editing ~/.agent-cowork/targets.json.
    """
    if not os.path.exists(CONFIG_PATH):
        # Create default folder if it doesn't exist
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        # Create an empty config file
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"default": "", "projects": {}}, f, indent=2)
        data = {"default": "", "projects": {}}
    else:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Error loading targets.json: {e}", file=sys.stderr)
            data = {"default": "", "projects": {}}

    env_path = os.getenv("TARGET_PROJECT_PATH", "").strip()
    if env_path and os.path.isdir(env_path):
        projects = data.setdefault("projects", {})
        if "env" not in projects:
            projects["env"] = {
                "name": "ENV Target",
                "path": env_path,
                "description": "From TARGET_PROJECT_PATH environment variable",
            }
        if not data.get("default"):
            data["default"] = "env"

    return data

def is_pid_alive(pid):
    """Check if a process is still running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def validate_target(target_path):
    """Validate that the target project has a complete MAW_workflow contract."""
    project_root = get_project_root(target_path)
    if not os.path.isdir(project_root):
        return False, ["Target directory does not exist or is not a directory."]

    workflow_root = get_workflow_root(project_root)
    if not os.path.isdir(workflow_root):
        return False, [f"Missing {WORKFLOW_DIR_NAME}/ directory."]

    issues = []
    required_files = [
        ("AGENT_STATE.md", "file"),
        ("TASKS", "dir"),
        ("PLANNING", "dir"),
        ("REVIEWS", "dir"),
        ("scripts/trigger_antigravity.py", "file"),
        ("agent-runner/trigger-review.js", "file"),
        ("agent-runner/route-review-decision.js", "file"),
    ]
    for rel_path, kind in required_files:
        full = os.path.join(workflow_root, rel_path)
        if kind == "file" and not os.path.isfile(full):
            issues.append(f"Missing {WORKFLOW_DIR_NAME}/{rel_path}.")
        elif kind == "dir" and not os.path.isdir(full):
            issues.append(f"Missing {WORKFLOW_DIR_NAME}/{rel_path}/ directory.")

    workflow_gitignore = os.path.join(workflow_root, ".gitignore")
    if os.path.isfile(workflow_gitignore):
        try:
            with open(workflow_gitignore, "r", encoding="utf-8") as f:
                gi = f.read()
            for entry in ("AGENT_STATE.md", "TASKS/", "PLANNING/", "REVIEWS/", "*.tmp", ".maw_export.lock"):
                if entry not in gi:
                    issues.append(f"{WORKFLOW_DIR_NAME}/.gitignore missing required entry: {entry}")
        except Exception:
            issues.append(f"Could not read {WORKFLOW_DIR_NAME}/.gitignore.")

    project_gitignore = os.path.join(project_root, ".gitignore")
    if not os.path.isfile(project_gitignore):
        issues.append("Missing project root .gitignore.")
    else:
        try:
            with open(project_gitignore, "r", encoding="utf-8") as f:
                gi = f.read()
            if f"{WORKFLOW_DIR_NAME}/" not in gi:
                issues.append(f"Project .gitignore missing required entry: {WORKFLOW_DIR_NAME}/")
        except Exception:
            issues.append("Could not read project root .gitignore.")

    return len(issues) == 0, issues


def get_conversations_dir():
    """Resolve local MAW conversations directory (embedded council storage)."""
    maw_root = os.path.dirname(os.path.abspath(__file__))
    local_dir = os.path.join(maw_root, "data", "conversations")
    os.makedirs(local_dir, exist_ok=True)
    return local_dir

def acquire_export_lock(workflow_path, target_key):
    """Acquire a lock to prevent concurrent write collisions."""
    lock_file_path = os.path.join(workflow_path, ".maw_export.lock")
    pid = os.getpid()
    now_utc = datetime.now(timezone.utc).isoformat()
    
    if os.path.exists(lock_file_path):
        try:
            with open(lock_file_path, "r", encoding="utf-8") as f:
                lock_data = json.load(f)
            
            lock_pid = lock_data.get("pid", 0)
            lock_time_str = lock_data.get("startedAt", "")
            
            # Check 1: Check PID liveness
            if not is_pid_alive(lock_pid):
                print(f"Reclaiming lock: Lock owner PID {lock_pid} is dead.", file=sys.stderr)
                os.remove(lock_file_path)
            else:
                # Check 2: Check 5-minute timeout
                try:
                    lock_time = datetime.fromisoformat(lock_time_str.replace("Z", "+00:00"))
                    time_diff = datetime.now(timezone.utc) - lock_time
                    if time_diff.total_seconds() > 300:
                        print(f"Reclaiming lock: Lock is stale (> 5 minutes).", file=sys.stderr)
                        os.remove(lock_file_path)
                    else:
                        return False, f"Target project locked by active process PID {lock_pid} since {lock_time_str}."
                except Exception:
                    # In case timestamp is malformed, reclaim it
                    print("Reclaiming lock due to malformed lock timestamp.", file=sys.stderr)
                    os.remove(lock_file_path)
        except Exception as e:
            # Lock file might be corrupted/empty, delete it
            print(f"Reclaiming corrupt lock file: {e}", file=sys.stderr)
            try:
                os.remove(lock_file_path)
            except OSError:
                pass
                
    # Create new lock
    try:
        lock_info = {
            "pid": pid,
            "startedAt": now_utc,
            "target": target_key,
            "targetPath": workflow_path
        }
        with open(lock_file_path, "w", encoding="utf-8") as f:
            json.dump(lock_info, f, indent=2)
        return True, None
    except Exception as e:
        return False, f"Failed to write lock file: {e}"

def release_export_lock(workflow_path):
    """Release the lock file."""
    lock_file_path = os.path.join(workflow_path, ".maw_export.lock")
    if os.path.exists(lock_file_path):
        try:
            os.remove(lock_file_path)
        except OSError:
            pass

def allocate_task_num(agent_state_path):
    """Scan AGENT_STATE.md and find max(NNN) + 1."""
    if not os.path.exists(agent_state_path):
        return "001"
    
    try:
        with open(agent_state_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Look for TASK-NNN instances
        task_nums = [int(m) for m in re.findall(r"TASK-(\d{3})", content)]
        if not task_nums:
            return "001"
        
        next_num = max(task_nums) + 1
        return f"{next_num:03d}"
    except Exception as e:
        print(f"Error scanning task numbers: {e}", file=sys.stderr)
        raise RuntimeError("Failed to parse AGENT_STATE.md to allocate next Task ID.")

def slugify_title(title):
    """Deterministic slugify algorithm: lower, non-ascii alphanumeric -> -, collapsed, trimmed, fallback."""
    if not title:
        return "council_synthesis"
    
    # 1. Convert to lowercase
    slug = title.lower()
    
    # 2. Replace non-ascii alphanumeric with -
    slug = re.sub(r"[^a-z0-9]", "-", slug)
    
    # 3. Collapse repeated -
    slug = re.sub(r"-+", "-", slug)
    
    # 4. Trim leading/trailing -
    slug = slug.strip("-")
    
    # 5. Length less than 3 -> fallback
    if len(slug) < 3:
        return "council_synthesis"
    
    # 6. Truncate to 48 characters
    if len(slug) > 48:
        slug = slug[:48].rstrip("-")
        # Ensure we still have at least 3 chars after truncate-strip
        if len(slug) < 3:
            return "council_synthesis"
            
    return slug

def append_registry_row_atomic(agent_state_path, task_num, slug, date_str):
    """Atomically append task row to AGENT_STATE.md table."""
    temp_path = agent_state_path + ".tmp"
    try:
        with open(agent_state_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # Regex to locate registry table rows: | **TASK-NNN** |
        table_row_pattern = re.compile(r"\|\s*\*\*TASK-\d{3}\*\*\s*\|")
        
        # Find index of the last task row
        last_row_idx = -1
        for idx, line in enumerate(lines):
            if table_row_pattern.search(line):
                last_row_idx = idx
                
        if last_row_idx == -1:
            # Let's search for table header to append after header
            header_pattern = re.compile(r"\|\s*Task ID\s*\|\s*State\s*\|", re.IGNORECASE)
            for idx, line in enumerate(lines):
                if header_pattern.search(line):
                    # Table separator is usually next line, append after separator
                    if idx + 1 < len(lines) and lines[idx+1].strip().startswith("|-"):
                        last_row_idx = idx + 1
                    else:
                        last_row_idx = idx
                    break
        
        if last_row_idx == -1:
            raise ValueError("Could not locate central registry task table in AGENT_STATE.md.")
        
        # Build the new compliant 4-column row (using relative paths!)
        new_row = f"| **TASK-{task_num}** | `IN_PROGRESS` | [task/task_{task_num}_{slug}](./TASKS/task_{task_num}.md) | {date_str} |\n"
        
        # Construct new content
        new_lines = lines[:last_row_idx + 1] + [new_row] + lines[last_row_idx + 1:]
        
        with open(temp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        # Atomic replace
        os.replace(temp_path, agent_state_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise RuntimeError(f"Atomic update of AGENT_STATE.md failed: {e}")

def is_monitor_running(target_path):
    """Probe if a local agent-cowork monitor is running for this workspace."""
    # Method 1: Check process list
    try:
        import subprocess
        # ps ax -ww
        result = subprocess.run(["ps", "ax", "-ww"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            target_real = os.path.realpath(target_path)
            for line in result.stdout.splitlines():
                if "monitor.py" in line and (target_real in line or "python" in line):
                    return True
    except Exception:
        pass
        
    # Method 2: Probe local port 47821
    try:
        import urllib.request
        # If the monitor or colleague dashboard server is running on 47821, it will respond
        with urllib.request.urlopen("http://localhost:47821/", timeout=0.5) as response:
            if response.status == 200:
                return True
    except Exception:
        pass
        
    return False

def render_task_markdown(task_num, title, date_str, slug, objective, files_affected, non_goals, conversation_id, message_index):
    """Generate the markdown content for task_NNN.md matching agent-cowork template."""
    # Ensure relative paths only!
    return f"""# TASK-{task_num}: {title}

- **Status**: `IN_PROGRESS`
- **Created**: {date_str}
- **Created By**: Karpathy Council via MAW
- **Assigned To**: Antigravity (Layer 3)
- **Reviewer**: Grok Build (Layer 2)
- **Priority**: `MEDIUM`
- **Branch**: `task/task_{task_num}_{slug}`
- **PR URL**: `N/A (exported via MAW)`
- **Linked Council**: [council_{task_num}.md](../PLANNING/council_{task_num}.md) (Karpathy ID: {conversation_id}, Index: {message_index})
- **Planning Source**: `Karpathy Stage 3 Chairman Synthesis`
- **Linked Review**: `N/A`

---

## 1. Objective

{objective}

---

## 2. Files Affected

{files_affected}

---

## 3. Acceptance Criteria

- [ ] Implement the council-approved objective.
- [ ] Run the target project's relevant verification command.
- [ ] Preserve the task status workflow: move both `TASKS/task_{task_num}.md` and `AGENT_STATE.md` to `UNDER_REVIEW`, not `COMPLETED`.
- [ ] Keep `PLANNING/council_{task_num}.md` available as review context for Grok Build.

---

## 4. Non-Goals / Special Instructions

{non_goals}

---

**Antigravity 執行指示**：完成實作後，請將 `TASKS/task_{task_num}.md` 與 `AGENT_STATE.md` 的任務狀態改為 `UNDER_REVIEW`，而非 `COMPLETED`。
"""

def render_council_markdown(task_num, title, date_str, user_request, models_list, stage1_text, stage2_text, aggregate_rankings_text, stage3_text, export_options_text):
    """Generate the readable meeting transcript markdown."""
    return f"""# Council Meeting Record: TASK-{task_num}

- **Council ID**: council_{task_num}
- **Conversation Title**: {title}
- **Export Date**: {date_str}
- **Target Project Key**: {export_options_text.get('targetKey', '')}
- **Linked Task**: [TASK-{task_num}](../TASKS/task_{task_num}.md)

---

## 1. Original User Request

{user_request}

---

## 2. Council Configurations

- **Participating Models**:
{chr(10).join([f"  - {m}" for m in models_list.get('council', [])])}
- **Chairman Model**: {models_list.get('chairman', 'Unknown')}

---

## 3. Stage 1 - Individual Responses

{stage1_text}

---

## 4. Stage 2 - Peer Rankings & Evaluations

{stage2_text}

### Aggregate Ranking Result
{aggregate_rankings_text}

---

## 5. Stage 3 - Chairman Synthesis

{stage3_text}

---

## 6. Export Options

- **Files Affected Specification**: {export_options_text.get('filesAffected', '')}
- **Non-Goals / Special Instructions**: {export_options_text.get('nonGoals', '')}
"""

def export_to_target(target_key, conversation_id, message_index, title, priority, files_affected, non_goals):
    """
    Export council plan to target repo running atomic write sequence.
    Returns details on success, throws exceptions on failure.
    """
    # 1. Load and validate target project
    targets = load_targets()
    projects = targets.get("projects", {})
    if target_key not in projects:
        raise ValueError(f"Unknown target key: '{target_key}'. Please add it to targets.json.")
        
    target_info = projects[target_key]
    project_root = get_project_root(target_info.get("path", ""))
    if not project_root:
        raise ValueError(f"Target path not defined for project: '{target_key}'.")

    workflow_root = get_workflow_root(project_root)

    valid, issues = validate_target(project_root)
    if not valid:
        raise ValueError(f"Invalid target repo setup at '{project_root}': {', '.join(issues)}")

    # 2. Acquire lock
    lock_acquired, lock_err = acquire_export_lock(workflow_root, target_key)
    if not lock_acquired:
        # HTTP 409 Conflict
        raise PermissionError(lock_err)
        
    task_tmp_path = council_json_tmp_path = council_md_tmp_path = None
    try:
        # 3. Resolve next Task number
        agent_state_path = os.path.join(workflow_root, "AGENT_STATE.md")
        task_num = allocate_task_num(agent_state_path)
        task_id = f"TASK-{task_num}"

        # Ensure paths are safe
        tasks_dir = os.path.join(workflow_root, "TASKS")
        planning_dir = os.path.join(workflow_root, "PLANNING")
        os.makedirs(tasks_dir, exist_ok=True)
        os.makedirs(planning_dir, exist_ok=True)
        
        task_file_path = os.path.join(tasks_dir, f"task_{task_num}.md")
        # Fail if files already exist
        if os.path.exists(task_file_path):
            raise FileExistsError(f"Task file already exists at {task_file_path}. Aborting to prevent overwrite.")
            
        # 4. Read conversation from embedded MAW council storage
        conversations_dir = get_conversations_dir()
        conv_file_path = os.path.join(conversations_dir, f"{conversation_id}.json")
        if not os.path.exists(conv_file_path):
            raise FileNotFoundError(f"Conversation {conversation_id} JSON file not found in {conversations_dir}.")
            
        with open(conv_file_path, "r", encoding="utf-8") as f:
            conv_data = json.load(f)
            
        messages = conv_data.get("messages", [])
        if message_index >= len(messages):
            raise IndexError(f"Message index {message_index} out of bounds.")
            
        assistant_msg = messages[message_index]
        if assistant_msg.get("role") != "assistant":
            raise ValueError(f"Message at index {message_index} is not an assistant message.")
            
        # Extract core fields
        stage1 = assistant_msg.get("stage1")
        stage2 = assistant_msg.get("stage2")
        stage3 = assistant_msg.get("stage3")
        metadata = assistant_msg.get("metadata")
        
        if not stage1 or not stage2 or not stage3:
            raise ValueError("Target assistant message is missing Stage 1, Stage 2, or Stage 3 council results.")
            
        # Get original user request (the user message immediately preceding the assistant message)
        user_request = "No original user request found."
        for i in range(message_index - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_request = messages[i].get("content", "")
                break
                
        # Resolve config models from backend/config.py fallback or conversation metadata
        # Create models dictionary
        models_dict = {
            "council": [item.get("model") for item in stage1 if item.get("model")],
            "chairman": stage3.get("model", "Unknown")
        }
        
        # 5. Format Slug
        slug = slugify_title(title)
        date_str = datetime.now().strftime("%Y-%m-%d")
        
        # 6. Prepare Temp Files
        task_tmp_path = os.path.join(tasks_dir, f"task_{task_num}.tmp")
        council_json_tmp_path = os.path.join(planning_dir, f"council_{task_num}.json.tmp")
        council_md_tmp_path = os.path.join(planning_dir, f"council_{task_num}.md.tmp")
        
        # Construct task markdown (Ensure no local absolute paths in Git tracked file)
        task_markdown = render_task_markdown(
            task_num=task_num,
            title=title,
            date_str=date_str,
            slug=slug,
            objective=stage3.get("response", ""),
            files_affected=files_affected,
            non_goals=non_goals,
            conversation_id=conversation_id,
            message_index=message_index
        )
        
        # Construct provenance files
        stage1_text = ""
        for item in stage1:
            stage1_text += f"### Model: {item.get('model')}\n\n{item.get('response')}\n\n---\n\n"
            
        stage2_text = ""
        for item in stage2:
            stage2_text += f"### Model: {item.get('model')}\n\n{item.get('ranking')}\n\n---\n\n"
            
        agg_rankings = metadata.get("aggregate_rankings", []) if metadata else []
        agg_rankings_text = ""
        for r in agg_rankings:
            agg_rankings_text += f"- **{r.get('model')}**: Avg Rank: {r.get('average_rank')} (Rankings count: {r.get('rankings_count')})\n"
            
        export_opts = {
            "targetKey": target_key,
            "title": title,
            "priority": priority,
            "filesAffected": files_affected,
            "nonGoals": non_goals
        }
        
        council_markdown = render_council_markdown(
            task_num=task_num,
            title=conv_data.get("title", "Council Conversation"),
            date_str=date_str,
            user_request=user_request,
            models_list=models_dict,
            stage1_text=stage1_text,
            stage2_text=stage2_text,
            aggregate_rankings_text=agg_rankings_text,
            stage3_text=stage3.get("response", ""),
            export_options_text=export_opts
        )
        
        council_json = {
            "schemaVersion": 1,
            "councilId": f"council_{task_num}",
            "conversationId": conversation_id,
            "messageIndex": message_index,
            "conversationTitle": conv_data.get("title", "Council Conversation"),
            "userRequest": user_request,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "targetProject": target_key,
            "taskNum": task_num,
            "models": models_dict,
            "stage1": stage1,
            "stage2": stage2,
            "stage2Metadata": metadata,
            "stage3": stage3,
            "exportOptions": export_opts,
            "provenance": {
                "source": "Karpathy LLM Council",
                "stage": "Stage 3 Chairman Synthesis",
                "exporter": "MAW Council Export Adapter"
            }
        }
        
        # Write tmp files
        with open(task_tmp_path, "w", encoding="utf-8") as f:
            f.write(task_markdown)
            
        with open(council_json_tmp_path, "w", encoding="utf-8") as f:
            json.dump(council_json, f, indent=2)
            
        with open(council_md_tmp_path, "w", encoding="utf-8") as f:
            f.write(council_markdown)
            
        # 7. Atomic Write Sequence starts
        # A. Update AGENT_STATE.md atomically first
        append_registry_row_atomic(agent_state_path, task_num, slug, date_str)
        
        # B. Rename provenance files
        final_json_path = os.path.join(planning_dir, f"council_{task_num}.json")
        final_md_path = os.path.join(planning_dir, f"council_{task_num}.md")
        os.replace(council_json_tmp_path, final_json_path)
        os.replace(council_md_tmp_path, final_md_path)
        
        # C. Finally rename the task file to trigger the monitor (Trigger C)
        os.replace(task_tmp_path, task_file_path)
        
        # 8. Check monitor state
        monitor_active = is_monitor_running(project_root)
        dispatch_status = "dispatched_via_monitor" if monitor_active else "exported_not_dispatched"

        escaped_path = project_root.replace("'", "'\\''")
        manual_cmd = (
            f"cd '{escaped_path}' && "
            f"python3 monitor.py --project-root '{escaped_path}' "
            f"--dispatch-test '{{\"target\":\"antigravity\",\"task_num\":\"{task_num}\",\"trigger\":\"task_status\",\"state\":\"IN_PROGRESS\"}}'"
        )

        return {
            "taskId": task_id,
            "taskNum": task_num,
            "targetKey": target_key,
            "targetName": target_info.get("name"),
            "targetPath": project_root,
            "workflowPath": workflow_root,
            "taskPath": task_file_path,
            "councilJsonPath": final_json_path,
            "councilMarkdownPath": final_md_path,
            "monitorActive": monitor_active,
            "dispatchStatus": dispatch_status,
            "manualDispatchCommand": manual_cmd
        }
        
    except Exception as e:
        # Cleanup tmp files if something went wrong
        for fpath in [task_tmp_path, council_json_tmp_path, council_md_tmp_path]:
            if fpath and os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass
        raise e
        
    finally:
        # 9. Release lock
        release_export_lock(workflow_root)
