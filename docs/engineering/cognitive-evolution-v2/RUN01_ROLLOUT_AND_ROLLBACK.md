# RUN 01 — Preparação de rollout e rollback (candidato)

Status: **IN_PROGRESS / não autorizado em produção**.
PR #378, issue #376. Base de integração aprovada: 59dcbec (8/8 workflows).
A migração e os novos ensaios deste documento exigem CI e certificação adicionais.
O Northflank permanece como único ambiente operacional; Altare fora de escopo.

## Componente de schema

A fila é um add-on explícito de versão 1 sobre PostgreSQL 18 / schema canônico v18.
Não altera schema_migrations, não executa a migration v19, não importa registros
nem muda backend. A instalação usa DDL revisado, transação e advisory lock.
O módulo amora.storage.postgres_reply_job_migration oferece review_reply_schema
(somente leitura) e prepare_reply_schema (escrita explícita). O mesmo módulo
é exercitado pelo instalador sintético local. O alvo deve ser um
PostgresTargetPolicy previamente pinado, com PG18 e TLS verify-full quando remoto.
Não há comando automático de migração no startup. A confirmação de operador
não substitui verificação independente de backup/restore, capacidade e isolamento.

O review confere versão base, receipt do componente, colunas/tipos, nulabilidade,
identity, PK/unique, FK, presença dos checks e índices. Antes de promover,
certificar também definição de constraints, privilégios e recuperação dos
backups nativos no alvo real. Nunca adotar tabela antiga/desconhecida nem
dropar a fila para resolver uma divergência. Em commit desconhecido, executar
review, não repetir DDL cegamente. SQLite candidato compara as definições
de tabela e índices a uma referência em memória; instalação continua explícita.

## Modos de execução (não configurar agora)

AMORA_DURABLE_REPLY_MODE:
- off (padrão, aprovação ausente): rota legada; permitido apenas quando não
  há trabalhos duráveis por liquidar nem outbounds duráveis sem ACK.
- accept: aceitar novas DMs textuais elegíveis e processar a fila.
- drain: não aceitar novas DMs textuais elegíveis; processar os trabalhos
  aceitos, inclusive ACKs repetidos. Requisições novas elegíveis recebem 503,
  não uma confirmação falsa; antes da drenagem, pausar novos envios/ingresso
  em manutenção controlada. Não confiar nos retries finitos do worker para
  armazenar novas mensagens durante manutenção longa.

accept/drain requerem AMORA_DURABLE_REPLY_APPROVAL=CERTIFIED_REPLY_SCHEMA_V1_AND_ROLLBACK.
O registry PG só recebe os SQLs candidatos sob essa opção e o dispatcher
verifica o componente antes de aceitar novos trabalhos. Não executar esta
configuração até autorização da certificação e preflight aprovados.
Backend não suportado falha fechado; libSQL não certificado neste candidato.
O status autenticado /v1/messaging/worker/reply-jobs/status expõe modo,
contagens e número de trabalhos não liquidados, sem texto/IDs nativos.

## Gate de certificação real — ordem obrigatória

1. Fixar SHA do PR, confirmar backend efetivo, schema, revisão e saúde do
   Northflank, verificar que apenas um Gateway Discord está operacional.
2. CI integral no SHA, testes D01–D16 aplicáveis e revisão de concorrência,
   contexto causal, autenticação, efeito de appraisal/self-choice,
   tombstone/outbox e falhas do transporte. Obter baseline p50/p95, CPU/RAM,
   event-loop lag e conexões PG; não inventar métricas.
3. Gerar backup privado do banco efetivo, incluindo componente quando existir;
   guardar artefato/recibo e hashes sem expor dados. Restaurar independentemente
   em destino isolado e verificar conteúdo/schema, identidade e fila.
   Nunca restaurar em cima de produção. Para PG com projeções de memória,
   seguir também o contrato de epoch/restore existente.
4. Janela exclusiva: pausar ingressos novos e writers, validar endpoint/CA,
   capacidade e recibo do backup. Executar revisão/instalação aditiva
   autorizada em alvo certificado. Revisar pós-commit sem mudar backend.
5. Implantar revisão compatível, primeiro em drain ou ambiente canário
   isolado, sem segundo Gateway. Confirmar status/health, migrar modo
   somente após evidência e autorização.
6. Exercitar DM textual real, duplicação, retry de provider, restart de
   Core, recuperação de checkpoint, ACK final, ordenação e revogação.
   Verificar ausência de fallback técnico em histórico, resposta única,
   memória/reflexão preservada e registros sem conteúdo sensível.
7. Comparar medições ao baseline e registrar SHA implantado, evidências
   e problemas. Não declarar RUN 01 COMPLETED antes de aprovação live.
   Não iniciar RUN 02 antecipadamente.

## Rollback sem apagar pendências

- Se houver falha de schema pré-ativação: manter off, nenhuma ativação,
  revisar o commit da migração em leitura; não repetir ou dropar DDL.
- Depois de aceitar trabalho: preservar versão compatível com fila, passar
  a drain em manutenção controlada; manter processador/outbox e ACKs
  funcionando. Conferir contagens e registros bloqueados, outbounds e
  reconciliação; bloqueado conta como não liquidado, não se ignora.
- Se não for possível liquidar por indisponibilidade, permanecer no modo
  compatível ou pausar o dispatcher preservando leases/checkpoints no banco.
  Nunca retroceder para binário que ignore pendências, nem marcar enviado
  sem recibo do transporte. Não perder entradas ao restabelecer serviço.
- Só após zero pendências, incluindo reconciliação de inputs marcados e
  outbox, snapshot atualizado e conferência de Gateway, considerar off ou
  versão anterior sob aprovação separada. Manter a tabela e seus registros;
  não há DROP TABLE como caminho de rollback. Recuperação após desastre
  deve restaurar snapshot consistente, reconciliar entregas externas
  desconhecidas e nunca reenviar cegamente um outbound antigo.

## Ensaios adicionados nesta revisão

- Atomicidade de admissão (input/journal/job) e falha após INSERT antes
  de enqueue: rollback, retry sem perda ou duplicação.
- Modo drain em dispatcher substituído: aceita ACK repetido, recusa
  admissão nova, gera pendente e liquida com ACK.
- Verificação do schema e rejeição de índice removido; aprovação explícita.
- Backup SQLite nativo e PG18 pg_dump/pg_restore (serviço descartável
  GitHub), novo Core com uma resposta ready e uma pending. Reaproveitar
  outbound, respeitar ordem e confirmar que não houve nova geração da
  resposta ready. O teste não representa recuperação de um ACK físico
  desconhecido; essa janela exige prova adicional.
- CI mais recente ainda pendente neste documento.

Limites: web, imagem e expressões recebidas continuam no fluxo anterior.
O piloto não é uma solução multimodal definitiva. Avenças de configuração
e DDL não são uma certificação de produção.
