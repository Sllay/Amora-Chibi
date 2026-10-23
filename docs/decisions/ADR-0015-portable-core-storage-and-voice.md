# ADR-0015 — Core, persistência e voz com hosts substituíveis

Status: ACCEPTED (direção explicitamente solicitada por BK em 2026-10-23).
Data: 2026-10-23. Refina ADR-0013/0014 apenas quanto ao host; preserva
ADR-0010/0011 e autoridade semântica ADR-0003/0006/0007.

## Contexto e evidência

Termux/A15 deixa de ser host principal. A auditoria em
`docs/engineering/CLOUD_MIGRATION_AUDIT.md` identifica conexões SQLite,
assets externos, lifecycle e renderer acoplados ao processo local.
Migrar sem essas fronteiras colocaria continuidade persistente em risco.

## Alternativas

Reescrever o Core; trocar SQLite por HTTP SQL por substituição textual;
manter celular como servidor; ou extrair fronteiras conservando domínio/testes.
Escolhida a última alternativa.

## Decisão

Core/Web em container Linux, candidato Koyeb. StorageBackend local SQLite e
remoto libSQL (candidato Turso). VoiceBackend local e remoto (candidato Modal),
mesmo Supertonic 3 INT8, style 60/40, FINAL_B_PLUS60, Signature V1 Soft e
expressão bounded. A15 passa futuramente a Living Console/controlador.
Safari permanece superfície principal. Hosts não pertencem à identidade.

Estado, privacidade, intenção, memória, emoção, relações e delivery pertencem
ao Core. Worker recebe só texto/expressão de renderização e autenticação de
serviço; não recebe token de usuário, contexto cognitivo ou provider secrets.
Render WAV não é ACK. Falha de voz deixa texto funcional, sem trocar TTS.

## Consequências e gates

SQLite local/offline permanece default para desenvolvimento. Cloud só deve
ser usado com armazenamento durável, token injetado, TLS, uma réplica e backups
restauráveis. Não converter falha remota em banco local vazio. A direção está
aceita; certificação real Koyeb/Turso/Modal e acústica continua pendente.
Detalhes de implementação e limites devem ser atualizados a cada etapa.

Não muda personalidade, roadmap de percepção/estética, semântica social,
modelos cognitivos ou parâmetros vocais. Revisitar hosts por custo/latência,
licença, disponibilidade ou evidência de falha; nunca redefinir Amora para
acomodar infraestrutura.
