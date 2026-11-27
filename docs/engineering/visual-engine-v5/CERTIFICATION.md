# Visual Engine V5 — certificação e publicação
Estado inicial RUN 04: candidato de desenvolvimento; nenhum release autorizado.
## Evidência automatizada
- Executar workflow RUN 04 sobre SHA exato. Identidade MOC3 e Core SHA-256 pinados. Evidências: identidade, poses, overlay, vídeo, perfis e relay entre contextos reais.
- CPU p95 do JS não é FPS, GPU, bateria nem CPU do Northflank. A duração e os dados devem ser registrados. Não usar benchmark do runner para aprovar telefone.
## Teste físico único, depois dos gates verdes
1. Registrar aparelho/modelo/iOS, versão Safari/PRISM/Streamlabs, perfil, rede e bateria. Baseline 5 min de jogo sem avatar.
2. Companion: parear, preparar vídeo do modelo real, abrir PiP com gesto, sair Safari, jogar 5 min, verificar movimento contínuo, áudio, volta, rotação, suspensão e fechamento. Se apenas preview ou congelamento, FAIL/BLOCKED.
3. Live: fonte Web isolada, abrir setup=1 no próprio contexto e parear, depois remover setup; URL não tem segredo. Confirmar imagem do espectador com jogo em primeiro plano e fundo transparente; gravar trecho curto. Se não houver interação ou edição de cenas, BLOCKED; não confundir preview com transmissão.
4. Repetir janela de 5 min no perfil eco/balanced; FPS, consumo de bateria, estabilidade, latência áudio/boca e perda de rede. Alto somente se aparelho comportar. Não repetir a bateria completa por ajuste cosmético.
5. Core: medir baseline vs conectado, mesmas cargas e tempo, RSS/CPU/rede p95/pico; metas +15 MB, +0,005 vCPU idle/+0,02 ativo. Não incluir renderizador servidor. Testar rollback desativando mount e voltar V4.5.
## Decisão de licenciamento
Live2D SDK: desenvolvimento gratuito sujeito aos termos; indivíduos e pequenas empresas são normalmente isentos de publicação, exceto Expandable Applications, que exigem revisão/contrato. AI/chatbot possui classificação específica. Uso pessoal/fixo de um modelo não implica automaticamente aprovação de distribuição; confirmar classificação e termos do Core antes de release, sem assumir custo ou aceitar contrato pelo proprietário. https://www.live2d.com/en/sdk/license/ ; https://www.live2d.com/en/sdk/license/expandable/ ; https://help.live2d.com/en/other/other_33/ ; https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/
PRISM ScreenCast scene edit documenta iPhone 12+; web source depende de aparelho e app. https://guide.prismlive.com/mobile/guides/features/screencast/using-screencast . Streamlabs iOS não oferece mesma flexibilidade Android; não presumir compatibilidade. https://support.streamlabs.com/hc/en-us/articles/40763302927387-How-to-Stream-Mobile-Games-IRL
## Publicação e rollback
Nenhum Core Cubism no Git, evidência não contém vendor. CI mount local /avatar/v5/ condicionado a AMORA_VISUAL_V5_STAGING_ROOT; não configurar em produção até release autorizado e bundle verificado. V4.5 /visual-engine/ e /avatar/ preservados. Para release criar PR separado com artefato autorizado, manifest hash, cache, paths finais, opt-in e checks; não promover #370. Após merge verificar URL Northflank e SHA servido. Rollback: desabilitar staging mount e reverter imagem aprovada; não migrar DB.
## Pendências
- Aceite físico do iPhone (incl. iPhone XR), PRISM/Streamlabs, bateria, Northflank; acesso ao aparelho não disponível na CI.
- Discord worker atual não apresenta player de voz; não simular áudio de Discord. Integrar envelope no produtor real quando existir; testes de Web não certificam Discord.
- Fonte Web sem interação pode exigir configuração nativa / pareamento alternativo; não colocar token em URL.
- Avaliar cliente AVKit nativo se Safari PiP suspender JS; Android overlay separadamente, sem aumentar processos do Core.

## Private development staging (candidate on main)
- Opt-in `/avatar/v5/` requires `AMORA_VISUAL_V5_STAGING_ROOT` pointing to an authorized built bundle and `AMORA_VISUAL_V5_PREVIEW_PASSWORD` (random, separate, >=32 bytes), HTTP Basic user `preview` over HTTPS. All static dependencies are gated; no credential in URL. CI tests denial for HTML, model identity, SDK and both surfaces. Core pairing is still independent. Do not reuse Core master, do not publish password or SDK in public Git/artifacts. If PRISM cannot authenticate a private browser source, mark blocked, do not make it public to bypass.
- Current deployment tool access does not include Northflank writes. Existing NF_BACKUP_API_TOKEN is for read-only backups. Need owner-authorized deploy access or dashboard configuration of an isolated dev service plus authorized SDK bundle. No change to existing production service.

## Build opt-in no Northflank (sem novo serviço obrigatório)
O Dockerfile padrão mantém AMORA_VISUAL_V5_BUILD=0: nenhum download de SDK/modelo e nenhum Node no runtime. Para um build de desenvolvimento privado autorizado, configurar no serviço Core:
- Build argument `AMORA_VISUAL_V5_BUILD=1`. O proprietário deve aceitar os termos do SDK e fornecer o Core JS exato da distribuição oficial em Docker secret mount `amora_visual_v5_core` (SHA-256 8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47). Não usar build ARG para conteúdo/credencial.
- Docker secret mount `amora_visual_v5_github`: token GitHub restrito a Actions read do repo, para baixar o ZIP fixado 14986551731 (expira em 02/12/2026 02:41 UTC; após expiração, regenerar/verificar o mesmo rig e atualizar lock conscientemente). Token nunca entra na imagem/URL/log.
- Runtime env `AMORA_VISUAL_V5_STAGING_ROOT=/opt/amora/visual-v5` e `AMORA_VISUAL_V5_PREVIEW_PASSWORD=${fn.randomSecret(32)}` (gerar no Northflank e manter privada). Não habilitar antes de o build privado concluir. O usuário Basic é `preview`. Pareamento Core é separado.
- Acessos após deploy e verificação: `/avatar/v5/`, `/avatar/v5/companion/`, `/avatar/v5/live-overlay/?setup=1`. Não afirmar disponibilidade antes do smoke HTTPS/identity.json. `NF_GIT_SHA` do build identifica a versão. Remover a variável STAGING_ROOT e voltar BUILD=0 para desabilitar.
O script compartilhado com CI verifica SHA-256 do ZIP, SDK, commit do framework, limites de extração e gera manifest de todos os arquivos. Não disponibilizar a imagem habilitada em registry público. O merge do código sem SDK e o release do SDK são eventos separados. Links: https://www.live2d.com/en/sdk/download/web/ ; https://www.live2d.com/en/sdk/license/ ; https://northflank.com/docs/v1/application/secure/inject-secrets ; https://www.northflank.ai/docs/v1/application/secure/upload-secret-files .
