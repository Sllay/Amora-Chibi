# MM.00D.3A.2 — Identidade e consentimento de plataformas

Status: implementado, CI pendente; dependência #241 aprovada e integrada. Issue #238.
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

## Evidência de implementação
- #241 D.3A.1: 3/3 workflows, 7/7 jobs, HEAD `ac1f8282b446f9f6fc908120a61899bdb4848bc9`, merge `f39f182cfe46776cf2189e04b10a951e204a270c`.
- `181fc2a2` / `d9608a1c`: autoridade interna com roster bounded, resolução/revalidação por binding verificado, consentimento individual, escopo de conta/conversa, revogação coletiva, expiração e restart fail-closed. Cada participante/modalidade tem stream próprio, mesma correlation da sessão. Política remota é interseção; mudanças de política exigem nova sessão. IDs nativos ficam apenas no registro efêmero; contexto persistível contém identidade canônica.
- `e8397589`: testes SQLite para Discord e WhatsApp, JID/LID, alias inválido, binding observado, conta/conversa/roster, consentimento parcial, remote AND, revogação, capacidade, expiração e restart. CI pendente.
- Revalidação usa leituras existentes de binding por participante; não executar a cada frame/amostra bruta. Medir custo e definir unidade de admissão por segmento/evento em D.3B/D.3D. A autoridade depende de roster/principal autenticado e atualizado fornecido pela fronteira de transporte futura; não é prova de consentimento real do Discord/WhatsApp. Sem endpoint, captura, provider ou deploy.
