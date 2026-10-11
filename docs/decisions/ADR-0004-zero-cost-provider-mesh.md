# ADR-0004 — Malha de providers com custo operacional próximo de zero

**Status:** ACCEPTED  
**Data:** 2026-10-10  
**Supersedes:** ideia de tornar OpenAI paga dependência normal  
**Superseded by:** none

## Contexto

A Amora precisa permanecer utilizável de forma casual por muitas horas ao longo do dia sem custo recorrente obrigatório. Providers gratuitos mudam quotas/modelos e não podem ser tratados como infraestrutura eterna.

Uma sugestão anterior de integrar OpenAI paga como provider cotidiano não atende à meta atual de custo.

## Decisão

Objetivo operacional:

```text
R$ 0 sempre que praticável
```

Estratégia atual:

- **Groq:** provider principal enquanto certificado e com quota disponível;
- **Cloudflare Workers AI:** integrar como segundo eixo da malha gratuita/failover;
- **OpenRouter:** usar como broker especialista ou último recurso, preservando requests gratuitos para tarefas em que o acesso a modelos específicos realmente agrega valor;
- **Gemini:** uso específico permitido, mas sem dependência de identidade ou voz normal;
- **OpenAI/serviços pagos:** opcionais, não necessários ao funcionamento casual padrão.

O Model Router seleciona por task class, saúde, qualidade, quota, latência, privacidade e custo.

## Alternativas consideradas

- um único provider principal sem fallback;
- OpenAI paga como primary;
- OpenRouter como caminho normal de toda conversa;
- executar modelo LLM grande localmente no A15;
- malha de free tiers com abstração de provider.

## Motivos

- reduz custo obrigatório;
- evita lock-in;
- preserva cota de brokers para trabalhos especializados;
- permite trocar modelo sem trocar identidade;
- combina com Cognitive Replay e Provider Certification;
- degradação é preferível a indisponibilidade total.

## Consequências

Cada provider precisa de:

- adapter isolado;
- quota telemetry;
- cooldown/backoff;
- circuit breaker/failover;
- privacy gate;
- usage accounting;
- certification por task class.

Free tier atual não deve ser hardcoded como contrato eterno.

## Critério para revisitar

Revisitar a ordem quando quotas, qualidade, termos ou disponibilidade mudarem. A política de substituição permanece mesmo se os nomes dos providers mudarem.
