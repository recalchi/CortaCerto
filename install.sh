#!/bin/bash

echo "Instalando dependências do Editor de Vídeo Automático..."
echo

# Verificar se Python está instalado
if ! command -v python3 &> /dev/null; then
    echo "ERRO: Python3 não encontrado!"
    echo "Por favor, instale o Python 3.7 ou superior"
    exit 1
fi

echo "Python3 encontrado!"
echo

# Verificar se pip está instalado
if ! command -v pip3 &> /dev/null; then
    echo "ERRO: pip3 não encontrado!"
    echo "Por favor, instale o pip3"
    exit 1
fi

echo "Instalando bibliotecas necessárias..."
pip3 install moviepy pydub speechrecognition

if [ $? -ne 0 ]; then
    echo "ERRO: Falha na instalação das bibliotecas!"
    echo "Tente executar: pip3 install -r requirements.txt"
    exit 1
fi

echo
echo "Instalação concluída com sucesso!"
echo
echo "Para executar o programa, use:"
echo "python3 video_editor_final.py"
echo

# Tornar o script executável
chmod +x video_editor_final.py

