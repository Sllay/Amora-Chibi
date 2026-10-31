# PROJECT_STATE — Amora-Chibi

**Última atualização:** 2026-10-31  
**Base operacional:** `main` após PR #80 (`ca8fcf6d803657d99f323573cf482eb2d830a8a1`)  
**Objetivo:** checkpoint operacional curto; para detalhes do remapeamento ler `docs/engineering/reviews/PROJECT_REMAP_2026-10-31.md`.

## 1. Autoridade

Ordem de leitura:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/canon/DIALOGUE_EMOTION.md`;
5. `docs/canon/PROVIDER_MESH.md`;
6. `docs/canon/VOICE_IDENTITY.md`;
7. `docs/roadmap/VERSIONS.md`;
8. ADRs relevantes;
9. `docs/engineering/reviews/PROJECT_REMAP_2026-10-31.md`;
10. este snapshot;
11. código/testes/CI atuais.

Quando um documento antigo contradizer o código + ADR mais recente, não restaurar silenciosamente o método antigo.

## 2. Runtime operacional atual

### Core/Web

- Core + Web/PWA rodam em container Linux remoto.
- Deploy atualmente observado em **Northflank** (`loyal-coat`).
- Safari/Web é a superfície conversacional principal de iteração.
- Android debug não é o cliente final; deve evoluir para Living Console.
- `WEB_SAFARI != DEVICE_A15` permanece invariante.

### Persistência

- `StorageBackend` suporta SQLite local e libSQL remoto.
- O caminho cloud atual usa storage remoto libSQL.
- SQLite local permanece válido para desenvolvimento/offline; não remover.
- transactions/recovery/atomic writes remotos foram endurecidos em ADRs 0020/0021.
- migrations são histórico de schema necessário, não duplicação descartável.

### Voz Web

Caminho normal atual:

```text
WEB_SAFARI
→ Core
→ Fish Audio s2.1-pro-free + clone da Amora
→ PCM s16le incremental por turno cognitivo
→ Safari PcmStreamPlayer
```

Fallback:

```text
Fish falha antes do primeiro PCM
→ Gemini TTS explícito
```

Depois do primeiro PCM Fish não misturar Gemini na mesma fala.

Parâmetros atuais de estabilização Fish, PR #80:

- `latency=balanced`;
- `chunk_length=200`;
- `temperature=0.55`;
- `top_p=0.70`;
- `normalize=true`;
- emotion tag threshold `0.50`;
- máximo de 2 style tags;
- sem DSP cloud;
- stream e WAV recovery usam a mesma política de payload;
- telemetria `fish_headers_ms`, `fish_first_byte_ms`, `fish_first_pcm_ms`.

Supertonic/FINAL_B/VoiceSignature e RemoteVoiceBackend continuam preservados como laboratório/offline/opt-in, não default Web.

Deploy do PR #80 foi confirmado com status Northflank `success`.

## 3. Avatar

- Avatar Core/Studio estão ativos.
- Rig, layers/assets, transforms, bones, IK, physics, expressions e animations pertencem ao `AvatarProject`.
- Runtime Avatar existe separadamente em `/avatar/?mode=runtime`.
- Core traduz estado semântico/emocional para expressão/talk/gestures bounded.
- Portable bundle v1 exporta ZIP autossuficiente com `manifest.json`, `project.json` e assets com hashes.
- Import ZIP valida integralmente antes de mutar; JSON antigo permanece apenas fallback estrutural.
- Runtime Avatar ainda não está unificado à superfície conversacional principal.

## 4. Módulos realmente ativos

Ativos no runtime normal ou em contratos operacionais usados:

- API/auth/pairing/security;
- Event Bus;
- identity/device/session/secrets;
- storage SQLite/libSQL, migrations e backup;
- Memory retrieval e reflection gated;
- World Model e Self Model;
- Addressee/Social Scene;
- Emotion Engine + Interaction Appraiser;
- Conversation + DialoguePlan + RealizationFrame validator;
- provider mesh;
- STT Groq;
- Web/PWA atual;
- Fish/Gemini voice chain;
- Avatar Core/Studio;
- observability/operations/CI/container.

## 5. Implementados mas ainda não vivendo completamente

Auditoria 2026-10-31 encontrou estes gaps principais:

### Autonomy Scheduler

`AutonomyEngine.schedule_idle()` existe, porém o runtime normal não agenda o ciclo nem executa continuamente `Scheduler.run_due()`.

**Status:** PRONTO NÃO INTEGRADO.

### Autonomy → ActionEngine

`AutonomyEngine.cycle()` pode selecionar/persistir intenção e publica `autonomy.decision`, mas não existe subscriber normal levando a intenção ao planner/executor do `ActionEngine`.

**Status:** PRONTO/PARCIAL; execução autônoma não fecha o loop.

### ACIR normal

`PerspectiveResolver` e `ACIRCompiler` existem, mas o fluxo Web normal não compila ACIR antes da cognição/provider.

**Status:** PRONTO NÃO INTEGRADO.

### Relationship learning

`RelationshipEvolutionService` existe e relações iniciais são seedadas, porém não foi encontrado pipeline normal de aprendizagem relacional a partir de conversa/reflection. `relationship_learning` existe no contrato/test isolation, mas não controla hoje uma mutação relacional normal identificada.

**Status:** PRONTO/PARCIAL.

### Perspective consolidation

`PerspectiveConsolidator` é instanciado/testado, sem consumidor normal encontrado.

**Status:** PRONTO NÃO INTEGRADO.

### Speaker tracking

`SessionSpeakerTracker` e factory existem, mas a Web normal usa speaker declarado; diarização/identificação real não está integrada.

**Status:** PRONTO NÃO INTEGRADO.

### Core EnergyVad

Instanciado, mas o caminho Web atual usa VAD do navegador e não foi encontrado consumidor normal do `core.vad`.

**Status:** DORMENTE/LEGADO; não remover até definir plataforma consumidora.

## 6. Dívida / duplicação

### Cognição

Caminho vigente é uma pilha evolutiva:

```text
service
→ hardened
→ resilient
→ production
→ structured_runtime
```

Além de pares `context/context_v1`, `store/store_v1` e gerações de providers.

Não apagar bases isoladamente: a implementação final ainda herda comportamento. Refactor futuro deve achatar com replay/regressões.

### Web legado

`web/app.js` antigo não é carregado pelo HTML atual e não foram encontradas referências normais. Ele usa `speechSynthesis` superseded.

**Status:** MORTO / candidato forte a remoção.

### ADR duplicado

Existem dois ADRs numerados 0022: Fish streaming e Avatar bundle. Corrigir a numeração documental; Fish mantém 0022 e Avatar bundle passa a 0024.

## 7. Não implementado ainda

V1.1:

- visual perception;
- screen mapping;
- OCR/keyframes/scene changes;
- integração final de presença visual/corpo na superfície principal;
- Living Console final.

V1.2:

- preferência estética persistente;
- desejo de mudar aparência;
- escolha/avaliação de roupa;
- self-image ownership.

Backlog externo:

- Discord;
- browser/plugin integrations;
- Minecraft/games;
- OBS/PRISM avançado;
- outros plugins reais.

## 8. Próxima ordem recomendada

Antes de abrir uma grande frente nova:

```text
P0
1. Scheduler lifecycle + autonomia idle
2. autonomia → ActionEngine bounded
3. ACIR/Perspective no caminho normal
4. relationship learning real + test isolation efetivo
5. PerspectiveConsolidator

P1
6. remover web/app.js morto
7. corrigir/sincronizar docs/ADRs
8. expor TTFS Fish no diagnóstico Web
9. planejar flatten da cognição

P2
10. V1.1 percepção + presença visual
11. Android Living Console

P3
12. V1.2 estética/self-image
```

Regra: **conectar o cérebro já implementado antes de inventar mais cérebro.**

## 9. Últimos merges relevantes

- PR #79 — Portable Avatar ZIP bundle — merge `693538453a17e3eaf05b16543be01a3a68365647`.
- PR #80 — Fish voice stability + first-audio telemetry — merge `ca8fcf6d803657d99f323573cf482eb2d830a8a1`.

CI do #80: Core Python 3.11–3.14, pre-deploy security e cloud container contracts passaram. Deploy Northflank do merge foi confirmado `success`.
