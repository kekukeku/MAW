#!/bin/bash
# Start MAW Autonomous Workflow Engine
echo "Starting MAW on http://127.0.0.1:8002 ..."
uv run python -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload
