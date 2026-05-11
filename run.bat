@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title CortaCerto
echo Iniciando CortaCerto...
echo.

REM Prefer the ML stack venv, then the standard venv, then system Python.
set "PYTHON_CMD="
if exist venv311\Scripts\python.exe (
    set "PYTHON_CMD=venv311\Scripts\python.exe"
) else if exist venv\Scripts\python.exe (
    set "PYTHON_CMD=venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
    ) else (
        where py >nul 2>&1
        if not errorlevel 1 set "PYTHON_CMD=py -3"
    )
)

if not defined PYTHON_CMD (
    echo [ERRO] Python nao encontrado.
    echo Instale em: https://python.org e marque "Add to PATH".
    echo.
    pause
    exit /b 1
)

echo Python: %PYTHON_CMD%
%PYTHON_CMD% main.py %*
set "APP_EXIT=%errorlevel%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo [ERRO] CortaCerto encerrou com codigo %APP_EXIT%.
    echo Veja a mensagem acima para corrigir o problema.
    echo.
    pause
)

exit /b %APP_EXIT%
