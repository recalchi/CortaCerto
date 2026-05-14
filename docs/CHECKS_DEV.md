# Checks Dev

Atualizado em: 2026-05-14

## Checklist manual por versao

- [ ] Abrir o programa sem erro.
- [ ] Encerrar pelo terminal com `Ctrl+C` sem traceback.
- [ ] Criar projeto importando imagem antes do video principal.
- [ ] Carregar video principal curto.
- [ ] Adicionar imagem e b-roll na caixa de midia.
- [ ] Arrastar imagem da caixa de midia para `MIDIA`.
- [ ] Arrastar b-roll da caixa de midia para `MIDIA`.
- [ ] Inserir midia pelo botao `Inserir na timeline`.
- [ ] Confirmar que imagem/b-roll nao furam o video base.
- [ ] Usar `Ver tudo` e confirmar campo vazio para organizar clipes.
- [ ] Usar `Ctrl+=`, `Ctrl++`, `Ctrl+-` e `Ctrl+0` no zoom da timeline.
- [ ] Navegar com setas quadro a quadro.
- [ ] Navegar com `Shift+setas` de 1s em 1s.
- [ ] Usar `Home` e `End` no playhead.
- [ ] Mover overlay pela timeline.
- [ ] Aparar pontas do overlay.
- [ ] Ajustar duracao e opacidade do overlay no painel direito.
- [ ] Testar chroma key em overlay e comparar preview.
- [ ] Criar texto no playhead.
- [ ] Reproduzir e confirmar texto sincronizado com o preview.
- [ ] Editar texto, cor, fundo e tamanho no painel direito.
- [ ] Mover texto no preview.
- [ ] Aparar pontas do texto.
- [ ] Dividir texto e overlay com `B`.
- [ ] Duplicar texto e overlay com `Ctrl+D`.
- [ ] Copiar, colar e recortar texto/overlay com `Ctrl+C`, `Ctrl+V`, `Ctrl+X`.
- [ ] Desfazer/refazer edicoes com `Ctrl+Z`, `Ctrl+Y`, `Ctrl+Shift+Z`.
- [ ] Alternar selecao de itens sobrepostos por clique repetido.
- [ ] Reordenar camadas com `Frente` e `Tras`.
- [ ] Mutar audio e confirmar preview/export.
- [ ] Testar audio sem reducao de ruido.
- [ ] Testar reducao de ruido leve separada do loudnorm.
- [ ] Testar loudnorm, filtro de voz e compressao separadamente.
- [ ] Exportar trecho curto.
- [ ] Comparar preview com export.
- [ ] Verificar `%LOCALAPPDATA%\CortaCerto\logs\errors.jsonl`.

## Checklist de desenvolvimento

- [x] Rodar check focado da area alterada.
- [x] Rodar `python scripts\run_sprint_checks.py --strict-legacy`.
- [x] Avaliar testes automaticos da area alterada.
- [x] Atualizar roadmap/handoff quando houver mudanca funcional relevante.
- [x] Manter `.env` fora de leitura, log e versionamento.
- [x] Nao misturar ajuste de texto, overlay e clipe base no inspector.
- [x] Confirmar que preview e export continuam alinhados na area alterada.
