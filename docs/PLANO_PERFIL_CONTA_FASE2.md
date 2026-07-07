# Plano Fase 2 - Sistema de usuario, perfil, conta e saves vinculados

## Objetivo

Criar uma camada completa de usuario para o CortaCerto sem quebrar o modo local atual.
O perfil local e a base inicial; depois ele evolui para login, permissoes, sync,
preferencias por usuario, custos/API e biblioteca de projetos vinculada.

## Escopo

- [x] Avatar e perfil local no gerenciador de projetos.
- [x] Cadastro/login local opcional por senha.
- [x] Primeiro usuario local como MASTER.
- [x] Controle local de usuarios por nivel/status.
- [x] Biblioteca de projetos vinculada ao usuario.
- [ ] Preferencias por usuario: layout, pastas padrao, chaves/API, presets e favoritos.
- [x] Saves locais com dono identificado.
- [ ] Backup/sync opcional em nuvem.
- [ ] Historico de uso por projeto e por usuario.
- [ ] Controle de custo/API por usuario.

## Arquitetura sugerida

- [x] `UserProfile`: id local, nome, email opcional, avatar, plano, nivel, status e criado_em.
- [x] `ProjectOwner`: project_path, user_id, permissao, ultimo_acesso.
- [ ] `UsageLedger`: eventos de export, transcricao, geracao IA, download stock e custo estimado.
- [ ] `CloudSyncProvider`: interface para sincronizar metadados sem acoplar ao editor.
- [x] `AuthProvider`: login local por senha hash PBKDF2.
- [ ] `AuthProvider`: OAuth ou backend proprio.

## Fases

- [x] Fase 2.1: perfil local sem login remoto.
- [x] Fase 2.2: saves vinculados ao perfil local.
- [ ] Fase 2.3: painel de KPIs por usuario.
- [x] Fase 2.4a: login local opcional.
- [x] Fase 2.4a+: master local com administracao de usuarios.
- [ ] Fase 2.4b: login remoto opcional.
- [ ] Fase 2.5: sync/backup de projetos e presets.
- [ ] Fase 2.6: controle de plano/limites premium.

## Regras de seguranca

- [ ] Nunca salvar chaves sensiveis em projeto compartilhavel.
- [ ] Separar `.ccproj` do cofre local de credenciais.
- [ ] Permitir uso 100% offline.
- [x] Guardar senha local somente como hash PBKDF2 + salt.
- [x] Permitir manter conectado neste PC sem armazenar senha pura.
- [x] Impedir que usuario comum crie/edite/remova outros usuarios.
- [x] Impedir remocao/rebaixamento do ultimo MASTER local.
- [ ] Exportar dados do usuario em JSON.
- [x] Permitir remover perfil local sem apagar projetos, salvo confirmacao explicita.

## Pronto quando

- [x] O usuario abre o app e ve seu avatar/perfil.
- [x] Usuario MASTER ve e administra usuarios locais.
- [x] Projetos aparecem filtrados pelo perfil ativo.
- [ ] Saves, presets e favoritos acompanham o perfil.
- [ ] KPIs gerais e por projeto distinguem usuarios.
- [ ] Login remoto e sync podem ser desligados sem quebrar o editor.

## Proximas partes

- [ ] Migrar preferencias atuais do `.env` para escopo por usuario quando fizer sentido.
- [ ] Separar favoritos/presets por `user_id`.
- [x] Adicionar painel base de sessoes/seguranca do usuario.
- [ ] Evoluir painel de sessoes com historico de dispositivos.
- [ ] Adicionar exportacao/importacao dos dados do usuario.
- [ ] Criar interface `CloudSyncProvider` sem ativar sync por padrao.
- [ ] Criar `UsageLedger` por usuario para OpenAI, stock assets, transcricao e export.
- [ ] Mapear `UserProfile` local para colecao NoSQL/Firebase (`users`, `sessions`, `roles`, `project_access`).
