# AMORA — Documentation Index

## Canon atual

Leia nesta ordem para mudanças de arquitetura/comportamento:

1. [`canon/SKILL.md`](canon/SKILL.md) — como desenvolver a Amora sem perder continuidade.
2. [`canon/ARCHITECTURE.md`](canon/ARCHITECTURE.md) — arquitetura técnica vigente.
3. [`canon/GDD.md`](canon/GDD.md) — produto/comportamento vigente.
4. [`roadmap/VERSIONS.md`](roadmap/VERSIONS.md) — linhagem das versões e features movidas.
5. [`decisions/README.md`](decisions/README.md) — índice de ADRs e decisões históricas.

## Regra de autoridade

Documentos antigos de versão (`V0.*`, `V1.*` etc.) continuam úteis como histórico/checkpoint, mas **não substituem o canon atual** quando houver divergência.

Antes de restaurar um método antigo, consulte os ADRs para verificar se ele foi rejeitado ou substituído.

O histórico Git preserva versões anteriores do canon. Features movidas entre versões ficam rastreadas em `roadmap/VERSIONS.md` em vez de simplesmente desaparecerem.

## Documentação operacional existente

Arquivos como `V1.0_A15_CERTIFICATION.md`, `V1.0_COGNITIVE_INTEGRATION.md`, `TERMUX.md` e documentos das versões anteriores permanecem evidência técnica e histórico de implementação.

Quando um checkpoint antigo contradizer o canon, siga o canon e use o checkpoint apenas para entender a origem da decisão.
