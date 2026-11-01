import { spawn } from 'node:child_process';
import { createServer } from 'node:http';

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 160)}` : '';
  process.stderr.write(`[amora-discord] ${code}${suffix}\n`);
}

export function resolveHealthPort(env = process.env) {
  const raw = String(env.AMORA_WORKER_HEALTH_PORT || env.PORT || '').trim();
  if (!raw) return null;
  if (!/^\d{1,5}$/.test(raw)) throw new Error('worker health port must be numeric');
  const port = Number(raw);
  if (port < 1 || port > 65535) throw new Error('worker health port out of range');
  return port;
}

function startHealthServer(port) {
  if (port === null) return null;
  const server = createServer((request, response) => {
    if (request.method === 'GET' && (request.url === '/' || request.url === '/live')) {
      response.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      response.end('{"status":"alive"}\n');
      return;
    }
    response.writeHead(404, {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    response.end('{"detail":"not found"}\n');
  });
  server.listen(port, '0.0.0.0', () => safeLog('HEALTH_LISTEN', port));
  server.on('error', (error) => safeLog('HEALTH_ERROR', error?.code || error?.name || 'Error'));
  return server;
}

async function main() {
  const port = resolveHealthPort();
  const health = startHealthServer(port);
  safeLog('ENTRYPOINT_START');

  const child = spawn(process.execPath, ['workers/discord/worker.mjs'], {
    stdio: 'inherit',
    env: process.env,
  });

  let shuttingDown = false;
  const forward = (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    safeLog('SIGNAL', signal);
    try { child.kill(signal); } catch {}
  };
  process.on('SIGTERM', () => forward('SIGTERM'));
  process.on('SIGINT', () => forward('SIGINT'));

  const result = await new Promise((resolve) => {
    child.once('error', (error) => resolve({ code: 1, signal: null, error }));
    child.once('exit', (code, signal) => resolve({ code, signal, error: null }));
  });

  await new Promise((resolve) => {
    if (!health) return resolve();
    health.close(() => resolve());
  });

  if (result.error) {
    safeLog('PROCESS_ERROR', result.error?.code || result.error?.name || 'Error');
    process.exitCode = 1;
    return;
  }
  safeLog('PROCESS_EXIT', `code=${result.code ?? 'null'} signal=${result.signal ?? 'none'}`);
  if (result.signal) process.exitCode = shuttingDown ? 0 : 1;
  else process.exitCode = Number.isInteger(result.code) ? result.code : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    safeLog('ENTRYPOINT_FATAL', `${error?.name || 'Error'}:${error?.message || ''}`);
    process.exitCode = 1;
  });
}
