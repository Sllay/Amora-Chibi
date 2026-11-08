# Fase 2 — execução incremental

Base: audit/phase1-core-forensics 452a4d9 (ref PR166 confirmado via Git).
Main remoto7504d23 não contém auditoria; branches empilhadas preservam testes.
Estado/CI de PR166 não consultável: API GitHub GraphQL Forbidden. Nenhum merge/deploy.
Relatório Fase1 preservado como evidência histórica.

## A — privacidade e integridade

Antes: reprodução original --runxfail confirma11falhas/6pass (log local
/tmp/phase2-before.log). Baseline adversarial agente17fail/8pass.
Correções: AUTH/SECRET redigidos antes de inferência, floor, evento e retorno;
extra_payload descartado para escopos protegidos; messaging bloqueado antes
attention/audit; áudio protegido bloqueado antes de cache/STT; metadata de
histórico protegido descartada. Nenhum segredo real usado.
Atribuição separada em language/attribution.py: spans/atores/destinos e cláusulas,
sem lista de frases excepcionais. Report e disclosure não concedem escolha.
Commit recompila elegibilidade e confere evidência vinculada a texto/turno/ator/
destino/versão e consentimento/escopo; modo teste continua sem escrita.
As cinco reproduções A deixam de ser xfail somente após passarem.
Suites A:132pass/6xfail antes do último endurecimento de atribuição; rodada
específica posterior63pass, sem falhas. Xfails restantes pertencem aos blocos B–D.
Risco residual: atribuidor é gramática local conservadora, não parser geral;
ambiguidade nega preferência persistente sem proibir expressão adulta.

## B — transporte

F06 reprodução inicial falha; após catch tipado NvidiaStreamTimeout antes do
TimeoutError do caller:48pass/5xfail em forense+provider+SSE/budget.
Código NVIDIA_IDLE_TIMEOUT ou NVIDIA_TOTAL_TIMEOUT, etapa sanitizada e backoff
ficam no journal; caller curto mantém SURFACE_BUDGET_TIMEOUT sem health poison.
Fila Web agora inclui generation(fallback/provider/reasons); UI mostra fallback
como recuperação, sem fingir rejeição estrutural. Node Web executado.
