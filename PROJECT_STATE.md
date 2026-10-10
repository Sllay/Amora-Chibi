# PROJECT_STATE — Amora-Chibi

Última atualização: 2026-10-09/10 — branch `feat/v1.0-cognitive-integration`.

Este arquivo existe para permitir continuidade segura entre chats/agentes sem depender do histórico da conversa.

## Fonte de verdade

Antes de alterar arquitetura/comportamento, usar em conjunto:

1. `ARCHITECTURE.md` — arquitetura canônica do projeto quando disponível no contexto de desenvolvimento;
2. `GDD.md` — escopo/visão canônica;
3. `SKILL.md` — regras de implementação/continuidade;
4. `AGENTS.md` — guardrails essenciais do repositório;
5. este arquivo — estado operacional atual;
6. `docs/V1.0_COGNITIVE_INTEGRATION.md` — contrato técnico da V1.

## Regra de identidade

A Amora não é um LLM.

```text
Provider = capacidade cognitiva substituível
Amora Core = identidade + estado + memória + relações + emoção + drives + intenção + causalidade
```

Trocar Groq/Gemini/outro provider não pode trocar `ENTITY_AMORA`.

## Escopo atual

### V1.0

V1.0 é **Cognitive Integration / Soul**:

- Context Compiler;
- Cognitive Journal;
- persistent Conversation History;
- streaming cognition;
- streaming/expressive voice;
- causal barge-in;
- autonomia generativa;
- reflexão estruturada com gates locais;
- provider failover/quota/backoff;
- Cognitive Replay/model certification;
- privacidade/consentimento remoto;
- estabilidade, restart, backup/restore e observabilidade.

### V1.1

**Imagem, foto, câmera, screenshots e percepção visual avançada pertencem à V1.1.**

Não mover percepção visual de volta para V1.0 por causa de versões antigas do roadmap.

## Branch de trabalho

```text
feat/v1.0-cognitive-integration
```

A branch nasceu sobre a V0.9 e contém todo o trabalho cognitivo da V1. Não recriar V1 em outra branch sem motivo.

## Implementado na V1

### Cognitive persistence

- schema 10;
- `conversation_messages`;
- `cognitive_journal`;
- `speech_segments`;
- `model_usage`;
- restart e backup/restore preservam o estado cognitivo.

### Context Compiler

Compila estado canônico selecionado:

- Self Model;
- relationships;
- mood;
- directed affect;
- drives;
- active intention;
- memory retrieval;
- World Model;
- recent conversation history;
- privacy + remote-processing authorization.

### Streaming cognition

- provider gera incrementalmente;
- `SpeechSegmenter` libera cláusulas estáveis;
- `SpeechNormalizer` separa display, fala e nonverbal cues;
- segmentos são persistidos antes da entrega;
- delivery state diferencia queued/speaking/spoken/interrupted/failed.

### Causal cancellation / barge-in

- Android usa Acoustic Echo Cancellation quando disponível;
- barge-in exige energia humana sustentada acima do residual;
- pre-roll curto preserva o começo da fala humana;
- cancelamento usa executor de controle separado;
- Core cancela a task/stream cognitivo;
- segmentos novos não podem nascer após cancelamento;
- utterances restantes são canceladas;
- `TURN_FINISHED` só ocorre quando a entrega está terminal;
- TTS local registra prefixo falado conhecido;
- PCM remoto registra prefixo desconhecido em vez de inventar precisão.

### Voice

- Gemini expressive TTS quando permitido/configurado;
- streaming PCM para Android;
- fallback Android TextToSpeech;
- seleção de voz local;
- privacy gate antes de TTS remoto;
- usage/journal de voz.

### Autonomous cognition

Ações semânticas verificadas podem passar por:

```text
BDI/action semantic request
→ Turn Manager
→ Context Compiler
→ provider
→ streaming speech
```

Cognição remota autônoma exige opt-in independente:

```bash
AMORA_REMOTE_COGNITION_ALLOWED=true
```

Default: false.

### Structured reflection

Provider pode propor:

- memory candidates;
- belief hypotheses;
- action suggestions;
- tone metadata.

Gates locais:

- Memory Write Gate decide memória;
- beliefs do provider entram apenas como `HYPOTHESIS` temporária;
- action suggestion vira evento candidato/deferido, nunca execução direta;
- entidades inventadas são removidas/rejeitadas.

Reflexão possui failover de provider com seleção task-local via `ContextVar`.

### Provider resilience

- ranked candidates;
- quota-aware selection;
- Groq primary;
- Gemini cognitive fallback quando configurado;
- local fallback;
- 429/Retry-After;
- cooldown/backoff pelo Clock canônico;
- failover só antes de fala emitida;
- nunca costurar dois providers numa mesma fala parcial.

### Cognitive Replay

- snapshot local do contexto usado pelo turno;
- AUTH/SECRET não recebem snapshot;
- replay compara providers sobre o mesmo contexto;
- replay não altera history/journal/memory/world/emotion/autonomy/usage ledger;
- API: `POST /v1/cognition/replay/{turn_id}`.

### Remote cognition consent

Android não envia mais `remote_processing_allowed=true` fixo.

Existe checkbox explícito, desligado por padrão:

```text
Permitir cognição remota para minhas falas
```

Isso controla cognição depois do STT; autorização de STT/áudio continua sendo decisão separada.

## Testes adicionados/expandidos

Principais arquivos V1:

```text
tests/test_cognition_v10.py
tests/test_voice_v10.py
tests/test_cognition_cancellation_v10.py
tests/test_autonomous_cognition_v10.py
tests/test_reflection_v10.py
tests/test_cognitive_failover_v10.py
tests/test_cognitive_replay_v10.py
tests/test_delivery_truth_v10.py
tests/test_reflection_failover_v10.py
tests/test_cognition_backup_v10.py
android/app/src/test/java/dev/amora/chibi/audio/BargeInDetectorTest.kt
```

CI obrigatório antes de merge:

- Core Python 3.11;
- Core Python 3.12;
- Core Python 3.13;
- Core Python 3.14;
- Android unit tests;
- Android debug APK build;
- regressões V0.1–V0.9.

## Estado de release

A V1 está em **release-candidate**, não deve ser marcada como `v1.0.0` estável apenas por existir código.

Antes do merge final:

```text
[ ] todos os workflows do HEAD verdes
[ ] revisar diff contra main
[ ] procurar TODO/FIXME/stubs na superfície V1
[ ] confirmar README/documentação V1
[ ] abrir PR V1 → main
```

Antes da tag estável `v1.0.0`, executar também a certificação física descrita em `docs/V1.0_COGNITIVE_INTEGRATION.md` no A15.

## Não fazer

- não mover imagem/foto para V1;
- não transformar provider em identidade;
- não permitir provider gravar BELIEF/memória/ação diretamente;
- não tratar speaker ID como autenticação;
- não mandar AUTH/SECRET para provider;
- não inferir autorização remota a partir de disclosure scope;
- não usar `sleep()` como relógio de backoff canônico;
- não considerar texto gerado como texto efetivamente falado;
- não fazer merge de V1 com CI quebrado;
- não apagar migrations antigas nem recriar schema do zero.
