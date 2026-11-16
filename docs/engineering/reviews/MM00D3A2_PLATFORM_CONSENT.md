# MM.00D.3A.2 — Identidade e consentimento de plataformas

Status: plano pré-código; dependência #241 CI/merge. Issue #238.
Escopo interno: Discord/WhatsApp, imagem/fala, sessão privada ou grupo, sem endpoints ou captura real. Não confundir sessão de transporte com autorização. O worker autenticado ainda precisa ser ligado em D.3D.

## Critérios
- Usar PersonProfileService e bindings verificáveis por platform/account_scope/id nativo; Discord snowflake, WhatsApp JID/LID canônico. Não aceitar username/display name, PERSON_* do worker, nem vínculo observado como autoridade.
- Sessão emitida pelo Core com conjunto bounded de participantes nativos resolvidos; IDs de streams por participante/modalidade; correlation compartilhada; session_id canônico messaging_session_ref. Validar escopo, conta e conversa em cada chamada.
- Consentimento individual explícito por ator autenticado, associado à sessão e às modalidades. O Core não presume que o moderador consente por terceiros. Grupo: todos os participantes humanos da captura devem consentir antes de abrir processamento; política remota é AND. Revogação, alteração de membros, expiração ou mudança/desativação de binding invalida a sessão. Sem captura de tela nesta autoridade, que é um grant de device/ingest próprio.
- Contexto emitido com ator resolvido para fala, audiência apenas participantes consentidos; DM PERSONAL/PRIVATE conforme política (PRIVATE nunca remoto), grupo SOCIAL; não ampliar audiência. Sem publicação/persistência automática.
- Capacidade e TTL bounded, memória efêmera/restart fail closed. A foto já enviada via mensagem tem seu próprio contrato de consentimento de mídia pontual em D.3D; não exigir que todos os membros de um servidor autorizem uma imagem enviada por DM.
- Testes: SQLite com bindings reais, identidade nativa/alias incorreto, binding observado/desabilitado, conta/sala errada, dois falantes distintos, consentimento parcial, remote AND, revogação, alteração de membros, expiração e restart.

## Limites
Esta etapa exige que o chamador interno obtenha identidade autenticada e membership atual do transporte. Não cria uma prova de consentimento só por receber booleano de rede. Sem chamada Discord real, WhatsApp Calling, provider ou migração. D.3B/C/D seguem separados.
