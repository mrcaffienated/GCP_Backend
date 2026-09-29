#!/bin/bash
# Run the PawnPro FastAPI backend
# Usage: ./start.sh

cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"

# Activate the uv-managed venv
source .venv/bin/activate

# Start uvicorn — reloads only on src changes, ignores .venv
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --reload-exclude ".venv"
