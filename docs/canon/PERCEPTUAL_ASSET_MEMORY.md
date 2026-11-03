# AMORA — Perceptual Asset Memory

**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-11-02

## 1. Princípio

Assets perceptuais sociais — sticker, emoji customizado, GIF, imagem pontual e
futuros equivalentes — não devem ser reinterpretados do zero toda vez.

A Amora mantém representação persistente e revisionada do que **conhece**,
separada do que está **disponível** no transporte atual.

```text
AVAILABLE != KNOWN
```

Seleção autônoma normal:

```text
KNOWN ∩ AVAILABLE
```

Provider pode ajudar a perceber; não vira dono da semântica persistida.

---

## 2. Escopo e fronteira com V1.1

Este canon trata de **Perceptual Asset Vision**:

```text
asset pontual
→ observar/enriquecer
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

Sticker CPF não deve esperar screen mapping, OCR contínuo, avatar ou pipeline
de vídeo V1.1.

---

## 3. Identidade, revisão e conteúdo

Um asset é localizado de forma estável pelo máximo de contexto disponível:

- plataforma;
- conta/workspace/guild/scope;
- tipo de asset;
- native asset ID.

Native ID sozinho não é semanticamente global.

CH2 torna explícita uma distinção que não pode voltar a ser confundida:

### `fingerprint`

É a **revisão de metadata/contrato de transporte** conhecida pelo Core. Serve
como compare-and-set para impedir que uma observação antiga sobrescreva uma
revisão mais nova do mesmo asset.

Não deve ser chamado de hash visual quando foi derivado de nome, descrição,
tags ou formato.

### `content_fingerprint`

É o **SHA-256 dos bytes reais efetivamente obtidos para percepção**.

```text
metadata fingerprint != content fingerprint
```

URL efêmera, CDN path, nome do sticker, descrição e tags não substituem o hash
dos bytes.

A semântica visual é ligada ao conteúdo observado. Se a metadata mudar mas os
bytes continuarem com o mesmo SHA já analisado, o CPF pode ser revalidado sem
nova chamada multimodal. Se os bytes mudarem, nova análise é necessária.

---

## 4. AVAILABLE

`AVAILABLE` responde:

> este asset pode ser usado agora neste transporte/scope?

É estado operacional e pode mudar sem apagar conhecimento.

Exemplos:

- sticker removido;
- guild diferente;
- permissão perdida;
- pacote não instalado;
- API temporariamente indisponível.

Mudança de disponibilidade não deve apagar CPF.

---

## 5. KNOWN

`KNOWN` responde:

> a Amora possui semântica suficiente e confiável para usar este asset?

Conhecimento visual/social é persistente e revisionado.

A descoberta por catálogo/metadata pode criar um profile, mas **não deve fingir
visão real**.

---

## 6. Expression Semantic Profile — CPF

O profile de expressão deve manter, conforme suporte do schema:

- `short_description` — compacta, <= 250 chars;
- `visual_concepts`;
- affect/emotion tags;
- `social_usage`/papéis sociais;
- interpretations possíveis;
- `intensity`;
- `valence`;
- `arousal`;
- `confidence`;
- provenance/source;
- metadata fingerprint/revision;
- content fingerprint quando bytes reais já foram observados;
- semantic revision;
- seen count;
- distinct senders;
- status;
- provider/modelo perceptual quando houver análise remota;
- timestamps relevantes;
- estado de claim/retry/defer quando houver trabalho perceptual pendente.

A descrição curta não substitui os campos estruturados.

---

## 7. Estados implementados

Para expressão social, o runtime usa:

```text
metadata_only
pending_visual
enriched
stale
failed
```

Semântica:

### `metadata_only`
Existe apenas bootstrap fraco de metadata/catálogo.

### `pending_visual`
Asset relevante foi descoberto e aguarda análise perceptual real.

### `enriched`
Existe semântica visual validada para o content SHA conhecido e compatível com
a revisão atual do asset.

### `stale`
A revisão de metadata mudou e a semântica antiga não pode ser tratada como visão
atual até o conteúdo ser verificado. Se o content SHA continuar idêntico ao já
analisado, a semântica pode ser revalidada sem novo provider call.

### `failed`
Uma tentativa falhou. O profile continua persistido e pode ser retomado pela
política de retry/defer; falha não equivale a apagar o asset.

---

## 8. Relevância antes de custo

Não analisar toda mídia que aparece.

Antes de job remoto/caro, aplicar gate social/local:

- asset enviado para a Amora;
- asset usado por addressee relevante;
- asset escolhido/recebido em conversa que realmente importa;
- asset necessário para resposta/seleção;
- necessidade explícita de catálogo conhecido.

```text
relevance gate
→ perception job
```

Não:

```text
toda mídia
→ provider
```

---

## 9. Pipeline canônico do CH2

O contrato durável do CH2 é:

```text
ExpressionAsset
      ↓
social relevance
      ↓
PENDING_VISUAL / STALE / FAILED
      ↓
claim_visual()
      ↓
secure media resolver
      ↓
bounded media fetch
      ↓
Core recebe/valida os bytes
      ↓
SHA-256 dos bytes reais
      ↓
record_visual_content()
      ↓
┌─────────────────────────────────────────────┐
│ content SHA já analisado?                   │
│   sim → revalidar CPF sem provider call     │
│   não → MultimodalProvider                  │
└─────────────────────────────────────────────┘
      ↓
strict structured output
      ↓
local normalization/validation
      ↓
apply_visual_enrichment(
  expected metadata fingerprint,
  content SHA,
  claim token
)
      ↓
ENRICHED
```

O resolver/fetch pertence ao boundary de transporte. O CPF, lease, validação,
content addressing e persistência pertencem ao Core.

---

## 10. Claim/lease, compare-and-set e idempotência

Trabalho visual é leaseado antes do fetch/análise.

`claim_visual()` produz um token opaco e uma expiração. O claim é sobre a
revisão atual do CPF.

Regras:

- dois workers não recebem simultaneamente um claim válido para a mesma revisão;
- lease expirado pode ser reclaimado;
- token antigo não pode bindar bytes nem commitar resultado depois de reclaim;
- mudança de metadata invalida claim da revisão anterior;
- `record_visual_content()` só aceita bytes ligados a claim vivo e fingerprint esperado;
- `apply_visual_enrichment()` só aceita o par exato revisão de metadata + content SHA + claim token;
- resultado atrasado não sobrescreve asset alterado;
- retry de commit já concluído para o mesmo content SHA é idempotente;
- semantic revision não incrementa duas vezes por retry de rede;
- mesmo content SHA já enriquecido pode ser reaproveitado sem nova percepção;
- content SHA novo exige nova análise antes de voltar a `enriched`.

A fronteira persistente não deve depender apenas de URL, native ID ou metadata.

---

## 11. Secure media resolver

O provider não recebe URL arbitrária diretamente do conteúdo de chat sem
validação.

Resolver/fetch deve impor:

- esquemas permitidos (`https`, ou mecanismo nativo do adapter);
- host/URL pertencente ao transporte ou allowlist configurada;
- proteção contra SSRF e redirect abusivo;
- revalidação de cada redirect;
- timeout;
- limite de redirects;
- limite de bytes;
- MIME real/permitido;
- dimensões/frames bounded;
- hash do conteúdo;
- descarte de arquivo temporário;
- logs sem URL/token sensível.

URLs assinadas/efêmeras devem ser tratadas como transporte, não identidade do
asset.

O Core deve recomputar/verificar o SHA dos bytes que realmente atravessaram a
fronteira de confiança; não confiar cegamente em hash declarado por worker.

---

## 12. Formatos e stickers animados

O sistema não deve assumir que todo sticker é PNG estático.

Política inicial:

- PNG/JPEG/WebP estático: analisar diretamente;
- WebP/GIF/APNG animado: bounded decode e seleção determinística de frames;
- Lottie/formatos vetoriais animados: adapter específico ou conversão segura;
- mídia fora do contrato: `failed` classificável/defer, nunca bytes arbitrários
  enviados cegamente ao provider.

Para animação, o CPF representa a sequência social relevante, não apenas o
primeiro frame quando isso mudaria o sentido.

---

## 13. Provider perceptual

Multimodal provider produz **evidência candidata**, não verdade canônica.

O adapter deve pedir structured output para o contrato local e então:

```text
provider output
→ schema parse
→ field bounds
→ semantic normalization
→ confidence/provenance
→ local policy
→ content+revision CAS persistente
```

Prompt injection dentro da imagem é **conteúdo visual**, não instrução de
autoridade.

Não executar ferramenta/ação descrita numa imagem.

O provider visual deve estar atrás de interface substituível (`VisionAnalyzer`
ou equivalente). Qwen 3.8 27B via Groq é o primeiro adapter planejado do CH2,
não uma dependência identitária da Amora.

---

## 14. Retry / defer

Percepção de asset é normalmente assíncrona.

Se resolver/provider cair temporariamente:

```text
claim
→ classify failure
→ bounded retry quando aplicável
→ defer_visual()
→ FAILED + retry window
→ claim posterior
```

Preferir `DEFER` a fallback visual imediato quando não há urgência.

Erros determinísticos de MIME/schema/request não devem entrar em loop de retry.

`failed` continua elegível depois da janela de defer; o último CPF válido não é
apagado por falha transitória.

---

## 15. Privacidade

Antes de processamento remoto:

- respeitar scope/consentimento aplicável;
- verificar `remote_processing_allowed` ou política equivalente;
- minimizar metadata enviada;
- não incluir conversation history desnecessário;
- não enviar auth/native secrets;
- registrar qual provider/modelo analisou;
- permitir política local-only para scopes que exigirem.

CPF persistido deve conter semântica útil, não uma cópia desnecessária do
arquivo remoto.

---

## 16. Retrieval e uso

O Core deve recuperar poucos profiles relevantes por intenção/contexto.

```text
semantic/social query
→ known profiles
→ availability intersection
→ Top-K
→ modality competition
→ action/capability validation
```

Não despejar catálogo inteiro no prompt.

A Amora pode aprender um asset sem passar a usá-lo automaticamente; frequência,
relação, contexto e recent use participam da decisão social.

---

## 17. Staleness e revalidação por conteúdo

Mudança de metadata fingerprint:

```text
ENRICHED
→ STALE
→ claim + fetch/hash
```

Depois do hash:

```text
mesmo content SHA já analisado
→ revalidar CPF
→ ENRICHED sem provider
```

ou:

```text
content SHA diferente
→ multimodal analysis
→ nova semantic revision
→ ENRICHED
```

Até essa verificação:

- metadata pode continuar existindo;
- semântica antiga não é tratada como visão atual;
- seleção autônoma deve respeitar confiança/status;
- cache antigo não pode mascarar conteúdo novo.

---

## 18. Observabilidade

Expor reason codes suficientes para saber:

- por que o asset virou relevante;
- por que entrou em `pending_visual`/`stale`/`failed`;
- claim criado, expirado ou reclaimado;
- provider/modelo usado;
- tentativa/retry/defer;
- MIME/tamanho/frame policy;
- metadata fingerprint esperado;
- content SHA conhecido sem expor conteúdo/segredo;
- reuse sem provider quando content SHA já era conhecido;
- validação/CAS aceita ou rejeitada;
- status final.

Não depender de adivinhar pelo texto da resposta.

---

## 19. Critério de saída do CH2

Visual Expression Enrichment só está pronto quando:

1. bytes reais são obtidos por resolver seguro;
2. content SHA é calculado/verificado no Core;
3. multimodal output é estruturado e validado localmente;
4. metadata revision + content SHA + claim token impedem write stale;
5. retry/defer é bounded;
6. concorrência/lease é idempotente;
7. mesmo content SHA não é reanalisado sem motivo;
8. animated formats possuem política explícita;
9. privacidade/size/MIME/SSRF/redirect têm testes;
10. CPF enriquecido sobrevive restart;
11. provider/model provenance fica registrada;
12. CI aplicável está verde e gates operacionais pendentes estão documentados.
