@echo off

REM Instala as bibliotecas Python necessárias
pip install moviepy pydub SpeechRecognition

REM --- INSTRUÇÕES PARA INSTALAR FFMPEG ---
REM O FFmpeg é essencial para o funcionamento deste programa.
REM Por favor, siga os passos abaixo para instalá-lo e adicioná-lo ao PATH do Windows:
REM
REM 1. Baixe o FFmpeg: Acesse https://ffmpeg.org/download.html, clique no ícone do Windows e baixe uma versão 'release full' (por exemplo, de BtbN).
REM 2. Extraia os arquivos: Descompacte o arquivo .zip para um local de fácil acesso, como C:\ffmpeg. Certifique-se de que a pasta 'bin' (ex: C:\ffmpeg\bin) esteja dentro do diretório extraído.
REM 3. Adicione ao PATH do Windows:
REM    a. Pressione Win + R, digite sysdm.cpl e Enter.
REM    b. Vá para a aba 'Avançado' e clique em 'Variáveis de Ambiente...'.
REM    c. Na seção 'Variáveis do sistema', encontre a variável 'Path' e clique em 'Editar...'.
REM    d. Clique em 'Novo' e adicione o caminho completo para a pasta 'bin' do FFmpeg (ex: C:\ffmpeg\bin).
REM    e. Clique 'OK' em todas as janelas.
REM 4. Verifique: Abra um NOVO Prompt de Comando ou PowerShell e digite 'ffmpeg -version'. Se a instalação estiver correta, você verá as informações da versão.
REM -----------------------------------------

pause


