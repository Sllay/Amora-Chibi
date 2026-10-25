# ADR-0020 — Recovery de sessão libSQL e fronteira do usage ledger

Status: ACCEPTED por solicitação explícita de BK.
Data: 2026-10-25. Refina o lifecycle remoto da ADR-0015.
Base: main ae3b2f6. Preserva semântica de domínio, schema e contratos de voz/Web.

## Contexto, método anterior e evidência

Staging Northflank/Turso relatou falha HTTP em `CognitiveStore.record_usage`,
POST /v1/audio/transcribe, stage conversation, correlation
600bf18d-3302-4cca-bf56-18f4f0ac687c. O adapter corretamente levantou
SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN e invalidou o stream. Mas cada Store mantém
uma RemoteConnection durante toda sua vida, e o flag broken era terminal.
As tentativas seguintes falharam em get_message_by_dedupe/SQL_CONNECTION_CLOSED.
Não era falha de STT nem evidência de Turso permanentemente offline.

Os testes antigos fechavam a conexão após falha e alguns esperavam explicitamente
o fechamento permanente. Faltava a operação independente seguinte no mesmo Core.

## Alternativas

Restart do pod por qualquer erro; reconnect específico em cada Store; retry do
write/COMMIT ambíguo; ou recovery lazy do transporte na conexão existente.
Escolhido o último. Retry de operação ambígua é proibido; substituir dez objetos
de domínio duplicaria políticas e quebraria referências compartilhadas, como
SQLBlobStore.connection, sem resolver o ownership do lifecycle.

## Decisão: operação falha, sessão futura pode recuperar

RemoteConnection mantém identidade e faz recovery apenas ao entrar numa nova
operação, quando broken=true, transaction=false e closed=false. Fecha/descarta
o httpx.Client antigo, cria outro com as mesmas opções seguras e inicia stream
com baton=null e PRAGMA foreign_keys=ON. Não guarda SQL/argumentos para replay.
Em produção, o transporte HTTP/pool default também é criado com o client novo.
O transport injetável dos testes continua sendo o mock reutilizável sem rede.

| Estado | Comportamento |
|---|---|
| Saudável | Fluxo atual, incluindo COMMIT/ROLLBACK válidos com baton nulo |
| Falha de transporte | Resultado da operação atual continua desconhecido; poison |
| Broken e transação ativa | Nenhuma nova sessão, nenhum novo request SQL |
| Contexto abandonado | Flag transacional limpo; erro original não vira sucesso |
| Erro engolido dentro do contexto | Saída normal do contexto falha fechado |
| Próxima operação independente | Client/stream novos, sem reutilizar baton |
| Explicit close | Terminal, zero reabertura |

BEGIN faz o teste de recovery antes de ativar transaction. Falha no próprio BEGIN
abandona o contexto sem chamar close terminal. executescript abandona somente
uma transação que ele próprio abriu; não libera uma transação externa quebrada.
COMMIT/ROLLBACK ambíguos nunca são reenviados. Construtor de client que falha no
recovery gera SQL_CONNECTION_RECOVERY_FAILED, sem enviar a nova operação.

Poison e recovery geram logs seguros com connection_id/generation e machine
codes. Poison inclui classe da exceção, sem mensagem/cause/SQL. Recovery só é
anunciado depois de bootstrap, resultado SQL e continuidade do stream validados.
Nenhum segredo, URL, baton ou conteúdo privado entra nesses campos.

## Fronteira explícita para telemetria

CognitiveStore.record_usage continua fail-closed, com as mesmas transações.
O novo UsageRecorder central, usado por todos os caminhos cognitivos de usage,
captura apenas exceções DB-API dessa chamada. Não envolve journal, mensagens,
memória, world, fila ou qualquer outro write obrigatório. Erros de programação
fora da fronteira DB-API continuam propagando.

Resultado incerto gera USAGE_LEDGER_PERSISTENCE_UNCERTAIN e status DEGRADED;
rejeições conhecidas geram USAGE_LEDGER_PERSISTENCE_FAILED. Não há retry,
compensação com success=false, fila de replay, contador sintético no ledger ou
declaração de que uma escrita ambígua foi aplicada. A realização estruturada já
validada pode continuar; sua próxima operação SQL é independente e pode recuperar.
Se outro write obrigatório falhar, a request continua falhando fechado.

Estado bounded no processo e OperationMonitor autenticado expõem contagem,
fingerprint, turn ID e horários via Clock. Uma escrita posterior confirmada não
reconcilia uma anterior incerta nem limpa automaticamente o estado degradado.
Reiniciar zera essa projeção operacional, não corrige a completude histórica do
ledger; logs são evidência separada. Não se mudou schema para esse contador.

Quota/cooldown continuam usando sinais efetivamente recebidos do provider.
remaining_requests/tokens esgotados continuam bloqueando mesmo com usage incerto.
Não há inferência de quota infinita a partir de lacuna no ledger. O fallback de
reflection só consulta status persistido do próprio turno, evitando emprestar
um 429 antigo após perder o registro atual. Ordem/custo/guards do Provider Mesh
permanecem intactos. test_mode e sua exclusão do histórico/reflection não mudam.

## Fonte e limites

[Hrana HTTP v2 oficial](https://github.com/tursodatabase/libsql/blob/main/docs/HTTP_V2_SPEC.md),
consultado em 2026-10-25: baton=null solicita stream novo; requisições no mesmo
stream são seriais; HTTP 4xx/5xx invalida stream; streams abandonados expiram no
servidor. O client não tenta encerrar uma sessão desconhecida reutilizando baton.
Expiração/locks e recuperação de rede reais seguem sujeitos ao serviço; falha
persistente continua produzindo erro, sem fallback para SQLite local vazio.

## Consequências e critério para revisitar

Todas as nove owners de conexão e SQLBlobStore preservam referências sem hacks
de reconexão no domínio. Bootstrap, migrations, FTS e backup não ganham replay
parcial: uma operação que falhou continua falha. Testes exercitam SQL real,
batons rotativos, perda antes/depois de execução e dois STTs sem restart.

Não equivale a certificação física da revisão em Northflank/Turso. Reavaliar se
mudar contrato transacional, lifespan/compartilhamento de client, política de
retry, quota baseada em ledger completo, ou surgir reconciliação durável.
