#!/usr/bin/env bash
# PapAssist launcher for macOS / Linux. Usage: ./papassist.sh [path/to/paper.tex or folder or .zip]
set -e
cd "$(dirname "$0")"

# Find Python 3.11 or newer. macOS ships an older python3, so try the versioned names first.
PY=""
for cand in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "PapAssist needs Python 3.11 or newer, and none was found on this computer."
  echo "  macOS: install it from https://www.python.org/downloads/macos/  or run:  brew install python@3.12"
  echo "  Linux: sudo apt install python3.12 python3.12-venv   (or your distribution's equivalent)"
  echo "Then run this script again."
  exit 1
fi

# An environment made with an older Python is rebuilt.
if [ -x ".venv/bin/python" ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "Recreating the Python environment with $PY (the existing one used an older Python)..."
  rm -rf .venv
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating the Python environment with $("$PY" --version) (first run only, takes a minute)..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "[PapAssist] No ANTHROPIC_API_KEY found: running without the LLM. Put ANTHROPIC_API_KEY=... in a .env file next to this script to enable it."
fi
exec .venv/bin/python -m papassist "$@"
