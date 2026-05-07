@echo off
setlocal enabledelayedexpansion
title ContentForge — Setup ML Environment (Python 3.11)

echo ============================================================
echo  ContentForge ML Environment Setup
echo  Requires Python 3.11 + CUDA GPU for full ML stack
echo ============================================================
echo.

:: ── 1. Check / install Python 3.11 ──────────────────────────────────────────
echo [1/5] Verificando Python 3.11...
py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 nao encontrado. Instalando via winget...
    winget install --id Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo ERRO: Falha ao instalar Python 3.11.
        echo Instale manualmente em: https://www.python.org/downloads/release/python-3119/
        pause
        exit /b 1
    )
    echo Python 3.11 instalado. Reiniciando verificacao...
    :: Refresh PATH
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts"
)

for /f "tokens=*" %%i in ('py -3.11 -c "import sys; print(sys.executable)"') do set PY311=%%i
echo Python 3.11: !PY311!
echo.

:: ── 2. Create venv ───────────────────────────────────────────────────────────
echo [2/5] Criando ambiente virtual venv311...
if exist venv311 (
    echo venv311 ja existe. Pulando criacao.
) else (
    "!PY311!" -m venv venv311
    if errorlevel 1 (
        echo ERRO: Falha ao criar venv311.
        pause
        exit /b 1
    )
    echo venv311 criado com sucesso.
)
echo.

:: ── 3. Upgrade pip ───────────────────────────────────────────────────────────
echo [3/5] Atualizando pip...
venv311\Scripts\python.exe -m pip install --upgrade pip setuptools wheel --quiet
echo.

:: ── 4. Install base requirements ─────────────────────────────────────────────
echo [4/5] Instalando dependencias base...
venv311\Scripts\pip.exe install -r requirements.txt --quiet
if errorlevel 1 (
    echo AVISO: Alguns pacotes base falharam. Continuando...
)
echo.

:: ── 5. Install ML stack ──────────────────────────────────────────────────────
echo [5/5] Instalando stack ML (MediaPipe + YOLO + rembg)...
echo       Isso pode demorar 5-10 minutos na primeira vez...
echo.

:: MediaPipe (includes protobuf, absl-py etc.)
venv311\Scripts\pip.exe install mediapipe==0.10.14
if errorlevel 1 echo AVISO: mediapipe falhou — segmentacao usara GrabCut como fallback

:: YOLOv8 (ultralytics)
venv311\Scripts\pip.exe install ultralytics
if errorlevel 1 echo AVISO: ultralytics falhou — deteccao de pessoa usara Haar cascade

:: ONNX GPU runtime
venv311\Scripts\pip.exe install onnxruntime-gpu
if errorlevel 1 (
    echo AVISO: onnxruntime-gpu falhou. Tentando versao CPU...
    venv311\Scripts\pip.exe install onnxruntime
)

:: rembg (installs ONNX models on first run)
venv311\Scripts\pip.exe install "rembg[gpu]"
if errorlevel 1 (
    echo AVISO: rembg[gpu] falhou. Tentando versao CPU...
    venv311\Scripts\pip.exe install rembg
)

echo.
echo ============================================================
echo  Setup concluido!
echo.
echo  Para usar o ContentForge com ML:
echo    venv311\Scripts\python.exe main.py
echo.
echo  Para usar sem ML (Python 3.14, GrabCut):
echo    python main.py
echo ============================================================
pause
