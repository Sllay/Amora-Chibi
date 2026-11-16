# MM.00 — Fundação multimodal (proposta de implementação)

Status: auditoria inicial e plano de contratos; não constitui certificação operacional nem altera versões canônicas.
Base auditada: ac8857bde8d52b0d90dd0ab002154ace7394d657 (PCS.4C R1/R2/R3).
Dependência PCS.4C: recuperação real aprovada em 2026-11-16, conforme relatório operacional registrado em [#221](https://github.com/Sllay/Amora-Chibi/issues/221#issuecomment-6907256721). Backup/verify/restore: 48.059 linhas, 51/51 tabelas, schema 18; restart: dois boots remotos, cinco consultas com ranking equivalente, quatro tabelas de memória preservadas. Projeção local permanece desativada; os gates de promoção de storage são independentes do MM.00.

## 1. Reutilização obrigatória

| Contrato existente | Local | Decisão |
|---|---|---|
| AmoraEvent | core/amora/events/models.py | envelope de eventos; actor/target, timestamp com timezone, session_id, correlation/causation, sequence e dedupe_key |
| InboundMessage | core/amora/messaging/models.py | entrada social existente; manter identidade de entrega e escopo do transporte |
| PlatformIdentityBinding | core/amora/identity/profiles.py | autoridade de associação pessoa/plataforma; nunca inferir identidade por nome de exibição |
| AudioSegment, Transcript, SpeakerResult | core/amora/audio/models.py | áudio temporário, texto reconhecido, confiança e identidade; reutilizar |
| GroqWhisperSTT | core/amora/audio/stt.py | whisper-large-v3-turbo, no-speech gate, erros de quota; certificar latência e áudio real |
| LanguageSocialPipeline.observe_transcript | core/amora/language/pipeline.py | ligar fala à observação social, identidade, dedupe e sessão; não criar outro ACIR |
| MemoryCandidate / MemoryProvenance / MemoryQuery | core/amora/memory/models.py | usar actor, source_event_id, session_id, derived_from, confidence, scope/audience, entity_refs, intervalo temporal e recuperação limitada |
| MemoryWriteGate / MemoryService | core/amora/memory/gate.py, service.py | único gate de consolidação; inferência não vira fato automaticamente; respeitar escopo |
| CPF / CH2 | docs/canon/PERCEPTUAL_ASSET_MEMORY.md; core/amora/messaging/expression_memory.py | SHA real, lease/CAS, percepção estruturada, disponibilidade distinta de conhecimento |
| Worker visual Discord | workers/discord/visual-worker.mjs | certificar sticker real; outros tipos devem ter resolver seguro próprio |

O CPF descreve um asset conhecido; uma lembrança episódica descreve o que ocorreu com aquele asset em uma sessão. Não duplicar o CPF como episódio a cada exposição. O CH2 não é visão contínua; preservar a fronteira de V1.1 definida no canon e roadmap.

## 2. Contrato mínimo proposto (MM.00C)

Não adicionar novo bus, banco, personalidade ou pipeline de cognição. Validar um payload tipado e versionado dentro de AmoraEvent para observação sensorial:
- tipo/modalidade (fala final, imagem pontual, observação visual temporal), origem/transport scope, capture time e intervalo, identidade resolvida pelo Core, correlation/session e dedupe key; nenhum worker pode autodeclarar um actor canônico.
- distinção de observado, dito por participante, inferido e desconhecido; provider/model e confiança não concedem autoridade.
- referência opaca à evidência temporária, fingerprint de conteúdo quando pertinente e política de retenção; não embutir frame, áudio bruto, token ou URL privada em payload persistente.
- privacy_scope, owner/audience e remote_processing_allowed validados no boundary antes de provider. Derivação multimodal usa interseção das permissões de todas as fontes, sem escalada.
- sequência por stream, timestamp de captura e chegada separados, janela temporal limitada; eventos atrasados não substituem estados mais recentes. Desconexão gera gap, nunca visão inventada.
- separar amostras temporárias do evento durável relevante: a captura bruta não entra no event log persistente. Uma observação candidata só se torna memória após o gate existente. Episódio inclui IDs das evidências selecionadas; não armazenar resumo sem detalhes recuperáveis.

## 3. Pipeline e ciclos de validação

MM.00A/B: auditoria de contratos e topologia; mapear armazenamento, recuperação e quotas. MM.00C/D: modelo tipado, validação, idempotência, privacidade, testes negativos. Nenhuma mudança de schema sem ADR e teste de restauração.

MM.01: CH2 real → sticker recebido/semântica → resposta contextual → episódio pertinente → recall pós-restart; depois imagens estáticas, Web/WhatsApp e animação com renderer certificado. Não confundir metadata com percepção. GIF/APNG/Lottie continuam fail-closed até suporte específico.

MM.02: áudio assíncrono → decode/limite/VAD → Groq Whisper → LanguageSocialPipeline → resposta com Fish Audio existente → memória. Sem treinar TTS nem substituir a voz clonada; falhas de STT não viram fala fabricada.

MM.03: Discord Voice em canais de servidor; validar biblioteca DAVE, Opus, áudio por participante, interrupção e reconexão antes de produção. Identidade usa binding e speaker confidence; DM voice oficial não é dependência.

MM.04: PWA iPhone principal; chamada privada WebRTC, áudio/avatares, reconexão e limites reais de segundo plano. Não exigir IPA, Apple Developer Program ou Android.

MM.05: PoC EveryCord → RTMPS privado → receptor MediaMTX → seleção de frames → visão. Validar primeiro iPhone real, call simultânea, ingest seguro, latência e custo. Não depender do screen sharing Discord no Brasil. Não guardar vídeo por padrão. A visão contínua permanece V1.1.

MM.06/07: consolidação temporal/episódica, fusão entre fala e visão, ACIR perceptual, participação natural e recuperação entre plataformas. Cada etapa anterior já deve fechar persistência/recall, não adiar tudo para esta.

## 4. Gates de aceitação transversais

- Software: CI verde, contratos, integração, reinício, dedupe, ordenação, backpressure, erro de provider/timeout e isolamento de identidade.
- Memória: caso sintético compartilhado entre fala/visão, detalhes específicos recuperáveis após restart, sem multiplicar registros por replay; desconhecido permanece desconhecido. Grupo: nenhum vazamento cruzado de memória privada. Teste de conversa de duas horas com limites de RAM, fila, crescimento persistente e número de chamadas mensurados; metas calibradas com baseline real, não declaradas como resultados.
- Operacional: credencial Groq real, Discord real, iPhone real, call + EveryCord e autorização de captura. Nenhuma gravação permanente de mídia por padrão; encerrar sessão revoga ingest, expira buffer, conserva somente memórias permitidas.
- PCS.4C: backup verificado e Restore/restart reais aprovados; preservar evidência e volume. Não ativar projeção nem encerrar #221 sem revisar os gates restantes de promoção.

## 5. Próximo checkpoint

MM.00C: ler contratos de eventos e persistência do bus, schema de memória e inbound privacy/identity antes de escolher campos finais; implementar primeiro um contrato pequeno e testes de rejeição. Não integrar um pipeline paralelo; a recuperação PCS.4C está aprovada, e a promoção de projeção/storage permanece independente.
