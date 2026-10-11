# ADR-0003 — Semântica social pertence ao Core

**Status:** ACCEPTED  
**Data:** 2026-10-10  
**Supersedes:** dependência excessiva de prompt/provider para comportamento social  
**Superseded by:** none

## Contexto

Durante a integração cognitiva, respostas do provider apresentaram comportamentos típicos de assistente, como ofertas genéricas de ajuda, encerramentos repetitivos e metaficção sobre self-facts.

Alguns problemas iniciais foram agravados por histórico de conversa mal estruturado e prompts compactos, mas mesmo após corrigir papéis/history o provider continuava possuindo um prior forte de atendimento.

A filosofia do projeto exige que trocar de modelo não troque a identidade social da Amora.

## Método anterior

O Core fornecia estado e contexto, mas o provider tinha liberdade demais para escolher o ato social final e preencher lacunas comportamentais.

## Decisão

O Core local possui a autoridade semântica do diálogo.

Fluxo:

```text
contexto canônico
↓
Dialogue Orchestrator
↓
DialoguePlan autoritativo
↓
provider como surface realization
↓
validação estrutural
↓
saída
```

O plano deve controlar quando aplicável:

- dialogue/social act;
- objetivo;
- profundidade;
- fatos permitidos/desconhecidos;
- stance/opinião;
- atitude relacional;
- `ask_followup`;
- `offer_help`;
- closing mode;
- constraints de grounding.

Self-facts vêm do Core. Se não estiverem definidos, o provider não inventa.

## Alternativas consideradas

- aumentar permanentemente o system prompt;
- blacklist de frases indesejadas;
- trocar de provider sem mudar arquitetura;
- usar modelo RP como identidade completa;
- planejamento social local + realizador linguístico substituível.

## Motivos

- reduz dependência de prompt;
- preserva identidade entre providers;
- permite testes determinísticos;
- deixa decisões observáveis;
- evita transformar o modelo em fonte de verdade;
- permite no futuro usar modelos mais orientados a RP sem entregar a eles o estado canônico.

## Consequências

O Dialogue Orchestrator precisa crescer além de atos genéricos como `ANSWER_QUESTION` quando a experiência exigir opinião, discordância, zoeira, reclamação ou limite.

Será adicionado validator semântico estrutural. Ele não deve virar blacklist crescente de frases.

Providers continuam livres para escolher wording, ritmo, gíria e nuance dentro dos limites semânticos.

## Critério para revisitar

A divisão Core-semantics / provider-surface pode ser refinada, mas o provider não volta a ser autoridade da identidade social sem evidência forte e novo ADR.
