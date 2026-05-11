# CortaCerto

Editor de vídeo profissional para criadores de conteúdo — YouTube, Instagram Reels, TikTok e Shorts.

Corte automático de silêncios, color grade CapCut, bokeh de fundo, e geração de thumbnails com segmentação de pessoa.

---

## Requisitos

| Requisito | Versão mínima |
|---|---|
| Python | 3.10+ (testado em 3.14) |
| ffmpeg | 6.0+ (instalado via winget) |
| GPU NVIDIA | Opcional — usa NVENC se disponível |

---

## Instalação

### 1. Clonar / baixar o projeto

```bat
git clone https://github.com/seu-usuario/CortaCerto.git
cd CortaCerto
```

### 2. Instalar dependências

```bat
install.bat
```

O script detecta e instala o ffmpeg automaticamente (winget), depois instala os pacotes Python.

Notas atuais do instalador:

- Detecta `python` ou `py -3`, evitando falha em máquinas onde só o Python Launcher está no PATH.
- Depois do `winget install --id Gyan.FFmpeg`, revalida o `ffmpeg` de verdade antes de declarar erro.
- Inclui caminhos comuns do WinGet/WindowsApps na sessão atual para reduzir a necessidade de reiniciar o terminal.
- Se ainda falhar, mostra instruções manuais claras sem travar a inicialização do app.
- O build do instalador usa o Python da venv explicitamente, evitando chamadas acidentais para outro `python` do sistema.

Ou manualmente:

```bat
winget install --id Gyan.FFmpeg
pip install -r requirements.txt
```

### 3. Rodar

```bat
run.bat
```

Ou direto:

```bat
python main.py
```

---

## Estrutura de arquivos

```
CortaCerto/
├── main.py                    ponto de entrada
├── run.bat                    atalho de execução
├── install.bat                instalação rápida
├── setup_ml_env.bat           instala stack ML (Python 3.11 + rembg + MediaPipe)
├── requirements.txt           dependências base
├── requirements-ml.txt        dependências ML (opcional)
│
└── src/
    ├── config.py              ProcessingConfig, Platform, presets de plataforma
    ├── pipeline.py            orquestrador principal
    ├── ffmpeg_env.py          detecção de ffmpeg + encoder GPU (NVENC/AMF/QSV)
    │
    ├── core/
    │   ├── analyzer.py        detecção de silêncios via ffmpeg silencedetect
    │   ├── editor.py          corte, color grade, bokeh, loudnorm EBU R128
    │   ├── color_grade.py     presets de cor (CapCut, Cinematic, Neutral)
    │   ├── frame_scoring.py   seleção de frames (face + nitidez + composição)
    │   ├── segmentation.py    recorte de pessoa (rembg / MediaPipe / GrabCut)
    │   ├── thumbnail.py       geração de thumbnail básica
    │   ├── thumbnail_pro.py   engine profissional (5 temas, glow, tipografia)
    │   └── process_manager.py registro centralizado de processos ffmpeg
    │
    └── ui/
        └── app.py             interface CustomTkinter (dark theme)
```

---

## Como usar

### Validar a sprint

Antes de testar manualmente, rode:

```bat
python scripts\run_sprint_checks.py
```

Esse é o comando oficial da sprint. Ele usa `unittest discover -s tests` para ignorar scripts legados da raiz que não fazem parte da suíte atual.

Para incluir a validação real de FFmpeg/startup:

```bat
python scripts\run_sprint_checks.py --include-startup
```

Para falhar quando arquivos legados conhecidos ainda estiverem na raiz:

```bat
python scripts\run_sprint_checks.py --strict-legacy
```

Essa bateria cobre compilação, testes unitários e invariantes de editor para detectar descompasso entre playback, timeline compacta, cortes removidos e mapeamento de playhead.

### Fluxo básico

1. Abrir — `run.bat` ou `python main.py`
2. Escolher vídeo — clique em "Escolher vídeo…"
3. Configurar — ajuste silêncio, plataforma, música de fundo
4. Cor & Efeitos — color grade e bokeh com preview em tempo real
5. Processar — clique em "▶ Processar Vídeo"
6. Resultado — veja as 5 thumbnails, escolha a principal, abra a pasta

### Arquivos gerados

Salvos em `CortaCerto_output/` na mesma pasta do vídeo original:

| Arquivo | Descrição |
|---|---|
| `{nome}_editado.mp4` | Vídeo com silêncios removidos + cor + bokeh |
| `{nome}_vertical.mp4` | Versão 9:16 para Reels / TikTok / Shorts |
| `{nome}_thumb_1.jpg` … `_5.jpg` | 5 thumbnails profissionais |

---

## Configurações

### Detecção de silêncios

| Modo | Pausa mínima | Quando usar |
|---|---|---|
| Agressivo | ≥ 600 ms | Vídeos bem editados, cortes precisos |
| Natural | ≥ 900 ms | Padrão — mantém pausas naturais de fala |
| Leve | ≥ 1400 ms | Remove só silêncios muito longos |

**Limiar (dBFS):** `-40` é o padrão. Use `-50` se estiver cortando demais; `-30` se cortar pouco.

### Color Grade — preset CapCut padrão

| Parâmetro | Valor | Efeito |
|---|---|---|
| Temperatura | -10 | Ligeiramente mais frio / azulado |
| Matiz | -15 | Desloca levemente para ciano |
| Saturação | +10 | Cores mais vivas |
| Contraste | +10 | Mais definição |
| Brilho | +10 | Imagem um pouco mais clara |
| Sombras | -5 | Sombras mais profundas |
| Brancos | +10 | Altas luzes mais abertas |
| Pretos | -5 | Pretos mais escuros |
| Nitidez | +5 | Leve sharpening |

Presets disponíveis: **CapCut ref**, **Cinematico**, **Neutro**. Salve seus próprios na tela Cor & Efeitos.

### Bokeh (desfoque de fundo)

Desfoca o fundo mantendo a pessoa nítida, usando detecção de face para centrar a máscara.

| Intensidade | Resultado |
|---|---|
| 0% | Desativado |
| 20–40% | Efeito sutil, cinematográfico |
| 60–100% | Desfoque forte / retrato |

### Thumbnails

5 variações automáticas com temas: **Ocean**, **Fire**, **Purple**, **Gold**, **Noir**.

Cada thumbnail usa o melhor frame do vídeo selecionado por:
- Presença e tamanho do rosto (40%)
- Nitidez (30%)
- Composição — regra dos terços (20%)
- Brilho adequado (10%)

---

## Encoder de vídeo

Detectado automaticamente na inicialização:

| Encoder | Hardware | Velocidade |
|---|---|---|
| `h264_nvenc` | NVIDIA GPU | ★★★★★ |
| `h264_amf` | AMD GPU | ★★★★☆ |
| `h264_qsv` | Intel GPU | ★★★★☆ |
| `libx264` | CPU (fallback) | ★★★☆☆ |

O encoder ativo aparece na sidebar (canto inferior esquerdo).

Observação importante sobre GPU:

- NVENC/AMF/QSV aceleram o encode de vídeo.
- Efeitos OpenCV, preview e bokeh fast continuam rodando em CPU.
- Os logs diferenciam encode por hardware de efeitos em CPU para evitar promessa falsa de GPU.

---

## Estado de estabilidade da Sprint

Base consolidada a partir do commit funcional `6613d66`, com continuação registrada em `82d0b40` e atualizações posteriores.

Principais pontos já estabilizados:

- Preview usa `PreviewEngine` com fila de requests drenada corretamente, evitando frame preto por descarte indevido.
- Primeiro frame volta a aparecer após carregar vídeo.
- Carregamento de vídeo pede primeiro um frame rápido sem efeitos, depois atualiza o preview completo.
- Resize do preview tem teste unitário para evitar regressão como `Image`/`ImageTk` quebrado.
- Reprodução no preview usa caminho rápido sem efeitos por frame para ficar assistível durante a edição.
- Playback agora é dirigido pelo frame renderizado: a imagem avança junto com a timeline, sem descartar frames atrasados.
- Playback pula frames quando necessário para acompanhar o relógio e mostra FPS efetivo/render na barra de status.
- Playback respeita a timeline editada: ao excluir clipes, a reprodução pula lacunas removidas em vez de seguir pelo tempo bruto do arquivo.
- Áudio de preview usa `ffplay` em modo áudio puro (`-vn`) e é interrompido junto com pause/seek/início/fim.
- Áudio do preview passa a iniciar depois do primeiro frame renderizado no playback, reduzindo dessincronia ao pausar e voltar pela timeline.
- Timeline permite selecionar clipe, dividir no playhead e excluir clipe; export respeita os segmentos editados.
- Conversão clique/playhead da timeline usa a área real dos tracks, evitando cortes deslocados pela coluna de rótulos.
- Timeline ganhou modo **Juntar blocos**, que mostra os clipes mantidos encostados como ripple/compact view sem perder o tempo original de export.
- Cliques perto das bordas dos clipes usam snap para acertar com mais precisão o ponto exato de transição/corte.
- Split/delete/undo param o playback e reancoram o playhead em um trecho mantido para evitar timeline parada com preview avançando.
- Testes de invariantes do editor validam que playback, timeline compacta e cursor não apontam para lacunas removidas.
- Timeline mostra ação de desfazer na barra e informa o tempo exato quando um clipe é dividido.
- Atalhos: `Espaço` play/pause, `B` divide no playhead, `Delete`/`Backspace` exclui, `Ctrl+Z` desfaz ação da timeline; campos de texto não capturam esses comandos.
- Controle de silêncio ganhou ajuste de fala mínima para evitar microclipes e cortes nervosos.
- Corte automático de silêncio inicia desligado; a timeline pode ser analisada para sugestão, mas o export só aplica corte se o usuário ativar ou editar manualmente os clipes.
- Sliders de color grade e bokeh solicitam novo frame sem bloquear a UI.
- Callback do preview agora entrega frames pela fila da UI, mantendo renderização no thread principal.
- Timeline, play loop e diagnósticos de encode/segmentação também retornam para a UI pela fila principal.
- Play, pause e seek usam o mesmo caminho de preview.
- Export sem bokeh usa caminho rápido e registra que a segmentação foi pulada.
- Export sem corte de silêncio ou com timeline manual já pronta evita análise de áudio redundante e registra o caminho usado.
- Export limpa arquivos intermediários (`_effects`, `_effects_muxed`, `_audio`) e mantém o vídeo final como `{nome}_editado.mp4`.
- Export sem edições pesadas também cria `{nome}_editado.mp4` em `CortaCerto_output/`, copiando o original sem mover ou apagar o arquivo de entrada.
- Export registra explicitamente o nome do arquivo final entregue.
- Color grade sem bokeh usa ffmpeg quando possível, sem cair no pipeline frame a frame.
- Bokeh fast mantém progresso por frame e deixa claro que o efeito roda em CPU com encode selecionado.
- Cleanup do bokeh fast fecha pipe/captura com mais previsibilidade quando há erro ou cancelamento.
- Cancelamento de export aciona o evento de cancelamento e registra `[CANCEL]`.
- `ProcessManager` mantém o registro central de subprocessos ffmpeg/ffprobe e documenta claramente o fluxo de cleanup/cancelamento.
- Timeline foi protegida contra sobreposição de labels e playhead.
- Diagnóstico de encode diferencia CPU, NVENC, AMF e QSV.
- Instalador foi reforçado para não acusar falha quando o FFmpeg foi instalado mas o shell ainda não atualizou o PATH.
- Entrada do app agora usa um bootstrap dedicado para validar FFmpeg antes da UI e mostrar instruções claras de correção.
- `run.bat` escolhe a venv disponível, mostra qual Python será usado, mantém a janela aberta quando a inicialização falha e aceita `--check-startup` para validar sem abrir a UI.
- `install.bat` e o build do instalador validam `main.py --check-startup` para detectar falhas de entrada antes de declarar sucesso.
- O build PyInstaller valida `dist\CortaCerto\CortaCerto.exe --check-startup` antes de chamar o Inno Setup.
- Artefatos gerados pelo PyInstaller (`*.spec`, `LICENSE.txt`, `dist/`, `build/`) ficam fora do versionamento.

Limitações conhecidas:

- Smoke test completo depende de `ffmpeg`, OpenCV e CustomTkinter disponíveis no ambiente local.
- Bokeh fast ainda é CPU-bound; GPU, quando detectada, acelera principalmente o encode.
- Segmentação avançada por frame com anti-flicker continua como próxima fase.

---

## Stack ML — segmentação de alta qualidade (opcional)

Por padrão o app usa **GrabCut** (OpenCV) para recortar a pessoa nas thumbnails.
Para qualidade superior instale o stack ML:

```bat
setup_ml_env.bat
```

Isso instala Python 3.11 + **rembg** (U2Net ONNX) + MediaPipe numa venv separada (`venv311/`).

Para usar com ML ativo:

```bat
venv311\Scripts\python.exe main.py
```

O backend aparece na sidebar:

- Verde **rembg** — melhor qualidade (modelo U2Net, ~170 MB download na primeira execução)
- Azul **mediapipe** — rápido, boa qualidade para talking-head
- Cinza **grabcut** — padrão sem dependências extras

---

## Áudio

Normalização **EBU R128** automática (`loudnorm I=-16 TP=-1.5 LRA=11`) — padrão do YouTube e Netflix. Garante volume consistente sem clipping em qualquer microfone.

Redução de ruído com `afftdn` (FFT noise gate -25 dBFS) ativada por padrão.

---

## Problemas comuns

**"ffmpeg não encontrado"**

```bat
winget install --id Gyan.FFmpeg
```

Feche e reabra o terminal. Se persistir, o `install.bat` faz a busca automática.

**Silêncio cortando demais / de menos**

Ajuste o limiar na tela Configurações:
- Cortando demais → use `-50 dBFS` ou modo Leve
- Cortando pouco → use `-35 dBFS` ou modo Agressivo

**Thumbnail sem a pessoa recortada corretamente**

Garanta que o rosto apareça nos primeiros 30 segundos do vídeo.
Para melhor qualidade: `setup_ml_env.bat`

**Processamento lento (sem GPU)**

Verifique na sidebar: se mostrar "CPU (x264)" em vez de "NVIDIA NVENC":
1. Atualize drivers NVIDIA
2. Reinstale ffmpeg: `winget upgrade --id Gyan.FFmpeg`

**Erro com arquivo .MOV (iPhone)**

Suportado nativamente. Se falhar converta primeiro:

```bat
ffmpeg -i entrada.MOV -c copy saida.mp4
```

---

## Roadmap

- [x] Corte de silêncios (ffmpeg silencedetect)
- [x] Encoder GPU — NVENC / AMF / QSV
- [x] Color grade CapCut + bokeh face-aware
- [x] Normalização EBU R128 (sem clipping)
- [x] Thumbnails profissionais — 5 temas
- [x] Segmentação multi-backend (GrabCut / MediaPipe / rembg)
- [x] ProcessManager — limpeza garantida de processos ffmpeg
- [ ] Segmentação por frame com optical flow anti-flickering
- [ ] Timeline drag-and-drop (Electron + React + PixiJS)
- [ ] Preview em tempo real (WebCodecs)
- [ ] Multi-track — FastAPI backend + Yjs undo/redo
