# PRs empilhados — preparação verificável

Branches publicadas; PRs ainda não confirmados pela API. Não fazer merge fora da ordem.

| Bloco | Base | Head | Commit |
|---|---|---|---|
| A | `audit/phase1-core-forensics` | `fix/phase2-a-privacy-identity` | `c6ab0c7` |

Criação A:

```sh
gh pr create --base audit/phase1-core-forensics --head fix/phase2-a-privacy-identity --title "AMORA Fase 2 A: Privacidade na origem e autorização atribuída de preferências" --body-file docs/engineering/phase2/prs/A.md
```
| B | `fix/phase2-a-privacy-identity` | `fix/phase2-b-provider-diagnostics` | `d35ed25` |

Criação B:

```sh
gh pr create --base fix/phase2-a-privacy-identity --head fix/phase2-b-provider-diagnostics --title "AMORA Fase 2 B: Taxonomia de deadlines NVIDIA e diagnóstico Web" --body-file docs/engineering/phase2/prs/B.md
```
| C | `fix/phase2-b-provider-diagnostics` | `fix/phase2-c-context-consistency` | `480af7d` |

Criação C:

```sh
gh pr create --base fix/phase2-b-provider-diagnostics --head fix/phase2-c-context-consistency --title "AMORA Fase 2 C: Snapshot final e orçamento explícito de contexto" --body-file docs/engineering/phase2/prs/C.md
```
| D | `fix/phase2-c-context-consistency` | `fix/phase2-d-runtime-contracts` | `0a57c6a` |

Criação D:

```sh
gh pr create --base fix/phase2-c-context-consistency --head fix/phase2-d-runtime-contracts --title "AMORA Fase 2 D: Parentesco direcional e replay estruturado" --body-file docs/engineering/phase2/prs/D.md
```
| V3 | `fix/phase2-d-runtime-contracts` | `feat/phase2-v3-social-profile` | `b97c180` |

Criação V3:

```sh
gh pr create --base fix/phase2-d-runtime-contracts --head feat/phase2-v3-social-profile --title "AMORA Fase 2 V3: Perfil V3 explícito e salvaguardas da revisão adversarial" --body-file docs/engineering/phase2/prs/V3.md
```

Resultado da tentativa do PR A: `Post https://api.github.com/graphql: Forbidden`.
As demais criações não foram repetidas sobre o mesmo bloqueio. Consulta do PR166
usou seu ref Git (confirmado), mas estado/CI seguem indisponíveis pela API.

Cada comparação pode ser aberta manualmente em GitHub usando as bases/heads da
tabela. Exemplo da etapa final (não é um PR já criado):
https://github.com/Sllay/Amora-Chibi/compare/fix/phase2-d-runtime-contracts...feat/phase2-v3-social-profile?expand=1
