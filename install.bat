@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title ContentForge - Instalacao
echo ============================================================
echo  ContentForge - Instalacao rapida
echo ============================================================
echo.

echo Verificando Python...
set "PYTHON_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    echo [ERRO] Python nao encontrado.
    echo Instale em: https://python.org ^(marque "Add to PATH"^)
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do echo Python: %%v

echo.
echo Verificando ffmpeg...
call :check_ffmpeg
if errorlevel 1 (
    echo [AVISO] ffmpeg nao encontrado automaticamente.
    echo Instalando via winget...
    winget install --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements

    echo.
    echo Revalidando ffmpeg apos winget...
    call :check_ffmpeg
    if errorlevel 1 (
        echo [AVISO] O winget terminou, mas este shell ainda nao enxerga o ffmpeg.
        echo Atualizando PATH local com os aliases do WinGet...
        set "PATH=%LOCALAPPDATA%\Microsoft\WindowsApps;%LOCALAPPDATA%\Microsoft\WinGet\Packages;%PATH%"
        call :check_ffmpeg
    )

    if errorlevel 1 (
        echo.
        echo [ERRO] Nao foi possivel validar o ffmpeg neste terminal.
        echo Feche esta janela e abra um novo terminal, ou instale manualmente:
        echo winget install --id Gyan.FFmpeg
        echo Ou baixe em: https://www.gyan.dev/ffmpeg/builds/
        pause
        exit /b 1
    )
)

echo.
echo [1/2] Instalando dependencias Python...
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias Python.
    pause
    exit /b 1
)

echo.
echo [2/2] Instalacao concluida!
echo.
echo Para iniciar: python main.py
echo Ou clique em: run.bat
echo Stack ML opcional: setup_ml_env.bat
echo.
pause
exit /b 0

:check_ffmpeg
%PYTHON_CMD% -c "from src.ffmpeg_env import ensure_ffmpeg; p=ensure_ffmpeg(); print('ffmpeg:', p)"
exit /b %errorlevel%
