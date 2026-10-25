# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-25
**Objetivo:** permitir continuidade segura entre chats/agentes sem depender do histórico integral da conversa.

Este arquivo é um **snapshot operacional**. Para regras atuais, leia primeiro `docs/canon/*`, `docs/roadmap/VERSIONS.md` e ADRs relevantes.

---

## 1. Fonte de verdade

Ordem de leitura atual:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/VOICE_IDENTITY.md`;
7. `docs/roadmap/VERSIONS.md`;
8. ADRs relevantes em `docs/decisions/`;
9. este `PROJECT_STATE.md`;
10. código, `main`, PRs e workflows atuais.

GitHub é a fonte canônica. Resumos de chat são contexto auxiliar.

---

## 2. Estado operacional atual

### Superfície primária de iteração

Safari/Web é agora o laboratório conversacional principal, conforme ADR-0013.

```text
Amora Core / estado canônico
        |
        +-- Web/Safari -> superfície primária de iteração rápida
        |
        +-- Android debug -> referência / gate secundário
```

O APK debug atual não define o aplicativo Android oficial final e não deve voltar a ser o caminho padrão para cada pequeno teste.

### Web/PWA já integrado

O Safari Lab possui:

- pareamento de 6 dígitos;
- autenticação reutilizando o Core;
- chat por texto;
- microfone via Web Audio;
- STT;
- VAD/browser barge-in;
- fila de respostas;
- diagnóstico;
- `test_mode=true` por padrão;
- Cloudflare Quick Tunnel para HTTPS de desenvolvimento;
- routing separado `WEB_SAFARI`.

O bug inicial em que Safari buscava `WEB_SAFARI` mas o Core enfileirava em `DEVICE_A15` foi corrigido e integrado antes da voz local.

---

## 3. Voz Web local — integração atual

A implementação principal do Voice Worker Web foi integrada pelo PR #54 no commit:

```text
5ea4bd7d40e85cf7380f50f0aad05b54605f0ade
```

O head original da branch `feat/web-local-amora-voice-worker` foi:

```text
e3f85799831127adb42f643213ed6b3d9e2d8470
```

O squash do `main` e o head original possuem a mesma árvore Git:

```text
65610c5890b41fbddae5a475018d2a9c8e232dc3
```

Portanto a implementação da branch interrompida não ficou perdida; ela foi consolidada no `main`.

O fechamento pós-merge foi integrado pelo PR #56 no commit:

```text
79a186df1e49d106bcb26a4bfb7ce429323900d8
```

Esse fechamento adicionou testes explícitos de ACK/cancel/fail-open, cache do setup Termux, observabilidade Web separada de Android e sincronização do canon/roadmap/handoff.

### Pipeline

```text
Core
→ SpeechUtterance validada
→ AmoraLocalVoiceRenderer no Termux
→ sherpa-onnx Supertonic 3 INT8
→ amora_voice.bin 60/40
→ shared WORLD FINAL_B
→ AMORA_VOICE_SIGNATURE_V1_SOFT
→ Emotion / Prosody V2 bounded
→ WAV PCM16 mono
→ Safari AudioContext / Web Audio
```

Safari é apenas cliente, microfone, player e interface.

Não usar como transporte principal:

- `window.speechSynthesis`;
- Gemini TTS;
- fallback silencioso para outra voz.

---

## 4. Identidade vocal congelada

### Supertonic

```text
sherpa-onnx-supertonic-3-tts-int8-2026-05-11
```

Style base:

```text
60% speaker 0 + 40% speaker 4
```

`amora_voice.bin` é gerado por:

```text
android/tools/build_amora_voice_bin.py
```

### FINAL_B

```text
AMORA_CHILD_FINAL_B_PLUS60
```

Parâmetros congelados:

- target F0: 284.8 Hz;
- max F0 ratio: 1.186;
- pitch range: 1.048;
- warp: 0→1.000, 250→1.000, 700→1.126, 1500→1.111, 3000→1.068, 5000→1.020, 8000→1.000;
- shelf: -2.36 dB.

### VoiceSignature

```text
AMORA_VOICE_SIGNATURE_V1_SOFT
```

- aperiodicity scale: 0.965;
- harmonic drive: 1.30;
- harmonic wet: 0.05.

BK aprovou essa identidade como assinatura de laboratório. Não reabrir sem evidência nova.

Emotion/Prosody V2 continua variável e bounded e não altera a identidade-base.

---

## 5. Implementação compartilhada

O DSP de FINAL_B + Signature + expressão bounded vive em uma única implementação C++:

```text
android/app/src/main/cpp/amora_voice_processor.cpp
android/app/src/main/cpp/amora_voice_processor.h
```

Uso:

```text
Android JNI
    ↓
shared processor C++

Termux worker / ctypes
    ↓
libamora_voice_processor.so
    ↓
shared processor C++
```

Não manter uma versão Python aproximada em paralelo.

---

## 6. Contrato Web de delivery

Texto e áudio são desacoplados para falha graciosa.

O cliente:

1. reivindica `SpeechUtterance` de `WEB_SAFARI`;
2. mostra o texto imediatamente;
3. pede WAV autenticado ao Core;
4. decodifica com Web Audio;
5. envia `started` quando o player começa;
6. envia ACK `spoken=true` somente após o player terminar;
7. em barge-in/manual stop, aborta fetch/player e cancela no Core;
8. em falha de voz, mantém o texto, cancela o turno afetado e libera novas mensagens.

A fila Web não deve travar o chat quando o worker estiver indisponível.

`WEB_SAFARI != DEVICE_A15` continua sendo invariante de routing.

---

## 7. Setup local Termux

Script:

```text
tools/web/setup-local-voice.sh
```

Ele prepara:

- `sherpa-onnx==1.13.8` no venv do Core;
- modelo Supertonic 3 INT8;
- `amora_voice.bin` 60/40;
- WORLD pinado no commit `d625e7608ca23a870018f01e7c562ac683d9847f`;
- `libamora_voice_processor.so`;
- validação final de disponibilidade do renderer.

Downloads reutilizáveis ficam em:

```text
~/.amora/cache/web-local-voice
```

Modelos/vendor/lib ficam sob `~/.amora`, fora do Git.

A intenção é instalação pesada única. Depois disso, mudanças normais Web/Core devem exigir apenas atualização do código, restart do Core e refresh do Safari.

---

## 8. Evidência automatizada

### Integração principal — PR #54

No head original `e3f85799831127adb42f643213ed6b3d9e2d8470`:

- Core tests: PASS em Python 3.11, 3.12, 3.13 e 3.14;
- doctor smoke: PASS;
- autonomy smoke: PASS;
- actions smoke: PASS;
- conversation smoke: PASS;
- avatar smoke: PASS;
- backup smoke: PASS;
- Android build/test: PASS.

A implementação mergeada em `main` possui a mesma árvore Git desse head.

### Fechamento pós-merge — PR #56

Head testado:

```text
4dfa7044f5a6ff79bf4af6eec4f87df62870e8b0
```

Core CI run #937:

- Python 3.11: PASS;
- Python 3.12: PASS;
- Python 3.13: PASS;
- Python 3.14: PASS;
- doctor/autonomy/actions/conversation/avatar/backup smokes: PASS.

O workflow Android não rodou no PR #56 por `paths: android/**`; nenhum arquivo Android foi alterado no fechamento. O build Android relevante continua sendo o PASS do PR #54, que validou o DSP/JNI compartilhado.

Testes Web agora cobrem:

- rota WAV autenticada;
- indisponibilidade do worker;
- routing `WEB_SAFARI`;
- `test_mode`;
- ausência de `speechSynthesis` no cliente V2;
- ACK após confirmação explícita de playback;
- cancelamento manual de utterance claimed;
- recuperação após falha de voz sem prender a próxima mensagem.

---

## 9. O que AINDA NÃO é PASS

```text
CI PASS
!=
CERTIFICAÇÃO FÍSICA PASS
```

Ainda precisa de teste real de BK:

- executar o setup no Termux do Galaxy A15;
- confirmar que o worker carrega sherpa-onnx/modelo/WORLD;
- abrir Safari/iPhone pelo tunnel;
- ouvir a voz real;
- validar primeira latência e RTF;
- validar barge-in/manual stop;
- observar estabilidade prolongada;
- observar RAM/CPU/temperatura;
- confirmar que falha de voz não bloqueia texto;
- confirmar ausência de chamadas TTS remotas.

A API Web continua corretamente com:

```text
canonical_voice=false
```

Isso representa certificação física/final pendente, não identidade vocal indefinida.

---

## 10. Fluxo operacional desejado

### Primeira preparação da voz Web ou quando dependências/DSP mudarem

```bash
source ~/.venvs/amora/bin/activate
cd ~/amora-src
bash tools/web/setup-local-voice.sh
```

### Iteração normal depois do setup

```text
git pull / GitSync
→ reiniciar Core
→ atualizar Safari
→ testar
```

Sem APK novo e sem reinstalar modelos a cada pequena alteração.

---

## 11. Próximo trabalho imediato

Ordem atual:

```text
1. git pull / sincronizar o main atual no Galaxy A15
2. rodar setup-local-voice.sh uma vez no Termux real
3. reiniciar o Core
4. abrir/atualizar o Safari Lab pelo tunnel
5. testar voz física, ACK, barge-in e manual stop
6. coletar latência/RTF/estabilidade/RAM-CPU-temperatura
7. corrigir somente bugs comprovados no gate físico, preservando FINAL_B/Signature
8. continuar a iteração da Amora pelo Safari
```

Não voltar para:

- Safari `speechSynthesis` como transporte principal;
- Gemini TTS como fallback oculto;
- APK-per-small-test;
- retuning de FINAL_B/VoiceSignature sem evidência nova.

## Migração de infraestrutura — 2026-10-23

Decisão nova de BK: Core/Web → container (Koyeb candidato), persistência →
SQLite ou libSQL remoto (Turso candidato), voz → worker independente (Modal
candidato); A15 será futuramente Living Console. ADR-0015 registra a direção.
A seção de próximo trabalho local acima é histórico operacional anterior;
certificação acústica continua pendente e parâmetros congelados não mudam.

Etapa 1: auditoria em `docs/engineering/CLOUD_MIGRATION_AUDIT.md`, injeção
StorageBackend nos nove stores e contrato VoiceBackend, mantendo defaults
locais e queries/migrations. Testar interfaces antes da etapa cloud.

Etapa 2: Core/Web Linux possui host/port por ambiente/CLI, /live, /ready,
health com HTTP 503 em falha, lifespan e close idempotente, logs JSON stdout
cloud, Dockerfile e lock de dependências. Cloud falha fechado para token
não injetado ou SQLite sem volume durável declarado. Voz continua opcional.
Operação em `docs/engineering/CLOUD_OPERATIONS.md`. CI remoto e deploy Koyeb
não equivalem aos testes locais e continuam pendentes.

Etapa 3: LibSQLBackend Hrana HTTP v2 com transações stateful, tipos, constraints,
FTS, backup SQLite portátil e restore explícito para destino vazio. Migration 11
adiciona blobs de Avatar; AMORA_ASSET_STORAGE=database permite compute efêmero
com SQL remoto. SQLite local permanece default. Corrigidos claim concorrente,
ACK/cancel fora da transação, IntegrityError mascarado como dedupe e FTS que
mascarava falhas operacionais. 285 testes passaram, incluindo contratos de
memória/world/emoção/autonomia/ações/Web sobre adapter remoto mockado. Nenhuma
conta Turso/Koyeb foi utilizada; latência/quotas/dados grandes são gates de staging.

Etapa 4: VoiceRenderRequest mínimo versionado, RemoteVoiceBackend HTTPS,
worker ASGI independente e template Modal. Reusa o renderer Supertonic e o DSP
C++ existentes; não altera nenhum parâmetro vocal. Core valida WAV/identidade,
falhas de voz preservam texto e resultado após cancelamento é rejeitado.
Sete testes novos cobrem serviço autenticado, payload mínimo/bounds, sucesso,
worker indisponível, áudio inválido/identidade errada, ACK e cancelamento.
Bundle vocal nativo real e deploy/certificação Modal não foram executados;
preparação exige archive+SHA revisados e WORLD no commit congelado. Instruções
em CLOUD_OPERATIONS.md. Tickets de áudio direto ficam explicitamente futuros.

Etapa 5: /v1/operations/state autenticado, schema amora.operations.v1,
projeção sem texto/contexto privado, lifecycle de ações e erros bounded agrupados
por fingerprint+contagem. Jobs continuam opt-in; Dev MCP/Living Console recebem
fundação, não um servidor novo. Request correlation IDs e latência disponíveis.
Corrigidos também task cognitiva órfã por cancel externo, cleanup de bootstrap
incompleto, consumo concorrente do pairing e subscription WebSocket ociosa;
SECRET não é mais transmitido no stream. Snapshot remoto preserva high-water
marks AUTOINCREMENT e recusa perda de baton em transação. Gates de serviços
remotos/bundle real/custos/acústica permanecem abertos.

Validação final local: 304 pytest PASS, 5 testes Node Avatar PASS, pip check sem
inconsistências, Docker final com base por digest e build tools pinados PASS.
Smoke automático valida non-root, endpoints, autenticação, texto, fila persistente,
restart e SIGTERM; workflow cloud-contracts.yml reproduz esse gate na CI.
Avisos existentes de Pydantic/Starlette permanecem (5), sem testes pulados.
Relatórios para revisão por etapa em `docs/engineering/reviews/`.

## Security pass pré-deploy — 2026-10-23

ADR-0016 e `docs/engineering/security/PREDEPLOY.md` registram a nova fronteira:
cloud pairing com token web/avatar 12h, admin separado, auth ASGI global,
origins/hosts explícitos, proxies sem confiança implícita, limites de body/rate/
timeout, WebSocket administrativo com origem validada e headers CSP/security.
Bearer do browser passa a sessionStorage; logs redigem secrets observados.
Node CI 22.23.4 testado por distribuição oficial verificada; actions Node 24
pinadas. Runtime Docker non-root/read-only sem pip/setuptools/wheel.

Scans: Core lock (23) e worker lock (17) sem vulnerabilidades conhecidas ou skips.
Runtime Python sem CVEs reportados após remover tooling. **Deploy bloqueado:**
Debian mantém 44 ocorrências HIGH/oito CVEs sem FixedVersion; nenhuma exclusão.
CI security-predeploy deve bloquear HIGH/CRITICAL. 347 testes Python e cinco
Avatar passaram localmente; cloud providers/ingress/egress, bundle real e Safari
físico continuam gates. Inventário de 69 operações HTTP e evidências versionadas
na pasta de segurança. Não tratar preparação como aprovação de publicação.

## Runtime/container security — 2026-10-24

Continuação de feat/security-predeploy / eb03eb8. ADR-0017 e
`docs/engineering/security/RUNTIME_BASE_DECISION.md` analisam individualmente
as oito CVEs e explicam 44 findings por cinco pacotes-fonte. Posição Debian
obtida no espelho do Tracker, com revisão registrada; domínio direto bloqueado
pelo proxy, sem contorno. Não houve VEX, ignore list ou redução do gate.

**PASS do gate local de runtime:** Python oficial 3.12.15-alpine3.24 por digest,
após variante experimental separada; imagem final ~69,7 MB versus 139,2 MB Debian.
Trivy 0.76.0: zero findings em todas as severidades; HIGH/CRITICAL incluindo
unfixed retorna 0 na nova imagem e 1 na Debian original (controle negativo).
Wheels musllinux disponíveis; nenhum compilador/build tool adicionado. Pipeline
vocal e contratos Core/worker/providers/storage não foram alterados.

347 testes Python passaram em cada base e novamente na imagem canônica final;
cinco testes Avatar Node 22.23.4 PASS; pip check PASS. Smoke valida UID 10001,
read-only, capabilities zeradas, NNP, health/readiness sem dados privados,
Web/auth/pairing com escopo e proteção contra replay, texto sem voz, SQLite,
restart/SIGTERM e catálogo instalado. Corrigido bug de packaging: catálogo JSON
existente omitido da imagem causava 500; bytes e parâmetros preservados.
CI cloud-contracts agora executa suíte completa na libc da imagem e verifica
dependências do runtime sem reinstalar pip no estágio de produção.

Somente Linux amd64 foi executado; multiarch/wheels arm64 verificados como
disponibilidade, sem certificação. DNS/ABI musl em host real, limites de execução,
CI remoto, ingress/egress e serviços remotos continuam gates. Nenhum deploy.
Próximo passo: revisão/CI e gate de staging separado.

## Hrana HTTP — fechamento transacional real — 2026-10-25

Na branch `feat/runtime-base-security`, continuação de `4ee10fc`. Primeiro
deploy real Northflank → Turso, relatado por BK, revelou falso
SQL_TRANSACTION_STREAM_LOST/SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN: HTTP 200 e
COMMIT válido podiam terminar com baton=null. O mock anterior sempre mantinha
baton até close, por isso não reproduzia o comportamento real do servidor.

Adapter agora processa o resultado SQL antes de validar o stream: apenas
COMMIT/ROLLBACK explícitos bem-sucedidos permitem fechamento transacional sem
close; BEGIN e statements intermediários sem baton continuam fail-closed.
Erros SQL dos terminadores são propagados; HTTP/transport failure continua
OUTCOME_UNKNOWN, conexão inválida e nenhuma repetição automática de COMMIT.
Mock passa a fechar após terminador sem estado transacional restante, com
regressões de migrations completas, schema persistido/restart e falhas.

24 testes de storage remoto PASS; suíte Core completa: 357 PASS, 5 avisos
existentes, sem skips. Detalhes em `docs/engineering/reviews/03.md`.
Somente adapter, testes e documentação alterados; schema/migrations, domínio,
providers, segurança/tokens, seleção de storage, contratos públicos e voz intactos.
Nenhum novo deploy executado. Próximo passo: CI/revisão e repetir startup e
restart no staging Northflank → Turso real para confirmar o efeito operacional.

## Incidente Web/Safari cloud — 2026-10-25

Base canônica agora main / c5b8c4d (merge PR #59); correções em branch nova
`fix/cloud-web-runtime`. Sem novo deploy pelo agente. Boot/probes/restart de
staging relatados saudáveis por BK, mas texto/STT retornam DatabaseError por
request. Sem credenciais de staging neste ambiente; **incidente real aberto**.

Reprodução forte: Hrana com SQL/transações reais e códigos primários de constraint
falha no segundo INSERT idempotente em cognitive_journal.dedupe_key:
SQLITE_CONSTRAINT vira DatabaseError em vez de dedupe. Normalização conservadora
do prefixo UNIQUE corrige texto e STT/conversation, sem aceitar FK/NOT NULL como
sucesso. O mock antigo devolvia sempre o código estendido UNIQUE. Confirmação de
que esta é a mesma causa real ainda depende do traceback/código de staging.

`amora pair` usa somente paths/clock/PairingManager, sem bootstrap/storage.
Logs REQUEST_FAILED passam a identificar method/path template, stage,
correlation_id, latency e código seguro/store/operação, sem corpo/SQL/secrets.
Safari app-v2 exibe erro de claim em vez de escondê-lo; timeout de voz deixa
erro explícito e libera a fila; texto não depende do TTS
e UI distingue worker remoto de backend local. Sem fallback secreto ou mudança
de voz, schema, providers, tokens, regras de branch ou configuração Northflank.

Gates locais: 108 específicos PASS; remote storage 26 PASS; Core completo
366 PASS (5 avisos existentes); Node 22.23.4 Avatar+Web 10 PASS. Regressões de baton,
COMMIT ambíguo sem retry, constraints, STT/WAV, browser token, ACK/cancel e restart
preservadas. Detalhes, arquivos e gate real em
`docs/engineering/reviews/CLOUD_WEB_INCIDENT.md`. Encerramento exige validar a
revisão aprovada em Northflank/Turso/Safari real; áudio/config do worker remoto
não foram certificados aqui. Branch runtime antiga não foi removida.

## Staging: journal SQL e Web 1:1/persistente — 2026-10-25

Base main / 3683ea2; branch nova `fix/staging-journal-web-ux`. BK confirmou o
DatabaseError SQLITE_CONSTRAINT em CognitiveStore.put_journal, correlação
f638ecd5-1f5d-4c8e-99a4-53f3dd926038. O parser do texto de erro não funcionou
no Turso. Foi removido: journal usa ON CONFLICT(dedupe_key) DO NOTHING, retorna
1/0 por rowcount somente depois de COMMIT e não captura outras constraints como
dedupe. Adapter exige affected_row_count válido. Sem schema/migration ou retry.

Web texto e STT explicitamente WEB_SAFARI passam target ENTITY_AMORA com razão
EXPLICIT_SURFACE_TARGET, sem reescrever texto ou relaxar AddresseeEngine. O
privado não ocupa/limpa floor ambiente nem abre sua janela de continuidade;
regressões cobrem Web seguido de A15 e floor prévio dirigido a terceiros.

ADR-0018 autoriza Web bearer v2 durável, limitado a WEB_SAFARI, no localStorage;
admin nunca é retornado ao Web, mesmo local. Avatar v1/12h permanece. Credenciais
legadas potencialmente admin não são migradas; novo pairing único no upgrade.
Revogação global por rotação da chave continua; esquecer só remove cópia local.
Histórico visual local separado e versionado mantém no máximo 50 user + 50 amora,
em ordem, sem system/diagnóstico ou metadados/credenciais internos. Restauração
não é replay nem Memory; clear conversa e forget pairing são independentes.
Texto continua antes do TTS e persiste após 503/timeout, sem fallback vocal.

Gates locais: Core 397 PASS (5 avisos existentes); específicos 112 PASS;
Node 22.23.4 Avatar/Web 28 PASS; container Alpine com módulo .mjs, texto sem
vocativo, pairing, cap-drop/NNP/read-only, restart/SIGTERM PASS. CI/PR e resultados
finais devem ser reportados pela revisão. Nada de persona, canon emocional,
Provider Mesh, voz, schema, secrets, recursos Northflank ou branch protection
foi modificado. **Incidente ainda aberto até o checklist real Northflank/Safari**:
sem credenciais staging no ambiente e sem novo deploy pelo agente. Worker TTS
real permanece gate separado. Detalhes em
`docs/engineering/reviews/STAGING_JOURNAL_WEB_UX.md`.

## Chat por turno e controles vocais Web — 2026-10-25

Base main c82e76b (PR #61), branch nova `fix/chat-turns-voice-controls`.
ADR-0019 separa DisplayText/SpeechPlan: cada segmento de fala entrega snapshot
literal do turno; Safari usa cognitive_turn_id para uma única bolha/histórico.
Pruning mantém 50 user + 50 turnos Amora, também no DOM; diagnóstico é status
separado. Risadas/emojis geram cues neutras; todas explicitamente unsupported
no Supertonic atual. Marcadores sem semântica implementada são preservados como
unsupported_markers. Sem retuning, novo TTS/modelo ou alteração dos providers.

Clear abre confirmação acessível e só confirmação limpa histórico local.
Mute é preferência local persistente: texto/fila continuam sem pedir áudio ou
criar contexto de playback. Stop aborta todos os controllers, para fonte, avança
epoch, cancela todos os claims e impede retorno tardio do mesmo turno. Cancel
speech_only finaliza só delivery, não tarefa cognitiva; input/barge-in mantém
contrato anterior. Corrigidos ACK tardio sobre cancelado e lifecycle no pagehide.
Preparação WAV serial respeita o slot único do Core, mantendo look-ahead de três.

VoiceBackend.render permanece; protocolo streaming opcional descreve sessão,
chunks PCM, sequência/formato/final e cleanup. Nenhum backend/endpoint streaming
ativado. WavPlayer isola download/decode completo; PcmStreamPlayer recebe samples
incrementais com buffer bounded, backpressure, underrun e stop testáveis.
Nenhuma mudança SQL de schema, seleção de storage, tokens, secrets ou infra.

Core: 418 PASS (cinco avisos existentes), também dentro da imagem Alpine/musl;
regressões específicas 120 PASS; Avatar/Web Node 22.23.4: 50 PASS, sem skips.
Container smoke restrito e pip check PASS. CI/PR e SHA finais reportados no handoff.
Validação física Safari/Northflank/worker real continua gate separado; nenhum
deploy realizado nesta missão. Detalhes/arquivos/checklist em
`docs/engineering/reviews/CHAT_TURN_VOICE_CONTROLS.md`.

## Incidente libSQL: recovery de sessão e usage ledger — 2026-10-25

Base main ae3b2f6 (PR #62), branch nova `fix/libsql-connection-recovery`.
Staging real confirmou SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN em record_usage
durante POST /v1/audio/transcribe/conversation (600bf18d-3302-4cca-bf56-18f4f0ac687c),
seguido de SQL_CONNECTION_CLOSED em get_message_by_dedupe no mesmo Store.
O poison era correto, mas o wrapper de conexão longa ficava morto até restart.

ADR-0020: recovery lazy genérico, somente na próxima operação independente,
com client/pool novo, baton=null e PRAGMA foreign_keys=ON. Nenhum replay de
INSERT/UPDATE/DELETE/COMMIT. Contexto broken não reconecta; close explícito é
terminal. Corrigidos BEGIN que fechava terminalmente, script que retinha
transaction após erro e contexto que engolia falha e simulava saída bem-sucedida.
Nove owners e SQLBlobStore mantêm a mesma conexão lógica, sem reconnect no domínio.

UsageRecorder central captura exclusivamente falha DB-API de record_usage.
Realização validada continua, com logs e estado DEGRADED/contagem/fingerprint,
sem linha falsa de usage/failure ou retry. Escritas obrigatórias continuam
fail-closed. Sinais reais de quota são preservados; reflection não usa status
persistido de outro turno após uma lacuna de usage. test_mode não mudou.
Logs seguros distinguem poison, uncertainty e recovery após validação real
da sessão nova, com classe técnica/código/IDs; sem SQL, tokens ou conversa.

Validação: Core **452 PASS**, também na imagem Alpine/musl restrita; regressões
específicas **196 PASS**, incluindo 27 recovery; JS/Avatar **50 PASS** Node 22.23.4.
Smoke de container, pip check, Ruff e diff check PASS. Scenario bootstrap →
diálogo → STT/usage incerto → texto em fila → segundo STT sem restart PASS.
INSERT de usage enviado uma vez; perda de resposta no INSERT/COMMIT não repete
nenhum deles. CI/PR/SHA finais reportados no handoff.

Sem alterações de schema, UI/PCM, personalidade, memória por design, canon
emocional, malha de providers, voz, secrets, réplicas ou configuração Northflank.
Nenhum deploy.
Gate real desta revisão Northflank/Turso permanece: ambiente sem credenciais,
sem inventar validação física. Relatório com contagens de requests e comandos:
`docs/engineering/reviews/LIBSQL_CONNECTION_RECOVERY.md`.
