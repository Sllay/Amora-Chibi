# Agentes AMORA

Verificado com `codex-cli 0.167.0-alpha.4`: definições de roles em
`.codex/config.toml`, com `config_file` relativo ao mesmo arquivo e
`developer_instructions` nos dois TOMLs referenciados.

| Nome humano | Role Codex | Arquivo |
|---|---|---|
| amora test | amora_test | agents/amora-test.toml |
| core especionar | core_especionar | agents/core-especionar.toml |

Somente dois roles personalizados. O principal integra; esses agentes investigam
ou testam sem editar produção sem coordenação. Reutilizar as sessões existentes;
não criar agentes adicionais, substitutos ou aninhados.

`max_concurrent_threads_per_session=2` limita sessões filhas configuradas.
`max_depth=1` é suportado no backend V1; no V2 esse parâmetro não controla
profundidade. A proibição de aninhamento também está nas instruções de ambos.
A configuração local não altera retroativamente limites do host da conversa.

Validação sem inferência ou criação de agentes:

```sh
codex --version
codex --strict-config app-server --print-startup-config
python tools/probes/phase1_codex_config.py
```

O último comando inspeciona `config/read` e mostra apenas agentes/camadas.
Uma camada com `disabledReason` não é configuração ativa. O projeto precisa
estar trusted no Codex do usuário; nesta máquina, foi adicionada apenas a entrada
`projects."/workspace/Amora-Chibi".trust_level="trusted"`, preservando a
configuração anterior. Essa decisão local não é transportada pelo Git.

Na auditoria de 2026-11-08, os dois agentes existentes continuaram associados
ao executor anterior indisponível. Foram reutilizados como revisores por
mensagens. Os comandos, testes e persistência foram executados pelo principal.
Não confundir arquivos persistidos e `config/read` ativo com recuperação dessas
sessões antigas ou com teste de inferência dos roles em uma nova tarefa.
