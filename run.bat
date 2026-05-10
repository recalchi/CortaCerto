@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Prefer the ML stack venv, then the standard venv, then system Python.
if exist venv311\Scripts\python.exe (
    venv311\Scripts\python.exe main.py
) else if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe main.py
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        python main.py
    ) else (
        py -3 main.py
    )
)
