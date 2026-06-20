#!/bin/bash
# Start standalone MAW Council Export Adapter server
echo "Starting MAW Council Export Adapter on http://localhost:8002..."
uv run python -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload
