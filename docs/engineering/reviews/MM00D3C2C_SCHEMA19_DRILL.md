# D.3C.2C — Evolução e recuperação de schema 19

Issue #238. #247 aprovado 4/4 workflows, 8/8 jobs, merge 483e35071be555226c6f0c99c7b61ba95207259c.
Status: C.2C.1 aprovado e integrado (#248); produção permanece 18.

## Checkpoints
- C.2C.1: preparar cópia v19 offline a partir de backup v18 verificado (DB + avatar ZIP), sem escrever na origem. Diretório de saída novo, privado, falha limpa somente artefatos próprios. DDL aditivo existente, versão 19 registrada apenas na cópia. Provar todos os objetos antigos e todas as linhas antigas idênticos, exceto a nova linha de migration; três tabelas novas vazias. SHA de origem e sidecar preservados. Restore exato de v19 somente por opt-in explícito em funções de certificação; defaults/console PCS continuam v18. Testes SQLite/Hrana, assets, rollback, destino não vazio, corrupção e restart/rank. Não registrar versão 19 no bootstrap ou readiness nesta etapa.
- C.2C.2: validar cópia real dos 48.059 registros com operador, destino independente vazio, snapshot v18 e imagem certificada preservados. Gate separado para upgrade, restore, restart e estado da fila. Não modificar destino anterior nem origem de produção. Relatório sem conteúdo privado. Testes de mock não equivalem a Turso real.
- C.2C.3: só após certificação, registrar migração runtime e atualizar readiness, recovery e testes de compatibilidade. Rollback é imagem v18 + backup v18 em destino independente, nunca downgrade destrutivo. Avaliar impacto de deploy automático antes de merge. Nenhum worker ativo até C.3.

## Decisão
A versão 19 é preparada offline primeiro. Alterar LATEST_SCHEMA_VERSION agora faria o bootstrap migrar automaticamente e invalidaria o contrato de certificação v18. As funções PCS mantêm defaults estritos; novo caminho explícito de laboratório não é promoção. Boot/rank de v19 em laboratório com código atual não prova readiness v19 (readiness ainda exige 18). Gate operacional e promoção separados.

## Evidência C.2C.1
- `13e41247` / `22fdcea6` / `62f87a56`: opt-in v19 em verify/restore/restart exatos; schema runtime e defaults PCS.4C seguem 18. Restore antigo 10–18 continua compatível. Dois boots também comparam as três tabelas de trabalho; não executam worker.
- `c9fdb7cc` / `b3bacbb7` / `1200820b`: schema19_drill cria diretório privado novo, copia DB + ZIP, aplica DDL e registro 19 em transação, compara esquema/linhas anteriores e hash da origem; CLI explícita, relatório sem paths/tokens/hashes. Falha remove somente diretório criado pela operação.
- `86fad9dc` / `a7eeda19` / `0e455813`: validação do DDL candidato exato, rejeita snapshot falsamente rotulado ou com tabela/índice faltante.
- `d9e388fc` / `9ccf7ef2`: testes de upgrade/restore, dados/manifest/asset, rollback, colisão de diretório, v19 opt-in, novo destino não vazio, boot/rank e estado de jobs, retomada de lease e CLI sanitizada. CI aprovado no HEAD `e8e9b0c2321d1dfb05fa9e54212a7ac4d1e8c345` (4 workflows/8 jobs); merge `59c3b4bef582592bfc90a8d72b6af02fd7fbff6b`.

## Runbook de restore em destino novo — alternativa histórica de laboratório
Usar imagem de validação que contenha este checkpoint **e o backup v18 original** (DB + .avatar.zip), preservado no volume privado do Job. Não usar caminho de banco ativo. Criar um diretório de saída novo e um **novo destino Turso independente e vazio**, diferente tanto da origem quanto do destino do drill PCS.4C anterior. Credenciais via secrets; sem registrar URL/token. Verificar o commit da imagem, confirmar exclusividade de acesso ao destino, sem outros writers.

```sh
python -m amora.storage.schema19_drill prepare "$SNAPSHOT_V18" "$NEW_PRIVATE_DIR"
python -m amora.storage.schema19_drill verify "$NEW_PRIVATE_DIR/schema19.db"
python -m amora.storage.schema19_drill restore "$NEW_PRIVATE_DIR/schema19.db" --isolated-destination
python -m amora.storage.schema19_drill restart "$NEW_PRIVATE_DIR/schema19.db" --isolated-destination
```
As variáveis de origem e destino seguem AMORA_LIBSQL_URL, AMORA_SECRET_LIBSQL_AUTH_TOKEN, AMORA_PCS4C_DESTINATION_URL e AMORA_PCS4C_DESTINATION_TOKEN. Não trocar configuração do Core de produção. Não reutilizar o console v18 como se fosse v19. Para a amostra v18 certificada de 51 tabelas/48.059 linhas: esperado 54 tabelas/48.060 linhas, 50 tabelas de conteúdo antigas intactas + histórico antigo preservado com uma linha nova; equal_tables=54; boots=2, rank_equal=true, memory_tables_equal=4. Se qualquer gate falhar, interromper sem apagar/restaurar por cima. Guardar a evidência e a imagem v18 certificada. 

Limite: o Core atual ainda exige schema 18 em readiness. Os boots do drill verificam lógica e ranking, não readiness nem deploy de v19. C.2C.3 deve registrar a migração runtime, atualizar recuperação/compatibilidade e testar readiness após o gate real. Nenhum deploy/worker/migração de produção neste PR.


## Atualização de operador
O caminho vigente C.2C.2 é a migração aditiva do destino de teste PCS.4C existente via Console Dev e mesmo Job, sem novo banco ou secrets: [runbook e evidência](MM00D3C2C2_OPERATOR_DRILL.md). A revisão para reuso está implementada, novo CI pendente. O CLI acima permanece alternativa de laboratório em outro destino vazio; NÃO usar no destino PCS.4C existente. C.2C.3 permanece bloqueado pelo gate real.
