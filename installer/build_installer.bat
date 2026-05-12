@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo ============================================================
echo  CortaCerto - Build do Instalador
echo ============================================================
echo.

echo [1/7] Verificando Python...
set "PYTHON_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    echo [ERRO] Python nao encontrado.
    pause
    exit /b 1
)

echo [2/7] Verificando entrada do app...
%PYTHON_CMD% main.py --check-startup
if errorlevel 1 (
    echo [AVISO] A validacao de entrada falhou neste terminal.
    echo O instalador pode ser gerado, mas o usuario precisara corrigir FFmpeg/Python no destino.
)

echo [3/7] Preparando ambiente virtual...
if not exist venv\Scripts\activate.bat (
    echo Criando ambiente virtual...
    %PYTHON_CMD% -m venv venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar ambiente virtual.
        pause
        exit /b 1
    )
)
call venv\Scripts\activate.bat
set "VENV_PYTHON=%CD%\venv\Scripts\python.exe"

echo Instalando dependencias de build...
"%VENV_PYTHON%" -m pip install --upgrade pip --quiet
"%VENV_PYTHON%" -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias de build.
    pause
    exit /b 1
)

echo [4/7] Convertendo icone...
"%VENV_PYTHON%" -c "from PIL import Image; img=Image.open('corta_certo_icon.png'); img.save('corta_certo_icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if errorlevel 1 (
    echo [ERRO] Falha ao converter icone.
    pause
    exit /b 1
)

echo [5/7] Preparando LICENSE.txt...
if not exist LICENSE.txt (
    echo CortaCerto - Uso pessoal. > LICENSE.txt
    echo ffmpeg: https://ffmpeg.org/legal.html >> LICENSE.txt
)

echo.
echo [6/7] Gerando executavel com PyInstaller...
"%VENV_PYTHON%" -m PyInstaller --noconfirm --onedir --windowed ^
    --name "CortaCerto" ^
    --icon "corta_certo_icon.ico" ^
    --add-data "corta_certo_icon.png;." ^
    --hidden-import "PIL._tkinter_finder" ^
    --hidden-import "customtkinter" ^
    --hidden-import "tkinterdnd2" ^
    --collect-all customtkinter ^
    --collect-all tkinterdnd2 ^
    main.py

if errorlevel 1 (
    echo [ERRO] PyInstaller falhou.
    pause
    exit /b 1
)

echo.
echo [7/7] Validando executavel gerado...
dist\CortaCerto\CortaCerto.exe --check-startup
if errorlevel 1 (
    echo [ERRO] O executavel gerado falhou na validacao de startup.
    pause
    exit /b 1
)

echo.
echo Gerando instalador com Inno Setup...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if exist "%ISCC%" (
    "%ISCC%" installer\setup.iss
    if errorlevel 1 (
        echo [ERRO] Inno Setup falhou.
        pause
        exit /b 1
    )
    echo Instalador gerado em: dist\installer\
) else (
    echo [AVISO] Inno Setup nao encontrado.
    echo Baixe em: https://jrsoftware.org/isinfo.php
    echo O executavel standalone esta disponivel em: dist\CortaCerto\
)

echo.
echo Concluido!
pause
