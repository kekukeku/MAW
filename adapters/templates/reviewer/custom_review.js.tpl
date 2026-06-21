#!/usr/bin/env node
/** Custom reviewer wrapper — runs user command then expects review file or stdout decision. */

const { execSync } = require("child_process");
const path = require("path");

const taskNum = (process.argv[2] || "001").padStart(3, "0");
const root = path.join(__dirname, "..");
const cmd = process.env.MAW_CUSTOM_REVIEWER_CMD || "{{CUSTOM_REVIEWER_CMD}}";

if (!cmd || !cmd.trim()) {
  console.error("ERROR: No custom reviewer command configured.");
  process.exit(1);
}

const env = {
  ...process.env,
  MAW_TASK_NUM: taskNum,
  MAW_WORKFLOW_ROOT: root,
  MAW_PROJECT_ROOT: path.dirname(root),
};

console.log(`[custom-reviewer] Running: ${cmd}`);
execSync(cmd, { cwd: path.dirname(root), env, stdio: "inherit" });