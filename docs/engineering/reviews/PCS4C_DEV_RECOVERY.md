# PCS.4C — Recuperação pelo painel Dev existente

Este é o caminho preferido: Web da Amora > Console (Dev) > Backup e recuperação.
O painel web separado do PR #232 permanece apenas alternativa; NÃO criar outro
serviço. Usa-se um Job manual dedicado, uma réplica, sem cron/retry/concurrency,
com volume privado persistente. O Core apenas envia a ação permitida à API do
Northflank e consulta o estado e o relatório sanitizado; não executa restauração
nem recebe token de destino. O Discord continua no segundo serviço existente.

## 1. Configuração única no dashboard, sem Shell

- Confirme cota, preço e disponibilidade de volume persistente no seu plano
  antes de criar recursos. Sandbox anuncia dois serviços e dois jobs; volume
  tem cobrança/cota própria. Não ative plano pago por este procedimento.
- Crie um Job manual dedicado, por exemplo pcs4c-recovery, no projeto do Core.
  Selecione a mesma imagem atualizada do Core no commit integrado.
  Configure parallelism=1, retries/backoff=0, sem schedule, sem execução
  automática por mudança de código, sem concorrência, limite de execução
  suficiente para export/restore/3 boots (ex.: 1800 s, conforme cota).
- No CMD override do Job: `python -m amora.storage.pcs4_job`.
  Não configure porta HTTP; é execução sob demanda, não serviço.
- Anexe volume privado persistente ao Job em /private, gravável por UID 10001.
  Preserve o volume após os runs; confirme retenção independente e custo.
  A ferramenta rejeita mount efêmero. Não compartilhe o volume com Core.
- Secrets e variáveis do Job:
  - AMORA_PCS4_JOB_ENABLED=1
  - AMORA_PCS4_WEB_DURABLE=1
  - AMORA_PCS4_WEB_VOLUME=/private
  - AMORA_LIBSQL_URL: origem existente
  - AMORA_SECRET_LIBSQL_AUTH_TOKEN: preferencialmente token database-specific
    **somente leitura** da origem; nenhuma credencial de IA/Discord/admin.
  - AMORA_PCS4C_DESTINATION_URL: novo banco Turso independente e vazio
  - AMORA_PCS4C_DESTINATION_TOKEN: token exclusivo com escrita só no destino
  Não configure AMORA_PCS4_JOB_ACTION no Job: o Core passa o valor de cada
  execução. Não coloque os tokens no browser, chat, Git ou logs.
- Na configuração de secrets do Core existente:
  - AMORA_PCS4_DEV_ENABLED=1
  - AMORA_PCS4_NF_PROJECT: ID do projeto
  - AMORA_PCS4_NF_JOB: ID do Job dedicado
  - AMORA_PCS4_NF_TOKEN: token API Northflank de escopo mínimo para leitura
    do Job, Run Job e View Observability. Restrinja projeto/Job se disponível.
  - AMORA_PCS4_NF_TEAM: opcional ID do time, somente se token usa rota de time.
  Não injete o token de destino nem credenciais adicionais de origem no Core.
  Se as permissões disponíveis forem excessivas, não habilite o acionamento
  remoto; revise a função de API com o operador.
  Reimplante o Core com as variáveis e a imagem integrada; não altere V3,
  provedor, driver, schema, Discord ou token de acesso Web.

## 2. Usar o Console existente

Entre com a autenticação de operador atual, abra Console e a seção
Backup e recuperação. Os quatro botões são liberados em sequência.
1. Criar backup: confirme SNAPSHOT CONSISTENTE. Exporta as tabelas em
   uma única transação de leitura BEGIN/ROLLBACK e cria o ZIP dos assets
   a partir da própria cópia SQLite. Não reserva escritor nem inicializa
   Core na origem. Trata-se de fotografia point-in-time, com alterações
   posteriores fora da cópia; não equivale a migração final com pausa.
   O comportamento de concorrência/latência no Turso real ainda deve
   ser medido. Se o remoto não suportar o snapshot, falha sem fallback.
2. Verificar backup: confirme VOLUME PRIVADO após confirmar retenção.
   Confere integridade, hashes privados e assets do volume.
3. Restaurar banco de teste: confirme DESTINO VAZIO E INDEPENDENTE.
   O operador confirma pelo dashboard Turso que é outra base (hostnames
   distintos não provam isolamento físico). Nunca apontar para produção.
4. Certificar reinicialização: confirme DESTINO ISOLADO. Executa referência
   SQLite e dois boots remotos isolados, cinco consultas, quatro tabelas.
   O destino pode receber metadados de bootstrap, nunca a origem.

Copiar resultados envia apenas status/contagens/igualdade; sem arquivos,
hashes, texto ou tokens. O Core usa o histórico do Job e seu marcador de
relatório sanitizado, nunca expõe logs brutos. Status SUCCESS sem relatório
válido não libera a próxima etapa. Se logs demorarem, atualize o Console.
Falha/interrupção exige revisão; não apague state.json nem repita restore.

### Restore interrompido (PCS.4C-R4)
Atualize o **mesmo Job** e o Core existente para a imagem integrada, preservando todas as configurações e o volume. Confirme que não há execução ativa e que o banco Turso de teste configurado é o mesmo destino independente, sem outros escritores. No Console, escolha **Revisar restauração interrompida** e confirme `REVISAR DESTINO ISOLADO`. A ação só aceita estado durável com backup e verify concluídos; revalida ambos os hashes e consulta o destino em transações de leitura. Não importa, exclui ou altera dados do banco.
- `empty`: libera um Restore explícito com a mesma cópia. O importador revalida que o destino está vazio dentro da transação.
- `exact`: reexporta e compara schema e todas as linhas lógicas; registra Restore concluído, libera restart.
- Divergência, backup alterado, destino trocado ou falha de transporte: mantém o estado anterior e não libera Restore. Não apague nem troque o destino automaticamente. Uma nova base após divergência requer procedimento separado e revisado.
A revisão não certifica que a origem atual permaneceu idêntica ao snapshot; a cópia é point-in-time. Os novos Restores emitem `PCS4C_RESTORE_PROGRESS` com contagens e fase, sem conteúdo pessoal. O relatório final continua obrigatório.
O lock e estado no volume são autoridade e sobrevivem a restart do Core.
Se a submissão API tiver resultado ambíguo, bloqueia novas submissões na
instância atual; confira os runs no dashboard antes de reiniciar Core.
Não troque o Job/volume em meio à certificação. A retenção de logs do host
pode exigir revisão operacional; não há reset automático.

## 3. Limites de certificação

CI testa API com MockTransport, Hrana com SQLite e o container Alpine.
Não certifica execução real no Northflank nem disponibilidade de volume
ou quota. A issue #221 fica aberta até backup durável, restore e restart
reais; projeção local desativada. A alternativa de painel separado não é
necessária. Nenhum recurso externo foi criado por este código.

Referências oficiais:
- https://northflank.com/docs/v1/api/project/jobs/run-job
- https://northflank.com/docs/v1/api/project/jobs/get-job-runs
- https://northflank.com/docs/v1/api/project/jobs/get-job-logs
- https://northflank.com/docs/v1/api/project/volumes/attach-volume
- https://northflank.com/pricing
