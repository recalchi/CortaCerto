@echo off
echo Instalando dependencias do Editor de Video Automatico...
echo.

echo Verificando se o Python esta instalado...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado!
    echo Por favor, instale o Python 3.7 ou superior de https://python.org
    pause
    exit /b 1
)

echo Python encontrado!
echo.

echo Instalando bibliotecas necessarias...
pip install moviepy pydub speechrecognition

if errorlevel 1 (
    echo ERRO: Falha na instalacao das bibliotecas!
    echo Tente executar: pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo Instalacao concluida com sucesso!
echo.
echo Para executar o programa, use:
echo python video_editor_final.py
echo.
pause

