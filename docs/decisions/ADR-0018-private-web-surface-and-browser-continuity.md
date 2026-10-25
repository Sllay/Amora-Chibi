# ADR-0018 — Superfície Web 1:1 e continuidade no navegador

Status: ACCEPTED por solicitação explícita de BK em 2026-10-25.
Refina ADR-0016 apenas para credencial Web/persistência local; preserva seus
outros controles. Não muda GDD, AddresseeEngine, memória ou identidade vocal.

## Contexto e decisão

Staging confirmou erro no journal cognitivo, falta de destinatário em mensagens
Web sem vocativo e perda de pareamento/conversa ao reabrir Safari.

O journal declara idempotência no próprio INSERT com
`ON CONFLICT(dedupe_key) DO NOTHING`. O resultado só é reportado após COMMIT,
usando rowcount 1/0. Removida a inferência global de UNIQUE pelo texto de erro
Hrana. Outras constraints e resultado ambíguo continuam falhas, sem retry.

Texto da caixa Web e áudio explicitamente identificado como WEB_SAFARI usam
`LanguageSocialPipeline.explicit_addressee_id="ENTITY_AMORA"`, com razão
EXPLICIT_SURFACE_TARGET persistida no evento. Isso expressa intenção da
superfície, sem prefixar/reescrever texto ou relaxar inferência de áudio ambiente.
Speaker conhecido, sozinho, não habilita o override. A15 continua conservador.
Um target privado não ocupa/limpa o floor físico compartilhado nem estabelece
ou consome a janela de continuidade ambiente. A metadata de transporte
explicit_surface_target permite ao Core preservar essa fronteira: conversar
com Amora pela Web não autoriza resposta a um “oi gente” posterior no A15,
nem interrompe um floor ambiente já dirigido a terceiros.

## Pareamento persistente e threat model

Substituir, para novas credenciais Web, o TTL de 12h por bearer durável
`browser.v2`, assinado por HMAC e contendo exclusivamente role web + nonce
aleatório. O segredo de assinatura administrativo não sai do Core. A autorização
existente continua limitando esse bearer a rotas Web e utterances WEB_SAFARI;
ele não acessa admin, avatar, memória/journal privado ou DEVICE_A15.
Web local também recebe credencial limitada, nunca LOCAL_API_TOKEN.

Persistir esse bearer, e somente ele, em `localStorage[amora.web.pairing.v2]`.
Não há cookie ambient, nem refresh token/provider key/Turso token no navegador.
Não existe expiração temporal nessa versão: revogação global continua sendo
rotação do segredo de assinatura pelo operador, mecanismo já existente e não
executado nesta missão. Esquecer remove a cópia local, não é revogação remota
de uma cópia eventualmente extraída. Revogação individual é trabalho separado.

Esta decisão substitui explicitamente a seção sessionStorage/TTL Web da
ADR-0016 conforme pedido de BK. Um XSS na mesma origem pode ler bearer/histórico;
persistência aumenta a duração desse risco. CSP, TLS cloud, escopo mínimo,
ausência de segredo administrativo, headers/CORS/origin/rate/body gates continuam.
Não adicionar script remoto/eval/HTML de conversa ou liberar endpoints admin.

O browser aceita persistir apenas o formato Web v2; a autenticação da assinatura
é sempre do Core. Valores legados em amora.web.token.v1, inclusive sessionStorage,
não são migrados porque poderiam ser administrativos: exige pareamento único
após upgrade. O servidor ainda aceita Web v1 limitado até sua expiração original.
Avatar v1 e seu TTL 12h não mudam. Reinício com a mesma chave do Core preserva v2.
Falha temporária/rede/HTTP 500 não remove pareamento; HTTP 401 remove a cópia.

## Histórico local e ações independentes

`web/browser-state.mjs` mantém JSON versionado em amora.web.history.v1. Apenas
role, texto e opcional ID da utterance são serializados. User/Amora representam
bolhas visíveis, não turnos cognitivos, Memory ou fatos canônicos. Não há envio
ou replay do histórico ao Core; restauração somente desenha texto com textContent.

Por append e por load, percorrer do mais recente ao mais antigo, conservar no
máximo 50 de cada role e inverter o resultado para preservar cronologia. Logo
total <=100. IDs de utterance evitam bolhas repetidas por restauração/reclaim;
texto igual sem ID pode ser uma mensagem nova e não é descartado por conteúdo.

System, erros, diagnósticos e metadados técnicos não entram no JSON. AUTH/SECRET
e material reconhecível de credencial são excluídos da persistência visual.
Isso não é um classificador universal de segredos colados como texto arbitrário;
tokens de infraestrutura nunca são fonte dos dados do histórico. Histórico é
texto local do usuário e da Amora, sujeito à segurança/remoção dos dados do site.

Dados malformados limpam apenas a chave de histórico inválida. Quota/storage
indisponível não quebra a página; falha de salvar pareamento é diagnosticada.
Limpar conversa remove o JSON e as bolhas, mantém pairing, não chama APIs de
Memory/Turso e não limpa estado canônico. Esquecer pareamento não limpa histórico.
Texto continua sendo exibido e salvo antes do fetch TTS, sem fallback oculto.

## Gates e reavaliação

Testes locais/CI não encerram o incidente físico. Confirmar em Northflank real:
dedupe sem 500, mensagens sem vocativo, STT, reload/reopen Safari pareado com
histórico, limites 50+50, ações independentes e restart com chave estável.
Worker Supertonic real permanece gate separado; nada é instalado no Core.

Reavaliar com múltiplos usuários/origins, revogação por dispositivo, mudança de
escopos, scripts de terceiros, sincronização de histórico ou alteração das
garantias de rowcount/stream do backend SQL.
