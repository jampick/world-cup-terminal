#!/usr/bin/env bash
# ============================================================
#  WORLD CUP TERMINAL  ::  retro phosphor match-cast launcher
#  (macOS / Linux — the Windows equivalent is run-worldcup.bat)
#
#  First run: creates an isolated virtual environment (.venv)
#  and installs dependencies into it. Later runs just launch.
#  Nothing is installed into your global/system Python.
# ============================================================
set -e
cd "$(dirname "$0")"

# --- 1. require a base Python 3 to build the venv from ------
if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo " [X] python3 was not found on this machine."
    echo "     Install Python 3.9+ from https://python.org"
    echo "     (macOS: 'brew install python' · Debian/Ubuntu: 'sudo apt install python3-venv')"
    echo
    exit 1
fi

VENV_PY=".venv/bin/python"

# --- 2. create the isolated environment on first run -------
if [ ! -x "$VENV_PY" ]; then
    echo
    echo " [*] First-time setup: creating isolated environment (.venv) ..."
    python3 -m venv .venv
fi

# --- 3. ensure dependencies are present inside the venv ----
if ! "$VENV_PY" -c "import rich, requests" >/dev/null 2>&1; then
    echo " [*] Installing dependencies into .venv ..."
    "$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$VENV_PY" -m pip install -r requirements.txt
fi

# --- 4. launch the match-cast using the venv's Python ------
exec "$VENV_PY" worldcup.py "$@"
