# Contexto Geral do Projeto CortaCerto

Atualizado em: 2026-05-13

## Produto

CortaCerto e um editor de video desktop em Python/Tkinter voltado para criadores de conteudo. O foco atual e evoluir a experiencia de edicao manual com timeline, camadas, preview fiel ao export e diagnostico automatico de erros.

## Arquitetura resumida

Fluxo principal:

```text
main.py -> src/bootstrap.py -> src/ui/app.py -> src/core/* -> src/pipeline.py -> ffmpeg/OpenCV
```

Responsabilidades:

- `main.py`: entrada do app e modo `--check-startup`.
- `src/bootstrap.py`: validacao inicial de dependencias.
- `src/ui/app.py`: interface, eventos, timeline, preview e integracao com o usuario.
- `src/core/`: regras e servicos reaproveitaveis, como preview, timeline, manifesto, render, logs, efeitos e processos.
- `src/pipeline.py`: export final, corte, efeitos, overlays, audio, thumbnails e saidas.
- `scripts/run_sprint_checks.py`: runner oficial de validacao da sprint.

Regra de dependencia desejada:

```text
UI chama core/pipeline.
Core nao importa UI.
Pipeline orquestra servicos, sem depender de widgets.
```

Essa regra e protegida por teste de arquitetura.

## Estado funcional atual

Implementado ou em progresso avancado:

- projetos `.ccp`;
- biblioteca de midias com videos e imagens;
- timeline com tracks `TEXTO`, `MIDIA`, `BASE` e `AUDIO`;
- `overlay_track` para imagem/video externo sem cortar video base;
- `text_track` para texto independente;
- preview com base, overlay e texto;
- export com `layer=base/overlay`, compondo overlay visual sobre a base;
- chroma key em preview/export para overlay;
- inspector contextual para texto, overlay visual e clipe de fala, com duracao separada para itens de camada e opacidade de overlay;
- logs automaticos de erro em JSONL com contexto sanitizado;
- runner de testes com checagem de compilacao, unidade, segredos e textos.

## Prioridade atual

1. Refinar o inspector direito como painel profissional para texto/imagem.
2. Testar manualmente drag/drop real no Windows.
3. Fazer smoke test de export com video real, imagem, texto e chroma key.
4. Converter erros de usabilidade em testes automaticos.
5. Deixar instalador para depois da estabilizacao funcional.

## Cuidados permanentes

- Nao ler, expor ou versionar `.env`.
- Nao reverter mudancas locais sem pedido explicito.
- Rodar o check oficial antes de considerar uma etapa concluida.
- Registrar decisoes e resultados nos documentos em `docs/`.
- Manter o plano limpo; detalhes de testes ficam em [CHECKS_DEV.md](CHECKS_DEV.md).
