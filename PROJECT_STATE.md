# AMORA — Project State / Checkpoint Operacional

**Atualizado em:** 2026-11-02  
**Base funcional consolidada:** `main` após merge da PR #121 (`a42270db5ea427c483adf67c8e50206ec6e74aa9`)  
**Schema canônico atual:** **16**  
**Objetivo:** checkpoint curto para retomar o projeto sem reconstruir decisões por chat.

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

Se checkpoint e código divergirem, isso é dívida do CH atual e deve ser corrigido antes de abrir nova frente grande.

## 2. Invariantes

- **Amora não é um LLM.** O Core possui identidade, memória, relações, emoção, world/self state, `DialoguePlan`, policy/authority e observação de efeitos.
- Providers/modelos são capacidades substituíveis por task class.
- `AVAILABLE != KNOWN` para assets perceptuais.
- ação física só existe depois de capability/authority/policy/validation local.
- `WEB_SAFARI != DEVICE_A15`.
- CI verde não substitui certificação física real.
- objetivo de custo continua **R$ 0 sempre que praticável**.
- nenhuma chave/token/segredo vai para Git, prompt ou log.

## 3. CH1 — CONSOLIDADO

A antiga pilha empilhada foi promovida em ordem e recertificada em cada base nova:

- PR #116 — capability awareness + perceptual asset memory canon;
- PR #117 — canonical capability registry;
- PR #118 — capability projection para contexto cognitivo;
- PR #119 — ações cognitivas grounded nas capabilities projetadas;
- PR #120 — semantic response modality competition;
- PR #121 — persistent perceptual expression memory / CPF.

A PR #115, linha antiga de sticker expressions, foi **fechada sem merge** por estar supersedida por #120/#121.

Durante a promoção da #121, o Core CI encontrou cinco testes antigos hardcodando schema `15`. A migration para schema 16 estava correta. Os testes foram corrigidos para depender de `amora.storage.LATEST_SCHEMA_VERSION`, removendo a dívida em vez de trocar apenas `15 -> 16`. No head final da #121 passaram:

- Core tests;
- Perceptual expression memory;
- Response modality competition;
- Cloud container contracts;
- Pre-deploy security.

## 4. Capability Awareness — ATIVO

Fluxo canônico:

```text
Capability Registry
→ capability projection/retrieval
→ Decision / DialoguePlan
→ policy + authority
→ ActionBus / adapter
→ Observation
```

O provider não inventa capability IDs, permissões, resultados ou poderes. Linguagem natural não é keyword gate de autoridade.

## 5. Response Modality Competition — ATIVA

Texto, expressão nativa/sticker e outras modalidades competem semanticamente antes da execução.

Seleção de asset normal usa:

```text
KNOWN ∩ AVAILABLE
```

Catálogo nativo/IDs continuam autoridade local do adapter/Core; não são despejados no provider.

## 6. CPF perceptual persistente — ATIVO

Schema 16 mantém memória perceptual de expressão persistente com identidade por plataforma/account/scope/tipo/native ID, fingerprint, semantic revision, contadores e provenance.

Estados atuais:

```text
metadata_only
pending_visual
enriched
stale
failed
```

A base atual já suporta:

- descoberta/bootstrap por metadata fraca;
- separação `AVAILABLE` x `KNOWN`;
- persistência entre restarts;
- `pending_visual()`;
- `apply_visual_enrichment(expected_fingerprint)` com compare-and-set;
- rejeição de resultado stale;
- retry idempotente para mesmo fingerprint.

**Limite explícito:** a Amora ainda não vê pixels de sticker. Metadata bootstrap não é visão real e não deve ser apresentada como tal.

## 7. Duas visões diferentes

### Perceptual Asset Vision — CH2

Sticker/emoji/GIF/imagem pontual:

```text
asset relevante
→ fetch seguro
→ hash dos bytes reais
→ multimodal structured analysis
→ validação local
→ CPF persistente
→ reuso
```

Isso pode e deve existir antes da V1.1.

### Continuous Environment Vision — V1.1

```text
screen/video/environment
→ captura temporal
→ scene change / keyframes
→ OCR/objetos
→ contexto temporal + áudio
→ world/perception events
```

Não confundir as duas frentes.

## 8. Provider baseline e candidatos

Baseline até CH3 certificar mudança:

- **social dialogue:** Qwen 3.8 27B;
- **reasoning specialist:** GPT-OSS 120B;
- **Mistral Large 4 via NVIDIA** (`mistralai/mistral-large-4-1t-a52b`): candidato para diálogo, reasoning e percepção multimodal.

Se Mistral vencer diálogo, Qwen vira fallback. Se vencer reasoning, GPT-OSS vira fallback. Nenhuma promoção acontece por intuição ou porque um modelo parece mais permissivo.

## 9. Recovery-before-fallback — DECISÃO CANÔNICA

Fallback não reage ao primeiro timeout.

```text
primary
→ classify failure
→ bounded recovery
→ circuit/cooldown
→ fallback OR defer OR local degrade
```

Regras registradas em `docs/canon/PROVIDER_MESH.md` e ADR-0033:

- transient network/timeout/5xx/capacity: retry bounded com backoff + jitter se o budget permitir;
- `429`: respeitar `Retry-After`/budget;
- auth/bad request determinístico: sem retry cego;
- structured output inválido: repair/retry bounded;
- circuit breaker com `HALF_OPEN`;
- uma falha isolada não bane o primary;
- diálogo tem budget curto;
- perception job assíncrono prefere `DEFER` a fallback imediato;
- depois do início de delivery validado, não costurar outro provider na mesma fala.

A implementação/certificação completa dessa política pertence ao CH3; a decisão arquitetural já está congelada.

## 10. Gates físicos/operacionais ainda abertos

Não declarar PASS por inferência:

- Discord real: guild/DM/reply/reconnect/intents/permissões;
- WhatsApp linked-device real: pairing/reconnect/offline sync e storage auth produtivo;
- Background Voice / WebRTC Call Mode com iPhone + app em foreground;
- long-run voz/latência/térmica/barge-in;
- Continuous Environment Vision V1.1;
- corpo/avatar final.

## 11. Próximos CHs

Somente três movimentos grandes ficam ativos no mapa:

### CH1 — Consolidar e organizar

**Status:** `COMPLETED em código; documentação desta consolidação em fechamento.`

Entregas: promover #116–#121, fechar #115 supersedida, corrigir schema-test debt, atualizar checkpoint/canon/roadmap/ADR.

### CH2 — Visual Expression Enrichment

**Status:** `NEXT`

Entregar somente:

```text
pending_visual
→ secure media resolver/fetch
→ SHA-256 real
→ multimodal provider
→ strict CPF schema
→ local validation
→ CAS
→ enriched
```

Inclui SSRF/MIME/size/animated formats, lease/idempotência, privacidade e retry/defer. Não puxar screen capture/OCR contínuo/avatar para este CH.

### CH3 — Provider Certification + Resilience

**Status:** `AFTER CH2`

- replay A/B Qwen x Mistral no diálogo;
- Mistral x GPT-OSS no reasoning;
- conjunto fixo de assets para percepção;
- pt-BR, personalidade, humor/palavrão contextual, adult-context permitido, assistantism, moralização/recusa indevida, grounding, latência, custo e structured output;
- implementar/certificar recovery-before-fallback por task class;
- promover Mistral somente onde vencer.

## 12. Regra de fechamento de CH

Nenhum CH futuro é `COMPLETED` até a mesma linha de entrega registrar:

1. código/schema/testes integrados;
2. CI aplicável verde;
3. gates físicos ainda pendentes explicitamente;
4. `PROJECT_STATE.md` atualizado;
5. roadmap/canon/ADR afetados atualizados;
6. próximo estado reduzido a no máximo três CHs ativos;
7. nenhuma decisão crítica vivendo só no chat.

ADR desta consolidação: `ADR-0033-ch1-continuity-perceptual-boundary-and-provider-recovery.md`.
