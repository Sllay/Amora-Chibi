# Staging: journal idempotente, Web 1:1 e continuidade local

Base: main / `3683ea2ea98e6ea70974813f1ddefc7bd62b08e9`.
Branch: `fix/staging-journal-web-ux`. Decisão: ADR-0018.
**Incidente real ainda depende da validação desta revisão no Northflank.**
Nenhum recurso, secret ou configuração do host foi alterado pelo agente.

## Causa final identificada no staging

BK confirmou POST /v1/web/utterance, stage=conversation,
storage_module=amora.cognition.store, operation=put_journal,
DatabaseError / SQLITE_CONSTRAINT. Correlação:
`f638ecd5-1f5d-4c8e-99a4-53f3dd926038`.

O fluxo registra INPUT_OBSERVED em observe_input e novamente na preparação do
contexto. O mesmo dedupe_key pode reaparecer deliberadamente. A idempotência
estava implementada por exceção em torno de um INSERT simples, não no SQL.
Hrana informa um código primário; o patch anterior só o convertia em UNIQUE
quando a mensagem começava por um prefixo específico. Isso não correspondeu à
resposta do Turso real e o erro continuou fatal. Alterar a heurística novamente
não resolveria o contrato: a aplicação não deve depender da redação do servidor.

## SQL final e falhas preservadas

```sql
INSERT INTO cognitive_journal(
    id, turn_id, stage, timestamp, source, summary, reason_codes_json, payload_json,
    privacy_scope, provider, model, duration_ms, correlation_id, error_code,
    metadata_json, dedupe_key
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(dedupe_key) DO NOTHING
```

O método retorna `cursor.rowcount == 1` **fora do contexto transacional**:
somente depois de COMMIT bem-sucedido. 1 significa inserção; 0, no-op do alvo
dedupe. Outro count é falha, não dedupe. O adapter exige affected_row_count
inteiro não negativo, sem default silencioso para campo ausente/malformado.
O journal aceita somente 0/1. NULL dedupe_key continua permitindo entradas novas.

Removida a inferência global SQLITE_CONSTRAINT -> UNIQUE pelo texto Hrana.
PK com outra chave de dedupe, NOT NULL, CHECK, FK e erro de transporte continuam
falhando. Não há INSERT OR IGNORE, captura genérica transformando erro em False
ou retry de write/COMMIT. O schema/migrations Turso **não foram alterados**.

`test_journal_idempotence.py` usa schema real e backend SQLite local ou Hrana
sobre SQLite real, com transações/conexões reais. Erros remotos usam código
primário e mensagem opaca. Inserção/dedupe respondem 1/0 e não chegam a gerar
SQLITE_CONSTRAINT_UNIQUE no wire. Testes negativos introduzem CHECK/FK apenas
no banco descartável do teste; nada foi adicionado ao schema de produção.
COMMIT aplicado com resposta perdida gera OUTCOME_UNKNOWN e uma única tentativa.
Todos os testes anteriores de baton permanecem: fechamento pós-COMMIT/ROLLBACK
válido, perda intermediária inválida e conexão quebrada sem retry automático.

## Target privado Web, sem mudar o A15

LanguageSocialPipeline aceita explicit_addressee_id interno e valida a entidade.
Web texto sempre passa ENTITY_AMORA; áudio usa esse target apenas quando a
requisição declara speech_device_id=WEB_SAFARI. Speaker PERSON_BK sozinho não
autoriza o override. Nenhum prefixo/vocativo é adicionado ao texto original.
O evento registra target, explicit_addressee_id e EXPLICIT_SURFACE_TARGET.

O privado também não ocupa/limpa o floor ambiente nem estabelece/consome sua
janela de continuidade. Duas regressões demonstraram o efeito cruzado antes
dessa proteção: um Web “oi” autorizava resposta a “oi gente” no A15 e removia
um floor ambiente dirigido a ChatGPT. Ambas agora passam. AddresseeEngine não
foi relaxado; continuidade ambiente legítima segue passando nos testes próprios.

Foram exercitados POST Web -> cognition real -> speech queue WEB_SAFARI com
“oi”, “Olaa”, “fala comigo linda”, “Fala comigo, linda.”, “Como foi teu dia?” e
“Amora, fala comigo.”. STT usa WAV válido e GroqWhisperSTT com transporte HTTP
mockado, preservando transcript e direcionando a fala privada sem vocativo.
Esses testes não certificam a conta Groq nem um provider remoto cognitivo real;
cognition usa seu fallback existente quando não há provider configurado.

## Pareamento persistente

Novas credenciais Web são browser.v2: HMAC, nonce aleatório, role web, sem TTL
temporal. Mantêm os limites existentes para WEB_SAFARI e não acessam admin,
journal/memória privada, Avatar ou DEVICE_A15. O Core nunca entrega
LOCAL_API_TOKEN ao Web, inclusive em desenvolvimento local.

localStorage[amora.web.pairing.v2] contém somente essa credencial. Assinatura é
validada pelo Core a cada request. A verificação de formato no JS só impede
armazenar um token administrativo/Avatar/provider como pareamento. Nenhum secret
de infraestrutura é enviado ao navegador. Avatar v1 continua com TTL 12h.

Valores Web v1 de localStorage/sessionStorage não são migrados pois poderiam ser
administrativos: há um pareamento único após upgrade para obter a nova versão.
Depois disso reload/reopen e restart com a mesma chave preservam o acesso.
401 remove a credencial; falha temporária/500 não a remove nem exige novo código.
Esquecer remove a cópia local, mostra pairing e encerra captura/reprodução,
mantendo histórico. Não se alega revogação remota individual: rotação do segredo
de assinatura revoga globalmente; nenhuma rotação foi executada nesta missão.

Essa política substitui explicitamente a parte Web/sessionStorage/TTL da
ADR-0016 por autorização de BK. O risco de XSS com retenção maior está registrado
na ADR-0018; CSP, TLS, CORS/origin, escopos e demais controles permanecem.

## Histórico visual: política exata

JSON versionado em localStorage[amora.web.history.v1], independente do pairing.
Cada registro tem somente role user/amora, texto e opcional ID da utterance.
Restauração desenha com textContent e não envia entradas antigas ao Core.
Não há criação de fatos/Memory nem replay de turnos. IDs evitam duplicar bolhas
restauradas/reivindicadas; textos iguais de mensagens novas não são deduplicados.

Após cada append e load: percorrer registros do mais recente ao mais antigo,
guardar no máximo 50 de cada role e inverter para preservar a ordem de exibição.
Total máximo 100. A 51ª bolha de BK remove só a mais antiga de BK; o mesmo vale
para Amora. A unidade é a mensagem/bolha visível, inclusive segmentos de resposta.

System/erros/status TTS/diagnósticos/metadados nunca são serializados. AUTH/SECRET
e material de credencial reconhecido não são persistidos. Não é classificador
universal de segredos colados como texto arbitrário. Os secrets internos jamais
são fonte do histórico. Dados inválidos resetam apenas a chave do histórico;
falha de quota/storage não derruba a página. Limpar conversa remove JSON/bolhas,
mantém pairing, não chama APIs de Memory/Turso e não modifica relações/identidade.

Texto aparece e é salvo antes do fetch TTS. Um HTTP 503 ou timeout de worker
mantém a bolha e o histórico, sem ACK oculto. Não foi instalado TTS no Core,
alterado Supertonic ou adicionado fallback de voz.

## Validação e arquivos

- Core: `.venv/bin/python -m pytest -o addopts= tests -q`:
  **397 passed**, 5 avisos existentes, sem skips.
- Específicos: mesmo pytest com test_security_predeploy.py, test_pairing_api.py,
  test_pairing.py, test_cloud_web_incident.py, test_web_surface_target.py,
  test_journal_idempotence.py e test_remote_storage.py: **112 passed**, 1 aviso.
- Node oficial 22.23.4:
  `node --test avatar/tests/*.test.mjs tests/web_*.test.mjs`: **28 passed**;
  sintaxe app-v2.js/browser-state.mjs PASS.
- Container Alpine: build e tools/cloud/smoke-container.py PASS com non-root,
  read-only, cap-drop ALL, NNP, módulo .mjs com MIME JavaScript, pairing/escopo,
  texto “oi” sem worker, fila SQLite persistida, restart e SIGTERM.
  Static assets são root-owned e legíveis pelo usuário não-root mesmo quando
  o checkout local usa permissões privadas. Base/dependências não mudaram.

Arquivos de runtime: cognition/store.py, storage/remote.py, language/pipeline.py,
conversation/integrated.py, api.py, web/routes.py, security.py. Browser:
app-v2.js, browser-state.mjs, index.html, styles.css. Testes: novo
test_journal_idempotence.py, test_web_surface_target.py e web_persistence.test.mjs;
ajustes em test_remote_storage.py, pairing/security e web_browser.test.mjs.
CI Avatar/Web inclui todos tests/web_*.test.mjs. Dockerfile/smoke validam o
módulo servido. Documentação: ADR-0018, referência em ADR-0016, índice e estado.

## Gate real obrigatório para encerrar

Credenciais de staging não estão disponíveis neste ambiente; nenhum novo deploy
foi executado pelo agente. Após revisão/CI/PR e promoção aprovada, o operador
deve validar no Northflank/Safari:

1. /live e /ready HTTP 200, /web abre e carrega browser-state.mjs.
2. Parear uma vez no pod correto; “Olaa”, “Fala comigo, linda.” e
   “Amora, fala comigo.” produzem resposta textual, sem 500 de put_journal.
3. Fechar/reabrir aba e alternar apps: pairing e histórico restauram sem duplicar.
4. 51ª mensagem de cada role poda a mais antiga daquela role, máximo 50+50.
5. Limpar conversa mantém pairing/Memory; esquecer pairing mantém histórico.
6. STT aceito chega à conversa/fila sem 500; A15 ambiente não herda target Web.
7. Worker indisponível mantém texto; backend/áudio Supertonic real é gate separado.
8. Restart Northflank conserva acesso com chave estável e persistência Turso.
9. Erros novos preservam rota/código/correlação seguros, sem vazar secrets.

Mocks, testes Node com DOM simulado e CI verdes não substituem esse checklist.
