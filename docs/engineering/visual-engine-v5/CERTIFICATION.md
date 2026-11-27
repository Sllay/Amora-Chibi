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
