# MM.00D.3C — Seleção relevante e recuperação durável

Status: C.1 aprovado e integrado (#245); C.2A implementado isoladamente, CI pendente. Issue #238.

## Auditoria e decisão
MemoryWriteGate já possui pesos/thresholds e MemoryService protege proveniência; D.2 consolida evidências existentes e create-only atômico. ReflectionQueue referencia conversation_messages, platform_messaging e consentimento remoto; não reutilizar falsificando inputs. O event log tem índice (event_type,timestamp), mas não status/available_at/lease para job de episódio. Uma fila nova não deve transformar toda observação em trabalho durável. C.2 precisa de ADR comparando mecanismo existente, log de intents/receipts com scan bounded e tabela mínima de trabalho no mesmo banco (não outro banco). Qualquer migração tem gate PCS/backup/restore e revisão antes da ativação; nenhuma alteração de schema em C.1.

## C.1 — política de seleção
Entrada interna: até 8 propostas de EpisodeSelection vindas do Core, destinatário/sessão/correlation explicitamente autorizados. Não atribuir relevância a cada frame nem aceitar scores do worker como autoridade. Revalidar modelos e evidências via D.2; rejeitar escopo/identidade/ausência/desconhecido/limites com códigos seguros; falha de DB propaga, não fingir falta de relevância. Usar o MemoryWriteGate existente, sem outra fórmula. Ordenar por score, confiança, quantidade de fontes e UUID determinístico. Escolher até 4 episódios por lote, dedupe exato e não repetir evidência entre seleções do lote; sobreposição parcial é deferida, não apagada/truncada. Retornar índices/status sem conteúdo; sem persistir/consultar provider. Limites são de lote, não quota durável nem dedupe semântico. Testes gate, prioridade determinística, overlap, orçamento, privacidade, sessão, dados faltantes, falha e zero writes.

## C.2 — durabilidade
Definir unidade de trabalho por seleção/IDs, idempotência, bounded pending, claim/lease/CAS, terminal outcome, retries/backoff, invalidation e recuperação após crash. D.2 create-only protege memória mas não garante que o job será retomado. Não declarar C completo enquanto isso não passar. C.3: produtor real/admissão e política de persistir somente o selecionado; selecionar amostras efêmeras antes de gravar eventos para não poluir o log. C.1 opera sobre observações já selecionadas/persistidas em D.2, não autoriza gravar todos os frames.

## Evidência C.1
- `e1e66a1c` / `7399eefa`: EpisodeBatchSelector revalida propostas, lê evidências via consolidator existente, rejeita escopo/destinatário incorreto e falhas de relevância, usa score do gate; desempate determinístico, dedupe por conjunto de IDs, overlap e orçamento deferidos. Retorno contém apenas propostas autorizadas e status, nenhum conteúdo bruto. Até 8 propostas/4 seleções; leituras de até 32 evidências por lote (sem medir latência remota), nenhuma escrita.
- `211f7fad`: testes SQLite real, gate, ordem determinística, dedupe, overlap, orçamento, privacidade, sessão, dados faltantes/forjados, falha de storage e contagem de memórias inalterada. CI aprovado no HEAD `925c3fa6bf6a0f13f55026277350ac0cc54d1731` (7/7 jobs, 3 workflows); merge `f81094b08d5d1e9fe44405094c06a5612be98d66`.
- Não há produtor ativo, ranking automático de frames brutos, quota persistente, dedupe de episódios entre lotes ou fila durável. C.2 precisa ADR, C.3 integração de produtor e retenção. Sem schema/produção.

C.2A: [ADR e evidência](MM00D3C2_DURABLE_WORK_ADR.md), repository e DDL candidato implementados isoladamente, CI pendente. C.2B/C/C.3 permanecem abertos.
