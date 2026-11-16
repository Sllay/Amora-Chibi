# D.3C.2C — Evolução e recuperação de schema 19

Issue #238. #247 aprovado 4/4 workflows, 8/8 jobs, merge 483e35071be555226c6f0c99c7b61ba95207259c.
Status: C.2C.1 em implementação; produção permanece 18.

## Checkpoints
- C.2C.1: preparar cópia v19 offline a partir de backup v18 verificado (DB + avatar ZIP), sem escrever na origem. Diretório de saída novo, privado, falha limpa somente artefatos próprios. DDL aditivo existente, versão 19 registrada apenas na cópia. Provar todos os objetos antigos e todas as linhas antigas idênticos, exceto a nova linha de migration; três tabelas novas vazias. SHA de origem e sidecar preservados. Restore exato de v19 somente por opt-in explícito em funções de certificação; defaults/console PCS continuam v18. Testes SQLite/Hrana, assets, rollback, destino não vazio, corrupção e restart/rank. Não registrar versão 19 no bootstrap ou readiness nesta etapa.
- C.2C.2: validar cópia real dos 48.059 registros com operador, destino independente vazio, snapshot v18 e imagem certificada preservados. Gate separado para upgrade, restore, restart e estado da fila. Não modificar destino anterior nem origem de produção. Relatório sem conteúdo privado. Testes de mock não equivalem a Turso real.
- C.2C.3: só após certificação, registrar migração runtime e atualizar readiness, recovery e testes de compatibilidade. Rollback é imagem v18 + backup v18 em destino independente, nunca downgrade destrutivo. Avaliar impacto de deploy automático antes de merge. Nenhum worker ativo até C.3.

## Decisão
A versão 19 é preparada offline primeiro. Alterar LATEST_SCHEMA_VERSION agora faria o bootstrap migrar automaticamente e invalidaria o contrato de certificação v18. As funções PCS mantêm defaults estritos; novo caminho explícito de laboratório não é promoção. Boot/rank de v19 em laboratório com código atual não prova readiness v19 (readiness ainda exige 18). Gate operacional e promoção separados.
