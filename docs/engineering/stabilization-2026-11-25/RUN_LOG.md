# Diário de estabilização

2026-11-25 — RUN00: plano baseado nos 21 prints e logs fornecidos pelo proprietário; baseline e11011e. RUN01 IN_PROGRESS. Nenhuma mudança de produção.


## RUN01 A — orçamento, envelope e saída técnica

- 09104d7: reserva de failover no fluxo platform_messaging (inclusive fila off), min(30s, 35% saldo, 12s por candidato posterior elegível conhecido), sem reordenar preferências. 90s/3 alternativas => primeira janela 60s; cada tentativa recalcula. Tempo real de tentativa integral debitado, graça Numes limitada à janela atribuída; removida extensão durável não debitada no runtime (helper histórico mantido para testes). Novos campos numéricos de diagnóstico. Não garante latência de um provedor nem muda timeout HTTP global.
- Retry Groq 413/context_length_exceeded: estende mecanismo existente à DM privada owner textual, ContextVar vinculado a turno e lease do transporte, verificação live de identidade, consentimento, invalidação e renovação; uma projeção estritamente menor, nenhuma retry sem guard, nenhuma retry de visão/trechos web lidos. Mantém rota durável existente; não reduz memória canônica. Se irredutível, failover. Não confundir 413 com quota.
- Numes e Gemini usam referência ACIR idêntica no wire, sem remover fato/estado. Pedido explícito de documentação/explicação extensa privado aumenta limite de saída de Groq/Gemini para 2200, Numes 2600; sem mudança da conversa normal, schema mantém cinco moves x 1200 caracteres. Instrução de cobertura e de declarar insuficiência, não certificação semântica. Testes de limites e guard 413/400 em contextos válidos/revogados/ausentes/outro turno; testes de grace e orçamento.
- Corrigido escopo de import de InboundGenerationLost antes de tratamento e limitado detector de documentação a PERSONAL. CI pendente. RUN01 IN_PROGRESS: faltam roteamento empírico, medição real de bytes/token/latência, compactação mais ampla mantendo memória relevante, geração/validação e checkpoint por seção, perfil de CPU/RAM e certificação no Northflank. Não ativada fila durável, sem deploy ou alteração de main.


## RUN01 B — ranking, envelope e lease

- Baseline 57667d5: 7/7 workflows verdes (Core 45658009850, Provider certification 45658009912). A main segue e11011e, sem conflitos.
- Corrigida lacuna de integração: MessagingStorageLane agora permite renew do lease para o retry síncrono autenticado; antes o guard podia falhar na lane mesmo com autorização válida.
- Ranking empírico por modelo e workload (dialogue/detail/web/visual), máximo 32 chaves x 12 amostras, janela de 30 minutos e mínimo 3 amostras, histerese de 20%; respeita tiers de preferência explícita, roteador, quota e cooldown. Sem sondagens pagas, conteúdo, novos circuit breakers ou alteração de prioridade explícita. Cold start e reinício preservam ranking configurado. Estatísticas process-local.
- Deduplicação lossless de registros inteiramente idênticos em projeções de memória/world/reason_codes e da nota de especialista já presente no TurnFrame nos adapters que de fato enviam esse frame. Sem truncar texto literal nem colapsar evidências distintas. Limite semântico de caracteres para documentação explícita ajustado ao schema (6000), não ao chat normal.


## RUN01 C — geração técnica em seções

- Rota integrada de duas seções em pedido técnico explícito com ao menos duas cláusulas substanciais, exclusivamente texto privado Discord owner, sem mídia/web/temporal/self choices; usa o orçamento/failover/admission já existente. Cada provider recebe texto literal inteiro e escopo da seção, nunca uma mensagem reescrita. Até dois moves autorizados por seção. Validação estrutural, rejeição de repetição exata, sem afirmar validação semântica completa.
- Checkpoint privado em cognitive_journal, novo estágio detail_section_checkpoint (sem nova DDL); hash do contexto integral exceto created_at, sessão/nativo/revisão/escopos/versão, TTL lógico 900s, verificação do lease antes de leitura/geração/persistência/entrega. Reuso somente em contexto idêntico; mudança de contexto provoca regeneração. Prefixo recuperável no mesmo failover e após restart se fingerprint idêntico. Sem persistência canônica ou envio incremental de rascunho. Em caminho síncrono, se orçamento acaba após uma seção, entrega prefixo com aviso explícito de incompletude; caminho durável usa retry existente. Sem prometer exatamente uma entrega externa ou retenção física. Atribuição do plano ao último provedor, podendo haver seções de provedores diferentes; agregação de tokens disponíveis somente de chamadas novas, não métricas físicas completas por seção. CI e testes reais pendentes.

- Hardening C: contabilização de cada seção no ledger pelo provider real e respeito a quota esgotada antes de próxima seção, sem contabilizar reuso como nova chamada; plano atribuído ao último provider com código de mistura. Reuso/expiração/revogação/revisão em SQLite/PG18, caminho síncrono real com fila off, ranking e compactação lossless com bytes comparáveis em fixture sintético. Nenhum resultado de CI deste SHA ainda.

## RUN01 D — prova de recursos

- Probe offline do Core no Dockerfile de runtime, Docker --network none --cpus=0.2 --memory=512m --memory-swap=512m, verifica limites reais cgroup v2 antes de executar. 14 turnos sintéticos (12 normais, 2 detalhados), 16 chamadas ao provider sintético, recibos simulados, nenhuma pendência. Emite RUN01_RESOURCE_JSON com p50/p95, wall/CPU e RSS/cgroup peak. Workflow Core Python 3.12 na branch de estabilização; não é certificação de latência externa/Aiven ou produção. CI pendente, comparar depois com baseline e medir Northflank real. Sem deploy, main ou monitoramento.


### RUN01 D1 — correção do CI 7013806

- 7013806: 7/9 workflows PASS; Core 45671108355 job 139406305250 falhou em test_stabilization_live_dm_section_generation_and_usage (chamadas [0] vs [1,2]) e recuperação SQLite; PG18 45671108285 job 139406312462 falhou na recuperação SQLite/PG18 por KeyError section-primary. Probe de recursos não executado/certificado.
- Causa identificada em inspeção: ContextCompiler sempre projeta relógio normal em temporal_context, que o detector detalhado excluía indiscriminadamente. Diferenciado temporal_opportunity e modos reflexão/autonomia/reminder; relógio ordinário preservado. DialogueOrchestrator reconhece documentação explícita privada antes do fallback social, depois das rotas de identidade, boundary e sinais relacionais, sem ampliar autorização, sem pergunta compulsória. Proteção de contexto reportado/self choices mantida. Novo teste de relógio e plano técnico; teste real original não relaxado.
- Fixture de checkpoint registra ambos providers no roteador antes de contabilizar quota/uso; mantém assert de recuperação, expiração e revogação. Sem alterar thresholds, sem merge/deploy/monitoramento. Novo CI pendente.
