#!/bin/bash
cd "$(dirname "$0")"
open "http://127.0.0.1:8002" 2>/dev/null || true
uv run python -m uvicorn main:app --host 127.0.0.1 --port 8002