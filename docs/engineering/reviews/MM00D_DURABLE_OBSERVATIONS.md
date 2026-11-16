# MM.00D — Admissão durável e memória multimodal

Status: plano inicial; implementação condicionada à aprovação de MM.00C (#237).
Issue de acompanhamento: [#238](https://github.com/Sllay/Amora-Chibi/issues/238).
Base: contrato de observação sobre AmoraEvent, sem mudança de schema ou driver.

## Checkpoints

| ID | Escopo | Critério de saída | Estado |
|---|---|---|---|
| D.1A | Identidade imutável no event store existente | retry igual = dedupe; conteúdo/identidade/escopo divergente = conflito; primeira recepção preservada; SQLite e Hrana | Em implementação |
| D.1B | Leitura interna da observação durável | recuperar AmoraEvent validado por ID, sem criar API pública; casos ausente/outro tipo/linha inválida | Planejado |
| D.2A | Candidato de episódio autorizado | fontes duráveis lidas pelo Core, mesma sessão/correlação, interseção de audiência/escopo, evidência explícita, conteúdo bounded; nunca promover inferência a fato | Planejado |
| D.2B | Gate, replay e recuperação | MemoryWriteGate existente; ID determinístico de episódio; evitar sobrescrever memória existente ou reviver memória arquivada; testar recall, restart e falhas | Planejado |
| D.3 | Admissão dos transportes/ordenamento e consolidação durável | resolução real de identidade, consentimento, monotonicidade/epoch, gap e backpressure; usar fila/reconciliação existentes quando elegível; não fingir que visão é fala | Planejado |

## Decisões e limites

- O event store existente usa `ON CONFLICT(dedupe_key) DO NOTHING` e distingue tipos, mas ainda não verifica divergência semântica de eventos do mesmo tipo. D.1 adiciona comparação apenas para `perception.observation`, sem mudar dedupe genérico. O único campo não semântico de retry é `received_at`; timestamp de captura, conteúdo, identidade, confidência, política, provider/modelo e hash não podem mudar no mesmo delivery.
- Normalizar horários em UTC no builder; captura e recepção são diferentes. Ordem entre streams não é inferida pela chegada; D.3 terá política de watermark/gap.
- A leitura interna não autoriza divulgação: escopo/audiência são verificados antes de acesso ao conteúdo em cada consumidor.
- `MemoryProvenance.derived_from` referencia IDs de memórias, NÃO IDs de eventos; manter `source_event_id` e lista explícita de evidências de evento em metadata controlada. Não simular proveniência para contornar o MemoryService.
- `ReflectionQueue` atual exige input persistido em `conversation_messages`, superfície `platform_messaging` e permissão remota; só usar o contrato real onde é apropriado. Não publicar frame/áudio bruto no event log, não duplicar transcrição em novos bancos, não abrir outro worker de memória sem justificar.
- Não mudar V3, políticas de provider, produção ou projeção local. Testes sintéticos, CI e smoke real são gates separados.
- Ao finalizar cada item: atualizar ledger e issue #238 com SHA, resultados, limites, próximos passos.
