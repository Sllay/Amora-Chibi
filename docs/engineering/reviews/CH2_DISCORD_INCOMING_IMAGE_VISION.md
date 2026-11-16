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

## CH2.1 — Reparo de JSON visual e fallback multimodal tipado

O operador constatou no Core real (`2026-11-16T23:00:55Z`) `INCOMING_VISION_INVALID_RESULT` após o reconhecimento da imagem pelo worker, seguido de `INCOMING_IMAGE_NOT_ANALYZED` no contexto do diálogo. Não é erro de consentimento `AMORA_PLATFORM_REMOTE_PROCESSING=true`, nem erro do Numes (que concluiu o diálogo). A causa exata do envelope anterior não pôde ser inferida porque o parser unificava truncamento, vazio, JSON e schema.

Correção primária:
- Groq/Qwen 3.8 27B tem `reasoning_effort=none` oficialmente disponível: reservar os tokens para JSON visual, elevando `max_completion_tokens` de 700 para 1400. Não relaxar validação local.
- Classificar separadamente `INCOMING_VISION_TRUNCATED`, `INCOMING_VISION_EMPTY_RESPONSE`, `INCOMING_VISION_JSON_INVALID`, `INCOMING_VISION_SCHEMA_INVALID`, `INCOMING_VISION_ENVELOPE_INVALID` e `INCOMING_VISION_FINISH_UNEXPECTED`. Nunca logar JSON, OCR, imagem, URL ou token.
- O resultado validado segue efêmero e não canônico, sem alterar a persona nem o provider de fala.

Fallback visual (independente do fallback de diálogo):
- `AMORA_VISION_NVIDIA_FALLBACK=true` **somente no Core** ativa a segunda tentativa de análise no `mistralai/mistral-large-4-1t-a52b` da NVIDIA **se** `NVIDIA_API_KEY` já estiver no SecretStore.
- **Default=false** para não compartilhar a foto com um segundo serviço nem gastar créditos de teste sem autorização. Verificar quota/free endpoint do titular antes de ativar; nunca iniciar billing.
- O gateway NVIDIA aceita imagens em data URI e `reasoning_effort=none`. Enviar `stream=false`, `max_tokens=1400` e instrução JSON, mas o endpoint NVIDIA não tem certificação local de strict schema; **sempre** validar `VisionResponse` e não usar JSON imperfeito como fato.
- Uma só chamada Qwen → no máximo uma Mistral. Fallback apenas por erro/limite de JSON, rede, 5xx ou 429; não repetir automaticamente 400/401/402/403, mídia inválida ou visão desligada. 26s de orçamento total visual (timeout Qwen limitado a 16s quando fallback ativo), mais o guard já existente de 27s do Core.
- Se ambas falharem, `status=unavailable` e o modelo social não recebe descrição falsa. Logar somente códigos e provider, nunca conteúdo de imagem.
- **Sem promoção do Mistral para diálogo** e sem alterar `AMORA_SOCIAL_PRIMARY=numes`, o fallback social, o schema 18, o pipeline de stickers, ou a Agenda.

Teste real após deploy do PR: a mesma foto na DM com consentimento ativo; verificar `INCOMING_IMAGE_ANALYZED` em `context_compiled`. Se houver falha, log seguro distingue as causas. Para testar o fallback, habilitar explicitamente a opção e confirmar o evento `incoming_visual_fallback_attempt provider=nvidia_mistral`.
