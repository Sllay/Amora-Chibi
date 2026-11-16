# MM.00D.3C.2 — ADR proposto: trabalho episódico durável

Status: C.2A aprovado e integrado (#246); C.2B implementado isoladamente, CI pendente. Issue #238.
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

## Evidência C.2A
- #245 HEAD `925c3fa6bf6a0f13f55026277350ac0cc54d1731`, 3/3 workflows, 7/7 jobs; merge `f81094b08d5d1e9fe44405094c06a5612be98d66`.
- `689243a3`: DDL candidato com jobs e referências de evidências, não registrado em database.migrate/schema.py/recovery.py.
- `31877416`: ID de episódio compartilhado com consolidator, mesma identidade determinística já certificada no #240.
- `45cf1461`: EpisodeWorkRepository, gate existente, enqueue transacional, capacidade ativa global/sessão, links, claim/lease, retry com backoff e tentativas limitadas, cancelamento e ACK por CAS. Nenhum conteúdo duplicado; sem provider/worker/memory writer.
- `5bfca418`: testes isolados SQLite/Hrana, schema oficial 18, reinício e recuperação de lease, ACK antigo rejeitado, capacidade, retry, cancel, corrupt link e dois claimers concorrentes. CI pendente.
- Limites: ACK protegido não equivale a gravação de memória protegida; C.2B precisa fence no MemoryStore e invalidação de fontes, incluindo tombstone e crash antes/depois de commit. C.2C registra migração somente após revisão/certificação PCS. C.3 fecha produtor e reconciliação. Não aplicar DDL à produção, não alterar schema oficial, não ativar worker.

## Atualização C.2B
C.2A aprovado #246 (4 workflows, 8 jobs), merge `e03cf326be80a3d0af97162838d49b180a8ce900`. [Plano/evidência C.2B](MM00D3C2B_EPISODE_FENCE.md): fence e ACK na transação de memória, invalidação direta e testes implementados. CI pendente; schema oficial 18, candidato não registrado. C.2C, C.3 e D permanecem abertos.
