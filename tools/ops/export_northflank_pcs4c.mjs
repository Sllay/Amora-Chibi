import { ApiClient, ApiClientInMemoryContextProvider } from '@northflank/js-client';
import { mkdir, stat, rm } from 'node:fs/promises';
import path from 'node:path';

const token = process.env.NF_BACKUP_API_TOKEN;
if (!token) throw new Error('Missing NF_BACKUP_API_TOKEN');

const projectId = 'default';
const jobId = 'pcs4c-recovery';
const baseUrl = 'https://api.northflank.com/v1/projects/' + projectId + '/jobs/' + jobId;
const dir = '/tmp/amora-export-ci/source';
const files = [
  'amora-pre-pcs4c.db',
  'amora-pre-pcs4c.db.avatar.zip',
];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

await mkdir(dir, { recursive: true, mode: 0o700 });

const contextProvider = new ApiClientInMemoryContextProvider();
await contextProvider.addContext({ name: 'amora-backup-export', token });
const client = new ApiClient(contextProvider, { throwErrorOnHttpErrorCode: true });

const authHeaders = {
  'Authorization': 'Bearer ' + token,
  'Content-Type': 'application/json',
};
let runId = null;

async function startReadOnlyRun() {
  // Override de uma UNICA execucao; nao modifica a configuracao do Job.
  // O processo somente espera, permitindo transferir arquivos do volume.
  const response = await fetch(baseUrl + '/runs', {
    method: 'POST',
    headers: authHeaders,
    body: JSON.stringify({
      deployment: {
        docker: { configType: 'customCommand', customCommand: 'sleep 600' },
      },
    }),
  });
  if (!response.ok) {
    throw new Error('Northflank recusou a execucao temporaria (HTTP ' + response.status + ').');
  }
  const data = await response.json();
  const id = data?.data?.id;
  if (typeof id !== 'string' || id.length < 8) {
    throw new Error('Northflank nao retornou um identificador de run valido.');
  }
  return id;
}

async function transferFile(filename) {
  const remotePath = '/private/pcs4c/backup/' + filename;
  const localPath = path.join(dir, filename);
  let lastError = '';
  for (let attempt = 1; attempt <= 24; attempt += 1) {
    try {
      await rm(localPath, { force: true });
      const result = await client.download.job.files(
        { projectId, jobId },
        { localPath, remotePath },
      );
      if (result?.error) throw new Error('Northflank download failed');
      const info = await stat(localPath);
      if (!info.isFile() || info.size < 1) {
        throw new Error('Arquivo vazio ou nao encontrado');
      }
      console.log('Arquivo copiado e verificado: ' + filename + ' (' + info.size + ' bytes)');
      return;
    } catch (error) {
      lastError = String(error?.message || error).slice(0, 90);
      if (attempt < 24) await sleep(5000);
    }
  }
  throw new Error('Falha ao copiar ' + filename + '; confirme que o Job temporario esta ativo, o volume esta montado e o token permite download. Causa resumida: ' + lastError);
}

async function stopRun() {
  if (!runId) return;
  try {
    const response = await fetch(baseUrl + '/runs/' + encodeURIComponent(runId), {
      method: 'DELETE',
      headers: authHeaders,
    });
    if (!response.ok) {
      console.log('Nao foi possivel encerrar o run via API; ele terminara ao fim do sleep.');
    } else {
      console.log('Run temporario encerrado.');
    }
  } catch {
    console.log('Falha de rede ao encerrar o run; ele terminara automaticamente.');
  }
}

try {
  runId = await startReadOnlyRun();
  console.log('Run temporario de leitura iniciado; nenhuma alteracao no backup.');
  await sleep(8000);
  for (const filename of files) {
    await transferFile(filename);
  }
  console.log('Ambos os arquivos foram copiados para o runner temporario.');
} finally {
  await stopRun();
}
