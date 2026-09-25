#!/usr/bin/env bash
# PapAssist launcher for macOS / Linux. Usage: ./papassist.sh [path/to/paper.tex or folder or .zip]
set -e
cd "$(dirname "$0")"
PY=python3
command -v "$PY" >/dev/null 2>&1 || { echo "Python 3.11+ is required (python3 not found)."; exit 1; }
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating the Python environment (first run only)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "[PapAssist] No ANTHROPIC_API_KEY found: running without the LLM. Put ANTHROPIC_API_KEY=... in a .env file next to this script to enable it."
fi
exec .venv/bin/python -m papassist "$@"
