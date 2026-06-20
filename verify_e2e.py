import os
import sys
import json
import subprocess
import time
import httpx
from datetime import datetime

PAD_PATH = "/Users/kevin/Library/CloudStorage/GoogleDrive-kevink826@gmail.com/.shortcut-targets-by-id/1CFgqc7TbJd31W0rGGhR5LAE4OjMFS4za/all/Github projects/pixel-agent-desk"

def main():
    print("Starting MAW Council Export Adapter verification...")
    
    # 1. Start uvicorn server in the background
    server_process = subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn", "main:app", "--port", "8082"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to boot up
    time.sleep(2)
    
    # Check if server started successfully
    if server_process.poll() is not None:
        print("Error: Server failed to start.", file=sys.stderr)
        stdout, stderr = server_process.communicate()
        print(f"Stdout:\n{stdout.decode()}", file=sys.stderr)
        print(f"Stderr:\n{stderr.decode()}", file=sys.stderr)
        sys.exit(1)
        
    try:
        # 2. Get targets list
        print("Querying /api/maw/targets...")
        response = httpx.get("http://localhost:8082/api/maw/targets")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        targets_data = response.json()
        print(f"Targets found: {[t['key'] for t in targets_data['projects']]}")
        
        # 3. Get conversations list
        print("Querying /api/maw/conversations...")
        response = httpx.get("http://localhost:8082/api/maw/conversations")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        convs = response.json()
        print(f"Conversations found: {[c['id'] for c in convs]}")
        
        # 4. Trigger Export task
        payload = {
            "conversationId": "mock_conv_001",
            "messageIndex": 1,
            "targetKey": "pad",
            "title": "Implement targets config structure",
            "priority": "MEDIUM",
            "filesAffected": "To be determined by executor after repository inspection",
            "nonGoals": "Do not install agent-cowork into new projects in v1."
        }
        print("Triggering /api/maw/export...")
        response = httpx.post("http://localhost:8082/api/maw/export", json=payload, timeout=10.0)
        
        if response.status_code != 200:
            print(f"Export failed with status {response.status_code}: {response.text}", file=sys.stderr)
            sys.exit(1)
            
        export_result = response.json()
        task_num = export_result["taskNum"]
        task_id = export_result["taskId"]
        print(f"Successfully exported as {task_id}!")
        
        # 5. Verify files in target project
        task_file_path = export_result["taskPath"]
        council_json_path = export_result["councilJsonPath"]
        council_md_path = export_result["councilMarkdownPath"]
        agent_state_path = os.path.join(PAD_PATH, "AGENT_STATE.md")
        
        print("Verifying task files...")
        assert os.path.exists(task_file_path), f"Task file missing: {task_file_path}"
        assert os.path.exists(council_json_path), f"Council JSON missing: {council_json_path}"
        assert os.path.exists(council_md_path), f"Council Markdown missing: {council_md_path}"
        
        # 6. Absolute paths check (Blocking Review Gate)
        print("Checking for absolute path leaks in Git tracked files...")
        with open(task_file_path, "r", encoding="utf-8") as f:
            task_content = f.read()
            assert "/Users/kevin" not in task_content, "Leaked absolute path in task markdown file!"
            assert "mock_conv_001" in task_content, "Missing council link in task!"
            
        with open(council_md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
            assert "/Users/kevin" not in md_content, "Leaked absolute path in council markdown file!"
            
        with open(agent_state_path, "r", encoding="utf-8") as f:
            state_content = f.read()
            assert f"TASK-{task_num}" in state_content, "Registry row not appended!"
            new_row_lines = [l for l in state_content.splitlines() if f"TASK-{task_num}" in l]
            assert len(new_row_lines) == 1, "Expected exactly one row for new task in AGENT_STATE.md"
            assert "/Users/kevin" not in new_row_lines[0], "Leaked absolute path in new registry row!"
            assert "file://" not in new_row_lines[0], "Leaked file:// protocol in new registry row!"
            
        print("Absolute path checks passed! All files contain relative paths only.")
        
        # 7. Clean up generated files from PAD project to keep workspace clean
        print("Cleaning up target repository changes...")
        os.remove(task_file_path)
        os.remove(council_json_path)
        os.remove(council_md_path)
        
        # Restore AGENT_STATE.md (remove the newly added row)
        with open(agent_state_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        filtered_lines = [l for l in lines if f"TASK-{task_num}" not in l]
        with open(agent_state_path, "w", encoding="utf-8") as f:
            f.writelines(filtered_lines)
            
        print("Target repository cleaned successfully.")
        print("\nAll verification checks passed successfully! E2E system is fully validated.")
        
    finally:
        # Shutdown server
        print("Shutting down MAW backend server...")
        server_process.terminate()
        server_process.wait()

if __name__ == "__main__":
    main()
