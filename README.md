# ContentForge — Editor de Vídeo Automatizado

**ContentForge** é um software de produção de conteúdo para redes sociais que automatiza as etapas mais repetitivas da edição: corte de silêncios, color grade, efeitos de zoom, transições, geração de thumbnail e muito mais.

---

## 📦 Requisitos

| Componente | Versão mínima | Link |
|---|---|---|
| Python | 3.9+ (testado em 3.14) | [python.org](https://www.python.org/downloads/) |
| ffmpeg | 6.0+ | [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) ou `winget install Gyan.FFmpeg` |

> ⚠️ **ffmpeg deve estar no PATH do sistema.** O instalador verifica isso automaticamente.

---

## 🚀 Instalação rápida (Windows)

```bat
# 1. Clone ou extraia o projeto
# 2. Execute o instalador:
install.bat
```

O instalador:
1. Verifica se Python e ffmpeg estão instalados
2. Cria um ambiente virtual (`venv/`)
3. Instala as dependências Python
4. Detecta a GPU disponível (NVIDIA / AMD / Intel)

### Instalação manual

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

---

## ▶️ Como usar

### 1. Início
- Clique em **Escolher vídeo…** e selecione seu arquivo (MP4, MOV, AVI, MKV, WebM)
- Preencha o **título da thumbnail** (ou deixe em branco para usar o nome do arquivo)
- Escolha a **plataforma de destino** (YouTube, Reels, TikTok, Shorts)
- Opcionalmente, adicione uma **música de fundo** (MP3, WAV, AAC)

### 2. Configurações
Acesse a aba **Configurações** para ajustar:

| Parâmetro | Padrão | Descrição |
|---|---|---|
| Estilo de corte | Natural (900ms) | Agressivo corta mais; Leve mantém pausas |
| Limiar de silêncio | -40 dBFS | Mais negativo = detecta silêncios mais suaves |
| Padding de áudio | 150ms | Margem antes/depois de cada fala |

### 3. Cor & Áudio
Preset padrão baseado no CapCut de referência:
- **Temperatura** -10 (tom mais frio/azulado)
- **Matiz** -15
- **Saturação** +10
- **Contraste** +10, **Brilho** +10
- **Sombras** -5, **Brancos** +10, **Pretos** -5
- **Nitidez** +5
- **Redução de ruído** ativada (afftdn)
- **Volume da voz** 1.8× (compensa gravações baixas)

### 4. Processamento
A barra de progresso mostra o segmento atual e o tempo estimado restante. O botão **Cancelar** interrompe imediatamente o processo ffmpeg em execução.

### 5. Resultado
- **5 variações de thumbnail** geradas em frames diferentes — clique para selecionar a melhor
- Estatísticas de produção: duração original × final, % removido, tempo de render, encoder usado
- Botão para abrir a pasta de saída diretamente

---

## 🗂️ Estrutura de arquivos gerados

```
ContentForge_output/
├── nome_editado.mp4          ← vídeo editado (silêncio removido + efeitos)
├── nome_vertical.mp4         ← versão 9:16 (se habilitado)
├── nome_thumb_1.jpg          ← thumbnail — frame 10%
├── nome_thumb_2.jpg          ← thumbnail — frame 25%
├── nome_thumb_3.jpg          ← thumbnail — frame 40%
├── nome_thumb_4.jpg          ← thumbnail — frame 55%
└── nome_thumb_5.jpg          ← thumbnail — frame 70%
```

---

## 🎞️ Efeitos aplicados automaticamente

| Efeito | Frequência | Descrição |
|---|---|---|
| Zoom estático 1.06× | A cada 4° segmento | Aproxima levemente a cena |
| Fade de abertura | Primeiro segmento | 0.5s fade-in |
| Fade de fechamento | Último segmento | 0.6s fade-out |
| Transição suave | Em ~75% e penúltimo segmento | Fade-out/in de 0.4s |

---

## ⚡ GPU / Performance

O ContentForge detecta automaticamente a melhor forma de codificar:

| Encoder | GPU necessária | Velocidade |
|---|---|---|
| `h264_nvenc` | NVIDIA (GeForce/Quadro) | 5-10× mais rápido que CPU |
| `h264_amf` | AMD (Radeon RX) | 3-5× mais rápido |
| `h264_qsv` | Intel (UHD/Iris Xe) | 2-4× mais rápido |
| `libx264` | CPU (fallback) | Referência |

O encoder detectado é exibido na barra lateral e nos logs de processamento.

---

## 🖼️ Layout de thumbnail

```
┌─────────────────────────────────────────────────────┐
│                                    │                 │
│  [texto grande em maiúsculo]       │   [pessoa]      │
│  com gradiente colorido            │   bem visível   │
│  atrás do texto                    │   sem cobertura │
│                                    │                 │
│  [subtítulo menor]                 │                 │
└─────────────────────────────────────────────────────┘
Zona escura/blur (esquerda)    Zona brilhante (direita)
```

Temas disponíveis: `dark` (azul), `fire` (laranja/vermelho), `gold` (dourado), `purple` (roxo)

---

## 🔧 Arquitetura do projeto

```
CortaCerto/
├── main.py                  ← ponto de entrada
├── requirements.txt
├── run.bat                  ← atalho para iniciar sem ativar venv
├── install.bat              ← instalador Windows
├── src/
│   ├── config.py            ← ProcessingConfig, Platform, SilenceStyle
│   ├── pipeline.py          ← orquestra todos os passos
│   ├── ffmpeg_env.py        ← resolução de PATH + detecção de GPU
│   ├── core/
│   │   ├── analyzer.py      ← detecção de silêncio via ffmpeg silencedetect
│   │   ├── editor.py        ← corte, efeitos, áudio (tudo via ffmpeg subprocess)
│   │   ├── color_grade.py   ← preset CapCut → filtro ffmpeg -vf
│   │   └── thumbnail.py     ← geração de thumbnail (Pillow, duas camadas)
│   └── ui/
│       └── app.py           ← interface CustomTkinter
└── installer/
    ├── setup.iss            ← script Inno Setup
    └── build_installer.bat  ← compila o instalador
```

---

## ❓ Problemas comuns

### ffmpeg não encontrado
```
Instale via: winget install --id Gyan.FFmpeg
Abra um NOVO terminal após instalar.
```

### Vídeo de iPhone (HEVC/H.265)
Suportado nativamente. O ContentForge usa `-hwaccel auto` para decodificação acelerada por hardware.

### Processamento lento mesmo com GPU
1. Verifique o encoder na barra lateral — se exibir `CPU (x264)`, a GPU não foi detectada
2. Atualize os drivers da placa de vídeo
3. Confirme que ffmpeg foi compilado com suporte NVENC/AMF: `ffmpeg -encoders | findstr nvenc`

### Áudio muito alto após processamento
Reduza o **Volume da voz** na aba "Cor & Áudio" (padrão: 1.8×)

---

## 📋 Licença

Uso pessoal. ffmpeg é distribuído sob licença LGPL/GPL — veja [ffmpeg.org/legal](https://ffmpeg.org/legal.html).
