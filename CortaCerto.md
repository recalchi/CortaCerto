# CortaCerto

Um programa Python com interface gráfica para edição automática de vídeos que remove pausas longas e erros de fala prolongados.

## Funcionalidades

- **Remoção automática de silêncios**: Remove pausas longas (configurável, padrão > 1 segundo)
- **Detecção de erros de fala**: Remove segmentos muito curtos que podem ser erros como "eeeeh", "aah", etc.
- **Aceleração por GPU (NVIDIA NVENC)**: Opção para usar a placa de vídeo NVIDIA para codificação de vídeo, acelerando o processo (requer FFmpeg com suporte a NVENC).
- **Interface gráfica intuitiva**: Seleção fácil de arquivos e configurações, com um **terminal minimizado para logs**, **cálculo de tempo estimado para conclusão (ETA)** e **tema escuro** para melhor visualização.
- **Botão de Cancelar**: Permite interromper o processamento a qualquer momento.
- **Preservação de fala suave**: Adiciona um pequeno "padding" (margem) nos segmentos de fala para evitar cortes abruptos em palavras faladas mais baixo.
- **Configurações ajustáveis**: Threshold de silêncio e duração mínima personalizáveis

## Requisitos do Sistema

- Python 3.x
- **FFmpeg**: Essencial para processamento de vídeo e áudio. Veja as instruções de instalação abaixo.

### Instalação do FFmpeg (Windows)

1.  **Baixe o FFmpeg**: Acesse [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html), clique no ícone do Windows e baixe uma versão `release full` (por exemplo, de `BtbN`).
2.  **Extraia os arquivos**: Descompacte o arquivo `.zip` para um local de fácil acesso, como `C:\ffmpeg`. Certifique-se de que a pasta `bin` (ex: `C:\ffmpeg\bin`) esteja dentro do diretório extraído.
3.  **Adicione ao PATH do Windows**: 
    *   Pressione `Win + R`, digite `sysdm.cpl` e Enter.
    *   Vá para `Avançado` > `Variáveis de Ambiente...`.
    *   Em `Variáveis do sistema`, selecione `Path` e clique em `Editar...`.
    *   Clique em `Novo` e adicione o caminho completo para a pasta `bin` do FFmpeg (ex: `C:\ffmpeg\bin`).
    *   Clique `OK` em todas as janelas.
4.  **Verifique**: Abra um **novo** Prompt de Comando ou PowerShell e digite `ffmpeg -version`. Se a instalação estiver correta, você verá as informações da versão.

### Instalação do FFmpeg (Linux/macOS)

- **Linux (Ubuntu/Debian)**: `sudo apt update && sudo apt install ffmpeg`
- **macOS (Homebrew)**: `brew install ffmpeg`

## Instalação

1. **Clone ou baixe os arquivos do programa**
2. **Instale as dependências**:
   ```bash
   pip install moviepy pydub tkinter speechrecognition
   ```
3. **Execute o programa**:
   ```bash
   python video_editor_final.py
   ```

## Como Usar

### Interface Gráfica

1. **Execute o programa**: `python video_editor_final.py`
2. **Selecione o arquivo de vídeo**: Clique em "Procurar" na seção "Arquivo de Vídeo"
3. **Escolha a pasta de saída**: Clique em "Procurar" na seção "Pasta de Saída" (padrão: Desktop)
4. **Configure as opções**:
   - **Threshold de Silêncio**: Ajuste a sensibilidade para detectar silêncio (-60 a -10 dB)
   - **Duração Mínima de Silêncio**: Tempo mínimo para considerar como pausa (0.5 a 5.0 segundos)
   - **Remover erros de fala**: Marque para remover segmentos muito curtos
5. **Clique em "Processar Vídeo"**
6. **Aguarde o processamento**: A barra de progresso mostrará o andamento
7. **Vídeo editado será salvo** na pasta escolhida com o sufixo "_editado"

### Linha de Comando (Teste)

Para testar o funcionamento sem interface gráfica:
```bash
python test_video_editor.py
```

## Configurações Detalhadas

### Threshold de Silêncio
- **Valor padrão**: -40 dB
- **Faixa**: -60 dB (mais sensível) a -10 dB (menos sensível)
- **Descrição**: Define o nível de áudio considerado como silêncio

### Duração Mínima de Silêncio
- **Valor padrão**: 1.0 segundo
- **Faixa**: 0.5 a 5.0 segundos
- **Descrição**: Tempo mínimo de silêncio para ser removido

### Remoção de Erros de Fala
- **Padrão**: Ativado
- **Descrição**: Remove segmentos de áudio menores que 0.3 segundos, que geralmente são erros de fala

## Formatos Suportados

### Entrada
- MP4, AVI, MOV, MKV, WMV, FLV
- Qualquer formato suportado pelo FFmpeg

### Saída
- MP4 (H.264 + AAC)
- Qualidade preservada do arquivo original

## Estrutura dos Arquivos

```
video_editor_final.py     # Programa principal com interface gráfica
test_video_editor.py      # Script de teste sem interface
create_test_video.py      # Gerador de vídeo de teste
video_teste.mp4          # Vídeo de exemplo para testes
README.md                # Esta documentação
```

## Exemplo de Uso

### Cenário Típico
1. Você tem um vídeo de 10 minutos com várias pausas longas
2. O programa detecta e remove pausas maiores que 1 segundo
3. O vídeo final fica com 7 minutos, mantendo apenas o conteúdo relevante
4. A qualidade do vídeo permanece inalterada

### Resultado Esperado
- **Redução de tempo**: 20-40% em vídeos com muitas pausas
- **Qualidade preservada**: Mesma resolução e bitrate do original
- **Áudio sincronizado**: Sem problemas de sincronização

## Solução de Problemas

### Erro: "Arquivo de vídeo não encontrado"
- Verifique se o caminho do arquivo está correto
- Certifique-se de que o arquivo não está sendo usado por outro programa

### Erro: "FFmpeg não encontrado"
- O MoviePy baixará o FFmpeg automaticamente na primeira execução
- Aguarde alguns minutos para o download completar

### Processamento muito lento
- Vídeos grandes podem demorar vários minutos
- Considere usar configurações menos sensíveis para acelerar

### Vídeo final muito curto
- Ajuste o threshold de silêncio para um valor menor (mais negativo)
- Aumente a duração mínima de silêncio
- Desative a remoção de erros de fala

## Limitações

- **Detecção de erros de fala**: Implementação simplificada baseada em duração
- **Formatos de saída**: Apenas MP4 atualmente
- **Processamento**: Pode ser lento para vídeos muito grandes (>1GB)
- **Memória**: Vídeos muito longos podem consumir muita RAM

## Melhorias Futuras

- Reconhecimento de fala avançado para melhor detecção de erros
- Suporte a mais formatos de saída
- Processamento em lotes
- Preview do resultado antes de salvar
- Configurações de qualidade de saída

## Suporte Técnico

Para problemas ou dúvidas:
1. Verifique se todas as dependências estão instaladas
2. Teste com o vídeo de exemplo fornecido
3. Execute o script de teste para verificar o funcionamento

## Licença

Este programa é fornecido como está, para uso educacional e pessoal.



### Padding de Áudio
- **Valor padrão**: 150 milissegundos
- **Descrição**: Adiciona uma pequena margem (padding) no início e no final de cada segmento de fala para evitar cortes abruptos em palavras faladas mais baixo. Ajuste este valor se ainda notar cortes indesejados ou se o vídeo final estiver muito longo devido a inclusão de silêncios curtos.

