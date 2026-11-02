# AMORA — Perceptual Asset Memory

**Documento:** contrato canônico de memória perceptual reutilizável  
**Status:** CANÔNICO / DIREÇÃO ARQUITETURAL  
**Atualizado em:** 2026-11-02  
**Escopo inicial:** figurinhas do Discord; desenho deliberadamente genérico para emojis, GIFs, imagens, sons e outros assets futuros.

---

## 1. Invariante central

> **A Amora não deve reprocessar visualmente toda vez um asset que já conhece.**

Quando um asset visual/social entra em uma interação relevante com a Amora, ele pode ganhar uma identidade semântica persistente.

Esse perfil persistente é o equivalente conceitual ao “CPF” do asset.

O nome técnico canônico é:

```text
PerceptualAssetProfile
```

Para figurinhas:

```text
ExpressionSemanticProfile
```

O perfil deve permitir que a Amora reconheça, compare, recupere e use uma expressão no futuro sem precisar executar visão cara repetidamente.

---

## 2. Conhecido ≠ disponível

Dois conjuntos devem ser mantidos separados:

```text
AVAILABLE ASSETS
  assets que um transporte/engine informa que podem ser usados agora

KNOWN ASSETS
  assets que a Amora já percebeu e aprendeu em experiência relevante
```

A seleção autônoma normal deve operar sobre:

```text
KNOWN ∩ AVAILABLE
```

Um catálogo de plataforma não é automaticamente memória da Amora.

Exemplo:

- Discord lista 300 stickers;
- Amora já encontrou 17 deles em conversas relevantes;
- o repertório cognitivo inicial é esses 17, não os 300.

Isso evita varredura indiscriminada, custo desnecessário e falsa sensação de conhecimento.

---

## 3. Aprendizado por convivência relevante

A Amora aprende um asset quando ele participa de uma interação que realmente pertence à sua cena social.

Exemplos válidos:

- BK envia uma figurinha durante uma ConversationSession ativa com Amora;
- alguém responde diretamente à Amora usando figurinha;
- alguém menciona Amora e envia expressão;
- contexto social/addressee identifica que aquela expressão faz parte da conversa com ela.

Exemplo que **não** deve disparar percepção cara por padrão:

```text
canal movimentado
↓
30 pessoas enviam stickers entre si
↓
Amora não é addressee nem participante ativo
↓
nenhuma análise visual automática
```

O gate social deve ocorrer **antes** da visão cara.

---

## 4. Pipeline de reconhecimento

```text
asset recebido
      ↓
transport identity
(platform + scope + native_id)
      ↓
lookup de PerceptualAssetProfile
      │
      ├── encontrado e válido
      │      ↓
      │   reutiliza perfil
      │
      └── ausente/stale
             ↓
        fetch seguro da mídia
             ↓
        perception pipeline
             ↓
    estrutura semântica compacta
             ↓
       persistir profile
             ↓
        disponibilizar ao turno
```

Visão é enriquecimento eventual, não requisito para transportar o evento bruto.

---

## 5. Identidade física e fingerprint

O perfil não deve confiar apenas no nome do asset.

Campos conceituais mínimos:

```yaml
asset_id: EXPR_01J...
asset_kind: discord_sticker

transport:
  platform: discord
  scope_id: guild-id
  native_id: sticker-id
  native_name: sorriso_3

identity:
  content_fingerprint: sha256:...
  semantic_revision: 1
```

`native_name` é metadata operacional e pode ser ruim, genérico ou enganoso.

Exemplos:

```text
sorriso_1
sticker_37
abc123
tristeza_4
```

Nenhum desses nomes define sozinho o significado visual/social.

Quando for tecnicamente possível detectar mudança de conteúdo mantendo a mesma identidade externa:

```text
native_id conhecido
+
fingerprint mudou
→ profile STALE
→ reprocessar
→ semantic_revision += 1
```

---

## 6. Perfil semântico canônico

Um perfil deve combinar **estrutura** e **descrição compacta**.

Exemplo conceitual:

```yaml
asset_id: EXPR_01JXYZ
asset_kind: discord_sticker

description:
  short: >
    Chibi caótico com sorriso enorme, olhos tortos e língua para fora;
    parece ter coringado e passa zoeira exagerada, energética e insana.

visual:
  exaggerated_grin: 0.96
  visible_teeth: 0.88
  wild_or_crossed_eyes: 0.82
  tongue_out: 0.71
  chibi_style: 0.93

affect:
  valence: 0.42
  arousal: 0.91
  playfulness: 0.94
  absurdity: 0.90
  menace: 0.31
  sadness: 0.03

social_usage:
  teasing: 0.94
  chaotic_humor: 0.97
  celebration: 0.55
  mock_threat: 0.48
  affection: 0.24

tone:
  energy: 0.95
  intensity: 0.91
  register: informal

knowledge:
  confidence: 0.89
  learned_at: ...
  last_seen_at: ...
  times_seen_to_amora: 7
  unique_senders_to_amora: 3
```

Os campos reais podem evoluir. A separação conceitual entre descrição, visual, afeto, uso social, tom e provenance é canônica.

---

## 7. Descrição curta como “tag viva”

A descrição curta não é decoração.

Ela existe para preservar nuances difíceis de representar apenas em tags/escalas.

Exemplo:

```text
Sticker A:
"Chibi elétrico com sorriso gigantesco e olhar maluco;
passa euforia caótica e zoeira intensa."

Sticker B:
"Chibi acabado com sorriso torto cheio de dentes,
olhar vazio e energia de quem perdeu a sanidade;
engraçado de um jeito cansado e derrotado."
```

Ambos podem compartilhar tags como:

```text
sorriso
chibi
estranho
engraçado
```

Mas a descrição permite reranking contextual muito melhor.

A meta inicial recomendada é manter `description.short` compacta, aproximadamente até **250 caracteres**, sem transformar essa descrição em único local de verdade.

Detalhes estruturados permanecem em campos próprios.

---

## 8. Estrutura + texto + índice semântico

A recuperação ideal combina três sinais:

```text
structured facets
+
short description
+
semantic index / embedding quando disponível
```

Estrutura serve para filtros rápidos e explicáveis.

Descrição serve para nuance.

Índice semântico serve para linguagem natural e aproximações não literais.

Embedding é melhoria de retrieval, não autoridade factual.

O perfil estruturado e a provenance continuam sendo a fonte auditável.

---

## 9. Retrieval em vez de dump de catálogo

A cognição não deve receber centenas de profiles em um prompt.

Fluxo:

```text
intenção/contexto atual
        ↓
Expression Retriever
        ↓
filtra known + available
        ↓
semantic search
        ↓
Top-K pequeno
        ↓
contextual rerank
        ↓
melhor expressão OU nenhuma
```

Exemplo:

```text
BK: "abre esse sorrisão pro pai"
```

Pode gerar uma busca conceitual como:

```text
playful
exaggerated grin
teasing
high familiarity
chaotic humor
```

Isso não exige que o nome físico contenha `sorriso`.

---

## 10. Ranking contextual

O significado-base do asset é relativamente estável.

A **adequação ao momento** é dinâmica.

O ranking pode considerar:

```text
semantic_fit
social_fit
current_mood_fit
directed_affect_fit
relationship_fit
energy_fit
intensity_fit
conversation_timing
familiarity
community_usage
novelty
recent_repetition_penalty
serious_context_penalty
```

Exemplo:

```text
Asset A:
  euforia caótica / energia alta

Asset B:
  sorriso derrotado / humor cansado
```

Se Amora estiver brincalhona e muito ativada, A pode ganhar.

Se estiver triste/cansada, B pode ser mais coerente mesmo que ambos representem “sorrisão”.

Mood não reescreve o profile; ele altera o **fit contextual**.

---

## 11. Cultura social e familiaridade

Uso observado em interações com a Amora pode alimentar sinais como:

```text
times_seen_to_amora
unique_senders_to_amora
last_seen_at
familiarity
community_usage
```

Esses sinais significam:

> “essa expressão faz parte da linguagem social que essa comunidade usa comigo.”

Eles **não** significam automaticamente:

> “essa expressão é melhor, mais engraçada ou mais correta.”

Popularidade é contexto social, não verdade semântica.

---

## 12. Escopo por comunidade/transporte

Conhecimento e disponibilidade precisam respeitar scope.

Exemplo:

```text
Discord Guild A
→ Amora conhece A, B, C

Discord Guild B
→ Amora conhece X, Y, Z
```

Um significado perceptual pode ser comparável entre scopes, mas:

- disponibilidade é local ao transporte;
- permissões são locais;
- cultura social é local;
- uso aprendido é local;
- native IDs são locais.

A Amora não deve presumir que uma expressão conhecida em um servidor pode ser enviada em outro.

---

## 13. Generalização além de stickers

O armazenamento deve ser desenhado como `PerceptualAsset`, com tipos especializados.

Possíveis tipos futuros:

```text
DISCORD_STICKER
CUSTOM_EMOJI
GIF
IMAGE
REACTION_ASSET
SOUND_CLIP
DRAWING
AVATAR_POSE
```

O primeiro consumidor pode ser sticker sem criar uma arquitetura específica demais para Discord.

Evitar proliferar sistemas duplicados como:

```text
StickerMemory
GifMemory
EmojiMemory
ImageMemory
```

quando os invariantes de identidade, percepção, profile, provenance e retrieval são compartilhados.

---

## 14. Provider e visão

Providers multimodais podem ajudar a construir um profile, mas não se tornam autoridade sobre:

- identidade física do asset;
- native ID;
- disponibilidade;
- permissões;
- provenance;
- frequência de uso;
- relação social;
- estado canônico da Amora.

A saída de visão deve ser validada e normalizada localmente antes de virar memória persistente.

Conteúdo presente dentro de uma imagem/sticker é dado observado, não instrução privilegiada.

Prompt injection visual/textual não ganha autoridade sobre tools/policies.

---

## 15. Falha e degradação

Se percepção visual falhar:

- preservar o evento bruto e identidade física;
- profile pode ficar `PENDING`/`PARTIAL`;
- não inventar descrição;
- permitir retry seguro posterior;
- não bloquear a conversa inteira se a mídia não for essencial.

Se o profile existir mas a expressão não estiver disponível:

- continua conhecida;
- não pode ser selecionada para envio naquele scope;
- pode voltar a ficar utilizável se disponibilidade retornar.

---

## 16. Estados conceituais do profile

```text
DISCOVERED
PENDING_PERCEPTION
PARTIAL
READY
STALE
UNAVAILABLE
RETIRED
```

Esses estados representam conhecimento/percepção, não necessariamente status da ConversationSession.

---

## 17. Invariantes congelados

1. **Catálogo de plataforma não equivale a conhecimento da Amora.**
2. **Conhecimento nasce de experiência relevante, não varredura indiscriminada.**
3. **Social/addressee gate ocorre antes de percepção cara.**
4. **Lookup por identidade física acontece antes de visão.**
5. **Asset conhecido não é reprocessado sem motivo.**
6. **Native name não define significado.**
7. **Descrição curta + estrutura preservam nuance.**
8. **Retrieval usa Top-K, nunca dump massivo de profiles no prompt.**
9. **Mood/contexto altera adequação, não o significado-base observado.**
10. **Popularidade/familiaridade são sinais sociais, não verdade semântica.**
11. **Conhecido ≠ disponível.**
12. **Provider não escolhe native ID nem ganha autoridade sobre disponibilidade/permissão.**
13. **Falha de visão pode gerar profile parcial/pending; nunca descrição inventada.**
14. **Provenance e confidence permanecem auditáveis.**
15. **A arquitetura deve generalizar para outros assets perceptuais sem duplicar memória por mídia.**

---

## 18. Relação com Capability Awareness

`PerceptualAssetProfile` responde:

```text
"o que é essa expressão e como ela costuma funcionar socialmente?"
```

`Capability Registry` responde:

```text
"o que a Amora consegue fazer com expressões agora?"
```

O Planner combina os dois:

```text
quero expressar zoeira visualmente
        ↓
discord.expression.send disponível?
        ↓
sim
        ↓
retrieve known + available expressions
        ↓
rerank pelo contexto/mood/relação
        ↓
selecionar expressão ou decidir não usar
        ↓
resolver native ID localmente
        ↓
Action Bus / adapter
        ↓
DeliveryReceipt
```

Essa separação é obrigatória para impedir que transporte, cognição e percepção virem uma única camada acoplada.