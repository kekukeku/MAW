#!/usr/bin/env node
/** Review decision router for {{AGENT_LABEL}} ({{AGENT_ID}}). */

const decision = process.env.MAW_MOCK_REVIEW_DECISION || "APPROVE";
console.log(`DECISION: ${decision}`);