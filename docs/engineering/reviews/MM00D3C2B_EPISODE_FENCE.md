# MM.00D.3C.2B — Fence de gravação e invalidação

Status: em implementação. #246 aprovado (4 workflows/8 jobs, HEAD f20ff0438b2803333ce3550165f5b0aeb0cddfa5, merge e03cf326be80a3d0af97162838d49b180a8ce900). Issue #238.
Schema oficial 18, DDL candidato somente em testes isolados.

## Plano antes do código
1. MemoryStore: gravação create-only de episódio com lease/token/attempts e seleção persistida. Revalidar job, IDs de evidência, sessão/correlação, tombstones e existência das fontes dentro da mesma transação de escrita. ACK de sucesso/duplicata na mesma transação do registro e índices: elimina janela commit→ACK; perda da resposta após commit não regrava. Se lease expirar durante escrita, rollback. Sem alterar escritores legados.
2. Repository: método de processamento de claim reconstrói candidato pelo consolidator e gate existentes, não aceita texto arbitrário. Rejeição de gate tem ACK CAS; falhas de storage propagam para retry/reconciliação. ACK manual de stored/duplicate não permitido. Cancelamento e stale token não gravam. D.2 chamada explícita sem lease permanece disponível apenas quando schema candidato não está instalado.
3. Invalidation: tombstone durável por event ID, cancelamento de jobs associados (inclusive concluídos) e arquivamento das memórias episódicas relacionadas, tudo na mesma transação. Bloquear enqueue futuro da fonte; preservar evidência e tombstone, sem apagar conteúdo nesta etapa. Arquivamento não é eliminação física nem revogação retroativa de respostas enviadas. Limite de fanout por operação será explicitado, sem truncar silenciosamente. Não prometer invalidação transitiva de outras memórias derivadas; C.2C/3 deve fechar política e produtor real.
4. Testes SQLite/Hrana: sucesso atômico, rollback em índice/ACK, crash/reopen, stale/expired/cancelled lease, duplicata pré-existente, invalidação antes/depois de commit, replay, privacidade e concorrência. Nenhuma migração registrada, worker ativo ou deploy.

Decisão: ACK atômico é preferível a recuperar uma janela de duas transações; preservar padrão BEGIN IMMEDIATE existente. C.2C continua responsável por schema 19 e certificação PCS; C.3 por publicação/enqueue atômicos e integração de revogação dos transportes.
