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
- `PROJECT_STATE.md` = checkpoint operacional entre chats/agentes.
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
- Falha de plugin/provider/voz não pode derrubar o Core nem travar a interface inteira.
- Ações importantes verificam o efeito observado.
- Personalidade não depende apenas de prompt.
- Semântica social do diálogo pertence ao Core sempre que possível.
- Voz normal deve manter identidade pt-BR consistente.
- `WEB_SAFARI` e `DEVICE_A15` são destinos de plataforma diferentes.
- Safari `speechSynthesis` não é transporte principal da voz Web.
- Gemini TTS não é fallback oculto da voz Web local.
- FINAL_B e `AMORA_VOICE_SIGNATURE_V1_SOFT` estão congeladas como identidade aprovada de laboratório; não retunar sem evidência nova.
- CI não equivale a certificação física no Galaxy A15/Safari.

## Roadmap atual

A linhagem detalhada está em `docs/roadmap/VERSIONS.md`.

Resumo vigente:

```text
V1.0  fechamento cognitivo/voz/providers/estabilidade — IN_PROGRESS
  Step 1  voz local pt-BR / Faber path — IMPLEMENTED / PHYSICAL PATH PASS
  Step 2  identidade de diálogo + emoção — IMPLEMENTED
  Step 3  malha gratuita de providers — IMPLEMENTED
  Step 4  FINAL_B + Signature V1 Soft — LAB-APPROVED / PHYSICAL CERTIFICATION PENDING

V1.0.1 Web/PWA
  Safari = superfície primária de iteração
  local Voice Worker = MERGED
  teste físico Termux + Safari = PENDING

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
11. validar fisicamente quando hardware/áudio/browser mobile/performance estiver envolvido;
12. atualizar canon/roadmap/ADR/PROJECT_STATE quando necessário.

Se uma área entrar em sequência de patches sem estabilizar, interrompa o patch loop e faça análise arquitetural/pesquisa antes de continuar.

No Core Python, priorize `asyncio`, type hints, Pydantic/dataclasses, SQLite, logging estruturado e testes automatizados.

No Android, priorize Kotlin, lifecycle/cancellation corretos, APIs modernas e bridge explícita com o Core.

No Web/Safari, browser é platform adapter: UI, microfone, player e input. Não duplicar identidade, emoção, fila canônica ou DSP da voz no frontend.
