#!/usr/bin/env node
/** Mock review router: emits decision to stdout for MAW orchestrator. */

const decision = process.env.MAW_MOCK_REVIEW_DECISION || "APPROVE";
console.log(`DECISION: ${decision}`);