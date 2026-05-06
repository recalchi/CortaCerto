@echo off
echo ============================================================
echo  ContentForge — Instalador
echo ============================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado. Instale em https://python.org
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [AVISO] ffmpeg nao encontrado no PATH.
    echo.
    echo  Instale o ffmpeg:
    echo  1. Acesse: https://www.gyan.dev/ffmpeg/builds/
    echo     Baixe "ffmpeg-release-essentials.zip"
    echo  2. Extraia para C:\ffmpeg
    echo  3. Adicione C:\ffmpeg\bin ao PATH do sistema:
    echo     Win+R > sysdm.cpl > Avancado > Variaveis de Ambiente
    echo     Em "Path" do sistema, adicione: C:\ffmpeg\bin
    echo  4. Reinicie este terminal e execute install.bat novamente
    echo.
    pause
    exit /b 1
)

echo [1/2] Instalando dependencias Python...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo [2/2] Instalacao concluida!
echo.
echo  Para iniciar o ContentForge:
echo      python main.py
echo.
pause


