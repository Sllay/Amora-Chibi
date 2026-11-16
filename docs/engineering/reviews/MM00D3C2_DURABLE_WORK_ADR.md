# MM.00D.3C.2 — ADR proposto: trabalho episódico durável

Status: decisão proposta, C.2A em implementação isolada; depende #245 aprovado. Issue #238.
Não alterar schema de produção nem LATEST_SCHEMA_VERSION neste recorte.

## Contexto
D.2 cria memória atômica, idempotente por conjunto de evidências. D.3C.1 seleciona propostas; não cria trabalho durável. Uma falha entre seleção e consolidação pode perder a experiência. A fila ReflectionQueue (schema 18) exige input de conversation_messages e autorização remota; action_plans, speech_utterances e outbound queues têm outras semânticas. Não falsificar inputs ou reutilizar tabelas incompatíveis. O event log possui índice por tipo/tempo, mas não fila due/lease; intents/receipts append-only exigiriam varrer histórico crescente para achar jobs ou ignorar pendências antigas. Não criar outro banco.

## Decisão
Propor duas tabelas pequenas no banco existente: sensory_episode_jobs (ID determinístico do episódio, seleção de IDs e scores, sessão/correlation, status, attempts, timestamps, lease, reason) e sensory_episode_job_evidence (job ID -> 1–4 event IDs; índice reverso para invalidation). Nenhum texto de fala, imagem, áudio, URL ou embedding duplicado. Reusar o padrão de transação/lease/CAS do armazenamento existente. Apenas seleção aceita pelo gate vira job.

C.2A: schema candidato **não registrado**, repository interno com enqueue atômico por ID, limites de pendências global/sessão, claim/lease/attempts/backoff, terminalização, cancelamento e read interno sanitizado. Testes SQLite e Hrana em banco isolado aplicando explicitamente o DDL candidato; não iniciar worker ou mudar versão 18 no Core.

C.2B: fence da gravação create-only em MemoryStore na mesma transação que valida lease/status e evidências; recuperação após commit antes de ACK; invalidation por evidence ID, cancelar jobs e arquivar memória derivada de fonte invalidada, tombstone durável antes de apagar fonte. Não permitir stale worker criar memória depois de cancelamento. Definir retenção de jobs terminais com proteção contra reprocessamento de fontes removidas. Testes de crash, concorrência, cancelamento antes/depois de commit, replay e recall.

C.2C: somente após C.2A/B, registrar migração aditiva no schema e revisar compatibilidade de restore. Atualmente PCS.4C verify_snapshot exige LATEST_SCHEMA_VERSION=18; atualizar para 19 sem planejar os backups invalidaria a certificação exata do snapshot anterior. Manter o snapshot v18 e a imagem pinada que o certificou; teste de upgrade 18→19 em cópia isolada, preservação integral das 51 tabelas anteriores (exceto registro de migration), teste backup/restore/restart do novo schema, plano de rollback por imagem compatível + backup. CI SQLite/Hrana, testes PCS e prova operacional em ambiente isolado antes de ativar a migração em produção. Nenhuma mudança no Turso atual por este ADR.

C.3: produtor real CH2/call usa seleção efêmera antes de persistir; transação de publicação e enqueue ou reconciliação durável do crash gap, limites por sessão e política de retenção, integração de worker com shutdown/restart. Não marcar C completo enquanto não fechar esse ciclo.

## Gate
C.2A é contrato/repository em draft, não fila operacional nem mudança de schema. Testes sintéticos não certificam Turso real. Documentar commits, resultados e limites no #238.
