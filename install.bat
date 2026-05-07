@echo off
setlocal enabledelayedexpansion
title ContentForge — Instalacao
echo ============================================================
echo  ContentForge — Instalacao rapida
echo ============================================================
echo.

:: ── Python ───────────────────────────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado.
    echo Instale em: https://python.org ^(marque "Add to PATH"^)
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python: %%v

:: ── ffmpeg ────────────────────────────────────────────────────────────────────
echo.
echo Verificando ffmpeg...
python -c "from src.ffmpeg_env import ensure_ffmpeg; p=ensure_ffmpeg(); print('ffmpeg:', p)" 2>nul
if %errorlevel% neq 0 (
    echo [AVISO] ffmpeg nao encontrado automaticamente.
    echo Instalando via winget...
    winget install --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo [ERRO] Instalacao automatica falhou.
        echo Instale manualmente: winget install --id Gyan.FFmpeg
        echo Ou baixe em: https://www.gyan.dev/ffmpeg/builds/
        pause & exit /b 1
    )
)

:: ── Dependencias Python ───────────────────────────────────────────────────────
echo.
echo [1/2] Instalando dependencias Python...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar dependencias.
    pause & exit /b 1
)

echo.
echo [2/2] Instalacao concluida!
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║  Para iniciar:  python main.py           ║
echo  ║  Ou clique em:  run.bat                  ║
echo  ║  Stack ML:      setup_ml_env.bat         ║
echo  ╚══════════════════════════════════════════╝
echo.
pause
