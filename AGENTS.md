# AGENTS.md — Amora-Chibi

Este repositório implementa a Amora.

Antes de alterar arquitetura, comportamento, voz, providers, roadmap ou persistência, leia:

1. `docs/canon/SKILL.md`;
2. `docs/canon/ARCHITECTURE.md`;
3. `docs/canon/GDD.md`;
4. `docs/roadmap/VERSIONS.md`;
5. ADRs relevantes em `docs/decisions/`;
6. documentação específica da versão/módulo afetado.

## Autoridade documental

- `docs/canon/*` = especificação atual.
- `docs/roadmap/VERSIONS.md` = origem/destino das features por versão.
- `docs/decisions/*` = por que decisões foram aceitas, rejeitadas ou substituídas.
- documentos antigos de versão = histórico/checkpoint, não autoridade atual quando divergirem.
- histórico Git = versões anteriores exatas.

Nunca restaurar uma solução antiga sem verificar se existe ADR explicando por que ela foi abandonada.

## Regras essenciais

- A Amora não é um LLM; providers são capacidades substituíveis.
- Estado canônico, identidade, memória, relações e emoções vivem fora do provider.
- `BK`, `Bad King`, `Slay` e `Nick` representam a mesma entidade `PERSON_BK`.
- Relações como mãe/pai/filha não são aliases globais; resolver por contexto e Relationship Graph.
- Não salvar memória ambígua como fato.
- Speaker ID não é autenticação.
- Pessoa, dispositivo e sessão são identidades diferentes.
- Secrets nunca entram na memória cognitiva, ACIR, logs, embeddings, TTS ou contexto de provider.
- Disclosure e autorização de processamento remoto são decisões distintas.
- Toda lógica dependente de tempo usa TimeService/Scheduler.
- Event Bus deve ser idempotente/rastreável.
- Schemas persistentes exigem migrations.
- Runtime, banco, caches, backups e secrets nunca devem ser commitados.
- Preferir algoritmo/local state a request remoto quando adequado.
- Falha de plugin/provider não pode derrubar o Core.
- Ações importantes verificam o efeito observado.
- Personalidade não depende apenas de prompt.
- Semântica social do diálogo pertence ao Core sempre que possível.
- Voz normal deve manter identidade pt-BR consistente.
- CI não equivale a certificação física no Galaxy A15.

## Roadmap atual

A linhagem detalhada está em `docs/roadmap/VERSIONS.md`.

Resumo vigente:

```text
V1.0  fechamento cognitivo/voz/providers/estabilidade
  Step 1  voz local pt-BR — IN_PROGRESS
  Step 2  identidade de diálogo — PLANNED
  Step 3  malha gratuita de providers — PLANNED
  Step 4  voz canônica + certificação V1 — PLANNED

V1.1  percepção visual + screen mapping + corpo/avatar final
V1.2  estética persistente + aparência/roupa + autoimagem
```

O planejamento antigo V0.9/V0.95 foi dividido; itens movidos não foram cancelados.

## Desenvolvimento

Para mudanças relevantes:

1. ler canon e ADRs;
2. identificar ownership e impacto;
3. pesquisar quando a decisão depender de tecnologia/quota/modelo/licença atual;
4. definir critério de sucesso;
5. preservar interfaces quando possível;
6. revisar eventos/schemas/privacy/custo;
7. adicionar migration quando persistência mudar;
8. adicionar/atualizar testes;
9. implementar em branch/PR;
10. rodar CI;
11. validar fisicamente quando hardware/áudio/Android/performance estiver envolvido;
12. atualizar canon/roadmap/ADR.

Se uma área entrar em sequência de patches sem estabilizar, interrompa o patch loop e faça análise arquitetural/pesquisa antes de continuar.

No Core Python, priorize `asyncio`, type hints, Pydantic/dataclasses, SQLite, logging estruturado e testes automatizados.

No Android, priorize Kotlin, lifecycle/cancellation corretos, APIs modernas e bridge explícita com o Core.
