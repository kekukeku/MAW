#!/bin/bash
# Start MAW v2 — File-driven multi-agent workflow coordinator
cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

uv run python -m v2.app --help

if [ -n "$TARGET_PROJECT_PATH" ]; then
    echo ""
    echo "Starting watcher for target: $TARGET_PROJECT_PATH"
    exec uv run python -m v2.app watch --target "$TARGET_PROJECT_PATH"
else
    echo ""
    echo "Set TARGET_PROJECT_PATH in .env or pass --target explicitly to watch a project."
fi
