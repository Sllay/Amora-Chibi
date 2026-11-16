# MM.00D.3C — Seleção relevante e recuperação durável

Status: C.1 em implementação isolada; depende #244 CI/merge. Issue #238.

## Auditoria e decisão
MemoryWriteGate já possui pesos/thresholds e MemoryService protege proveniência; D.2 consolida evidências existentes e create-only atômico. ReflectionQueue referencia conversation_messages, platform_messaging e consentimento remoto; não reutilizar falsificando inputs. O event log tem índice (event_type,timestamp), mas não status/available_at/lease para job de episódio. Uma fila nova não deve transformar toda observação em trabalho durável. C.2 precisa de ADR comparando mecanismo existente, log de intents/receipts com scan bounded e tabela mínima de trabalho no mesmo banco (não outro banco). Qualquer migração tem gate PCS/backup/restore e revisão antes da ativação; nenhuma alteração de schema em C.1.

## C.1 — política de seleção
Entrada interna: até 8 propostas de EpisodeSelection vindas do Core, destinatário/sessão/correlation explicitamente autorizados. Não atribuir relevância a cada frame nem aceitar scores do worker como autoridade. Revalidar modelos e evidências via D.2; rejeitar escopo/identidade/ausência/desconhecido/limites com códigos seguros; falha de DB propaga, não fingir falta de relevância. Usar o MemoryWriteGate existente, sem outra fórmula. Ordenar por score, confiança, quantidade de fontes e UUID determinístico. Escolher até 4 episódios por lote, dedupe exato e não repetir evidência entre seleções do lote; sobreposição parcial é deferida, não apagada/truncada. Retornar índices/status sem conteúdo; sem persistir/consultar provider. Limites são de lote, não quota durável nem dedupe semântico. Testes gate, prioridade determinística, overlap, orçamento, privacidade, sessão, dados faltantes, falha e zero writes.

## C.2 — durabilidade
Definir unidade de trabalho por seleção/IDs, idempotência, bounded pending, claim/lease/CAS, terminal outcome, retries/backoff, invalidation e recuperação após crash. D.2 create-only protege memória mas não garante que o job será retomado. Não declarar C completo enquanto isso não passar. C.3: produtor real/admissão e política de persistir somente o selecionado; selecionar amostras efêmeras antes de gravar eventos para não poluir o log. C.1 opera sobre observações já selecionadas/persistidas em D.2, não autoriza gravar todos os frames.
