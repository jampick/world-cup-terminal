@echo off
REM ============================================================
REM  WORLD CUP TERMINAL  ::  retro phosphor match-cast launcher
REM
REM  First run: creates an isolated virtual environment (.venv)
REM  and installs dependencies into it. Later runs just launch.
REM  Nothing is installed into your global/system Python.
REM ============================================================
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
title WORLD CUP TERMINAL
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

REM --- 1. require a base Python to build the venv from --------
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [X] Python was not found on this machine.
    echo      Install it, then re-run this file:
    echo          winget install Python.Python.3.12
    echo      ^(or from https://python.org - tick "Add Python to PATH"^)
    echo.
    pause
    exit /b 1
)

REM --- 2. create the isolated environment on first run -------
if not exist "%VENV_PY%" (
    echo.
    echo  [*] First-time setup: creating isolated environment ^(.venv^) ...
    python -m venv .venv
    if errorlevel 1 (
        echo  [X] Could not create the virtual environment.
        pause
        exit /b 1
    )
)

REM --- 3. ensure dependencies are present inside the venv ----
"%VENV_PY%" -c "import rich, requests" >nul 2>&1
if errorlevel 1 (
    echo  [*] Installing dependencies into .venv ...
    "%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [X] Dependency install failed ^(check your internet connection^).
        pause
        exit /b 1
    )
)

REM --- 4. launch the match-cast using the venv's Python ------
"%VENV_PY%" worldcup.py %*
endlocal
