# AGENDA.1 — agenda durável e iniciativa agendada

## Decisão
Usar `AmoraDatabase.events` existente (índice event_type/time e dedupe_key), `TimeService/Scheduler` e `messaging_outbound_queue`. **Nenhum schema/migration, backup, serviço ou novo banco**. A agenda é distinta da memória semântica; é uma instrução temporal explícita do proprietário, com hora, destino, modo de repetição e possibilidade de cancelar. Fatos sobre agenda podem entrar no contexto cognitivo somente por consulta autorizada, não por promessa do LLM.

- A1: modelos estritos e eventos imutáveis `agenda.created` + `agenda.cancelled` com owner PERSON_BK, privacy PERSONAL; calendário local America/Recife convertido em UTC, anos futuros e intervalo mínimo de 10 min. Lista por ano, data, estado, IDs estáveis; cancelar exige ID ou seleção inequívoca, sem adivinhar.
- A2: agenda em execução do Core a cada 30s (sem idle_only), sem loop por ocorrência para cada agenda distante, janela de atraso de 5 min e 5 min para expiração do outbound. Gerar uma mensagem de saída por ocorrência usando UUID5 e `ON CONFLICT`; cancelamentos, DM identity/presence e deadline revalidados quando o worker busca a mensagem. Em claim ambíguo não reenviar automaticamente: melhor mostrar falha/incerteza e não duplicar. Sem worker/discord conectado, tentativa aparece como não entregue.
- A3: comandos explícitos no DM privado de PERSON_BK verificado, formulários e lista na Web com autenticação **operador**, sem permitir que um nome/username se faça passar pelo dono. Pedidos vagos como “aquela de 2030” listam candidatas para desambiguação. O conteúdo da agenda é texto privado e não vai para logs nem outros chats.
- A4: testes sintéticos: reboot, 2030, 10 minutos até fim do dia, DST/UTC, duplicata, cancelamento antes do claim, revogação de identidade, calendário errado, falha de conexão e UI. CI verde antes de main. Certificação de entrega real separada, sem alegar notificação se dispositivo/worker offline.

## Condições sociais dinâmicas
Agenda é um **tipo de oportunidade**, não uma lista fixa de “estados emocionais”. O executor recebe `kind`, horário agendado, assunto, preferência e origem verificada. Entrega determinística para compromissos explícitos (não permitir LLM alterar a hora). Iniciativa livre já tem seus estados de conversa; expansão futura poderá construir novos rótulos sem permitir que o modelo crie ações recorrentes por sugestão não confirmada. Limites: 64 tarefas ativas por pessoa, min 10 min por ocorrência, um destino DM verificado por agenda, máximo 5 min após a ocorrência, sem catch-up em massa depois de meses offline. Não implementar mensagens em grupos.


## Implementação (primeira revisão)
- `b9d6a4c` contrato strict de horários aware, até 30 anos, intervalo mínimo 10 min.
- `c0f3e58`/`a772ddc` ledger de criação/cancelamento, dedupe UUID5, owner DM real, janela de entrega 5 min, status.
- `0a97ff1` claim-time fence no worker, estado `AGENDA_DELIVERY_UNCERTAIN` em claim expirado, sem reenvio cego.
- `621034b`/`79d8772` ciclo do Scheduler de 30s (não idle-only), retomada sem cache de processo.
- `16dbf3f`/`946b8aa` API operator-only; payload privado sem IDs de transporte.
- `59bec3e`/`75119f7` comandos diretos verificados PT-BR para hoje/amanhã/ano e repetição de 10 min, intenção de cancelar por pesquisa/ano e ambiguidade.
- `8373ea9`/`dac6881`/`a36a08d` Web: aba Agenda, calendário, filtro ano, criar/cancelar e recorrência.
- `195bc8c` testes de fluxo real, identidade, restart, 2030, repetição, dedupe, autorização e parser.
- `fe9cb7e` tratamento de repetição pendente expirada, status entregue/incerto e comparação de pedido idempotente com fuso UTC.
**Ainda não aprovado:** aguarda CI e testes; nenhuma mudança de produção ou Turso. Agenda atual envia a frase explícita do usuário, sem alucinar horário; a linguagem social não tem autoridade de alterar automaticamente compromissos.
