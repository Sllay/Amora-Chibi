# Visual Engine V5.0 — RUN LOG

## Estado inicial (25/11/2026)
Planejamento aprovado: 4 RUNs (1+2; 3+4; 5+6; 7 expandida). Nenhuma implementação V5 iniciada. O objetivo mudou de laboratório Canvas manual para runtime MOC3 compartilhado entre live e Companion. V4.5 permanece fallback; PR experimental #370 preservado. Limites 0,2 vCPU / 512 MB. Não criar monitoramento automático.

| RUN | Estado | Evidência |
|---|---|---|
| 01 | PLANNED | Pendente |
| 02 | PLANNED | Pendente |
| 03 | PLANNED | Pendente |
| 04 | PLANNED | Pendente |

## Formato de checkpoint
- RUN / Cxx / data / estado (PLANNED, IN_PROGRESS, BLOCKED, PASS, FAIL).
- Escopo entregue, arquivos e commit/PR.
- Comandos/testes, workflow/job/artefato e métricas observadas.
- Falhas, causa, correções e revalidação.
- Decisões, licenças, limitações e testes físicos pendentes.
- Gate, rollback e próximo checkpoint.

Não preencher PASS sem evidência. Histórico cumulativo; não apagar falhas anteriores.
