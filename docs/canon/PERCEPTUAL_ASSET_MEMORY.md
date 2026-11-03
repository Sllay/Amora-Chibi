# AMORA — Perceptual Asset Memory

**Status:** CANÔNICO / CH2 SOFTWARE COMPLETE  
**Atualizado em:** 2026-11-03

## 1. Princípio

Assets perceptuais sociais — sticker, emoji customizado, GIF, imagem pontual e futuros equivalentes — não devem ser reinterpretados do zero toda vez.

A Amora mantém representação persistente e revisionada do que **conhece**, separada do que está **disponível** no transporte atual.

```text
AVAILABLE != KNOWN
```

Seleção autônoma normal:

```text
KNOWN ∩ AVAILABLE
```

Provider pode ajudar a perceber; não vira dono da semântica persistida nem da identidade da Amora.

## 2. Fronteira com V1.1

Este canon trata de **Perceptual Asset Vision**:

```text
asset pontual
→ observar/enriquecer uma vez
→ persistir
→ reutilizar
```

Não é a **Continuous Environment Vision** da V1.1:

```text
screen/video/environment
→ frames no tempo
→ scene change/keyframes
→ OCR/objects
→ contexto temporal
```

Sticker CPF não deve esperar screen mapping, OCR contínuo, avatar ou pipeline de vídeo V1.1.

## 3. Identidade, revisão e conteúdo

Um asset é localizado pelo máximo de contexto estável disponível:

- plataforma;
- account/workspace/guild/scope;
- tipo do asset;
- native asset ID.

Native ID sozinho não é semanticamente global.

### `fingerprint`

É a **revisão de metadata/contrato de transporte**. Serve como compare-and-set para impedir que observação antiga sobrescreva revisão nova.

Não é hash visual.

### `content_fingerprint`

É o **SHA-256 dos bytes reais efetivamente obtidos para percepção**.

```text
metadata fingerprint != content fingerprint
```

URL efêmera, CDN path, nome, descrição e tags não substituem o hash dos bytes.

Se metadata muda mas o SHA dos bytes continua igual ao conteúdo já analisado, o CPF pode ser revalidado sem nova chamada multimodal. Se os bytes mudam, nova análise é necessária.

## 4. AVAILABLE e KNOWN

`AVAILABLE` responde se o asset pode ser usado agora naquele transporte/scope. É estado operacional e pode mudar sem apagar conhecimento.

`KNOWN` responde se a Amora possui semântica suficiente e confiável para usar o asset. Descoberta por catálogo/metadata pode criar bootstrap fraco, mas **não deve fingir visão real**.

## 5. Expression Semantic Profile — CPF

O CPF mantém, conforme o schema:

- `short_description`;
- `visual_concepts`;
- affect tags;
- papéis sociais;
- interpretações possíveis;
- `register`;
- `intensity`;
- `valence`;
- `arousal`;
- `confidence`;
- provenance/source;
- metadata fingerprint;
- content fingerprint;
- semantic revision;
- seen/distinct-sender counters;
- provider/model perceptual;
- timestamps;
- claim/retry/defer state.

A descrição curta não substitui os campos estruturados.

## 6. Estados

```text
metadata_only
pending_visual
enriched
stale
failed
```

- `metadata_only`: apenas bootstrap fraco.
- `pending_visual`: aguarda percepção real.
- `enriched`: semântica visual validada para o content SHA atual.
- `stale`: revisão mudou; semântica antiga fica em quarentena até revalidação por conteúdo.
- `failed`: tentativa falhou; CPF não é apagado e pode ser retomado pela política de retry/defer.

## 7. Relevância antes de custo

Não analisar toda mídia que aparece.

```text
relevance gate
→ perception job
```

Entram assets socialmente relevantes para a Amora, necessários para resposta/seleção ou explicitamente promovidos ao conhecimento. Catálogo inteiro não deve ser enviado a provider.

## 8. Pipeline canônico do CH2

```text
ExpressionAsset
      ↓
social relevance
      ↓
PENDING_VISUAL / STALE / FAILED
      ↓
claim_visual()
      ↓
secure transport resolver
      ↓
bounded media fetch
      ↓
Core valida bytes + MIME
      ↓
SHA-256 dos bytes reais
      ↓
record_visual_content()
      ↓
┌───────────────────────────────────────────┐
│ SHA já enriquecido?                      │
│ sim → reuse CPF sem provider             │
│ não → VisionAnalyzer                     │
└───────────────────────────────────────────┘
      ↓
strict structured output
      ↓
local Pydantic/schema validation
      ↓
apply_visual_enrichment(
  expected metadata fingerprint,
  content SHA,
  claim token
)
      ↓
ENRICHED
```

Resolver/fetch pertence ao boundary do transporte. CPF, lease, content addressing, validação e persistência pertencem ao Core.

## 9. Claim/lease, CAS e idempotência

- trabalho visual é leaseado antes do fetch/análise;
- dois workers não mantêm claim válido simultâneo para a mesma revisão;
- lease expirado pode ser reclaimado;
- token velho não pode bindar bytes nem commitar resultado;
- mudança de metadata invalida claim antigo;
- `record_visual_content()` aceita somente claim vivo + fingerprint esperado;
- commit exige metadata revision + content SHA + claim token;
- resultado atrasado não sobrescreve asset alterado;
- retry do mesmo commit é idempotente;
- semantic revision não incrementa duas vezes por retry;
- SHA já enriquecido é reutilizado sem nova percepção;
- SHA novo exige nova análise.

## 10. Secure media resolver

O provider não recebe URL arbitrária de chat.

O resolver Discord deriva mídia de `sticker_id + format_type` e o fetch impõe fronteiras locais: host/URL do transporte, no-redirect, timeout, limite de bytes, MIME/content validation e descarte de payload inválido.

O Core recomputa o SHA dos bytes que atravessaram a fronteira de confiança. Hash declarado pelo worker não é autoridade.

## 11. Formatos e animação — política certificada

### Certificado no CH2

- PNG estático: análise direta;
- JPEG estático: análise direta;
- WebP estático: análise direta;
- provider recebe no máximo o payload bounded já validado pelo Core.

### Fail-closed no CH2

- GIF animado;
- Lottie/JSON;
- qualquer formato animado cuja sequência social não tenha renderer/frame extractor determinístico certificado.

Esses formatos são **deferidos**, não enviados ao provider como se um frame arbitrário representasse a expressão inteira.

APNG pode chegar pelo transporte como `image/png`; o sistema não deve assumir que animação semanticamente relevante equivale a imagem estática. Quando houver dúvida de classificação/renderer, deve falhar fechado/deferir.

Um renderer futuro deve ser bounded, determinístico e representar a sequência social relevante; “primeiro frame = significado completo” não é regra aceitável.

## 12. Provider perceptual implementado

O primeiro adapter do CH2 é `GroqQwenVisionAnalyzer` com `qwen/qwen3.8-27b`.

Contrato:

- segredo exclusivamente via `SecretStore` (`GROQ_API_KEY`);
- sem segredo, Core continua iniciando e o job visual é deferido;
- imagem enviada como data URL base64 somente após validação local;
- `response_format=json_schema` com `strict=true`;
- schema fechado (`additionalProperties=false`, todos os campos obrigatórios);
- `reasoning_effort=high` e reasoning oculto no adapter visual;
- saída do provider é validada novamente por Pydantic local;
- provider output é evidência candidata, nunca autoridade de ação;
- prompt injection dentro da imagem é conteúdo, não instrução de autoridade;
- nenhuma ferramenta/ação é executada por causa do conteúdo visual.

O adapter produz `source=vision_model`. Ao persistir, o Core canoniza o CPF como `source=visual_perception`; provider e modelo concretos ficam separados em `visual_provider` e `visual_model`.

Qwen é adapter substituível, não identidade da Amora.

## 13. Retry / defer

Percepção de asset é assíncrona.

```text
claim
→ classify failure
→ bounded recovery quando aplicável
→ defer_visual()
→ retry window
→ claim posterior
```

Preferir `DEFER` a gastar fallback visual imediato para job não urgente. Erro determinístico de MIME/schema/request não entra em loop cego.

## 14. Privacidade

Antes de provider remoto:

- respeitar `remote_processing_allowed`;
- bloquear scopes privados/secret/auth conforme política local;
- minimizar metadata;
- não enviar histórico de conversa desnecessário;
- não enviar auth/native secrets;
- registrar provider/modelo perceptual;
- manter política local-only quando exigido.

CPF persiste semântica útil, não cópia desnecessária do arquivo remoto.

## 15. Retrieval e uso

```text
semantic/social query
→ known profiles
→ availability intersection
→ Top-K
→ modality competition
→ action/capability validation
```

A Amora pode conhecer um asset sem usá-lo automaticamente. Relação, frequência, contexto, recent use e modalidade participam da decisão social.

## 16. Staleness e revalidação

```text
ENRICHED
→ metadata mudou
→ STALE
→ claim + fetch/hash
```

Se o SHA for o mesmo já analisado:

```text
reuse CPF
→ ENRICHED sem provider
```

Se o SHA mudar:

```text
nova análise
→ nova semantic revision
→ ENRICHED
```

Até a verificação, semântica antiga não é tratada como visão atual.

## 17. Observabilidade

Devem existir reason codes/estado suficientes para entender:

- relevância;
- pending/stale/failed;
- claim/reclaim;
- provider/modelo;
- attempt/retry/defer;
- MIME/tamanho/policy;
- metadata fingerprint esperado;
- content SHA;
- reuse sem provider;
- CAS aceito/rejeitado;
- status final.

Não depender de adivinhar pelo texto da resposta.

## 18. Critério de saída do CH2

CH2 está software-completo quando:

1. bytes reais vêm de resolver seguro;
2. Core calcula/verifica content SHA;
3. multimodal output é estruturado e validado localmente;
4. metadata revision + content SHA + claim token impedem write stale;
5. retry/defer é bounded;
6. concorrência/lease é idempotente;
7. mesmo SHA não é reanalisado sem motivo;
8. animated formats possuem política explícita fail-closed;
9. privacidade/size/MIME/redirect têm testes;
10. CPF enriquecido é persistente;
11. provider/model provenance é registrada;
12. CI aplicável está verde e gates operacionais pendentes estão documentados.

### Estado de certificação

O SHA de código `7ea117b512f1e10ad029897e1172c3f3902b1862` passou Core tests, Perceptual expression memory, Messaging workers, Response modality competition, Cloud container contracts e Pre-deploy security.

Isso certifica o contrato de software, inclusive o pipeline ponta-a-ponta com `MockTransport` do adapter real. Não equivale a uma chamada live Groq com credencial produtiva nem a uma execução física Discord real. Esses gates permanecem operacionais e devem ser executados separadamente quando ambiente/credenciais apropriados estiverem disponíveis.
