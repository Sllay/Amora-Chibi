# AMORA — Project State / Checkpoint Operacional

**Atualizado em:** 2026-11-03  
**Base funcional consolidada em `main`:** PR #128 / `847ef29ef0c1c3519c642733b1fd44340f5e4183`  
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

Baseline de providers até promoção explícita baseada em evidência:

- social dialogue: **Qwen 3.8 27B**;
- reasoning specialist: **GPT-OSS 120B**;
- Mistral Large 4 via NVIDIA: **candidato somente**, não promovido.

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
- merge em `main`: **`ac13f4d5a84dff2a908c47d28ccf4a4c539af`**;
- schema atual: **17**;
- head de código certificado: `7ea117b512f1e10ad029897e1172c3f3902b1862`.

Contrato entregue:

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

- metadata fingerprint separado de `content_fingerprint` real;
- claim/lease transacional, CAS e stale-result rejection;
- retry/defer persistente;
- reuse do mesmo SHA sem nova chamada remota nem nova semantic revision;
- provider/model provenance;
- worker boundary autenticado;
- Discord sticker resolver derivado de `sticker_id + format_type`, sem URL arbitrária de chat;
- bounded fetch com host/formato/MIME/content/size/redirect/timeout validation;
- política de privacidade persistida antes do job remoto;
- Core recomputa SHA; worker não é autoridade do hash;
- `VisionAnalyzer` substituível;
- `GroqQwenVisionAnalyzer` para `qwen/qwen3.8-27b`;
- segredo exclusivamente por `SecretStore`;
- imagem base64 + strict JSON Schema no provider e validação Pydantic local;
- pipeline end-to-end certificado com adapter real + `MockTransport`.

Formatos certificados diretamente: PNG/JPEG/WebP estáticos.

GIF/Lottie/animação sem renderer/frame extractor determinístico permanecem **fail-closed/deferred**. Não inferir significado completo por primeiro frame arbitrário.

Gates operacionais CH2 ainda abertos e separados do software:

- chamada live Groq/Qwen com credencial operacional;
- mídia real Discord atravessando resolver/fetch/worker/Core/provider;
- reconnect/permissões/intents do Discord em ambiente real;
- validação física/operacional longa no dispositivo/infra alvo.

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

## 9. CH3 — Provider Certification + Resilience — SOFTWARE MERGED / LIVE CERTIFICATION PENDING

Delivery de software:

- **PR #124** `feat: add provider certification harness and Mistral Large 4 candidate`;
- head final certificado: **`769cba5eae1677a5867a3765aab12c077abcf6fb`**;
- merge em `main`: **`b72e7d1e7628ff58e02042b3658863b481c02bfe`**;
- schema permanece **17**; CH3 não alterou storage schema.

Acesso operacional live integrado depois do harness:

- **PR #127** `feat: add CH3 live provider certification runner` → `run_live_social_ab()` + CLI one-shot; merge/head em `main`: **`72a9b615266df41182d48a8ab039ef574ffca66f`**;
- **PR #128** `feat: add Amora Shell admin command bus for live A/B` → Command Registry autenticado, jobs estruturados e `/web/amora-shell.html`; merge em `main`: **`847ef29ef0c1c3519c642733b1fd44340f5e4183`**;
- ADR-0035 + `docs/canon/ADMIN_CONSOLE.md` tornam o Amora Shell parte da arquitetura administrativa, sem bash/eval/exec/subprocess arbitrário;
- desbloqueio Web pode usar credencial operator existente ou a chave pessoal Dev assinada localmente no navegador, sem depender do shell da hospedagem;
- comando oficial para este gate: `providers certify social`.

Integrado em `main`:

- `SOCIAL_SURFACE_REPLAY_V1`;
- Mistral Large 4 NVIDIA como candidato `certification_only`;
- A/B identical-context Qwen × Mistral;
- fingerprint canônico de contexto e execução sequencial;
- `promotion_decision=none` por contrato;
- runner live read-only com `GROQ_API_KEY` + `NVIDIA_API_KEY` somente via SecretStore;
- Amora Command Bus com registry explícito, role operator/admin, timeout e histórico transitório de jobs;
- A/B social exclusivo no bus para impedir duas certificações simultâneas/quota burst;
- Retry-After em delay-seconds e HTTP-date;
- bounded exponential backoff + jitter;
- long `Retry-After` fora do budget de diálogo retorna controle ao Core em vez de dormir/retry inline;
- structured repair bounded e opt-in no runtime estruturado;
- `ResilientProviderProxy` preservado como transport-only por padrão para callers diretos;
- erro semântico original de realization preservado junto da classificação operacional `PROVIDER_STRUCTURED_OUTPUT_INVALID`;
- `ProviderCircuitRegistry` por provider + task class;
- `CLOSED → OPEN → HALF_OPEN` com probe único/lease;
- sucesso fecha, falha reabre e quota esgotada mantém OPEN;
- ProviderRouter continua dono do ranking/quota.

### Regressões encontradas no fechamento e corrigidas pela raiz

Ao recertificar #124 diretamente sobre `main`, o Core suite encontrou três incompatibilidades:

1. teste legado esperava retry inline em `429 + Retry-After: 60s`, contrariando o novo budget interativo;
2. o proxy de resiliência havia misturado transporte com validação semântica por padrão;
3. o wrapper preservava internamente o erro semântico de realization, mas a observabilidade externa o substituía pelo tipo da exceção.

Correção final:

- `Retry-After` longo → cooldown/circuit/failover, sem retry inline;
- `Retry-After` curto → bounded inline retry, coberto pelos testes CH3;
- proxy direto → transporte/retry por padrão;
- runtime social estruturado → um repair explícito;
- observabilidade mantém o código semântico original e a classificação operacional separadamente.

### CI final do harness CH3 — head `769cba5e...`

Todos os gates aplicáveis passaram no mesmo head:

- Core tests — Python 3.11 / 3.12 / 3.13 / 3.14;
- Provider certification;
- Cloud container contracts;
- Pre-deploy security;
- Cognitive capability projection;
- Capability grounded intent;
- Response modality competition.

### Recertificação do acesso operacional — PR #128 / head `102ab630...`

Todos os 7 workflows disparados pelo PR passaram:

- Core tests;
- Cloud container contracts;
- Pre-deploy security;
- Provider certification;
- Avatar tests;
- Capability grounded intent;
- Response modality competition.

Testes adicionais cobrem registry fechado, rejeição de texto de shell arbitrário, missing-secret sem vazamento, exclusão de A/B concorrente e acesso real por role operator via API.

### Ainda pendente no CH3 — live / decisão de provider

- executar o A/B live canônico Qwen × Mistral no social replay pelo **Amora Shell** (`providers certify social`) após a infra carregar o `main` atual;
- revisão humana de naturalidade, humor, personalidade, over-refusal e qualidade de recusa;
- evidência live de latência/quota/custo;
- Mistral × GPT-OSS no reasoning;
- promoção de provider **somente se evidência justificar**.

**Nenhum modelo foi promovido pelos merges #124, #127 ou #128.** Qwen e GPT-OSS mantêm os papéis atuais; Mistral permanece candidato.

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
- certificação live dos providers do CH3;
- Continuous Environment Vision V1.1;
- corpo/avatar final.

## 12. Linha ativa

### CH1 — Consolidar e organizar

**Status:** `COMPLETED`

### CH2 — Visual Expression Enrichment

**Status:** `COMPLETED / MERGED — PR #123 / ac13f4d5`

### CH3 — Provider Certification + Resilience

**Status:** `IN_PROGRESS — SOFTWARE + LIVE RUNNER + AMORA SHELL MERGED / LIVE CERTIFICATION PENDING — PRs #124/#127/#128`

Nenhum CH4 será aberto antes de fechar a decisão live do CH3 ou registrar explicitamente sua deferral.

## 13. Regra de fechamento de CH

Nenhum CH é `COMPLETED` até registrar:

1. código/schema/testes integrados ou prontos para promoção;
2. CI aplicável verde;
3. gates físicos/live pendentes explicitamente;
4. `PROJECT_STATE.md` atualizado;
5. roadmap/canon/ADR afetados atualizados;
6. próximo estado reduzido a no máximo três CHs ativos;
7. nenhuma decisão crítica vivendo só no chat.
