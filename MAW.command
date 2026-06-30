#!/bin/bash
cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

if [ -n "$TARGET_PROJECT_PATH" ]; then
    uv run python -m v2.app status --target "$TARGET_PROJECT_PATH" 2>/dev/null
    echo ""
    echo "MAW v2 — Next commands:"
    echo "  uv run python -m v2.app create --target \"$TARGET_PROJECT_PATH\" --request \"...\""
    echo "  uv run python -m v2.app watch  --target \"$TARGET_PROJECT_PATH\""
    echo "  uv run python -m v2.app decide --target \"$TARGET_PROJECT_PATH\" --workflow-id workflow_001 APPROVE"
else
    uv run python -m v2.app --help
    echo ""
    echo "Quick start:"
    echo "  uv run python -m v2.app create --target /path/to/project --request \"your task\""
    echo "  uv run python -m v2.app watch  --target /path/to/project"
    echo "  uv run python -m v2.app status --target /path/to/project --workflow-id workflow_001"
fi
