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

## 3. Identidade do asset

Um asset é identificado de forma estável pelo máximo de contexto disponível:

- plataforma;
- conta/workspace/guild/scope;
- tipo de asset;
- native asset ID;
- fingerprint/revision do conteúdo ou metadata confiável.

Native ID sozinho não é semanticamente global.

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
- `social_usage`;
- interpretations possíveis;
- `intensity`;
- `valence`;
- `arousal`;
- `confidence`;
- provenance/source;
- fingerprint;
- semantic revision;
- seen count;
- distinct senders;
- status;
- timestamps relevantes.

A descrição curta não substitui os campos estruturados.

---

## 7. Estados implementados

Para expressão social, o runtime atual usa:

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
Existe análise multimodal validada localmente para o fingerprint atual.

### `stale`
O fingerprint/revision mudou; semântica antiga não pode ser tratada como visão
atual até reenriquecimento.

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

## 9. Pipeline do CH2

A base persistente já existe. O enriquecimento real deve seguir:

```text
ExpressionAsset
      ↓
social relevance
      ↓
PENDING_VISUAL
      ↓
secure media resolver
      ↓
bounded media fetch
      ↓
SHA-256 dos bytes reais
      ↓
MultimodalProvider
      ↓
strict structured output
      ↓
local normalization/validation
      ↓
apply_visual_enrichment(expected_fingerprint)
      ↓
ENRICHED
```

### 9.1 Metadata fingerprint x content fingerprint

Metadata pode bootstrapar identidade/revision, mas a visão real deve amarrar o
resultado aos **bytes efetivamente analisados** quando o transporte permitir.

O worker deve conhecer:

- fingerprint esperado ao claimar o job;
- hash dos bytes baixados;
- fingerprint/revision vigente no momento do commit.

Resultado atrasado não pode sobrescrever asset alterado.

---

## 10. Compare-and-set e idempotência

`apply_visual_enrichment(expected_fingerprint)` é a fronteira de commit.

Regras:

- se fingerprint atual != esperado: rejeitar resultado como stale;
- retry da mesma análise para o mesmo fingerprint deve ser idempotente;
- semantic revision só avança quando existe mudança semântica válida;
- dois workers concorrentes não podem “vencer” sem controle;
- falha depois de download/análise não deve corromper o último CPF válido.

Para worker distribuído, usar claim/lease bounded ou equivalente.

---

## 11. Secure media resolver

O provider não recebe URL arbitrária diretamente do conteúdo de chat sem
validação.

Resolver/fetch deve impor:

- esquemas permitidos (`https`, ou mecanismo nativo do adapter);
- host/URL pertencente ao transporte ou allowlist configurada;
- proteção contra SSRF e redirect abusivo;
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
→ CAS persistente
```

Prompt injection dentro da imagem é **conteúdo visual**, não instrução de
autoridade.

Não executar ferramenta/ação descrita numa imagem.

---

## 14. Retry / defer

Percepção de asset é normalmente assíncrona.

Se o provider cair temporariamente:

```text
pending_visual
→ classify
→ bounded retry
→ DEFER
→ retry posterior
```

Preferir `DEFER` a fallback visual imediato quando não há urgência.

Erros determinísticos de MIME/schema/request não devem entrar em loop de retry.

---

## 15. Privacidade

Antes de processamento remoto:

- respeitar scope/consentimento aplicável;
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

## 17. Staleness

Mudança de fingerprint/revision:

```text
ENRICHED
→ STALE
→ pending reenrichment
```

Até reenriquecer:

- metadata pode continuar existindo;
- semântica antiga não é tratada como visão atual;
- seleção autônoma deve respeitar confiança/status;
- cache antigo não pode mascarar conteúdo novo.

---

## 18. Observabilidade

Expor reason codes suficientes para saber:

- por que o asset virou relevante;
- por que entrou em `pending_visual`;
- provider/modelo usado;
- tentativa/retry/defer;
- MIME/tamanho/frame policy;
- fingerprint esperado e revision sem expor segredo;
- validação/CAS aceita ou rejeitada;
- status final.

Não depender de adivinhar pelo texto da resposta.

---

## 19. Critério de saída do CH2

Visual Expression Enrichment só está pronto quando:

1. bytes reais são obtidos por resolver seguro;
2. fingerprint de conteúdo é verificável;
3. multimodal output é estruturado e validado localmente;
4. CAS impede write stale;
5. retry/defer é bounded;
6. concorrência é idempotente;
7. animated formats possuem política explícita;
8. privacidade/size/MIME/SSRF têm testes;
9. CPF enriquecido sobrevive restart;
10. o mesmo fingerprint não é reanalisado sem motivo.
