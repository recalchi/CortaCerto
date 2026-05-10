@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo ============================================================
echo  ContentForge - Build do Instalador
echo ============================================================
echo.

echo [1/6] Verificando Python...
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

echo [2/6] Verificando ffmpeg...
%PYTHON_CMD% -c "from src.ffmpeg_env import ensure_ffmpeg; print(ensure_ffmpeg())"
if errorlevel 1 (
    echo [AVISO] ffmpeg nao foi encontrado por este terminal.
    echo O instalador pode ser gerado, mas o usuario precisara instalar ffmpeg.
)

echo [3/6] Preparando ambiente virtual...
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

echo Instalando dependencias de build...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias de build.
    pause
    exit /b 1
)

echo [4/6] Convertendo icone...
python -c "from PIL import Image; img=Image.open('corta_certo_icon.png'); img.save('corta_certo_icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if errorlevel 1 (
    echo [ERRO] Falha ao converter icone.
    pause
    exit /b 1
)

echo [5/6] Preparando LICENSE.txt...
if not exist LICENSE.txt (
    echo ContentForge - Uso pessoal. > LICENSE.txt
    echo ffmpeg: https://ffmpeg.org/legal.html >> LICENSE.txt
)

echo.
echo [6/6] Gerando executavel com PyInstaller...
pyinstaller --noconfirm --onedir --windowed ^
    --name "ContentForge" ^
    --icon "corta_certo_icon.ico" ^
    --add-data "corta_certo_icon.png;." ^
    --hidden-import "PIL._tkinter_finder" ^
    --hidden-import "customtkinter" ^
    --collect-all customtkinter ^
    main.py

if errorlevel 1 (
    echo [ERRO] PyInstaller falhou.
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
    echo O executavel standalone esta disponivel em: dist\ContentForge\
)

echo.
echo Concluido!
pause
