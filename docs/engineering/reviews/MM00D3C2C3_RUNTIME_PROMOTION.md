# MM.00 D.3C.2C.3 — Runtime v19, readiness e recuperação (gate explícito)

## Entrada e limite de autorização
O operador concluiu o checkpoint real C.2C.2 **somente no Turso isolado**: v18 com 51 tabelas/48.059 linhas, candidato v19 com 54 tabelas/48.060 linhas, migrate equal_tables=54, 2 boots, 4 tabelas de memória e 3 tabelas de episódios idênticas, rank_equal=true, histórico de 7 tabelas preservado. O diagnóstico v18 histórico reporta deriva não-crítica em `platform_presence` e `sqlite_sequence`, mantida na baseline isolada. Esse relatório **não** comprova readiness da produção v19 nem autoriza migração produtiva sem backup e aprovação operacional.

## Política de compatibilidade
- O valor `LATEST_SCHEMA_VERSION` permanece **18** por padrão. A versão **19** fica registrada em `migrations_v109.py` com as definições exatas de `sensory_episode_schema.py`; o runtime a executa apenas com **duas** configurações explícitas:
  - `AMORA_RUNTIME_SCHEMA_TARGET=19`
  - `AMORA_RUNTIME_SCHEMA_CONFIRMATION=CERTIFIED_V19_BACKUP_AND_ROLLBACK`
- Ambas ausentes: boot/migrate/readiness/PCS.4C continuam 18, inclusive após mero deploy da nova imagem. Configurações incompletas ou inválidas bloqueiam o boot **antes** de criar o DB. O target é fixado na instância da conexão, e readiness compara a versão real com esse target.
- Banco v19 aberto por runtime autorizado somente para v18: falha `RUNTIME_SCHEMA_AHEAD_OF_ALLOWED_TARGET` antes de inicializar identidade/memória. Não executar downgrade SQL.
- Banco v18 com opt-in autorizado: migração 19 aditiva em uma transação, recusa qualquer objeto de trabalho preexistente, verifica DDL exato antes de registrar a versão, mantém histórico/tabelas antigas. Boot v19 subsequente revalida DDL antes de carregar o runtime.
- O restauro remoto de schema 19 segue opt-in explícito por parâmetro `target_schema=19` **somente para um destino vazio e independente**. Os comandos de backup/restauração PCS.4C não são retargetados automaticamente; v18/19 não são confundidos.
- A rotina isolada de certificação `pcs4_restart` injeta o target 19 somente nos processos filhos de teste v19, sem herdar credenciais de origem e sem executar worker.

## Testes do gate
`tests/test_schema19_runtime_c2c3.py`: opt-in incompleto, boot v18 por padrão, v18→19 local e Hrana, idempotência, retenção de registros/histórico, recusa de objetos parciais, DDL alterado, readiness v19 e bloqueio de runtime antigo. Regressões C.2C.1/C.2C.2 e demais suites de Core/cloud/security devem passar no HEAD do PR antes do merge.

## Rollback e ativação
**Não aplicar os envs em produção nesta etapa** e não alterar o Turso produtivo. Se, em promoção futura autorizada, houver falha na ativação após migração: congelar escrita, coletar relatório sem segredos e restaurar **backup v18 certificado em destino novo e vazio**, com imagem v18, redirecionando o serviço somente depois de validação e decisão sobre dados gravados depois do corte. Não executar downgrade in-place de v19 nem sobrescrever o banco atual. Confirmar contrato/CI e readiness em banco isolado antes de qualquer promoção real.

Nenhum produtor de episódios, projeção multimodal, novo transporte ou worker D.3C.3 é ativado neste checkpoint. Os modos de Agenda e iniciativa temporal continuam independentes.
