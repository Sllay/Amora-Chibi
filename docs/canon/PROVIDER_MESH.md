# AMORA — Malha de Providers Cognitivos

**Status:** CANÔNICO / ATUAL  
**Atualizado em:** 2026-10-11

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
