# ADR-0017 — Base oficial Alpine para Core/Web

Status: ACCEPTED. Data: 2026-10-24. Refina ADR-0016 somente no runtime.

## Contexto

A base Debian slim pinada no commit eb03eb8 mantinha oito CVEs HIGH, replicadas
em 44 findings de pacotes binários. Não havia correção para trixie no scan.
Non-root não demonstra ausência de exploração. Exceções exigiriam análise e
manutenção por CVE e por perfil de execução.

## Decisão e evidência

Selecionar Python oficial 3.12.15 Alpine 3.24, digest OCI
`b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d`.
Experimento separado passou 347 testes Python sobre musl, pip check e smoke
non-root/read-only/cap-drop/NNP, Web/auth/pairing, SQLite/restart/SIGTERM.
Cinco testes Avatar passaram em Node 22.23.4. Imagem canônica final foi
reconstruída e revalidada. Trivy 0.76.0 não reporta vulnerabilidades nela;
mesmo gate continua retornando 1 para o Debian e 0 para Alpine.

Não criar VEX/ignore list, trocar scanner ou enfraquecer severidades. Conservar
`PYTHON_BASE` substituível e bloquear quando surgir HIGH/CRITICAL, inclusive
unfixed. Dependências exigem wheels binários; falta de wheel falha build sem
adicionar toolchain. Não migrar o worker vocal ou seus binários para musl.

Relatório, fontes das oito CVEs, comparação e limitações:
[RUNTIME_BASE_DECISION.md](../engineering/security/RUNTIME_BASE_DECISION.md).

## Consequências e revisão

Menos componentes herdados e imagem menor. musl muda ABI, DNS/NSS, locales e
comportamento de threads; plugins nativos, outra CPU e novos digests exigem
revalidação. Só amd64 foi executado; disponibilidade oficial multiarch e wheels
arm64 não certificam execução arm64. Corrigida omissão de empacotamento do
catálogo JSON existente, preservando bytes/parâmetros de voz.

Staging e controles reais do host continuam gates separados, sem deploy nesta
decisão. Reavaliar com shell/console/plugins privilegiados, mount/nsenter/ACL,
homed, TAR Perl/terminfo externo, namespaces/FDs compartilhados, CAP_SYS_ADMIN
ou alteração da fronteira entre Core e worker. Nenhum contrato cognitivo,
social, vocal, Turso, Modal ou provider muda.
