# Aiven Fase 2C — importador sintético SQLite v18 → PostgreSQL 18

Status: laboratório isolado, sem acesso ao Aiven e sem integração ao Core de produção.

## Implementado

O código core/amora/storage/postgres_synthetic_import.py importa **somente dados inventados de fixtures** de SQLite v18 para PostgreSQL 18 de CI, banco local amora_pg_synthetic e schema amora_pg_v18_lab. Ele não recebe URI, não abre arquivos reais de backup e nunca muda AMORA_STORAGE_BACKEND.

Garantias do laboratório:
- Confere integridade SQLite, FOREIGN KEY, schema version 18, nomes de todas as 51 tabelas lógicas e colunas contra migrations canônicas.
- Bloqueia PostgreSQL fora do host loopback, banco com nome incorreto, versão incorreta e destino já preenchido.
- Importa linhas das 49 tabelas relacionais em lotes de 100, mantendo exatamente IDs e serializações TEXT/REAL/INTEGER/BLOB/NULL, com comparação por multiconjunto de hashes tipados em memória, sem imprimir conteúdo ou digests.
- Recupera o conteúdo lógico da tabela virtual FTS5 (não suas shadow tables) para a tabela PG memory_search, verificando o conteúdo.
- Mantém foreign keys ativos: na transação de importação permite deferimento de FK, retornando à semântica imediata padrão ao final. Rollback automático em caso de erro.
- Para source SQLite AUTOINCREMENT, ajusta o gerador PostgreSQL com o máximo entre IDs importados e SQLite sqlite_sequence, sem utilizar sqlite_sequence como tabela de aplicação.
- Valida contagens de todas as tabelas dentro da mesma transação. Não retorna resultado de sucesso sem commit.
- Relatório sintético só contém status e contagens: nunca nomes de usuários, textos, credenciais, caminhos ou hashes.

## Limites e gates

- Esta implementação não é importação de backup real: não existe entrada de caminho nem DSN de produção. Não lê volume /private.
- Os testes sintéticos validam valores e integridade, mas não provam a compatibilidade de todos os registros do backup de 75.563.008 bytes, nem benchmarks de memória/tempo.
- Não certifica igualdade de ranking FTS5/bm25 com PostgreSQL ts_rank, ACL complexa, unicode, consultas JSON1, transações e leases concorrentes no Core, boot/restart ou reconciliação de dados posteriores ao snapshot.
- Em uma futura importação privada, também serão exigidos checksum e sidecar originais, relatório sanitizado, limite de armazenamento e conexões do Free, rollback ensaiado, comparação por tabela e aprovação explícita do operador.

**Nenhuma ação no Northflank necessária neste checkpoint.** Não executar o laboratório contra Aiven/Turso.
