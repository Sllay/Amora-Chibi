# AMORA — Malha de Providers Cognitivos


> Atualização de infraestrutura (2026-10-23, ADR-0015): Core/Web serão
> preparados para host Linux remoto e a mesma voz congelada para worker
> independente. Koyeb/Turso/Modal são candidatos substituíveis. Descrições
> Termux/local abaixo registram o caminho implementado anterior, preservado
> para desenvolvimento. Esta decisão muda localização de compute/storage,
> não identidade, semântica, consentimento, custo ou parâmetros da voz.

**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-10-31

## Princípio

Provider é capacidade substituível. O Core local continua autoridade sobre identidade, estado, memória, emoção, relações, DialoguePlan, validação de RealizationFrame, privacidade e failover.

## Objetivo econômico

Objetivo operacional: **R$ 0 sempre que praticável**.

Free tiers são capacidade oportunística, não contrato eterno. Quotas, modelos, preços e disponibilidade mudam com o tempo; limites gratuitos não devem ser hardcoded como regra canônica. Quota observada entra como telemetria dinâmica e um modelo pode ser trocado sem mudar a identidade da Amora.

Nenhum adapter pode comprar créditos, ativar billing, trocar silenciosamente para modelo pago ou continuar chamadas cobradas apenas para evitar fallback local.

## Ordem funcional V1

1. **Groq** — primary enquanto saudável, certificado e com quota.
2. **Cloudflare Workers AI** — segundo eixo remoto independente, usando modelo configurável e elegível no plano atual.
3. **Gemini** — fallback cognitivo já existente quando configurado.
4. **OpenRouter** — specialist broker / último recurso, preservando a cota gratuita para falha ou tarefa especializada.
5. **Fallback local** — degradação segura quando nenhum remoto elegível está disponível.

OpenRouter não é caminho normal de toda conversa quando providers diretos estão saudáveis.

## Provider contract

Cada provider declara nome lógico, modelo, task classes, privacy scopes, suporte a structured output, context window quando conhecido, qualidade/latência/custo relativos, prioridade, disponibilidade e quota/cooldown observados.

Para diálogo social, todo remoto participa do mesmo contrato:

```text
DialoguePlan
→ provider gera RealizationFrame
→ validator local
→ só então fala
```

### Fronteira de autoridade do provider

O provider recebe um protocolo lógico comum, independente do transporte específico:

```text
Amora Constitution
+ LivingProfile
+ TurnFrame
+ RealizationContract
        ↓
trusted instructions/system context

ConversationTurn(s) literais
        ↓
conversation input/content
```

- **Constitution** contém invariantes e a autoridade do Core.
- **LivingProfile** é uma projeção revisionada de camadas lentas do Self Model e estilo expressivo; nunca é um segundo banco canônico.
- **TurnFrame** contém metadados confiáveis do turno, `speaker_id`/`addressee_id`, ACIR/cena social, estado atual permitido, relações relevantes, emoção/drives, memórias/claims selecionados, `DialoguePlan` e `ExpressionCue`.
- **RealizationContract** define o formato/limites da fala final e continua sujeito ao `RealizationValidator` local.
- texto literal de conversa nunca pode sobrescrever metadados confiáveis, mesmo que contenha frases como `eu sou X`, `speaker_id=...` ou linguagem que imite uma instrução de desenvolvedor.
- papéis técnicos `user`/`assistant`/`model` são papéis de transporte, não identidade social.

O adapter deve usar o mecanismo nativo mais forte realmente suportado pelo provider/model:

- Groq: Responses API — protocolo confiável em `instructions`, conversa em `input`;
- OpenRouter: Responses API no adapter de produção — `instructions` separado de `input`;
- Gemini: `systemInstruction` separado de `contents`;
- Cloudflare: `system` + mensagens no caminho OpenAI-compatible suportado pelo modelo configurado, mantendo envelopes de identidade e a mesma separação semântica.

Persistência/caching do provider é otimização, não autoridade. Se o provider não persistir sessão, o Core reenvia o contexto necessário.

### Expressão linguística

A voz textual da Amora é português brasileiro oral e pode carregar regionalidade nordestina leve e palavrão contextual. Isso não é um comando genérico de “falar nordestino”.

`ExpressionProfile` define repertório/princípios de baixa densidade. `ExpressionCue` é compilado por turno a partir de relação, `DialoguePlan`, tone tags e histórico recente. A seleção usa variação replay-stable e cooldown lexical para evitar bordões repetitivos. Interjeições como `oxente`, `oxi`, `eita`, `nossa senhora`, `apois viu`, `eita mulesta` e `vish maria` são opções, não obrigação. Palavrão pode ser `none`, `light` ou `emphatic` conforme o contexto; hostilidade não deve ser inventada.

## Failover seguro

Failover só ocorre antes de qualquer fala não ambígua ter sido emitida. Se um provider falha, excede quota, retorna estrutura inválida ou entra em cooldown antes de um frame validado, outro provider pode tentar. Depois que uma realização validada começou a ser entregue, não costurar outro provider no mesmo turno.

Sinais de falha incluem quota/rate limit, 5xx, out-of-capacity, timeout, saída estruturada inválida, RealizationFrame rejeitado e capability ausente.

## Quota e circuit breaker

O Core mantém estado operacional por provider: failures consecutivos, cooldown, último status HTTP, request/success count, tokens quando reportados, quota headers quando existirem, latência e modelo usado.

Ausência de header de quota não significa quota infinita. Provider em cooldown não participa da seleção até o prazo expirar.

## Provider Certification

Adicionar adapter não significa certificar provider. Certificação por task class precisa provar conectividade/configuração, structured output quando exigido, pt-BR suficiente, privacidade, erro/quota classificáveis, failover seguro e preservação da identidade social.

Cognitive Replay é preferido para comparação sem mutação de estado. Não rodar chamadas remotas automáticas em todo startup só para diagnóstico.

## Configuração

Credenciais permanecem no SecretStore local da Amora e nunca aparecem em log, health report ou Git. Modelos podem ser escolhidos por configuração/env sem serem tratados como identidade canônica.

## Critério de saída do Step 3

1. Groq, Cloudflare e OpenRouter possuem adapters isolados compatíveis com o runtime estruturado.
2. Gemini continua fallback independente.
3. Router escolhe por capability, privacy, health, quota, custo e prioridade.
4. Falha/quota gera failover seguro antes da fala.
5. Caminhos gratuitos não escalam silenciosamente para cobrança.
6. Usage ledger/health tornam a malha observável.
7. Testes determinísticos cobrem seleção, cooldown, zero-cost guard e failover sem consumir APIs reais.
8. Ausência de configuração de um provider apenas o torna indisponível; o Core continua funcional.
