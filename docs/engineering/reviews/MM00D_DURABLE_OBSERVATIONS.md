# MM.00D — Admissão durável e memória multimodal

Status: D.1 aprovado e integrado (#239); D.2 aprovado e integrado (#240); D.3A.1 aprovado e integrado (#241); D.3A.2 implementado, CI pendente.
Issue de acompanhamento: [#238](https://github.com/Sllay/Amora-Chibi/issues/238).
Base: contrato de observação sobre AmoraEvent, sem mudança de schema ou driver.

## Checkpoints

| ID | Escopo | Critério de saída | Estado |
|---|---|---|---|
| D.1A | Identidade imutável no event store existente | retry igual = dedupe; conteúdo/identidade/escopo divergente = conflito; primeira recepção preservada; SQLite e Hrana | Aprovado; integrado #239 |
| D.1B | Leitura interna da observação durável | recuperar AmoraEvent validado por ID, sem criar API pública; casos ausente/outro tipo/linha inválida | Aprovado; integrado #239 |
| D.2A | Candidato de episódio autorizado | fontes duráveis lidas pelo Core, mesma sessão/correlação, interseção de audiência/escopo, evidência explícita, conteúdo bounded; nunca promover inferência a fato | Aprovado; integrado #240 |
| D.2B | Gate, replay e recuperação | MemoryWriteGate existente; ID determinístico de episódio; evitar sobrescrever memória existente ou reviver memória arquivada; testar recall, restart e falhas | Aprovado; integrado #240 |
| D.3 | Admissão dos transportes/ordenamento e consolidação durável | resolução real de identidade, consentimento, monotonicidade/epoch, gap e backpressure; usar fila/reconciliação existentes quando elegível; não fingir que visão é fala | [D.3A.1 aprovado; A.2 em validação](MM00D3_ADMISSION_AND_RECOVERY.md) |

## Decisões e limites

- O event store existente usa `ON CONFLICT(dedupe_key) DO NOTHING` e distingue tipos, mas ainda não verifica divergência semântica de eventos do mesmo tipo. D.1 adiciona comparação apenas para `perception.observation`, sem mudar dedupe genérico. O único campo não semântico de retry é `received_at`; timestamp de captura, conteúdo, identidade, confidência, política, provider/modelo e hash não podem mudar no mesmo delivery.
- Normalizar horários em UTC no builder; captura e recepção são diferentes. Ordem entre streams não é inferida pela chegada; D.3 terá política de watermark/gap.
- A leitura interna não autoriza divulgação: escopo/audiência são verificados antes de acesso ao conteúdo em cada consumidor.
- `MemoryProvenance.derived_from` referencia IDs de memórias, NÃO IDs de eventos; manter `source_event_id` e lista explícita de evidências de evento em metadata controlada. Não simular proveniência para contornar o MemoryService.
- `ReflectionQueue` atual exige input persistido em `conversation_messages`, superfície `platform_messaging` e permissão remota; só usar o contrato real onde é apropriado. Não publicar frame/áudio bruto no event log, não duplicar transcrição em novos bancos, não abrir outro worker de memória sem justificar.
- Não mudar V3, políticas de provider, produção ou projeção local. Testes sintéticos, CI e smoke real são gates separados.
- Ao finalizar cada item: atualizar ledger e issue #238 com SHA, resultados, limites, próximos passos.

## Evidência de implementação D.1

- `a9b5758c`: builder normaliza horários UTC; parser reconstrói envelope canônico e rejeita alterações de IDs/sequência/política/campos não autorizados; replay permite apenas alteração de chegada. Erro de validação sanitizado.
- `1cb15982`: event store valida observações antes de inserir; verifica replay semântico apenas no tipo reservado, mantém dedupe legado; leitura interna por ID com validação. Sem tabela nova.
- `f755c709`: rota genérica /v1/events rejeita tipo reservado com 403, inclusive cliente autenticado; admissão futura ainda precisa resolver sessão e identidade no Core.
- `d0d233d6`: testes SQLite e Hrana com reabertura do banco, primeira chegada preservada, conflitos em conteúdo/ator/escopo/modelo/confiança, envelope forjado/corrompido, rota externa e ausência/other type. CI final do #239: 4/4 workflows, 8/8 jobs, HEAD `54c4ed423713d4768c2cfb3bc5a788b9263c13e4`, merge `210b50cc6fce0e6256b76f7bc3e171cfe0f48c76`.
- D.2/D.3 permanecem abertos: não há ordering gate ou adaptador real ativo. A alteração de D.1 não grava frames/áudio nem habilita percepções no runtime; o evento só chega ao store mediante chamada interna explícita.

## Plano D.2 — registrado antes do código

1. D.2A: serviço interno recebe somente 1–4 IDs de observações duráveis e identidade de destinatário resolvida pelo Core; valida existência, sessão/correlação comum, janela temporal de até 120s, audiência efetiva de cada fonte e identidade conhecida no registry. Seleção de relevância é explícita, não automática. Desconhecido não vira fato; inferências ficam rotuladas. Conteúdo determinístico com detalhes textuais originais e timestamps, sem resumo inventado; limite total 12k, rejeitar excesso em vez de truncar silenciosamente. Não expor mídias ou URLs. Proveniência com IDs de eventos em metadata e source_event_id, sem misturar IDs de memórias.
2. D.2B: MemoryService/MemoryWriteGate existentes, tipo episódico, ID determinístico pelo conjunto de evidências ordenadas. Inserção create-only atômica na mesma transação dos índices; conflito não sobrescreve memória nem revive arquivo. Replay retorna estado sem revelar conteúdo; falha antes de commit é reexecutável por IDs, após commit não duplica. Não alterar comportamento de outros escritores.
3. Testes: SQLite/Hrana, duas modalidades, recall após restart, escopo/audiência/remote, identidade desconhecida, fontes faltantes/fora da sessão, baixa relevância, desconhecido/inferência, IDs repetidos, rollback, concorrência, memória arquivada/alterada. Nenhuma alteração de schema ou provider; não conectar ao bus automaticamente. D.3 permanece responsável pela autorização real da sessão, seleção automática, fila/reconciliação durável e ordenação.

Limites: a janela e o máximo de fontes são parâmetros conservadores iniciais, não medição de produção. O serviço só recebe seleção interna autorizada; não oferece endpoint de gravação nem prova que a captura já funciona. Deduplicação por conjunto exato não resolve ainda episódios semanticamente semelhantes/overlap, tarefa de consolidação avançada.

## Evidência D.2 — código e testes

- `9cd0a726`: MemoryStore create-only atômico por ID, preserva registro/índices/arquivo no conflito e rollback integral; comportamento legado permanece.
- `295b0519`: MemoryService usa o gate normal e create-only apenas com ID estável, sem misturar leases ou pedido explícito do proprietário.
- `240bd147`: consolidator interno recebe IDs duráveis e destinatário conhecido; interseção de audiência, escopo máximo, remote AND, janela e limite; conteúdo determinístico com texto original e evidência rotulada, proveniência derivada sem forjar fatos.
- `53fbc68f` / `2692b38f`: testes SQLite/Hrana, recall após restart, replay, arquivamento, rejeições, rollback, dois escritores independentes, audiência de grupo e limite de conteúdo. CI aprovado no HEAD `e10a235918a7566fcaff6ef4f6e9bf45f74c0f56` (3 workflows/7 jobs), merge `fbb2284b664c0a71de09084e1bdbe93bc9f8d83b`.
- Sem consumidor ativo, API, provider, schema ou configuração de produção. D.3 deverá autorizar as sessões reais, ordenar streams, selecionar eventos, tratar invalidação/revogação e recuperar jobs automaticamente; D.2 só recupera via chamada explícita/replay do conjunto de IDs, não promete fila durável.
