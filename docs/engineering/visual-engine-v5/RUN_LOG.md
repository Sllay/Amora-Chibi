# Visual Engine V5.0 — RUN LOG

## Estado inicial (25/11/2026)
Planejamento aprovado: 4 RUNs (1+2; 3+4; 5+6; 7 expandida). Nenhuma implementação V5 iniciada. O objetivo mudou de laboratório Canvas manual para runtime MOC3 compartilhado entre live e Companion. V4.5 permanece fallback; PR experimental #370 preservado. Limites 0,2 vCPU / 512 MB. Não criar monitoramento automático.

| RUN | Estado | Evidência |
|---|---|---|
| 01 | IN_PROGRESS | Branch feat/visual-engine-v5-run01 |
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

## RUN 01 — implementação inicial
- Framework oficial 5-r.5 fixado no commit 198a3769c26ca3d7b600e932590433badd392edd; Core oficial família 06 apenas em ambiente de desenvolvimento, hash a medir e fixar; licença de publicação ainda não certificada.
- Modelo exato V4.5: workflow 45534504067, artefato 14986551731, source 333b935d12e7b82397efbc4d797c471e1aec05f1.
- C01–C04: implementação de laboratório WebGL2, referências do modelo, física, motions, expressões, parâmetros, perfis e recuperação; validação CI pendente. Não houve alteração do servidor. Testes físicos adiados por instrução do usuário para RUN 04.

- Primeira CI 45732022065: download exato do modelo e Core, build passaram. Browser falhou ao iniciar com null.length; adicionar stack e fase para identificar causa, sem reduzir gate. Core SHA-256 fixado: 8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47; MOC3 dd23be7602621ea558b3bca17e2a23ebadb4b5d9b5a08eb73b96169b114e5cc0.

- CI 45732160043 (b815edb): model digest, Core digest, build and textures succeeded; browser failed at first frame in CubismMotion.doUpdateParameters reading null.length. Official pinned framework initializes EyeBlink/LipSync arrays to null and requires setEffectIds. Fix initializes both arrays from model3 Groups (empty arrays when absent), validates declared parameter IDs and calls setEffectIds on every motion before scheduling. Add explicit empty favicon to avoid unrelated browser 404. Revalidation pending; original assertions preserved. Security checks image/dependencies passed; Altare separate failure not certified here.
