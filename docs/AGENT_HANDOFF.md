# CortaCerto Sprint Handoff

Atualizado em: 2026-05-13

## Contexto

Projeto: CortaCerto, editor NLE desktop em Python/Tkinter.

Prioridade atual: funcionalidade pratica do editor, estabilidade de preview/timeline/export, autoregistro de erros de usabilidade e testes automaticos. Instalador fica por ultimo, exceto quando uma mudanca funcional exige ajuste simples.

Autor dos commits:

```text
YoungDarks <recalchi02@gmail.com>
```

Nao ler, expor ou versionar `.env`. O runner tem checagem de segredos.

## Ultimo commit local confirmado

```text
79e1b1f - Add text track editing handoff
```

## Versao concreta candidata

Estado local atual: `v0.3-dev-preview-text-image-media-errorlog`.

Ainda nao foi commitado. O runner oficial esta verde; considerar esta versao concreta depois do teste manual basico abrir projeto, arrastar midia para timeline, aparar texto pelas pontas, selecionar texto no preview e registrar erros em `errors.jsonl`.

## Mudancas locais pendentes

Arquivos alterados/adicionados:

```text
docs/AGENT_HANDOFF.md
docs/CHECKS_DEV.md
docs/CONTEXTO_GERAL.md
docs/INDICE_DOCUMENTACAO.md
docs/PLANO_DESENVOLVIMENTO_EDITOR.md
README.md
scripts/run_sprint_checks.py
src/core/error_log.py
src/core/effect_renderer.py
src/core/timeline_manifest.py
src/ui/app.py
tests/test_architecture.py
tests/test_editor_consistency.py
tests/test_effect_renderer.py
tests/test_error_log.py
tests/test_preview_ui.py
tests/test_sprint_checks.py
tests/test_timeline_manifest.py
```

Principais mudancas pendentes:

- Preview renderiza a `text_track` diretamente.
- Durante playback, o preview usa o frame renderizado recebido para selecionar texto/overlay ativo, evitando desincronia visual do texto.
- Texto ativo pode ser selecionado por clique no preview.
- Arraste/edicao/delecao de texto sincroniza o overlay legado do clipe de video para manter o export atual compativel.
- Novo modulo `src/core/error_log.py` registra erros em JSONL.
- A UI instala hook de erro do Tkinter e registra falhas de abertura/salvamento/analise/export.
- Contexto do erro inclui dados resumidos do projeto/timeline, sem caminhos completos de midia e com mascaramento de chaves sensiveis.
- `CORTACERTO_ERROR_LOG_DIR` permite redirecionar logs em testes ou diagnostico.
- Runner da sprint compila e executa testes do novo registro de erros.
- Runner da sprint ganhou `--list` para mostrar a ordem exata das automacoes sem executar.
- Teste de arquitetura garante que `src/core` nao importe `src/ui`.
- README ganhou resumo "Arquitetura em 2 minutos".
- README documenta o registro automatico de erros.
- Projeto passa a aceitar imagens como midias (`jpg`, `png`, `webp`, `bmp`).
- Imagens podem ser inseridas/substituidas na timeline como clipes visuais estaticos.
- Lista de midias diferencia videos e imagens com prefixos `[VID]` e `[IMG]`.
- Status do projeto mostra contagem separada de videos e imagens.
- Botao `Adicionar midia` aceita selecao multipla de videos/imagens.
- Caixa de midias tem drop dedicado: soltar arquivos nela adiciona ao projeto sem trocar o video principal.
- Caixa de midias ganhou botao `Remover`; remove so da biblioteca, nao apaga arquivo, bloqueia video principal e midia em uso na timeline.
- Botoes da caixa de midias foram reorganizados em duas linhas para reduzir aperto no painel lateral.
- Salvamento do caminho do video principal preserva a lista de midias ja adicionadas.
- Arraste da caixa de midias para a timeline mostra marcador visual de drop com nome da midia e tempo de insercao.
- Duplo clique em imagem ou video externo na caixa de midias insere no playhead quando ja existe video principal/timeline.
- Botao `Abrir principal` continua dedicado a carregar/trocar o video principal.
- Insercao/substituicao cria `clip_type="image"` para imagens e `clip_type="media"` para videos externos.
- Clipes externos de imagem/video podem ser movidos pelo corpo do bloco na faixa VIDEO; clipes de fala nao entram nesse modo.
- Texto e midia externa selecionados podem ser deslocados com teclado: `Alt+Left/Right` para ajuste fino, `Alt+Shift+Left/Right` para 1s.
- Texto e midia externa selecionados podem ser duplicados pelo botao `Duplicar` ou `Ctrl+D`; clipes de fala ficam protegidos.
- Texto e midia externa selecionados podem ser copiados/colados com `Ctrl+C`/`Ctrl+V`; cola no playhead e preserva duracao/estilo/fonte.
- Texto e midia externa selecionados podem ser recortados com `Ctrl+X`; o item vai para o clipboard interno e e removido da timeline sem afetar a base.
- Timeline permite desfazer/refazer edicoes com `Ctrl+Z`, `Ctrl+Y`, `Ctrl+Shift+Z` e botoes `Desfazer`/`Refazer`.
- Playhead pode navegar com `Left`/`Right` quadro a quadro, `Shift+Left`/`Shift+Right` de 1s em 1s, `Home` e `End`.
- Texto e overlay selecionados podem ser divididos no playhead com `B` ou botao `Dividir`, preservando propriedades nas duas partes.
- Ao mover texto ou overlay com o mouse, inicio/fim do item encaixam nas bordas dos clipes base quando chegam perto.
- Inserir/soltar midia na timeline usa o mesmo encaixe de bordas, inclusive no marcador de drag da caixa de midia.
- Clique repetido em texto ou overlay sobreposto alterna a selecao entre as camadas ativas naquele ponto, inclusive quando o clique cai no corpo do bloco.
- Inspector mostra modo atual: texto/camada vazada, midia visual ou clipe de fala.
- Timeline usa cores diferentes para fala, video externo e imagem estatica.
- Caixa de midias permite arrastar item e soltar na timeline para inserir no ponto desejado.
- Timeline ficou mais alta, zoom vai de 0.5x a 8x e `Ver tudo` abre uma visao larga com margem vazia para facilitar drop/organizacao.
- Timeline aceita zoom por teclado: `Ctrl+=`, `Ctrl++`, `Ctrl+-` e `Ctrl+0` para `Ver tudo`.
- Timeline ganhou pan horizontal quando esta com zoom: botoes `<`/`>`, `Shift+roda` para deslocar e `Ctrl+roda` para zoom.
- Hitbox das pontas dos clipes foi ampliada para facilitar esticar/encurtar.
- Painel do clipe ganhou `Novo texto`, criando um item de texto no playhead e selecionando para edicao.
- Texto ganhou fundo configuravel: toggle para desligar o retangulo e campo hex para cor de fundo.
- Texto ganhou cor configuravel via campo hex e swatches de presets para cor/fundo; cor e fundo persistem em `clip_options`/`text_options`, aparecem no preview, export e manifesto.
- Campos hex de texto/fundo/chroma aplicam no Enter/foco fora e redesenham o preview imediatamente.
- Texto selecionado no preview usa caixa de controle no proprio texto, nao mais uma moldura do frame inteiro; clicar fora preserva a selecao.
- Timeline agora usa faixas separadas e consistentes para TEXTO overlay, VIDEO e AUDIO.
- A faixa TEXTO fica acima da faixa VIDEO para representar camada vazada/overlay.
- Handles de trim de video ficam restritos a faixa VIDEO; clicar na faixa AUDIO nao troca mais selecao de clipe.
- Blocos de texto sem fundo aparecem com preenchimento visual vazado na timeline.
- Inspector passa a alternar controles por modo: texto, midia visual, clipe de fala ou nada selecionado.
- Selecionar texto esconde controles de escala/chroma/volume; selecionar midia ou fala mostra controles visuais/audio relevantes.
- Acoes do inspector foram separadas por contexto: texto usa `Novo texto`, `Aplicar texto`, `Duplicar`; midia/fala usa `Texto no clipe`, `Aplicar transicao`, `Duplicar`.
- Texto selecionado no preview ganhou handle proprio de tamanho no canto da caixa do texto; arrastar esse handle altera `text_size_pct`.
- Escala de texto no preview ficou separada da escala de video/midia, aproximando o controle do estilo Canva.
- Midia/imagem selecionada no preview agora desenha a caixa de controle sobre a area visual real quando `scale_pct < 100`, respeitando posicao X/Y.
- Handle de escala de midia/imagem acompanha o canto real da caixa visual, evitando manipular o frame inteiro quando o item esta reduzido.
- Timeline ganhou controles simples de camada no cabecalho: `Visual`, `Texto` e `Audio mute`.
- Desligar `Visual` oculta a camada visual no preview e deixa a faixa VIDEO esmaecida na timeline.
- Desligar `Texto` oculta overlays de texto no preview e deixa a faixa TEXTO esmaecida na timeline.
- Ligar `Audio mute` interrompe o audio do preview e esmaece a faixa AUDIO; export ainda nao usa esse estado.
- Estados de camada agora persistem no projeto em `track_options` (`visual_visible`, `text_visible`, `audio_muted`) e sao restaurados ao reabrir.
- Projetos antigos sem `track_options` usam defaults seguros: visual/texto ativos e audio sem mute.
- Export agora recebe `track_options` no `ProcessingConfig`.
- No export, `Texto` desligado remove overlays de texto, `Audio mute` transforma volume dos clipes em 0%, e `Visual` desligado desativa substituicoes/transform/chroma de clipes visuais.
- Tratamento de audio foi separado: reducao de ruido leve desativada por padrao, loudnorm independente, filtro de voz e compressao leve opcionais.
- Observacao: `Visual` desligado ainda preserva o video base no export; gerar tela preta/sem video base exige um passe dedicado futuro.
- `TimelineModel` ganhou `overlay_track` para midias/imagens externas em camada separada da `video_track` principal.
- Inserir midia/imagem pela caixa de midia agora cria overlay em `overlay_track`, sem recortar/substituir o video base; apagar overlay nao deixa furo no video principal.
- Timeline desenha uma faixa `MIDIA` separada entre `TEXTO` e `BASE`.
- Metadados de clipe agora fazem round-trip de `overlay_track` junto com `video_track`, preservando compatibilidade posicional de `text_track`.
- Preview agora separa o clipe base do overlay ativo: imagem/midia em `overlay_track` e composta sobre o video base.
- Overlay reduzido/posicionado no preview preserva o video base visivel fora da caixa do item, sem preencher o restante com preto.
- Se houver overlays sobrepostos, o preview seleciona o overlay mais alto no tempo atual sem trocar o clipe base.
- Timeline finaliza o drag da caixa de midias mesmo quando o release e entregue ao canvas da timeline, tornando o drop interno mais confiavel no Windows/Tk.
- Overlays de imagem/video agora tem alcas de trim na faixa `MIDIA`; arrastar as pontas encurta/estica so aquela camada, sem cortar a base.
- Duplicar e nudges com `Alt+setas` agora funcionam tambem para overlays selecionados.
- Caixa de midia agora usa rotulos mais claros: `Inserir na timeline` cria camada superior no playhead e `Trocar overlay` substitui a fonte do overlay selecionado.
- Projeto criado com imagem importada no launcher guarda a imagem em `media_paths` sem preencher `video_path`, evitando tentativa de abrir imagem como video principal.
- `clip_options` agora marca `layer=base` ou `layer=overlay`, preservando compatibilidade com projetos antigos por posicao.
- Export passa a compor overlays visuais sobre o video base em vez de substituir o frame inteiro.
- Overlays no export sao projetados pela timeline compactada/manual; se um overlay cruza um trecho cortado, ele e dividido em intervalos de saida validos.
- Chroma key de overlay no export revela o frame base por baixo, alinhando melhor com o preview.
- Inspector direito agora usa mapa de linhas por modo: texto, overlay visual e clipe de fala nao compartilham controles indevidos.
- Modo texto mostra controles de conteudo/cor/fundo/posicao/tamanho; modo overlay mostra escala/posicao/chroma; modo fala mostra volume/transicao e criacao de texto.
- Botoes do inspector mudam conforme selecao: overlay permite trocar fonte, clipe de fala permite criar texto.
- Texto e overlay ganharam botoes `Frente`/`Tras` no inspector para reorganizar a ordem da camada dentro da propria track, com undo.
- Preview agora compoe todos os overlays ativos na ordem da track; o overlay mais alto continua sendo usado para selecao/controles.
- Texto selecionado ganhou campo dedicado de conteudo no inspector; o campo antigo fica como nome curto/label.
- Preview e export renderizam texto multiline em ate 4 linhas.
- Inspector direito ganhou controle de duracao para texto e overlay visual, mantendo clipes de fala/base sem esse ajuste acidental.
- Overlay visual ganhou `opacity_pct`: controle no inspector, persistencia em `clip_options`, preview, export e manifesto.
- Preview do chroma key em midia externa agora compoe a cor removida sobre o frame base do video, em vez de apenas indicar a mascara.
- Itens de texto agora tem alcas de trim proprias na faixa TEXTO; arrastar ponta ajusta so aquele texto e preserva undo.
- Trim de texto pode sobrepor outros textos, como em editor NLE, sem ser travado pelo texto vizinho.
- Itens de texto podem ser movidos pelo corpo do bloco na faixa TEXTO, preservando duracao e undo.
- Preview renderiza clipes de imagem com letterbox no tamanho do video principal.
- Export por `source_path` tambem aceita imagens, usando o frame estatico no trecho do clipe.
- Manifesto de timeline marca referencias de midia como `kind: video` ou `kind: image` e clips com seu `clip_type`.

## Testes ja executados nas mudancas pendentes

Resumo completo de checks, comandos e checklist manual: [CHECKS_DEV.md](CHECKS_DEV.md).

```powershell
python -m py_compile src\core\error_log.py src\ui\app.py tests\test_error_log.py scripts\run_sprint_checks.py
python -m unittest tests.test_error_log tests.test_preview_ui
python -m unittest tests.test_sprint_checks tests.test_architecture
python -m unittest tests.test_preview_ui tests.test_effect_renderer tests.test_timeline_manifest
python -m unittest tests.test_editor_consistency tests.test_preview_ui tests.test_effect_renderer tests.test_timeline_manifest tests.test_error_log
python -m unittest tests.test_editor_consistency
python -m unittest tests.test_preview_ui tests.test_editor_consistency tests.test_effect_renderer
python -m py_compile src\ui\app.py tests\test_preview_ui.py
python -m unittest tests.test_preview_ui tests.test_editor_consistency
python -m py_compile src\core\timeline_model.py src\ui\app.py src\core\effect_renderer.py src\core\timeline_manifest.py tests\test_preview_ui.py tests\test_effect_renderer.py
python -m unittest tests.test_preview_ui tests.test_effect_renderer tests.test_timeline_manifest tests.test_editor_consistency
python scripts\run_sprint_checks.py --list --strict-legacy
python scripts\run_sprint_checks.py --strict-legacy
```

Ultimos resultados:

```text
tests.test_error_log + tests.test_preview_ui: 60 testes OK
tests.test_sprint_checks + tests.test_architecture: 11 testes OK
tests.test_preview_ui: 61 testes OK
tests.test_preview_ui + tests.test_timeline_manifest: 61 testes OK
tests.test_preview_ui + tests.test_effect_renderer + tests.test_timeline_manifest: 69 testes OK
tests.test_preview_ui + tests.test_effect_renderer + tests.test_timeline_manifest: 75 testes OK
tests.test_editor_consistency + tests.test_preview_ui + tests.test_effect_renderer + tests.test_timeline_manifest + tests.test_error_log: 92 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 84 testes OK
tests.test_editor_consistency: 18 testes OK
tests.test_preview_ui + tests.test_editor_consistency + tests.test_effect_renderer: 97 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 86 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 87 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 88 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 89 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 90 testes OK
tests.test_pipeline_cleanup + tests.test_preview_ui: 87 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 91 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 93 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 93 testes OK apos trim/drop de overlay
tests.test_preview_ui + tests.test_pipeline_cleanup + tests.test_effect_renderer: 105 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 94 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 95 testes OK
tests.test_preview_ui + tests.test_effect_renderer + tests.test_editor_consistency: 111 testes OK
tests.test_preview_ui + tests.test_effect_renderer + tests.test_editor_consistency: 113 testes OK
tests.test_preview_ui + tests.test_editor_consistency: 98 testes OK
tests.test_preview_ui + tests.test_effect_renderer + tests.test_timeline_manifest + tests.test_editor_consistency: 118 testes OK
run_sprint_checks.py --strict-legacy: 164 testes OK, 1 skip
inventario: 13 arquivos, 165 casos declarados
```

Apos qualquer ajuste novo, rode:

```powershell
python scripts\run_sprint_checks.py --strict-legacy
```

Atualize [CHECKS_DEV.md](CHECKS_DEV.md) quando houver novo resultado relevante.

## Proximos passos recomendados

1. Fazer teste manual curto: abrir app, criar/abrir projeto, carregar video, adicionar imagem ao projeto, inserir imagem na timeline, mover/escala pelo preview e exportar trecho curto.
2. Criar texto, selecionar texto pelo preview, arrastar texto, aparar inicio/fim pela faixa TEXTO e salvar projeto.
3. Validar que erros reais geram `%LOCALAPPDATA%\CortaCerto\logs\errors.jsonl` ou a pasta definida por `CORTACERTO_ERROR_LOG_DIR`.
4. Commitar a candidata se o teste manual passar.
5. Criar painel especifico de propriedades de texto e imagem quando `clip_type`/`source_path` pedir controles dedicados.
6. Tornar export baseado diretamente na `text_track`, removendo o espelhamento no clipe quando o pipeline estiver preparado.
7. Transformar qualquer erro recorrente do `errors.jsonl` em teste automatico focado.

## Cuidados tecnicos

- Nao reverter mudancas locais do usuario.
- Manter compatibilidade de projetos antigos sem `text_options`.
- `text_track` e incremental; export ainda usa `clip_options` com `text_overlay`.
- A timeline compacta usa conversao entre tempo de fonte e tempo display; mexer nisso exige testes em `tests/test_preview_ui.py` e `tests/test_editor_consistency.py`.
- O log de erros nao deve registrar `.env`, API keys, tokens, senhas ou caminhos completos desnecessarios.
- Use `python scripts\run_sprint_checks.py --list` quando precisar explicar ou auditar a ordem das automacoes.
- Drag-and-drop depende de `tkinterdnd2`; se indisponivel, o app mantem fallback por botoes.
