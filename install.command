#!/bin/bash
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "請先安裝 uv: https://docs.astral.sh/uv/"; exit 1; }
chmod +x MAW.command install.command 2>/dev/null || true
uv sync
[ -f .env ] || cp .env.example .env
mkdir -p ~/.agent-cowork
exec ./MAW.command