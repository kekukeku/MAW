#!/usr/bin/env node
/** Mock reviewer for {{AGENT_LABEL}} ({{AGENT_ID}}). */

const fs = require("fs");
const path = require("path");

const taskNum = (process.argv[2] || "001").padStart(3, "0");
const root = path.join(__dirname, "..");
const reviewsDir = path.join(root, "REVIEWS");

fs.mkdirSync(reviewsDir, { recursive: true });

const reviewPath = path.join(reviewsDir, `review_${taskNum}.md`);
const content = `# Review: TASK-${taskNum}

- **Reviewer**: {{AGENT_LABEL}} ({{AGENT_ID}})
- **Decision**: APPROVE
- **Summary**: Mock review passed. Implementation meets acceptance criteria.

## Findings
No blocking issues found.
`;

fs.writeFileSync(reviewPath, content, "utf-8");
console.log(`[{{AGENT_ID}}] Wrote ${reviewPath}`);