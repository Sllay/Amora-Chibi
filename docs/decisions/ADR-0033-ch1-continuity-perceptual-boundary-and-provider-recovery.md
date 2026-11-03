# ADR-0033 — CH1: continuidade documental, fronteira perceptual e recuperação antes de fallback

**Status:** ACCEPTED  
**Data:** 2026-11-02

## Contexto

A linha de trabalho #116–#121 avançou Capability Awareness, grounding de ações, competição de modalidade e memória perceptual persistente. O código evoluiu mais rápido que `PROJECT_STATE.md`/roadmap, o que criou três riscos recorrentes:

1. decisões válidas ficarem apenas em conversas e se perderem entre sessões;
2. “visão de sticker” ser confundida com a visão contínua planejada para V1.1;
3. uma falha transitória de API provocar fallback prematuro, mudando provider sem tentar recuperação bounded do primary.

Pesquisa de engenharia sobre AIRI, Neuro SDK, Open-LLM-VTuber, Letta/elizaOS e padrões de routing/retry reforçou a arquitetura já adotada pela Amora: um Core persistente e soberano, capabilities tipadas, providers substituíveis e execução/validação fora do LLM.

## Decisão

### 1. Todo CH fecha código e documentação juntos

Um CH só pode ser marcado `COMPLETED` quando a mesma linha de entrega registra:

- código/schema/testes integrados;
- CI aplicável;
- gates físicos ainda pendentes explicitamente;
- `PROJECT_STATE.md` atualizado;
- roadmap/canon/ADR afetados atualizados;
- próximos CHs curtos e explícitos;
- nenhuma decisão crítica dependendo apenas da memória do chat.

GitHub continua fonte técnica de verdade. Se checkpoint e código divergirem, a divergência é dívida do CH atual, não problema para uma conversa futura descobrir.

### 2. Existem duas classes de visão diferentes

**Perceptual Asset Vision** pertence à linha atual e ao CH2:

```text
asset social pontual
→ fetch seguro
→ fingerprint dos bytes reais
→ análise multimodal estruturada
→ validação local
→ CPF persistente
→ reuso
```

Ela é adequada para sticker/emoji/GIF/imagem pontual e não exige captura contínua de tela.

**Continuous Environment Vision** permanece V1.1:

```text
screen/video/environment
→ captura temporal
→ scene change / keyframes
→ OCR/objetos/contexto
→ sincronização temporal/áudio
```

Implementar a primeira não adianta nem cancela a segunda.

### 3. Fallback só depois de recuperação classificada e bounded

Provider failure é classificada antes de decidir retry/fallback/defer.

Fluxo canônico:

```text
primary request
→ classify failure
→ bounded recovery do primary
→ circuit/cooldown
→ fallback OR defer OR local degrade
```

Regras mínimas:

- network/timeout/5xx/capacity podem receber retry curto com exponential backoff + jitter;
- `429` respeita `Retry-After` e budget da task;
- auth e bad request determinístico não recebem retry cego;
- structured output inválido recebe no máximo repair/retry bounded;
- circuit breaker possui cooldown e `HALF_OPEN`;
- uma falha isolada não remove permanentemente o primary;
- diálogo tem budget curto; reasoning pode ter budget maior;
- percepção assíncrona de asset prefere `DEFER` a trocar provider imediatamente;
- depois que fala validada começou a ser entregue, outro provider não é costurado no mesmo turno;
- provider/model/task e razão de fallback ficam observáveis.

### 4. Modelo é escolhido por task class, não por identidade

Baseline enquanto CH3 não certificar alternativa:

- social dialogue: Qwen 3.8 27B;
- reasoning specialist: GPT-OSS 120B;
- Mistral Large 4 via NVIDIA: candidato para diálogo, reasoning e percepção.

Mistral só se torna primary onde vencer replay/benchmark e cumprir custo, latência, privacidade, structured output/capability e resiliência. Se vencer diálogo, Qwen permanece fallback; se vencer reasoning, GPT-OSS permanece fallback.

A identidade, relações, memória, emoção, `DialoguePlan`, policy/authority e efeitos continuam pertencendo ao Core.

## Consequências

### Positivas

- sessões futuras retomam o projeto pelo repositório, não por reconstrução de chat;
- CH2 pode entregar percepção real de sticker sem puxar screen vision V1.1;
- API instável não provoca troca imediata e desnecessária de provider;
- troca de modelo não troca a identidade da Amora;
- provider promotion passa a ser decisão reproduzível por benchmark.

### Custos

- cada CH inclui disciplina documental obrigatória;
- router precisa de taxonomia de erros, budgets, circuit state e telemetria;
- testes precisam simular falhas transitórias/determinísticas e time budgets;
- Mistral não pode ser promovido apenas por impressão subjetiva.

## Implementação por CH

- **CH1:** consolidar #116–#121, checkpoint/canon/roadmap e esta decisão.
- **CH2:** Visual Expression Enrichment do CPF, com fetch seguro, fingerprint real, structured multimodal output, CAS e retry/defer.
- **CH3:** Provider Certification + implementação/certificação completa de recovery-before-fallback e promoção por task class.

Não abrir uma quarta frente grande enquanto estes três CHs estiverem sem fechamento documentado.
