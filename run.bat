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

REM If no --web / --web-dev flag is passed, default to --web (React UI via WebView2).
REM Use --web-dev only when running Vite separately with "npm run dev" in web\.
set "EXTRA_ARGS=%*"
echo %EXTRA_ARGS% | findstr /I "\-\-web" >nul 2>&1
if errorlevel 1 set "EXTRA_ARGS=--web %*"

REM Auto-build the React frontend if web\dist\index.html is missing.
REM This runs on first launch or after a git pull that added new frontend features.
if not exist "web\dist\index.html" (
    echo [CortaCerto] Construindo interface React (primeira vez)...
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [AVISO] npm nao encontrado - instale Node.js para construir a interface.
    ) else (
        pushd web
        call npm install --prefer-offline --no-audit --no-fund >nul 2>&1
        call npm run build
        popd
        if errorlevel 1 (
            echo [ERRO] Falha ao construir a interface. Verifique npm e Node.js.
            pause
            exit /b 1
        )
        echo [CortaCerto] Interface construida com sucesso.
    )
)

%PYTHON_CMD% main.py %EXTRA_ARGS%
set "APP_EXIT=%errorlevel%"

if not "%APP_EXIT%"=="0" (
    echo.
    echo [ERRO] CortaCerto encerrou com codigo %APP_EXIT%.
    echo Veja a mensagem acima para corrigir o problema.
    echo.
    pause
)

exit /b %APP_EXIT%
