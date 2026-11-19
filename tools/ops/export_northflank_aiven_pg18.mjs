// Copy only the preexisting native PostgreSQL 18 dump from the existing
// pcs4c-recovery durable volume. Never download /private memory/SQLite or
// expose bytes in logs. Explicitly uses an ephemeral read-only run.
import { ApiClient, ApiClientInMemoryContextProvider } from '@northflank/js-client';
import { mkdir, rm, stat } from 'node:fs/promises';

const token = process.env.NF_BACKUP_API_TOKEN;
if (!token) throw new Error('PG_BACKUP_NF_TOKEN_MISSING');

const projectId = 'default';
const jobId = 'pcs4c-recovery';
const remotePath = '/private/pcs4c/backup/amora-aiven-pg18-v18-prewrite.dump';
const localPath = '/tmp/amora-aiven-pg18-export/amora-aiven-pg18-v18-prewrite.dump';
const base = 'https://api.northflank.com/v1/projects/' + projectId + '/jobs/' + jobId;
const headers = {
  Authorization: 'Bearer ' + token,
  'Content-Type': 'application/json',
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const ctx = new ApiClientInMemoryContextProvider();
await ctx.addContext({ name: 'amora-aiven-pg18-offsite-export', token });
const client = new ApiClient(ctx, { throwErrorOnHttpErrorCode: true });
await mkdir('/tmp/amora-aiven-pg18-export', { recursive: true, mode: 0o700 });
let runId;

async function startTransferRun() {
  const res = await fetch(base + '/runs', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      deployment: { docker: { configType: 'customCommand', customCommand: 'sleep 600' } },
    }),
  });
  if (!res.ok) throw Error('PG_BACKUP_NF_RUN_BLOCKED');
  const payload = await res.json();
  const id = payload?.data?.id;
  if (typeof id !== 'string' || id.length < 8) throw Error('PG_BACKUP_NF_RUN_ID_INVALID');
  return id;
}

async function download() {
  for (let attempt = 0; attempt < 24; attempt++) {
    try {
      await rm(localPath, { force: true });
      const result = await client.download.job.files(
        { projectId, jobId }, { localPath, remotePath },
      );
      if (result?.error) throw Error('PG_BACKUP_FILE_DOWNLOAD_FAILED');
      const info = await stat(localPath);
      if (!info.isFile() || info.size < 4096) throw Error('PG_BACKUP_FILE_NOT_READY');
      console.log('AIVEN_PG18_DUMP_TRANSFERRED_BYTES ' + info.size);
      return;
    } catch {
      if (attempt === 23) throw Error('PG_BACKUP_VOLUME_FILE_UNAVAILABLE');
      await sleep(5000);
    }
  }
}

async function stopTransferRun() {
  if (!runId) return;
  try {
    await fetch(base + '/runs/' + encodeURIComponent(runId), {
      method: 'DELETE', headers,
    });
  } catch {
    console.log('PG_BACKUP_TRANSFER_RUN_AUTO_EXPIRY');
  }
}

try {
  runId = await startTransferRun();
  await sleep(8000);
  await download();
} finally {
  await stopTransferRun();
}
