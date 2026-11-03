# AMORA — Malha de Providers Cognitivos

> Atualização de infraestrutura (2026-10-23, ADR-0015): Core/Web podem operar
> em host Linux remoto e a mesma voz pode operar em worker independente.
> Localização de compute/storage não altera identidade, consentimento, custo ou
> autoridade canônica do Core.

**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-11-02

## 1. Princípio

Provider é capacidade substituível.

O Core continua autoridade sobre:

- identidade;
- self e relações;
- memória;
- emoção/drives;
- world state;
- `DialoguePlan`;
- policy/authority;
- escolha e validação de capabilities;
- privacidade/consentimento;
- failover;
- aceitação do `RealizationFrame`;
- observação de efeitos.

**Provider não é identidade e não ganha autoridade por estar saudável, rápido ou ser o primary.**

---

## 2. Objetivo econômico

Objetivo operacional: **R$ 0 sempre que praticável**.

Free tier é capacidade oportunística, não contrato arquitetural. Quota, preço,
modelo e disponibilidade são telemetria dinâmica.

Nenhum adapter pode:

- comprar crédito;
- ativar billing;
- trocar silenciosamente para rota paga;
- continuar chamadas cobradas só para evitar degradação;
- esconder do Core qual provider/modelo respondeu.

---

## 3. Papéis por task class

A malha não possui um “modelo central” único.

```text
AMORA CORE
├─ social surface actor
├─ reasoning specialist
└─ perception provider
```

Cada papel é certificado separadamente.

### Baseline atual

- **social dialogue:** Qwen 3.8 27B no caminho Groq configurado;
- **reasoning specialist:** GPT-OSS 120B;
- **Mistral Large 4 / NVIDIA (`mistralai/mistral-large-4-1t-a52b`): CANDIDATO**, não promovido;
- Cloudflare Workers AI, Gemini, OpenRouter e fallback local continuam eixos
  substituíveis conforme capability, health, privacy, custo e certificação.

Se o Mistral vencer o CH3:

```text
dialogue:
Mistral Large 4
→ recovery do primary
→ Qwen fallback

reasoning:
Mistral Large 4
→ recovery do primary
→ GPT-OSS 120B fallback
```

Essa ordem é **resultado de benchmark**, nunca identidade canônica.

---

## 4. Provider contract

Cada provider/modelo declara:

- nome lógico;
- modelo;
- task classes;
- privacy scopes;
- structured output real suportado;
- multimodalidade real suportada;
- context window observado/contratado;
- quality/latency/cost class;
- prioridade;
- disponibilidade;
- quota/rate-limit observados;
- cooldown/circuit state;
- capabilities específicas.

Não inferir capability pelo nome do modelo. O adapter deve provar o que o
endpoint realmente aceita.

Para diálogo social:

```text
Amora Constitution
+ LivingProfile
+ TurnFrame
+ RealizationContract
        ↓
trusted provider instructions

ConversationTurn(s) literais
        ↓
conversation content
```

Depois:

```text
provider
→ RealizationFrame
→ local validator
→ delivery
```

Texto do usuário nunca sobrescreve metadata confiável como `speaker_id`,
`addressee_id`, policy, platform ou authority.

---

## 5. Recovery-before-fallback

### 5.1 Regra

**Fallback é consequência de recuperação bounded falhar; não é reação ao primeiro erro.**

```text
request
  ↓
primary
  ↓ falha
classify
  ↓
bounded recovery
  ↓
circuit/cooldown
  ↓
fallback OR defer OR local failure
```

### 5.2 Classificação mínima

| classe | exemplos | retry |
|---|---|---|
| `TRANSIENT_NETWORK` | reset, DNS temporário, connect error | sim, bounded |
| `TIMEOUT` | connect/read timeout | sim, se budget permitir |
| `SERVER_5XX` | 500/502/503/504 | sim, bounded |
| `CAPACITY` | overloaded/out-of-capacity | sim/defer conforme budget |
| `RATE_LIMIT` | 429 | respeitar `Retry-After`; não martelar |
| `AUTH` | 401/403 | não retry cego |
| `BAD_REQUEST` | request determinístico inválido | não repetir idêntico |
| `INVALID_OUTPUT` | JSON/frame inválido | no máximo repair/retry bounded |
| `POLICY_REJECTION` | provider recusou conteúdo permitido pelo plano | registrar; retry só por política explícita |
| `UNKNOWN` | erro não classificado | conservador, bounded |

### 5.3 Backoff

Retries transitórios devem usar:

```text
exponential backoff
+ jitter
+ limite de tentativas
+ latency budget da task
```

Não criar retry storm. Não repetir automaticamente uma chamada paga ou com
quota cara apenas porque o retry budget técnico permitiria.

### 5.4 Budget por task

**Diálogo:** curto. A Amora precisa responder; após recuperação bounded,
fallback elegível pode assumir.

**Reasoning:** maior que diálogo, mas continua bounded.

**Perceptual asset enrichment:** normalmente assíncrono. Se o provider cair:

```text
PENDING_VISUAL
→ retry/defer posterior
```

e não “troca de cérebro” imediata só porque a NVIDIA/Groq espirrou.

---

## 6. Circuit breaker

Estado operacional mínimo por `provider + model + task_class`:

```text
HEALTHY
SUSPECT
DEGRADED
COOLDOWN
HALF_OPEN
UNAVAILABLE
```

Uma falha isolada não deve banir o primary.

Falhas consecutivas/relevantes abrem o circuito. Depois do cooldown:

```text
COOLDOWN
→ HALF_OPEN
→ probe/request elegível
   ├─ success → HEALTHY
   └─ failure → COOLDOWN maior
```

Cooldown global de um provider não deve ser aplicado a task/modelo não
afetado sem evidência.

---

## 7. Regra de delivery

Failover só pode ocorrer antes de qualquer fala não ambígua ter sido entregue.

```text
provider falha antes do frame/delivery
→ recovery/fallback permitido

fala validada começou a ser entregue
→ NÃO costurar outro provider no mesmo turno
```

Se uma stream cai depois de entrega parcial, o sistema registra interrupção
e decide novo turno/reparo; não mistura duas vozes cognitivas numa sentença.

---

## 8. Repair de structured output

Structured output inválido é diferente de indisponibilidade.

Fluxo recomendado:

```text
invalid frame
→ local parse/validation reason
→ no máximo um repair/retry contextual
→ se persistir: provider failure for this task
→ fallback/defer
```

Nunca aceitar JSON inválido só para “não perder a resposta”.

---

## 9. Ordem funcional da malha V1

A ordem é dinâmica por task class, mas os eixos disponíveis permanecem:

1. provider direto primário certificado;
2. segundo eixo remoto direto certificado;
3. Gemini quando elegível/configurado;
4. OpenRouter como specialist broker/último recurso gratuito elegível;
5. fallback local/degradação segura.

OpenRouter não é caminho normal de toda conversa quando providers diretos
estão saudáveis.

Ausência de configuração de um provider apenas o torna indisponível.

---

## 10. Observabilidade

Registrar por request/tentativa:

- provider/model/task class;
- request id interno;
- tentativa N;
- failure class;
- status HTTP quando houver;
- latency;
- quota headers quando houver;
- tokens quando reportados;
- retry/backoff escolhido;
- circuit state antes/depois;
- resolution:
  - `PRIMARY_SUCCESS`
  - `RETRY_PRIMARY`
  - `RETRY_REPAIRED`
  - `DEFER`
  - `FALLBACK_PROVIDER`
  - `LOCAL_DEGRADE`
  - `FAILED`
- provider final usado.

O Console pode resumir sem spam; logs estruturados guardam o detalhe.

---

## 11. Provider Certification

Adicionar adapter não certifica provider.

Certificação por task class precisa provar:

- conectividade/configuração;
- pt-BR suficiente quando aplicável;
- fidelidade ao `DialoguePlan`;
- personalidade/continuidade;
- ausência de assistantism intrusivo;
- structured output quando exigido;
- multimodalidade quando exigida;
- erro/quota classificáveis;
- latency dentro do budget;
- privacy/consent;
- custo;
- recovery/fallback seguro;
- identidade social preservada.

`Cognitive Replay` é o método padrão para comparação sem mutação de estado.

Não rodar chamadas remotas automáticas em todo startup só para diagnóstico.

---

## 12. Social Surface Certification

Para social dialogue, o replay deve incluir também:

- português brasileiro natural;
- gíria e palavrão contextual;
- humor quebrado/ironia;
- conflito e provocação;
- afeto;
- conversa adulta comum;
- sexualidade mencionada em contexto permitido;
- identidade relacional;
- perguntas sobre self;
- recusa apropriada versus recusa social indevida;
- moralização desnecessária;
- `assistantism` / “como uma IA...” intrusivo;
- fidelidade ao plano e aos fatos do Core.

O objetivo é reduzir over-refusal e quebra de personagem **sem mover policy e
segurança de ações para o provider**.

---

## 13. Configuração e secrets

Credenciais ficam no SecretStore/configuração segura e nunca aparecem em:

- Git;
- prompt;
- log;
- health report público;
- exception serializada para cliente.

Modelos podem ser alterados por config/env sem serem tratados como identidade.

---

## 14. Critério de saída da malha

A malha é considerada pronta para uma task class quando:

1. primary e fallback têm adapters isolados;
2. erros são classificados;
3. retry/backoff/jitter são bounded;
4. circuit breaker possui cooldown + half-open;
5. zero-cost guard continua fail-closed;
6. troca de provider é observável;
7. não há costura após início de delivery;
8. testes determinísticos cobrem falhas sem consumir APIs reais;
9. replay/certificação real prova o comportamento esperado.
