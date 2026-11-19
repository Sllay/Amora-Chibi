# Exportar backup PCS.4C pelo GitHub Actions (sem terminal)

## Estado

Execucao manual solicitada pelo operador apos configurar os dois secrets em 2026-11-19. O token sera usado exclusivamente no runner GitHub Actions; nenhuma credencial entra no repositorio.

Implementação isolada em `ops/backup-export-ci-20261119`. Nao altera o runtime,
migracao, jobs permanentes ou banco de producao. O job do GitHub Actions so
efetua transferencia quando ativado por commit com marcador
`[export-amora-backup]` nessa branch. Futuramente, apos integrar o workflow
na branch padrao, `Run workflow` permite acionamento manual.

## Pre-requisitos

1. Em GitHub > `Sllay/Amora-Chibi` > Settings > Secrets and variables >
   Actions > Repository secrets, criar:
   - `NF_BACKUP_API_TOKEN`: token Northflank do time autenticado pelo token,
     limitado ao Job `default/pcs4c-recovery`, com permissoes necessarias
     para iniciar run temporario, acesso a container e download dos arquivos.
     Para encerrar o run antecipadamente, o token pode precisar de permissao
     de Update do Job; sem ela, o comando `sleep` termina por si so.
   - `AMORA_BACKUP_PASSPHRASE`: frase-senha **aleatoria** com 32 ou mais
     caracteres. Salve em gerenciador de senhas. Nao enviar por chat ou
     salvar no repo.
2. Confirmar no Northflank que o Job `pcs4c-recovery` continua associado ao
   volume persistente em `/private` e que nao ha run concorrente.
3. Confirmar quota e eventual cobranca por uma execucao curta do Job.

## Procedimento

1. O workflow inicia um run isolado do Job com comando temporario
   `sleep 600`, sem alterar a configuracao persistente.
2. Copia as fontes exclusivamente para o runner efemero:
   - `/private/pcs4c/backup/amora-pre-pcs4c.db`
   - `/private/pcs4c/backup/amora-pre-pcs4c.db.avatar.zip`
3. Confere `PRAGMA quick_check` (SQLite) e `ZipFile.testzip` (assets).
4. Produz `amora-backup.zip` com criptografia WinZip AES-256, verifica
   descompactando cada entrada com senha e comparando SHA-256 em memoria.
5. Publica SOMENTE o ZIP criptografado no GitHub Actions Artifact com
   expiracao em 1 dia. A interface GitHub pode oferecer um ZIP externo
   contendo o `amora-backup.zip` interno.
6. O operador baixa pelo navegador no iPhone, salva no Google Drive
   manualmente, verifica o arquivo e pode excluir o artifact GitHub antes
   da expiracao. O ZIP exige suporte a WinZip AES-256 na restauracao.

## Restricoes de seguranca

- `Sllay/Amora-Chibi` e publico: artifacts ficam acessiveis a pessoas com
  permissao de leitura. Nunca publicar o banco em claro ou senha em logs.
- Somente arquivo criptografado e enviado. O runner e temporario e limpa
  os arquivos no fim. Ainda assim, para isolamento adicional, e preferivel
  um repositorio PRIVADO de backups.
- Nao colocar token ou senha em inputs do workflow_dispatch, porque entradas
  de workflows nao sao um cofre de segredos.
- Nunca dar commit no ZIP, SQL ou arquivos privados.
- O fluxo e exportacao read-only. Nao restaura banco, nao muda storage
  backend, nao faz merge em main.
- Em caso de erro, nao fazer retry infinito nem mudar o Job permanentemente;
  diagnosticar permissao de download, status do container e montagem do volume.

Documentacao oficial:
- https://northflank.com/docs/v1/api/project/jobs/run-job
- https://northflank.com/docs/v1/api/copy-files
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts
