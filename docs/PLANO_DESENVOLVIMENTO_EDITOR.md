# Plano de Desenvolvimento do Editor CortaCerto

Atualizado em: 2026-05-13

Este documento serve como painel de acompanhamento do desenvolvimento do editor CortaCerto. A ideia e manter aqui o estado real do projeto, o que ja foi validado, o que ainda precisa de teste manual e qual e a proxima frente de trabalho.

## Versao candidata atual

`v0.3-dev-preview-text-image-media-errorlog`

Status: candidata tecnica com testes automaticos verdes. Ainda precisa de rodada manual completa na interface.

Validacao automatica e checklist manual ficam centralizados em [CHECKS_DEV.md](CHECKS_DEV.md).

## Objetivo do editor

Construir um editor de video desktop pratico, focado em:

- importar video, imagens e midias auxiliares;
- montar timeline com camadas separadas;
- editar texto, imagem, video e audio de forma visual;
- permitir preview fiel ao export;
- registrar erros automaticamente para virar teste e correcao;
- manter arquitetura simples para evoluir sem quebrar o projeto.

## Estado atual por area

| Area | Status | Observacao |
| --- | --- | --- |
| Arquitetura base | Em progresso estavel | Separacao entre `src/core`, `src/ui`, `src/pipeline` ja esta protegida por teste. |
| Sistema de testes | Estavel | Runner oficial e checklist manual documentados em [CHECKS_DEV.md](CHECKS_DEV.md). |
| Registro automatico de erros | Implementado | `src/core/error_log.py` registra erros em JSONL com contexto sanitizado. |
| Biblioteca de midias | Em progresso | Aceita video/imagem, drop e lista de midias. Precisa teste manual intenso de drag/drop. |
| Timeline base | Em progresso | Tem tracks `TEXTO`, `MIDIA`, `BASE`, `AUDIO`. Falta refinamento de UX. |
| Overlay de imagem/video | Implementado tecnico | Preview e export compoem overlay por cima do video base, com escala/posicao/opacidade. |
| Texto em camada | Em progresso avancado | Existe `text_track`, preview, trim/move e controles dedicados. Falta validacao manual completa. |
| Inspector direito | Em progresso | Alterna por contexto e ja separa texto, overlay visual e fala. Falta polimento de UX e teste manual. |
| Chroma key | Parcialmente implementado | Preview/export ja compoem sobre base; precisa teste manual com midia real. |
| Export | Em progresso avancado | Export entende base/overlay/texto, mas precisa smoke test manual com arquivo real. |
| Instalador | Aguardando | Deixar por ultimo, depois de estabilizar funcionalidades principais. |

## Roadmap

### Etapa 1 - Estabilizar timeline e camadas

Status: em progresso avancado.

Objetivo: garantir que a timeline funcione como editor NLE basico, com camadas independentes.

Concluido:

- Tracks separadas para `TEXTO`, `MIDIA`, `BASE` e `AUDIO`.
- Imagem/video externo entram em `overlay_track`, sem furar o video base.
- Apagar overlay nao corta o video principal.
- Mover, duplicar e ajustar overlay por `Alt+setas`.
- Copiar/colar texto e overlay por `Ctrl+C`/`Ctrl+V`.
- Recortar texto e overlay por `Ctrl+X`.
- Desfazer/refazer edicoes de timeline por `Ctrl+Z`/`Ctrl+Y` ou `Ctrl+Shift+Z`.
- Navegacao fina do playhead por setas, `Shift+setas`, `Home` e `End`.
- Dividir texto e overlay selecionados no playhead.
- Movimento de texto/overlay encaixa nas bordas dos clipes base.
- Insercao/drop de midia tambem encaixa nas bordas dos clipes base.
- Clique repetido em texto/overlay sobreposto alterna a selecao entre as camadas ativas, inclusive clicando no corpo do bloco.
- Pontas de texto e overlay podem ser aparadas.
- Zoom/pan da timeline e botao `Ver tudo` com visao larga para margens vazias.
- Zoom da timeline por teclado com `Ctrl+=`, `Ctrl++`, `Ctrl+-` e `Ctrl+0`.
- Drop interno da caixa de midia para timeline com fallback no canvas.
- Projeto pode nascer com imagem na caixa de midia sem tentar abrir a imagem como video principal.

Pendente:

- Teste manual do drag/drop real no Windows.
- Validar manualmente o fluxo de imagem antes de video base.

Criterio de pronto:

- Arrastar video/imagem da caixa de midia para qualquer ponto vazio da timeline funciona de forma previsivel.
- Imagem, texto e b-roll ficam em camadas superiores, sem alterar clipe base.
- Trim pelas pontas funciona para texto, imagem/video overlay e base sem selecionar itens errados.

### Etapa 2 - Inspector profissional para texto e imagem

Status: em progresso.

Objetivo: deixar o painel direito previsivel, separado por tipo de item, com experiencia parecida com editor visual.

Pendente:

- Validar manualmente se a troca de modo no painel acompanha selecao real da timeline/preview.
- Criar modo `Texto` com controles dedicados:
  - conteudo do texto;
  - cor da fonte;
  - tamanho;
  - posicao;
  - fundo ligado/desligado;
  - cor do fundo;
  - duracao;
  - camada.
- Criar modo `Imagem/Video overlay` com controles dedicados:
  - escala;
  - posicao X/Y;
  - opacidade, se aplicavel;
  - chroma key;
  - duracao;
  - substituir fonte;
  - camada.
- Evitar misturar controles de texto com controles de midia base.
- Garantir que clicar fora nao volte para clipe errado.

Concluido parcialmente:

- Mapa de linhas do inspector foi separado por modo testavel.
- Modo `Texto` mostra apenas conteudo/texto X/Y/tamanho/cor/fundo/acoes de texto.
- Modo `Imagem/Video overlay` mostra escala/posicao/chroma/acoes visuais, sem controles de texto.
- Modo `Clipe de fala` mostra volume/transicao e acao para criar texto no clipe.
- Botoes do inspector mudam conforme o tipo selecionado: overlay permite trocar fonte, fala permite criar texto.
- Texto e overlay ganharam comandos `Frente` e `Tras` para reorganizar a ordem da camada dentro da propria track.
- Texto selecionado ganhou campo dedicado de conteudo no inspector, separado do nome curto.
- Preview e export aceitam texto em multiplas linhas, limitado a 4 linhas para manter a composicao controlada.
- Texto e overlay visual ganharam controle de duracao no inspector direito, sem alterar clipe de fala/base.
- Overlay visual ganhou controle de opacidade no inspector, persistindo no projeto, preview, export e manifesto.

Criterio de pronto:

- Selecionar texto sempre mostra painel de texto.
- Selecionar imagem/video overlay sempre mostra painel visual.
- Selecionar base nao altera texto/overlay por acidente.
- Alteracoes aparecem imediatamente no preview.

### Etapa 3 - Preview fiel ao export

Status: em progresso.

Concluido:

- Preview separa base, overlay e texto.
- Overlay reduzido preserva video base visivel.
- Chroma key no preview compoe sobre o frame base.
- Export compoe overlays visuais em vez de substituir frame inteiro.
- Export projeta overlays pela timeline compactada/manual.
- Preview e export agora preservam a ordem de pilha de multiplos overlays ativos.
- Preview e export aplicam opacidade em overlays visuais.

Pendente:

- Smoke test com arquivos reais:
  - video base;
  - imagem PNG/JPG como overlay;
  - video b-roll como overlay;
  - texto sem fundo;
  - chroma key;
  - export curto.
- Comparar visualmente preview x arquivo exportado.

Criterio de pronto:

- O que aparece no preview deve sair igual ou muito proximo no export.
- Chroma key, escala, posicao e texto devem bater entre preview e render final.

### Etapa 4 - Biblioteca de midias e importacao

Status: em progresso.

Concluido:

- Adicionar multiplas midias.
- Aceitar imagens e videos.
- Drop na caixa de midia.
- Lista diferencia `[VID]` e `[IMG]`.
- Remover midia da biblioteca sem apagar arquivo.
- Bloqueio de remocao se a midia estiver em uso.
- Botao claro `Inserir na timeline` para criar overlay no playhead.

Pendente:

- Melhorar feedback visual durante drag/drop.
- Permitir reordenar biblioteca, se necessario.
- Avaliar suporte a audio externo no futuro.

Criterio de pronto:

- Usuario consegue adicionar, visualizar, inserir, substituir e remover midias sem duvida do que vai acontecer.

### Etapa 5 - Edicao de audio e tracks

Status: inicial.

Concluido:

- Track `AUDIO` visivel.
- Mute de audio no preview/export via `track_options`.
- Tratamento de audio separado em reducao de ruido leve, loudnorm, filtro de voz e compressao.
- Volume por clipe no pipeline.

Pendente:

- Separar melhor tracks de audio e video.
- Permitir mutar/solo por track.
- Visualizar waveform por camada quando houver audio externo.
- Possivel suporte futuro a musica/audio independente na timeline.

Criterio de pronto:

- Usuario entende onde esta o audio, consegue mutar/ajustar volume e exportar com resultado esperado.

### Etapa 6 - Autoregistro de erros e testes automaticos

Status: implementado e em expansao.

Concluido:

- Hook de erro Tkinter.
- Registro JSONL com contexto do projeto.
- Sanitizacao para evitar segredos.
- Testes dedicados para o modulo de log.
- Runner oficial inclui os testes.

Pendente:

- Criar rotina de revisao dos logs depois de testes manuais documentada em [CHECKS_DEV.md](CHECKS_DEV.md).
- Transformar erros recorrentes em testes automaticos.
- Criar opcao de abrir pasta de logs pela interface.

Criterio de pronto:

- Quando der erro na usabilidade, deve haver log suficiente para reproduzir e criar teste.

### Etapa 7 - Arquitetura e manutencao

Status: em progresso.

Concluido:

- Documento [AGENT_HANDOFF.md](AGENT_HANDOFF.md) com historico tecnico.
- Teste de arquitetura impedindo `src/core` de importar `src/ui`.
- README com resumo de arquitetura.

Pendente:

- Reduzir tamanho de `src/ui/app.py` separando componentes quando a funcionalidade estabilizar.
- Separar melhor regras de timeline de eventos Tkinter.
- Criar testes puros para operacoes de overlay/texto sem depender da UI.

Criterio de pronto:

- Uma feature nova deve ser adicionada mexendo em poucos pontos previsiveis.
- Ordem de automacoes/testes deve ficar clara no runner e em [CHECKS_DEV.md](CHECKS_DEV.md).

### Etapa 8 - Instalador e distribuicao

Status: aguardando estabilizacao funcional.

Pendente:

- Validar dependencias reais.
- Testar PyInstaller/build.
- Revisar `installer/build_installer.bat`.
- Gerar build instalavel apenas depois de uma rodada manual aprovada.

Criterio de pronto:

- Usuario instala, abre, edita e exporta sem depender do ambiente de desenvolvimento.

## Registro de decisoes

| Data | Decisao | Motivo |
| --- | --- | --- |
| 2026-05-13 | Midias externas entram como `overlay_track`. | Evitar que imagens/b-roll cortem ou substituam o video base sem intencao. |
| 2026-05-13 | Texto fica em `text_track`. | Permitir texto como camada vazada independente. |
| 2026-05-13 | Export recebe `layer=base/overlay`. | Alinhar preview com render final e preservar compatibilidade com projetos antigos. |
| 2026-05-13 | Inspector controla duracao de texto/overlay. | Ajustar tempo de itens de camada sem depender apenas do trim na timeline. |
| 2026-05-13 | Registro de erros em JSONL. | Melhorar diagnostico de usabilidade e transformar falhas em testes. |

## Proximas acoes imediatas

1. Refinar painel direito para texto e imagem com controles separados.
2. Testar manualmente drag/drop de midia no Windows usando [CHECKS_DEV.md](CHECKS_DEV.md).
3. Fazer smoke test de export com video real, imagem, texto e chroma key.
4. Revisar logs gerados durante o teste manual.
5. Atualizar este documento marcando o que passou, falhou e virou nova tarefa.
