#!/bin/bash
# ===== Media Downloader launcher (macOS) =====
# Double-click in Finder. The macOS counterpart of run.bat.
cd "$(dirname "$0")" || exit 1

VENV_PY=".venv/bin/python"

# The system python3 on macOS is 3.9, and current yt-dlp needs 3.10+.
# Prefer uv (it fetches a modern Python); otherwise use a python3 that is new enough.
if [ ! -x "$VENV_PY" ]; then
  echo "Creating virtual environment..."
  if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
    UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
    "$UV" venv --python 3.12 .venv
  else
    for PY in python3.13 python3.12 python3.11 python3.10 python3; do
      if command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        "$PY" -m venv .venv
        break
      fi
    done
  fi
fi

if [ ! -x "$VENV_PY" ]; then
  echo
  echo "ERROR: Could not create the virtual environment."
  echo "Install Python 3.10 or newer (python.org or 'brew install python') and try again."
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "Installing/updating dependencies..."
if [ -n "$UV" ] || command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
  UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
  "$UV" pip install --python "$VENV_PY" -r requirements.txt
else
  "$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1
  "$VENV_PY" -m pip install -r requirements.txt
fi

echo
echo "Starting Media Downloader..."
"$VENV_PY" app.py
