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

## CH2.2 — Imagens grandes, artes com transparência e falhas explícitas

**Problema real:** o operador enviou artes de melhor qualidade pelo Discord e a Amora não conseguiu enxergar algumas. O antigo worker rejeitava todas as imagens acima de **4 MiB** antes de qualquer chamada ao Groq. Além disso, priorizava `proxy_url`, que pode transcodificar PNG para WebP enquanto o Core exigia que o MIME da imagem processada fosse exatamente igual ao `content_type` do anexo nativo.

**Tratamento final de entrada (worker Discord):**
1. Selecionar a primeira imagem PNG/JPEG/WebP entre os 16 anexos normalizados da DM. Manter os mesmos vínculos de canal e attachment ID, allowlist estrita de CDN HTTPS, sem redirect, cache, dump, ou URL externa.
2. Preferir `url` original da CDN para preservar arte e pixels; recorrer ao `proxy_url` validado **apenas** se o download nativo falhar e não aceitar MIME diferente sem identificar o proxy. Se o proxy converter o formato, checar assinatura real do conteúdo e informar ao Core o MIME resultante.
3. Baixar bytes nativos em streaming com limite de **25 MiB** (o velho 4 MiB era um limite interno, não o limite de entrada da Groq). Arquivos de até 4 MiB chegam ao Core sem alteração.
4. Se o original exceder 4 MiB, processar **localmente no worker** via libvips 8.18 (`vipsthumbnail`) até WebP de no máximo 4 MiB. Preservar proporção e canal alfa, usar teto inicial 2560 px e depois 2048/1536/1280 com qualidade decrescente somente se necessário; nunca ampliar fotos menores. A prévia *é uma versão de percepção*, não substitui nem altera o original no Discord.
5. Conversão em subprocesso sem shell, argumentos fixos, timeout de 8s por conversão completa, ambiente com concorrência libvips limitada, arquivos temporários 0600 em diretório randômico apagado após sucesso e erro. Sem persistir imagens no Turso, servidor, memória canônica ou logs.
6. A saída para o Core permanece exatamente no limite anterior de **4 MiB** (até 5,6 MiB em Base64) para não ampliar a carga do HTTP síncrono, proxy e app de 512 MB. O Core aceita MIME convertido WebP *somente* se os metadados nativos autorizam um raster PNG/JPEG/WebP com attachment ID idêntico; revalida assinaturas em base64.
7. Se o fetch ou a conversão falhar, registrar código sanitizado (`IMAGE_NATIVE_TOO_LARGE`, `IMAGE_TRANSFORM_FAILED`, etc.) em `incoming_image_status`; o Core transforma-o numa categoria `visual_observation.failure_reason` fechada (`too_large`, `download_failed`, `processing_failed`, `unsupported_format`) e **não** faz alegações sobre o que há na imagem. Em mensagens só de imagem, a Amora recebe aviso explícito de indisponibilidade para poder responder sem fingir que viu.

**Fronteiras:** não há mudança em personalidade, provider social, runtime schema 18/19, Core privacy/identity, fallback visual opcional ou persistência. O Qwen continua como percepção primária; o Mistral como fallback somente com opt-in. O limite de 20 MB documentado na Groq é específico de URL de imagem; não é usado para justificar upload JSON arbitrário de Base64. A prévia compacta evita esse risco. A integração não tenta editar URL assinada para solicitar thumbnails do Discord, já que esses parâmetros não têm contrato estável para anexos.

**Testes de liberação:** Node com assinatura, original/proxy, arquivo >4 MiB que chega convertido, falha de conversor, nativo >25 MiB e status explícito; Python com WebP convertido ligado ao mesmo anexo, recusa de arquivo não-imagem, visão indisponível sem alucinação; container Alpine com libvips instalado e smoke **real** de PNG→WebP pelo binário nativo. CI verde é necessário, mas teste físico com uma arte real de >4 MiB e exame da resposta ainda é o gate de qualidade visual.

**Limitação conhecida:** somente a *primeira* imagem por mensagem é analisada nesta etapa; múltiplas fotos anexadas simultaneamente e capturas de vídeo não estão incluídas. Para próximas iterações, considerar percepção multi-imagem/crops específicos após medir custo, qualidade e latência.

## CH2.3 — Fotos grandes sem resposta: diagnóstico do worker e recuperação de 413

**Incidente relatado:** imagem nativa de aproximadamente 10 MB enviada em DM após CH2.2; logs fornecidos **apenas do Core** entre 2026-11-16 23:53 UTC e 2026-11-17 00:19 UTC mostram `reflection_idle`, `tool_call wait` e startup, sem `messaging_inbound_result` ou `context_compiled` para essa foto. Estes logs **não** confirmam nem descartam que o worker Discord recebeu o gateway event. A versão de deploy do worker não foi observada. Nenhuma causa exata pode ser declarada sem o log do worker.

**Falhas de diagnóstico corrigidas:**
- O worker informa no startup `vision_pipeline=ch2.3` e, para DMs com imagem relevante, `IMAGE_INGEST_BEGIN pipeline=ch2.3 native_mib_ceil=N`, `IMAGE_INGEST_STAGE candidate/original_fetch/downloaded/transcode_started/transcode_completed/ready`, `IMAGE_INGEST_READY mime=... wire_kib_ceil=N`. Somente tamanho arredondado, etapas e MIME de um pequeno conjunto validado; **nunca** conteúdo/assinatura, filename, URL, bearer, mensagem, base64 ou IDs sociais.
- `content_type` de anexos Discord é opcional. JPEG/PNG/WebP com filename permitido e MIME ausente ou `application/octet-stream` passam pela mesma verificação de bytes reais, sem confiar na extensão como prova. Rótulo MIME do mesmo ID só é preenchido após verificação da assinatura.
- Adicionado suporte a ingestão nativa HEIC/HEIF/AVIF (marcas do cabeçalho ISO BMFF verificadas, conversão libvips local para WebP), inclusive para nativo inferior a 4 MiB, pois os providers visuais continuam aceitando somente PNG/JPEG/WebP. Caso o runtime de imagem não suporte esse formato, registrar erro de conversão; não inventar imagem.
- Formato não suportado (ex. GIF nesta via) recebe `IMAGE_UNSUPPORTED_FORMAT` e observação de indisponibilidade, evitando ignorar imagens sem notificação.
- Se o Core/ingress devolver HTTP **413** para o JSON Base64 (até aproximadamente 5,6 MiB), a mesma mensagem lógica recebe **no máximo uma** recompactação WebP ≤640 KiB (payload JSON <1 MiB). Se a recompactação ou segunda chamada ainda falhar por 413, encaminhar **metadados apenas** com `IMAGE_TRANSPORT_TOO_LARGE`, preservando o turno e informando falta de visão. Não há retry ilimitado nem alteração do ID/lease; o Core continua responsável por dedupe. A redução mais agressiva acontece **apenas após um 413 observado**, preservando a primeira análise em qualidade maior.
- Core só aceita imagem convertida em WebP ligada ao mesmo ID de attachment raster nativo; dados inválidos/não autorizados nunca chegam ao modelo. Falhas ficam como `visual_observation.failure_reason` não canônico (`too_large` ou `unsupported_format`), sem alucinações.
- Testes: Node simula efetivamente 10 MiB de download, HEIC/AVIF, MIME opcional, 413 com retry pequeno, 413 repetido e falha de codec; Python valida mesma identidade e resposta não-hallucinada no Core; CI worker usa libvips real em container.

**Gate físico após merge e novo deploy dos dois containers:**
1. No log do **worker Discord**, confirmar `[amora-discord] START ... vision_pipeline=ch2.3`. Não confundir serviço Core com worker.
2. Enviar a mesma foto de 10 MB por DM. Checar etapas `IMAGE_INGEST_*` no **worker**. Se nenhuma aparecer, olhar `READY`, eventos do gateway, `remote_processing` e se o envio de foto foi um anexo reconhecido. Não conclua nada a partir de `reflection_idle` do Core.
3. Se houver `IMAGE_TRANSPORT_RECOVERY reason=CORE_HTTP_413`, o gateway rejeitou o payload; confirmar retry pequeno e `CORE_INBOUND_OK`.
4. No Core, procurar `INCOMING_IMAGE_ANALYZED` e comparar a resposta com a imagem. Se houver `INCOMING_IMAGE_NOT_ANALYZED`, buscar `incoming_image_analysis_failed` (modelo) ou `incoming_image_unavailable` (ingestão).
5. Nunca publicar a foto privada ou assinaturas CDN em issues/logs; somente códigos e tempos. Não afirmar sucesso no Discord antes dessa prova.

**Ajuste de desempenho CH2.3:** a conversão WebP por libvips usa `effort=2` (o padrão é 4), preserva os fatores Q previstos e tem orçamento limitado de **12 segundos totais** e **6 segundos por preset**, em vez de 8s/4s no CH2.2. O objetivo é reduzir timeouts em CPUs menores com imagens de aproximadamente 10 MiB; o teste físico ainda é obrigatório. O modelo social continua separado da preparação de imagem.


## CH2.4 — Incidente real de HTTP 413: limite de 256 KiB do próprio Core

**Evidência de produção de 2026-11-17:** worker Discord `vision_pipeline=ch2.3` em execução e `remote_processing=true`. Imagens nativas de 707 KiB e 1572 KiB foram baixadas e preparadas com sucesso (`IMAGE_INGEST_READY`), mas o primeiro `POST /v1/messaging/worker/inbound` retornou HTTP 413. Algumas tentativas de WebP menor foram aceitas e produziram `INCOMING_IMAGE_ANALYZED`; outras receberam novo 413 e só chegaram como `IMAGE_TRANSPORT_TOO_LARGE`. O `CORE_INBOUND_OK` posterior não implica que o modelo viu a foto.

**Causa confirmada no repositório:** o `SecurityMiddleware` do Core, em `core/amora/security.py`, selecionava no ramo geral `maximum = 256 * 1024` para **todas** as requisições JSON de messaging inbound. Isso bloqueava toda foto que, codificada em Base64 + JSON, ultrapassasse 256 KiB **antes da validação, do parser e do Groq**. O fallback CH2.3 reduzia para até 640 KiB *binários*, que ainda podem virar ~854 KiB de Base64 e assim continuar recebendo 413. Não há evidência de que o limite de ingress do Northflank tenha causado estes 413; o limite interno já explica os registros.

**Correção CH2.4:** quando e somente quando `SecurityMiddleware` está no **Core** (`worker=False`), o método é **POST**, a rota exata é `/v1/messaging/worker/inbound` e o papel autenticado é **`messaging`** (chave `AMORA_MESSAGING_SERVICE_TOKEN`), o middleware permite um envelope HTTP de até **6 MiB**. Essa capacidade suporta Base64 para o limite anterior de **4 MiB binários** mais o envelope JSON; não altera o contrato Pydantic de `InboundImageContent` nem o download máximo de 25 MiB no worker. Todos os outros endpoints JSON e papéis (incluindo admin no mesmo path) preservam o limite 256 KiB; pairing e worker-mode mantêm 16 KiB; áudio/assets seguem seus tetos anteriores. Autenticação, autorização, concorrência, streaming/chunked limit e timeout de leitura precedem ou continuam impondo o limite de corpo.

**Regressões:** testes no próprio ASGI Core simulam uma requisição de serviço com **4 MiB de bytes codificados em 5.59 MiB Base64 + JSON** — o tamanho deve alcançar a validação do Pydantic (422 por envelope propositalmente incompleto), não falhar no middleware (413). Outros papéis, métodos, caminhos, requisições sem autorização e requisições acima de 6 MiB continuam negados. Uploads em chunks também são interrompidos acima de 6 MiB. Não usar conteúdo pessoal nesses testes.

**Pós-deploy:** atualizar **somente Core** é necessário para corrigir a causa identificada; o worker CH2.3 já consta ativo pelos logs. Validar fisicamente imagens pequenas (~707 KiB) e maiores (~1.5 MiB/10 MiB): ausência de `IMAGE_TRANSPORT_RECOVERY reason=CORE_HTTP_413`, presença de `CORE_INBOUND_OK` e `INCOMING_IMAGE_ANALYZED` no Core. Se 413 persistir mesmo com o Core novo, examinar proxy/ingress ou outros limites a partir dos headers/logs, sem aumentar globalmente limites de segurança e sem fabricar observação visual.


## CH2.5 — Teto de arquivo do modelo como contrato do transporte (supersede CH2.4)

Solicitação do operador: evitar reduzir obras artísticas apenas por um limite interno de 4 MiB se o modelo Qwen 3.8 27B anuncia teto de arquivo de **20 MB**.

**Fonte oficial:** https://console.groq.com/docs/model/qwen/qwen3.8-27b anuncia `MAX FILE SIZE 20 MB`; https://console.groq.com/docs/vision explicita 20 MB **para imagens fornecidas por URL**. A mesma página documenta data URI/Base64, porém **não garante explicitamente** que uma data URI com 20 MB de imagem seja aceita. O teste real do provider continua obrigatório. Não aplicar limites da API QwenCloud/Alibaba como se fossem Groq.

**Limites sincronizados CH2.5:**
- Discord coleta nativo de até **25 MiB**, como antes; arquivo PNG/JPEG/WebP **até 20.000.000 bytes** passa pelo wire sem perda de fidelidade. Arquivo maior é convertido localmente para WebP de até 20.000.000 bytes; HEIF/AVIF ainda requerem conversão obrigatória independentemente do tamanho.
- `InboundImageContent.content_base64` (Pydantic) e `valid_image_bytes` (Core) aceitam no máximo 20.000.000 bytes decodificados = **26.666.668 caracteres Base64**, com MIME/assinatura e binding de attachment id validados.
- `SecurityMiddleware` permite no máximo **28 MiB de JSON bruto** somente ao papel `messaging` autenticado, **POST /v1/messaging/worker/inbound** no Core. O teto HTTP é necessariamente superior ao teto binário do modelo, porque transporta Base64 + metadata. Outras rotas e outros papéis continuam limitados a 256 KiB ou a seus tetos anteriores. No Core há no máximo **uma** requisição de visão grande (corpo declarado acima de 6 MiB ou chunked) em andamento para limitar pressão de memória no serviço de 512 MB; a leitura desses corpos tem timeout de 20s, os menores continuam em 10s. O custo e a latência de payloads muito grandes podem aumentar.
- O worker mantém fallback do HTTP 413 para uma prévia WebP ≤640 KiB e, por último, metadata-only, preservando a mensagem sem alucinar imagem. A versão CH2.4 antiga que limitava a 6 MiB foi substituída e seus testes atualizados.
- **Não** alegar que um arquivo de 20 MB com Base64 necessariamente será aceito pela Groq em produção. A aprovação depende da integração Core + worker e envio real ao modelo. Caso Groq rejeite 20 MB na modalidade Base64, não elevar ainda mais o limite; verificar um teto adequado, envio por URL com consentimento/controles específicos ou reduzir a cópia visual mantendo o original intacto no Discord.

**Testes:** boundary de 20.000.000 bytes em worker e Core, campo Base64 fechado (26.666.668 caracteres), HTTP serviço aceita >256 KiB até ~26,7 MB Base64, rejeita >28 MiB, limita streaming e concorrência, mantém autenticação/restrições das outras rotas; smoke nativo libvips e restante da matriz de segurança/cognição. **Merge apenas após CI verde.**


## CH2.6 — Alinhamento real ao limite Groq: 20 MB por URL, Base64 menor (supersede CH2.5)

**Atenção:** o teto oficial de 20 MB de `qwen/qwen3.8-27b` é descrito especificamente para **entrada por URL de imagem** (https://console.groq.com/docs/vision). Fontes independentes sobre integrações Groq registram um limite de aproximadamente **4 MB para requisições com imagem inline/Base64**. Portanto, permitir 20 MB binários dentro de um JSON Base64 de ~27 MB no Core não garante que a Groq aceite a foto e pode elevar a memória de um serviço de 512 MB. A tentativa CH2.5 de transportar 20 MB integralmente em Base64 foi substituída pela solução seguinte **antes do merge**.

**Fluxo de duas vias:**
1. O Discord worker continua baixando o original de CDN autenticada, com URL HTTPS/host/canal/attachment-id conferidos, até 25 MiB. Apenas quando o original PNG/JPEG/WebP é maior que 2.800.000 bytes e menor ou igual a **20.000.000 bytes**, o worker apresenta uma referência `source_url` assinada de CDN **somente no payload visual efêmero**, ao lado de uma prévia WebP de até 2.800.000 bytes binários. Imagens nativas menores são enviadas só por Base64; fotos acima de 20 MB, ou HEIC/AVIF, dependem da prévia convertida. Nunca enviar URL de mensagem escrita pelo usuário, rede privada, redirecionamento ou servidor arbitrário.
2. O `InboundMessage` Pydantic Core recusa `source_url` salvo em **DM Discord** de `remote_processing_allowed=true` e `PrivacyScope.PERSONAL`, associada a um **mesmo ID de canal e anexo** dos metadados nativos. Exige host exatamente `cdn.discordapp.com` ou `media.discordapp.net`, caminho de `/attachments/<channel>/<attachment>/<filename>`, somente chaves opcionais `ex/is/hm` de assinatura e nenhuma credencial, porta, URL HTTP, fragmento ou query arbitrária. A autoridade cognitiva do `PERSON_BK` é verificada no `modality_service` antes do provider.
3. **Groq/Qwen usa primeiro `source_url` original** e é o único provider que recebe essa URL. Se houver HTTP 400/403/404/413/422 ou falha de transporte/timeout, faz **uma tentativa** com a miniatura Base64 no mesmo turno e dentro do orçamento visual. Caso falhe, preserva a classificação de erro e não inventa imagem. A URL nunca vai para o Numes social, Turso, logs ou prompt de memória; a assinatura original é entregue à Groq, que pode acessá-la enquanto válida, como parte do consentimento de processamento remoto do usuário.
4. O `SecurityMiddleware` Core limita exatamente `POST /v1/messaging/worker/inbound` autenticado pelo papel `messaging` a **5 MiB de JSON** para suportar uma prévia Base64 de até `3.733.336` caracteres (<4 MB) + o envelope, sem ampliar o teto de requisições genéricas (256 KiB). O decoder visual aceita até 2.800.000 bytes; a máquina de visão mantém validação estrita de assinatura PNG/JPEG/WebP. O Core continua com limites de concorrência, polling, framing, acesso e timeouts anteriores.
5. Guardrails: URL de CDN assinada não é logada nem salva; vínculo de mesma DM/attachment, MIME/assinatura verificados, sem URL de usuário nem fetch arbitrário, sem prompt injection de texto visual. Conversão local mantém qualidade quando o original não estiver disponível via URL e previne perda total do turno. Acesso remoto do Qwen à CDN **não é garantido** sem teste físico; se o Discord bloquear o acesso do provedor à URL, a rota Base64 de recuperação é usada.

**Liberação:** testes Node para envio de foto original 10 MB por URL com WebP fallback e limites acima de 20 MB, Python Core para URL autorizada/bloqueios SSRF e contrato do Qwen URL-first+fallback, teste HTTP da rota privada de 5 MiB e matriz completa. Confirmar em ambiente real `vision_pipeline=ch2.5`, `IMAGE_INGEST_READY`, `CORE_INBOUND_OK`, `INCOMING_IMAGE_ANALYZED`; verificar visualmente a resposta. Nenhuma garantia de produção antes do deploy e do teste efetivo.
