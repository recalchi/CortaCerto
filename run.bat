@echo off
cd /d "%~dp0"

:: Tenta venv311 (ML stack completo) primeiro, senão usa Python do sistema
if exist venv311\Scripts\python.exe (
    venv311\Scripts\python.exe main.py
) else if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe main.py
) else (
    python main.py
)
