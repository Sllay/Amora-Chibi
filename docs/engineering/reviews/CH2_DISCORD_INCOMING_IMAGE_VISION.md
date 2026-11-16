# CH2 — visão pontual de imagens recebidas no Discord (DM)

## Escopo
Conectar a visão que já existia para **figurinha como expressão** à percepção de **fotos enviadas para a Amora**. Este checkpoint é separado de visão contínua de tela/ambiente (V1.1), câmeras, vídeo, chamadas, guilds, uploads Web e inferência visual automática de imagens antigas.

### Fluxo
Discord `MESSAGE_CREATE/UPDATE` → normalização factual de anexos → (se `AMORA_PLATFORM_REMOTE_PROCESSING=1` e DM) baixar **a primeira** imagem PNG/JPEG/WebP de CDN Discord, com URL e attachment ID verificados, redirect proibido, limite de bytes 4 MiB, timeout, assinatura → payload efêmero `incoming_image` do worker → roteamento de atenção Core → somente DM do `PERSON_BK` com binding verificado + PRIVACY=PERSONAL + `remote_processing_allowed` + ação RESPOND → Groq Qwen 3.8 27B (configurado em `GROQ_API_KEY`) → JSON fechado de observação `description/visible_text/uncertainties/confidence` → TurnFrame do mesmo turno em `visual_observation` (source=vision_model, **canonical=false**, persist_as_fact=false, texto visível é dado não instrução) → resposta no provider social padrão, mantendo persona V3.

- Não persistir raw base64, URL assinada nem screenshot no histórico da conversa; o Core mantém somente um resumo visual limitado ligado à mensagem. É observação do modelo visual, não verdade canônica nem uma fala enviada pelo usuário.
- Sem imagem validada/provider indisponível: frame `status=unavailable`; não fingir que viu. A mensagem ainda pode ser respondida com conteúdo textual.
- Quotas/erros/falha de visão não devem derrubar chat; rejeição de formato, >4 MiB e anexos não suportados ficam sem análise. `AMORA_DISCORD_IMAGE_VISION=0` desativa o caminho tanto no worker quanto no Core.
- Proteção e consentimento: não analisar grupo, remetente desconhecido, scopes AUTH/SECRET, mensagens não endereçadas. O modelo não substitui a decisão social do Core e não herda automaticamente acesso à memória privada de outros participantes.
- O valor `AMORA_PLATFORM_REMOTE_PROCESSING` no **worker Discord** deve estar explicitamente habilitado para permitir envio de imagens ao Groq; a chave `GROQ_API_KEY` deve existir no SecretStore do **Core**. Sem ambos a funcionalidade degrada para texto, sem inventar visão.

## Testes
- Node: CDN allowlist, ligação entre attachment/channel e URL, signature, byte limit, recusa de redirects e grupos, ausência de envio quando remote off.
- Python: validação de payload, JSON do modelo mock, erros estruturais, usuário desconhecido bloqueado, turno do owner com resumo disponível no contexto do provider social e ausência de base64 no CognitiveRequest.
- CI: `Messaging workers` + `Core tests` + `Cloud container contracts` + `Pre-deploy security`.

## Prova operacional futura
No ambiente real, confirmar versões do Core e do worker, remote processing habilitado e segredo visual configurado, então enviar uma imagem pequena PNG/JPEG/WebP na DM verificada com o texto *O que você vê nessa foto?*. Checar a descrição concreta e a sinalização de erro na falta de análise. Para testar silêncio/DM espontânea, usar gate temporal independente (não é resultado desta entrega). Não publicar nenhum token, foto privada, URL assinada ou conteúdo da memória em logs/issues.
