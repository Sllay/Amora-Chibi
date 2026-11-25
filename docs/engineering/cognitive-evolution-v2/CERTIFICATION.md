# RUN 07 — matriz de integração e certificação

Estado: IN_PROGRESS / LIVE_PENDING. PR #388. Base 1638748 (RUN06 11/11).
Não é aprovação para ativação ou deploy. Cada linha exige evidência no SHA
candidato e, quando aplicável, em ambiente isolado antes da produção.

| Contrato | Evidência existente | Bloqueio ainda aberto |
|---|---|---|
| R01 fila textual | testes SQLite/PG18, atomicidade, restart/restore, ACK | carga prolongada, ACK físico, rollback/drain live, limite de fila |
| R02 providers | mocks de quota, backoff, 413, circuit e recuperação | cobertura completa de adapters, limites reais, latência/quotas, cancelamento |
| R03 compreensão | offsets, mapa global experimental, fallback Groq, testes Unicode; candidato para overflow pré-seleção em 2deeef2 | CI da correção, leitura sob demanda, geração por partes e avaliação semântica |
| R04 percepção | lote até 3 imagens e checkpoint exato/retry com guarda CI dc08bdf 13/13; candidato 589901f continuidade explícita entre turnos | CI da continuidade, retenção física, referência implícita, avaliação semântica e prova live |
| R05 web | busca aprofundada limitada, fontes parciais, notebook; candidato 9eed31e para retomada explícita após gravação e TTL de reutilização | CI, checkpoint durante pesquisa/por turno, retenção/revogação, contradições, quotas, screenshots e SSRF completo |
| R06 diálogo | snapshot causal, janela curta, followups separados | atualização durante geração, supersessão atômica, ACK/retomada por segmento, voz/avatar |
| R07 integração | diagnósticos iniciais e teste de burst/restart | fechar todos os bloqueios acima, medir recursos, certificar Northflank |

## Método da bateria

1. Congelar SHA candidato e base de comparação, schema efetivo e configuração
   sanitizada. Verificar árvore, workflows e migrations; não ativar produção.
2. Laboratórios SQLite e PostgreSQL 18 descartável com dados fictícios.
   Casos: burst, redelivery, conflito de leases, 413/429/503 simultâneos,
   queda após checkpoint antes da fila, falha de ACK, restart e restore,
   edição/exclusão, revogação, texto longo/negações, foto+legenda, web parcial,
   memória/identidade, interrupções e voz. Usar relógio controlado onde possível.
3. Após fechar contratos, executar teste de duração e carga em ambiente
   equivalente ao alvo inicial de 0,2 vCPU / 512 MB. Comparar a mesma carga,
   volume de memória, versões, configuração e sequência de falhas. Medir
   CPU/RSS/lag, p50/p95 de etapas e turno, TTFS validado e entregue,
   operações DB, chamadas e tokens por tarefa (estimativa identificada).
   Nenhuma meta de redução ou throughput é resultado até medição.
4. Northflank: confirmar backend PostgreSQL, backup nativo e restore em
   destino separado, configuração/schema e um único gateway Discord. Teste
   de staging sem disputa com gateway real, consentimento e política ativos.
   Capturar confirmação física de texto, anexo, voz e avatar; não usar
   outbox ready como entrega. Se não houver acesso, LIVE_PENDING/BLOCKED.
5. Rollback: interromper novas admissões sem desligar recuperação, drenar
   pendentes/ready, verificar zero unsettled e entregas reconciliadas,
   conservar backup e usar procedimento revisado da RUN01. Nunca fazer
   downgrade destrutivo nem reexecutar mensagens enviadas.

## Métricas iniciais

Contagens persistentes da fila representam estados mutuamente exclusivos
dos jobs existentes; ready significa pronto para transporte, sent depende
de ACK. Receipts são observações do processo (incluindo redelivery), não
total de mensagens distintas. Tentativas são observações do dispatcher
por tentativa concluída/cancelada; recovered_ready significa tentativa >1
com estado ready, não prova ACK. p50/p95 são nearest-rank sobre as últimas
256 durações de tentativa, não latência end-to-end nem TTFS. Reinício zera
telemetria local; contagens duráveis permanecem. Não registrar conteúdo,
credenciais, prompts, mídia, URLs ou identificadores nativos.

## Critério de saída

Todas as pendências acima fechadas e evidências com SHA, ambiente,
denominadores, resultados negativos e links. Zero perdas/duplicatas
nos cenários definidos; ausência de truncagem silenciosa e regressões.
Se faltar evidência, não declarar COMPLETED/LIVE_CERTIFIED.
