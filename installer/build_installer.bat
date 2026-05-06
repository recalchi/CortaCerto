@echo off
cd /d "%~dp0.."
echo ============================================================
echo  ContentForge — Build do Instalador
echo ============================================================
echo.

REM ── 1. Verificar dependências ─────────────────────────────────────────────
where python >nul 2>&1 || (echo [ERRO] Python nao encontrado && pause && exit /b 1)
where ffmpeg  >nul 2>&1 || (echo [AVISO] ffmpeg nao esta no PATH - adicione antes de distribuir)

REM ── 2. Ativar venv e instalar PyInstaller ──────────────────────────────────
if not exist venv\Scripts\activate.bat (
    echo Criando ambiente virtual...
    python -m venv venv
)
call venv\Scripts\activate.bat
pip install pyinstaller --quiet

REM ── 3. Converter icone PNG → ICO ──────────────────────────────────────────
echo Convertendo icone...
python -c "from PIL import Image; img=Image.open('corta_certo_icon.png'); img.save('corta_certo_icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

REM ── 4. Criar LICENSE.txt se nao existir ───────────────────────────────────
if not exist LICENSE.txt (
    echo ContentForge — Uso pessoal. > LICENSE.txt
    echo ffmpeg: https://ffmpeg.org/legal.html >> LICENSE.txt
)

REM ── 5. PyInstaller — gerar .exe standalone ────────────────────────────────
echo.
echo [1/2] Gerando executavel com PyInstaller...
pyinstaller --noconfirm --onedir --windowed ^
    --name "ContentForge" ^
    --icon "corta_certo_icon.ico" ^
    --add-data "corta_certo_icon.png;." ^
    --hidden-import "PIL._tkinter_finder" ^
    --hidden-import "customtkinter" ^
    --collect-all customtkinter ^
    main.py

if %errorlevel% neq 0 (
    echo [ERRO] PyInstaller falhou.
    pause
    exit /b 1
)

REM ── 6. Inno Setup — gerar instalador ──────────────────────────────────────
echo.
echo [2/2] Gerando instalador com Inno Setup...
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"

if exist %ISCC% (
    %ISCC% installer\setup.iss
    if %errorlevel% equ 0 (
        echo.
        echo Instalador gerado em: dist\installer\
    )
) else (
    echo [AVISO] Inno Setup nao encontrado.
    echo Baixe em: https://jrsoftware.org/isinfo.php
    echo O executavel standalone esta disponivel em: dist\ContentForge\
)

echo.
echo Concluido!
pause
