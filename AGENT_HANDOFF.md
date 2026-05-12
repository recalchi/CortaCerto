# CortaCerto Sprint Handoff

Atualizado em: 2026-05-12

## Contexto

Projeto: CortaCerto, editor NLE desktop em Python/Tkinter.

Prioridade atual: funcionalidade prática do editor, estabilidade de preview/timeline/export e testes automáticos. Instalador fica por último, exceto quando uma mudança funcional exige ajuste simples.

Autor dos commits:

```text
YoungDarks <recalchi02@gmail.com>
```

Não ler, expor ou versionar `.env`. O projeto tem checagem de segredos no runner.

## Último commit remoto confirmado

```text
01ff13f - Improve timeline zoom window
```

Depois desse commit existem mudanças locais testadas, ainda não commitadas por limite do ambiente Codex.

## Mudanças locais pendentes

Arquivos alterados:

```text
README.md
src/core/timeline_manifest.py
src/core/timeline_model.py
src/ui/app.py
tests/test_preview_ui.py
tests/test_timeline_manifest.py
```

Principais mudanças pendentes:

- Texto agora possui `text_track` no `TimelineModel`.
- Texto é salvo em `text_options` no projeto.
- Manifesto exporta uma track `Texto`.
- Texto ainda é espelhado no clipe de vídeo para manter compatibilidade com o export atual.
- Timeline desenha uma faixa TEXTO.
- Itens da track TEXTO podem ser selecionados independentemente.
- Inspector edita texto selecionado.
- Delete remove item de texto selecionado.
- Inserir/substituir ganhou duração configurável no painel de mídias.
- O mesmo valor de duração vale para botão e drag-and-drop na timeline.
- README foi atualizado com esses pontos.

## Testes já executados nas mudanças pendentes

```powershell
python -m py_compile src\ui\app.py src\core\timeline_model.py src\core\timeline_manifest.py tests\test_preview_ui.py tests\test_timeline_manifest.py
python -m unittest tests.test_preview_ui tests.test_timeline_manifest
python scripts\run_sprint_checks.py --strict-legacy
```

Últimos resultados:

```text
tests.test_preview_ui + tests.test_timeline_manifest: 53 testes OK
run_sprint_checks.py --strict-legacy: 110 testes OK, 1 skip
```

Após qualquer ajuste novo, rode novamente:

```powershell
python scripts\run_sprint_checks.py --strict-legacy
```

## Commit pendente

Quando o ambiente permitir:

```powershell
git add README.md src\core\timeline_manifest.py src\core\timeline_model.py src\ui\app.py tests\test_preview_ui.py tests\test_timeline_manifest.py
git commit --author="YoungDarks <recalchi02@gmail.com>" -m "Add text track editing controls"
git push origin main
```

Se o remoto rejeitar push, sincronizar sem descartar mudanças locais.

## Próximos passos recomendados

1. Commitar as mudanças pendentes.
2. Melhorar seleção independente de texto no preview, não só na timeline.
3. Criar painel específico de propriedades de texto quando `clip_type == "text"`.
4. Tornar export baseado diretamente na `text_track`, removendo o espelhamento em clipe quando o pipeline estiver preparado.
5. Refinar inserir/substituir com escolha de origem do clipe e preview claro antes de aplicar.

## Cuidados técnicos

- Não reverter mudanças locais do usuário.
- Manter compatibilidade de projetos antigos sem `text_options`.
- `text_track` é incremental; export ainda usa `clip_options` com `text_overlay`.
- A timeline compacta usa conversão entre tempo de fonte e tempo display; mexer nisso exige testes em `tests/test_preview_ui.py` e `tests/test_editor_consistency.py`.
- Drag-and-drop depende de `tkinterdnd2`; já foi adicionado em `requirements.txt` e no build PyInstaller.

