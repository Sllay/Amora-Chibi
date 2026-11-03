# AMORA — Project State / Checkpoint Operacional

**Atualizado em:** 2026-11-03  
**Base funcional consolidada em `main`:** PR #123 / `ac13f4d5a84dff2a908c47d28ccf4cf4a4c539af`  
**Schema atual em `main`:** **17**  
**Objetivo:** retomar o projeto sem reconstruir decisões por chat.

## 1. Fonte de verdade

Ordem de leitura para trabalho novo:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/roadmap/VERSIONS.md`;
5. canons especializados relevantes;
6. ADRs relevantes;
7. este `PROJECT_STATE.md`;
8. código, schemas, testes, PRs e CI atuais.

Se checkpoint e código divergirem, isso é dívida do CH atual.

## 2. Invariantes

- **Amora não é um LLM.** Core possui identidade, memória, relações, emoção, world/self state, `DialoguePlan`, policy/authority e observação de efeitos.
- Providers/modelos são capacidades substituíveis por task class.
- `AVAILABLE != KNOWN` para assets perceptuais.
- ação física só existe depois de capability/authority/policy/validation local.
- `WEB_SAFARI != DEVICE_A15`.
- CI verde não substitui certificação física/live.
- custo alvo continua **R$ 0 sempre que praticável**.
- chave/token/segredo não vai para Git, prompt ou log.

## 3. CH1 — COMPLETED

Promovidos e recertificados: #116, #117, #118, #119, #120, #121 e #122.

#115 foi fechada sem merge por estar supersedida.

Durante #121, cinco testes antigos hardcodavam schema 15; foram corrigidos para depender de `amora.storage.LATEST_SCHEMA_VERSION`.

ADR-0033 registra continuidade, fronteira **Perceptual Asset Vision != Continuous Environment Vision** e recovery-before-fallback.

## 4. Capability + cognition baseline

Fluxo canônico:

```text
Capability Registry
→ projection/retrieval
→ Decision / DialoguePlan
→ policy + authority
→ ActionBus / adapter
→ Observation
```

Provider não inventa capability IDs, permissões, resultados ou poderes.

Baseline de providers até promoção explícita:

- social dialogue: **Qwen 3.8 27B**;
- reasoning specialist: **GPT-OSS 120B**;
- Mistral Large 4 via NVIDIA: candidato em CH3, não promovido.

## 5. Response Modality Competition — ATIVA

Texto, expressão nativa/sticker e futuras modalidades competem semanticamente antes da execução.

Seleção normal de asset:

```text
KNOWN ∩ AVAILABLE
```

Catálogo/IDs continuam autoridade local do Core/adapter.

## 6. CPF perceptual persistente

Schema 16 introduziu memória perceptual persistente, bootstrap fraco por metadata, estados `metadata_only/pending_visual/enriched/stale/failed`, compare-and-set e reuso entre restarts.

CH2 promoveu o contrato para schema **17** com content addressing, lease visual e percepção multimodal pontual real.

## 7. CH2 — Visual Expression Enrichment — COMPLETED / MERGED

Delivery:

- **PR #123** `feat: add content-addressed visual expression enrichment`;
- merge commit em `main`: **`ac13f4d5a84dff2a908c47d28ccf4cf4a4c539af`**;
- schema atual: **17**;
- certificação de código: `7ea117b512f1e10ad029897e1172c3f3902b1862`.

### Contrato entregue

```text
relevant asset
→ PENDING_VISUAL / STALE / FAILED
→ claim/lease
→ Discord secure resolver + bounded fetch
→ Core valida bytes e recalcula SHA-256
→ bind content ao claim
→ [SHA conhecido? reuse : VisionAnalyzer]
→ strict structured output
→ local Pydantic validation
→ metadata revision + content SHA + claim token CAS
→ ENRICHED
```

Implementado:

- schema 17;
- `fingerprint` de metadata separado de `content_fingerprint` real;
- claim/lease transacional e token opaco;
- reclaim após expiração e rejeição de token velho;
- CAS por metadata revision + content SHA + claim;
- stale-result rejection;
- retry/defer persistente;
- reuse do mesmo SHA sem nova chamada remota nem nova semantic revision;
- provider/model provenance;
- recovery/import schema 16 → 17;
- worker boundary autenticado claim/content/defer;
- Discord sticker resolver derivado de `sticker_id + format_type`, sem URL arbitrária de chat;
- bounded fetch com host/formato/MIME/content/size/redirect/timeout validation;
- visual worker isolado de heartbeat/reconnect;
- política de privacidade persistida antes do job remoto;
- Core recomputa SHA; worker não é autoridade do hash;
- `VisionAnalyzer` substituível;
- `GroqQwenVisionAnalyzer` para `qwen/qwen3.8-27b`;
- segredo exclusivamente por `SecretStore`;
- sem Groq secret, bootstrap continua funcionando e job visual é deferido;
- imagem base64 + strict JSON Schema no provider;
- validação local Pydantic depois do provider;
- provider source `vision_model` canonizado no CPF como `visual_perception`, com provider/model em campos separados;
- pipeline end-to-end certificado com `MockTransport` do adapter real;
- testes de concorrência, lease, CAS, privacidade, mídia, provider contract, persistência e reuse.

### Formatos

Certificado para análise direta no CH2:

- PNG estático;
- JPEG estático;
- WebP estático.

Fail-closed/defer até renderer/frame extractor determinístico:

- GIF animado;
- Lottie/JSON;
- animação cuja sequência social não possa ser representada com segurança pelo contrato atual.

Não fingir que primeiro frame representa a expressão inteira.

### CI do código final

O SHA `7ea117b512f1e10ad029897e1172c3f3902b1862` passou:

- Core tests;
- Perceptual expression memory;
- Messaging workers;
- Response modality competition;
- Cloud container contracts;
- Pre-deploy security.

Depois desse SHA houve apenas três commits de documentação antes do merge.

### Gates operacionais ainda abertos

Não inferir PASS por CI:

- chamada live Groq/Qwen com credencial operacional;
- mídia real Discord atravessando resolver/fetch/worker/Core/provider;
- reconnect/permissões/intents do Discord em ambiente real;
- validação física/operacional longa no dispositivo/infra alvo.

Esses gates continuam explicitamente pendentes; não reabrem o CH2 software.

## 8. Duas visões diferentes

### Perceptual Asset Vision — CH2

```text
asset relevante
→ fetch seguro
→ hash real
→ percepção pontual
→ CPF persistente
→ reuso
```

### Continuous Environment Vision — V1.1

```text
screen/video/environment
→ captura temporal
→ keyframes/scene changes
→ OCR/objects
→ contexto temporal + áudio
→ world/perception events
```

Não puxar V1.1 para dentro do CH2.

## 9. CH3 — Provider Certification + Resilience — IN PROGRESS

PR ativa:

- **#124** `feat: add provider certification harness and Mistral Large 4 candidate`;
- branch: `feat/ch3-provider-certification-resilience`;
- dependência CH2 resolvida;
- próximo passo: retarget/sync #124 para `main` e rodar full Core regression.

Já implementado/certificado na branch do CH3:

- `SOCIAL_SURFACE_REPLAY_V1`;
- Mistral Large 4 NVIDIA como candidato `certification_only`;
- identical-context A/B Qwen x Mistral;
- context fingerprint e execução sequencial;
- `promotion_decision=none` por contrato;
- Retry-After;
- bounded exponential backoff + jitter;
- structured repair bounded;
- `ProviderCircuitRegistry` por provider + task class;
- `CLOSED → OPEN → HALF_OPEN` com probe único;
- sucesso fecha / falha reabre;
- ProviderRouter continua dono do ranking.

Ainda pendente no CH3:

- retarget/sync #124 para `main`;
- full Core regression na base final;
- A/B live Qwen x Mistral + revisão humana;
- evidência live de latência/quota/custo;
- Mistral x GPT-OSS no reasoning;
- promoção somente se evidência justificar.

## 10. Recovery-before-fallback

Direção canônica:

```text
primary
→ classify failure
→ bounded recovery
→ circuit/cooldown
→ fallback OR defer OR local degrade
```

Diálogo tem budget curto. Job perceptual assíncrono prefere `DEFER` a fallback imediato. Depois de delivery válido, não costurar provider no meio da mesma fala.

## 11. Gates físicos/operacionais gerais ainda abertos

- Discord real: guild/DM/reply/reconnect/intents/permissões;
- WhatsApp linked-device real;
- Background Voice / WebRTC Call Mode com iPhone;
- long-run voz/latência/térmica/barge-in;
- Visual Expression Enrichment live;
- Continuous Environment Vision V1.1;
- corpo/avatar final.

## 12. Linha ativa

### CH1 — Consolidar e organizar

**Status:** `COMPLETED`

### CH2 — Visual Expression Enrichment

**Status:** `COMPLETED / MERGED — PR #123 / ac13f4d5`

### CH3 — Provider Certification + Resilience

**Status:** `IN_PROGRESS — PR #124; retarget para main é o próximo passo`

Nenhum CH4 será aberto antes de CH3 ser consolidado.

## 13. Regra de fechamento de CH

Nenhum CH é `COMPLETED` até registrar:

1. código/schema/testes integrados ou prontos para promoção;
2. CI aplicável verde;
3. gates físicos/live pendentes explicitamente;
4. `PROJECT_STATE.md` atualizado;
5. roadmap/canon/ADR afetados atualizados;
6. próximo estado reduzido a no máximo três CHs ativos;
7. nenhuma decisão crítica vivendo só no chat.
