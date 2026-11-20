# Aiven Fase 2D.2 — comparação FTS5/BM25 vs PostgreSQL 18

Status: laboratório experimental, sem integração ao backend de produção. Não há importação de dados reais nem chamada ao Aiven.

## Código auditado

O Core atual executa FTS5 com o tokenizador `unicode61 remove_diacritics 2`, recuperação por `MATCH` e ordenação `bm25(memory_fts)`. O ranking final também envolve `RRF` e `MMR` em `core/amora/memory/store.py`. PostgreSQL 18 padrão oferece `to_tsvector`, `to_tsquery` e `ts_rank`, mas **não oferece automaticamente semântica idêntica** ao FTS5.

O novo `postgres_fts_parity_lab.py` compara em PostgreSQL 18 descartável:
- Correspondência do conjunto de documentos encontrados por FTS5 e por busca PG experimental com termos ASCII;
- Ordenação por `bm25` versus `ts_rank`, sem declarar equivalência quando houver coincidências em uma fixture;
- Filtro de owner, AUTH/SECRET, arquivamento, valid_until e processamento remoto aplicado **antes** do limite de 150;
- Caso adversarial com `café` indexado e busca por `cafe` para demonstrar diferenças da normalização de acentos;
- Recusa de consulta PostgreSQL com tokens não ASCII ainda não certificados, e trava explícita `FTS5_BM25_PARITY_NOT_CERTIFIED` para impedir promoção ao Core.

Os testes usam apenas registros inventados em SQLite v18 em memória, importados pelo laboratório PostgreSQL 18 existente. O comparador só retorna contagens e indicadores, nunca IDs, textos, hashes ou URI.

## Gate para fase seguinte

Este PR é uma ferramenta de medição, não a solução final de recuperação semântica. O próximo checkpoint precisa definir uma implementação de índice com compatibilidade explícita do tokenizador e escore ou um contrato novo de relevância com aprovação do usuário. Exigir testes de português, acentuação, Unicode, termos compostos, top-K, limites 150/300, várias pessoas, privacidade, RRF/MMR e latência antes de habilitar PostgreSQL como backend.

O fallback simples não pode declarar sucesso após degradar a recuperação; qualquer diferença não resolvida deve bloquear o cutover. Manter `AMORA_STORAGE_BACKEND` e Turso intactos.
