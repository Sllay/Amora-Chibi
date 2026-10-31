# ADR-0021 — Writes atômicos de uma instrução sem transação explícita remota

Status: ACCEPTED por evidência física de latência e continuidade da ADR-0020.  
Data: 2026-10-30. Refina somente o custo transacional da ADR-0020; preserva sua política de recovery, poison, ausência de replay e falha fechada para writes obrigatórios.

## Contexto / problema

A telemetria cirúrgica v3 em Safari/Northflank/Turso mediu um turno real com:

```text
22991 ms total
15812 ms cognition
389 ms provider
7502 ms context
22588 ms remote SQL / 126 pipelines
sql r21/6083 ms · w35/5027 ms · tx70/11478 ms
```

As categorias fecham exatamente `21 + 35 + 70 = 126`. Os 70 pipelines de controle transacional correspondem a dois controles (`BEGIN` + `COMMIT`) para cada um dos 35 writes observados. Só o controle transacional acumulou 11,478 s, mais que os próprios writes e mais que o provider por larga margem.

O Core possui múltiplos Stores/conexões independentes. Criar uma mega-transação de turno entre cognition, events, emotion, conversation e outros domínios aumentaria acoplamento e mudaria ownership de persistência. Também não é aceitável voltar ao experimento de lazy-BEGIN no `RemoteConnection`: CI anterior demonstrou que isso altera contratos certificados de recovery e observabilidade.

## Método anterior

Stores frequentemente usavam:

```text
with connection:
    execute(um único INSERT/UPDATE)
```

No SQLite local isso garante commit da transação implícita. No adapter libSQL/Hrana atual, o mesmo padrão produz três pipelines de rede independentes:

```text
BEGIN IMMEDIATE
statement
COMMIT
```

Uma única instrução SQL já é atomicamente aplicada pelo banco; o envelope explícito não adiciona uma segunda invariável quando não existe read-modify-write nem outra instrução que precise compartilhar a mesma transação.

## Alternativas consideradas

1. **Mega-transação por turno entre Stores** — rejeitada: conexões independentes, ownership ruim e domínio acoplado à infraestrutura.
2. **Lazy BEGIN genérico no RemoteConnection** — rejeitado: já alterou timing/semântica de falha e contratos protegidos pela ADR-0020.
3. **Batch/replay assíncrono de writes** — rejeitado: muda durabilidade/ordem e cria reconciliação que a arquitetura não possui.
4. **Write unitário em autocommit apenas quando a operação possui exatamente uma instrução** — escolhido.

## Decisão

Introduzir `execute_atomic_statement(...)` na fronteira de storage.

Regras:

- a função representa **exatamente uma instrução SQL**;
- em `libsql`, executa a instrução fora de uma transação explícita, usando a atomicidade/autocommit da própria instrução e um único pipeline Hrana;
- em SQLite local, abre/fecha o contexto da conexão para preservar commit antes do retorno;
- se SQLite local já estiver dentro de uma transação externa, apenas executa a instrução e deixa a unidade externa decidir commit/rollback;
- read-modify-write, múltiplos statements, migrations, leases, claim/ACK/cancel e invariantes multi-row continuam usando transação explícita;
- nenhum write ambíguo é repetido;
- falha de transporte continua `SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN` e poison conforme ADR-0020;
- nenhum SQL/argumento é guardado para replay;
- duplicate/idempotency/rowcount continuam sendo decididos pelo resultado real do statement.

Primeiros consumidores: writes unitários de CognitiveStore, Event Store/identidade de sessão, decisão de turno e EmotionStore. Operações multi-statement nesses mesmos Stores permanecem inalteradas.

## Consequências

Esperado no caminho físico: queda forte de `tx<count>/<ms>` sem exigir redução equivalente de `w<count>`, pois a quantidade semântica de writes pode permanecer igual enquanto dois round-trips de controle por write são removidos.

A política não transforma libSQL em eventual/assíncrono, não relaxa durabilidade e não move estado para browser/provider. Também não elimina a necessidade futura de reduzir reads ou consolidar writes quando houver evidência de volume excessivo.

O helper discrimina explicitamente o backend `libsql`; um backend remoto futuro não herda essa otimização automaticamente sem demonstrar contrato equivalente de statement atomic/autocommit.

## Critério de validação

CI deve provar:

- um write unitário libSQL usa um pipeline;
- SQLite local persiste o statement após fechar/reabrir;
- helper dentro de transação SQLite externa respeita rollback externo;
- regressões existentes de recovery, rowcount, dedupe e container permanecem verdes.

Validação física deve comparar a mesma telemetria v3. Sucesso primário: `tx` cair materialmente dos 70 pipelines observados, mantendo resposta/estado corretos. Reavaliar se surgirem requisitos de atomicidade entre múltiplos Stores ou backend remoto com semântica diferente.
