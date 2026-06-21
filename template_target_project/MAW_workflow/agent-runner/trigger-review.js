#!/usr/bin/env node
/** Mock reviewer: writes REVIEWS/review_NNN.md */

const fs = require("fs");
const path = require("path");

const taskNum = (process.argv[2] || "001").padStart(3, "0");
const root = path.join(__dirname, "..");
const reviewsDir = path.join(root, "REVIEWS");

fs.mkdirSync(reviewsDir, { recursive: true });

const reviewPath = path.join(reviewsDir, `review_${taskNum}.md`);
const content = `# Review: TASK-${taskNum}

- **Reviewer**: Grok Build (mock)
- **Decision**: APPROVE
- **Summary**: Mock review passed. Implementation meets acceptance criteria.

## Findings
No blocking issues found.
`;

fs.writeFileSync(reviewPath, content, "utf-8");
console.log(`[mock-reviewer] Wrote ${reviewPath}`);