"""Private, operator-only PCS.4C web service; never mounted on the chat API.

Run as a separate single-replica service with a mounted durable volume.
No HTTP route returns backup bytes, credentials, paths, hashes or memory text.
"""
from __future__ import annotations

from contextlib import closing
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from .pcs4_recovery import (
    certify_restore, destination_from_environment, verify_snapshot, _origin_host,
    review_restore_destination,
)
from .pcs4_restart import certify_restart
from .remote import LibSQLBackend

from .pcs4_contract import STEPS as _STEPS, REPORT_KEYS as _REPORT_KEYS
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
_NAME = "amora-pre-pcs4c.db"


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            _sync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def _origin_digest(backend):
    return hashlib.sha256(backend.url.encode()).hexdigest()


def _mounted(path):
    # Check an actual Linux mountpoint; reject ephemeral pseudo-filesystems.
    if path == Path("/"):
        return False
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, after = line.split(" - ", 1)
            mount = before.split()[4].replace("\\040", " ").replace("\\134", "\\")
            if mount == str(path):
                return after.split()[0] not in {"tmpfs", "ramfs", "overlay", "rootfs", "proc", "sysfs"}
    except (OSError, IndexError, ValueError):
        pass
    return False


def export_backup(source, snapshot, *, read_only=False):
    """Existing audited full export and SQL asset archive; no Core bootstrap."""
    from amora.avatar.store import AvatarStore
    from amora.storage.backend import SQLiteBackend
    from amora.storage.blobs import SQLBlobStore
    from amora.time import SystemClock
    import sqlite3

    with closing(source.connect(snapshot)) as connection:
        if read_only:
            class ReadSnapshot:
                def __enter__(self):
                    self.transaction = connection.read_transaction()
                    return self.transaction.__enter__()
                def __exit__(self, *args):
                    return self.transaction.__exit__(*args)
                def execute(self, sql, parameters=()):
                    if not sql.lstrip().upper().startswith('SELECT '):
                        raise ValueError('PCS4C_SOURCE_READ_ONLY')
                    return connection.execute(sql, parameters)
            source.backup(ReadSnapshot(), snapshot)
        else:
            source.backup(connection, snapshot)
    # Archive is derived from the same snapshot, not the live source.
    with closing(sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as read:
        store = AvatarStore(snapshot, snapshot.parent / "assets-private", SystemClock(),
                            backend=SQLiteBackend(), blobs=SQLBlobStore(read))
        try:
            store.backup_assets(snapshot)
        finally:
            store.close()
    return verify_snapshot(snapshot)


class RecoveryPanel:
    def __init__(self, root, source, destination=destination_from_environment, *, online_snapshot=False):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.source = source
        self.online_snapshot = online_snapshot
        self.destination = destination
        self.state_path = self.root / "state.json"
        self.snapshot = self.root / "backup" / _NAME
        self.lock_path = self.root / "operation.lock"
        self.source_id = _origin_digest(source)

    def _lock(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _read(self):
        if not self.state_path.exists():
            return {"completed": [], "running": None, "status": "idle",
                    "reports": {}, "source_id": self.source_id}
        return json.loads(self.state_path.read_text())

    def _write(self, state):
        _atomic_json(self.state_path, state)

    def status(self):
        state = self._read()
        if state["source_id"] != self.source_id:
            return {"status": "blocked", "reason": "SOURCE_CHANGED", "completed": [],
                    "running": None, "reports": {}, "next": None}
        if state["running"]:
            try:
                fd = self._lock()
            except BlockingIOError:
                pass
            else:
                # No worker holds the lock: previous process was interrupted.
                # Never retry a potentially committed restore automatically.
                try:
                    state = self._read()
                    if state["running"]:
                        state.update(status="interrupted", running=None,
                                     reason="INTERRUPTED_REVIEW_REQUIRED")
                        self._write(state)
                finally:
                    os.close(fd)
        completed = state["completed"]
        next_step = _STEPS[len(completed)] if len(completed) < len(_STEPS) else None
        if state["status"] in ("error", "interrupted", "blocked") or state["running"]:
            next_step = None
        return {"status": state["status"], "running": state["running"],
                "completed": completed, "reports": state["reports"],
                "reason": state.get("reason"), "next": next_step}

    def start(self, action, confirmation):
        if action not in _STEPS:
            raise HTTPException(404, "unknown action")
        expected = {"backup": "PAUSEI OS ESCRITORES", "verify": "VOLUME PRIVADO",
                    "restore": "DESTINO VAZIO E INDEPENDENTE",
                    "restart": "DESTINO ISOLADO"}[action]
        if action == "backup" and self.online_snapshot:
            expected = "SNAPSHOT CONSISTENTE"
        if confirmation != expected:
            raise HTTPException(400, "confirmation required")
        try:
            fd = self._lock()
        except BlockingIOError:
            raise HTTPException(409, "operation running") from None
        try:
            state = self._read()
            if (state["source_id"] != self.source_id or state["running"]
                    or state["status"] in ("error", "interrupted", "blocked")
                    or state["completed"] != list(_STEPS[:_STEPS.index(action)])):
                raise HTTPException(409, "invalid recovery state")
            if action == "backup" and self.snapshot.parent.exists():
                raise HTTPException(409, "backup already exists")
            target = None
            if action in ("restore", "restart"):
                try:
                    target = self.destination()
                    # Recheck independently even for an injected backend.
                    if _origin_host(target.url) == _origin_host(self.source.url) or target._token == self.source._token:
                        raise ValueError("unsafe destination")
                    if action == "restore" and state.get("attempted_destination_id", _origin_digest(target)) != _origin_digest(target):
                        raise ValueError("destination changed")
                    if action == "restart" and state.get("destination_id") != _origin_digest(target):
                        raise ValueError("destination changed")
                except Exception:
                    raise HTTPException(409, "independent destination not configured") from None
            if action == "restore":
                state["attempted_destination_id"] = _origin_digest(target)
            state.update(status="running", running=action)
            self._write(state)
            worker = threading.Thread(target=self._execute, args=(fd, state, action, target),
                                      name="pcs4c-operator", daemon=False)
            try:
                worker.start()
            except BaseException:
                state.update(status="error", running=None, reason="WORKER_START_FAILED")
                self._write(state)
                raise
        except BaseException:
            os.close(fd)
            raise
        return {"status": "running", "action": action}

    def review_restore(self):
        """Explicit operator-only reconciliation; never imports or deletes rows."""
        fd = self._lock()
        try:
            state = self._read()
            prefix = ["backup", "verify"]
            already_restored = (state["completed"] == prefix + ["restore"]
                                and state["status"] == "ok" and state["running"] is None
                                and state.get("destination_id"))
            interrupted = (state["completed"] == prefix
                           and ((state["status"] == "running" and state["running"] == "restore")
                                or (state["status"] in ("error", "interrupted")
                                    and state["running"] is None)))
            reviewed_empty = (state["completed"] == prefix and state["status"] == "ok"
                              and state["running"] is None
                              and state.get("restore_review", {}).get("outcome") == "empty"
                              and state.get("attempted_destination_id"))
            if state["source_id"] != self.source_id or not (
                    already_restored or interrupted or reviewed_empty):
                raise ValueError("PCS4C_REVIEW_STATE")
            if any(state["reports"].get(step, {}).get("status") != "ok"
                   for step in ("backup", "verify")):
                raise ValueError("PCS4C_REVIEW_STATE")
            target = self.destination()
            if (_origin_host(target.url) == _origin_host(self.source.url)
                    or target._token == self.source._token):
                raise ValueError("PCS4C_DESTINATION_IS_SOURCE")
            target_id = _origin_digest(target)
            if (state.get("attempted_destination_id", target_id) != target_id
                    or (already_restored and state["destination_id"] != target_id)):
                raise ValueError("PCS4C_REVIEW_DESTINATION_CHANGED")
            self._pin(state, verify_snapshot(self.snapshot))
            outcome, report = review_restore_destination(self.snapshot, target)
            self._pin(state, report)
            if already_restored and outcome != "exact":
                raise ValueError("PCS4C_REVIEW_STATE")
            # Repeated review after a lost final report must retain the original
            # interruption evidence, not replace it with the later successful state.
            if not (already_restored or reviewed_empty) or not state.get("restore_review"):
                state["restore_review"] = {
                    "previous_status": state["status"], "previous_reason": state.get("reason"),
                    "previous_report": state["reports"].get("restore"),
                    "outcome": outcome,
                }
            else:
                state["restore_review"]["outcome"] = outcome
            state["attempted_destination_id"] = target_id
            if outcome == "exact":
                state["destination_id"] = target_id
                state["reports"]["restore"] = {"status": "ok", **{
                    key: report[key] for key in _REPORT_KEYS if key in report}}
                if not already_restored:
                    state["completed"].append("restore")
            else:
                state["reports"].pop("restore", None)
            state.update(status="ok", running=None, reason=None)
            self._write(state)
            return {**self.status(), "review_outcome": outcome}
        finally:
            os.close(fd)

    def _pin(self, state, report):
        if (report["database_sha256"] != state["database_sha256"]
                or report["archive_sha256"] != state["archive_sha256"]):
            raise ValueError("backup changed")

    def _execute(self, fd, state, action, target):
        try:
            if action == "backup":
                with tempfile.TemporaryDirectory(prefix="staging-", dir=self.root) as staging:
                    directory = Path(staging)
                    # Publish the pair together by renaming a directory.
                    pair = directory / "pair"
                    pair.mkdir(mode=0o700)
                    snapshot = pair / _NAME
                    report = export_backup(self.source, snapshot, read_only=self.online_snapshot)
                    for path in (snapshot, Path(str(snapshot) + ".avatar.zip")):
                        path.chmod(0o600)
                        with path.open("rb") as stream:
                            os.fsync(stream.fileno())
                    _sync_dir(pair)
                    if self.snapshot.parent.exists():
                        raise ValueError("backup exists")
                    pair.rename(self.snapshot.parent)
                    _sync_dir(self.root)
                state["database_sha256"] = report["database_sha256"]
                state["archive_sha256"] = report["archive_sha256"]
            else:
                report = verify_snapshot(self.snapshot)
                self._pin(state, report)
                if action == "restore":
                    report = certify_restore(self.snapshot, target)
                    self._pin(state, report)
                    if report["equal_tables"] != report["tables"]:
                        raise ValueError("restore mismatch")
                    state["destination_id"] = _origin_digest(target)
                elif action == "restart":
                    report = certify_restart(self.snapshot, target)
                    if report["rank_equal"] is not True or report["memory_tables_equal"] != 4:
                        raise ValueError("restart mismatch")
            state["reports"][action] = {"status": "ok", **{key: report[key] for key in _REPORT_KEYS if key in report}}
            state["completed"].append(action)
            state.update(status="ok", running=None, reason=None)
        except Exception as exc:
            reason = "OPERATION_FAILED_REVIEW_REQUIRED"
            if isinstance(exc, ValueError) and exc.args and exc.args[0] in {
                "PCS4C_INTEGRITY", "PCS4C_FOREIGN_KEYS", "PCS4C_SCHEMA",
                "PCS4C_ASSET_MANIFEST", "PCS4C_ASSET_MEMBERS", "PCS4C_ASSET_SIZE",
                "PCS4C_ASSET_HASH", "PCS4C_SQL_ASSET_HASH", "PCS4C_SCHEMA_MISMATCH",
                "PCS4C_DATA_MISMATCH", "PCS4C_SOURCE_CHANGED", "PCS4C_RESTART_TABLES",
                "PCS4C_RESTART_RANK_MISMATCH", "PCS4C_RESTART_EMPTY_RECALL",
                "PCS4C_RESTART_MEMORY_CHANGED",
            }:
                reason = exc.args[0]
            state.update(status="error", running=None, reason=reason)
            state["reports"][action] = {"status": "error", "reason": reason}
        finally:
            try:
                self._write(state)
            finally:
                os.close(fd)


class Confirmation(BaseModel):
    confirmation: str


def create_app(panel=None, token=None):
    if panel is None:
        if os.getenv("AMORA_PCS4_WEB_ENABLED") != "1":
            raise RuntimeError("PCS4C web is disabled")
        token = os.getenv("AMORA_PCS4_WEB_TOKEN", "")
        mount = Path(os.environ.get("AMORA_PCS4_WEB_VOLUME", "/private")).resolve()
        if os.getenv("AMORA_PCS4_WEB_DURABLE") != "1" or not _mounted(mount):
            raise RuntimeError("PCS4C requires an explicitly mounted durable volume")
        source = LibSQLBackend(os.environ.get("AMORA_LIBSQL_URL", ""),
                               os.environ.get("AMORA_SECRET_LIBSQL_AUTH_TOKEN", ""))
        panel = RecoveryPanel(mount / "pcs4c", source)
    if not token or len(token.encode()) < 32:
        raise RuntimeError("PCS4C operator token must contain at least 32 bytes")
    if token in (panel.source._token, os.getenv("AMORA_PCS4C_DESTINATION_TOKEN"),
                 os.getenv("AMORA_SECRET_LOCAL_API_TOKEN")):
        raise RuntimeError("PCS4C operator token must be distinct")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @app.middleware("http")
    async def secure_headers(request, call_next):
        response = await call_next(request)
        for name, value in _HEADERS.items():
            response.headers[name] = value
        return response

    def authorize(authorization):
        if not hmac.compare_digest((authorization or "").encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "authentication required")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _HTML

    @app.get("/panel.js")
    def script():
        return Response(_JS, media_type="text/javascript")

    @app.get("/panel.css")
    def css():
        return Response(_CSS, media_type="text/css")

    @app.get("/ready")
    @app.get("/live")
    def ready():
        return {"status": "ready"}

    @app.get("/api/status")
    def status(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return panel.status()

    @app.post("/api/actions/{action}", status_code=202)
    def start(action: str, body: Confirmation, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return panel.start(action, body.confirmation)

    return app


_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amora • Recuperação PCS.4C</title><link rel="stylesheet" href="/panel.css">
<script src="/panel.js" defer></script></head><body><main>
<header><p class="eyebrow">AMORA / OPERAÇÕES PRIVADAS</p><h1>Recuperação da memória</h1>
<p>PCS.4C · Backup, verificação e restauração isolada. Nenhuma projeção local será ativada.</p></header>
<section><h2>Acesso do operador</h2><label for="token">Token exclusivo do painel</label>
<input id="token" type="password" autocomplete="off" spellcheck="false">
<button id="connect">Conectar</button><p id="connection" role="status">Desconectado</p></section>
<section><h2>Certificação</h2><p id="state" role="status">Conecte para consultar o estado.</p>
<article><h3>1. Criar backup</h3><p>Pause o Core, Discord e todos os escritores. A cópia ficará no volume privado.</p>
<button data-action="backup" disabled>Criar backup</button></article>
<article><h3>2. Verificar backup</h3><p>Confirme a retenção do volume privado. Depois de verificar, retome a produção.</p>
<button data-action="verify" disabled>Verificar backup</button></article>
<article><h3>3. Restaurar Turso independente</h3><p>Use um banco novo, vazio, com credencial exclusiva. Nunca use a produção.</p>
<button data-action="restore" disabled>Restaurar banco de teste</button></article>
<article><h3>4. Testar reinicialização</h3><p>Com o destino isolado, compara cinco consultas e quatro tabelas de memória após dois boots.</p>
<button data-action="restart" disabled>Certificar reinicialização</button></article>
</section><section><h2>Relatórios seguros</h2><pre id="report">Nenhum resultado.</pre>
<button id="copy" disabled>Copiar resultados</button></section>
<footer>Não envie backups, tokens ou conteúdo de memória. Falhas exigem revisão; não há repetição automática.</footer>
</main></body></html>"""
_CSS = """*{box-sizing:border-box}body{margin:0;background:#11141c;color:#f5f5f7;font:16px system-ui,sans-serif}main{max-width:780px;margin:auto;padding:24px}header{margin:20px 0 28px}h1{font-size:clamp(26px,6vw,40px);margin:8px 0}h2{font-size:19px;margin:0 0 14px}h3{font-size:17px;margin:0}p,footer{color:#b8becf;line-height:1.5}section{background:#1c2130;border:1px solid #30384b;border-radius:16px;padding:20px;margin:16px 0}article{border-top:1px solid #30384b;padding:18px 0 5px}label{display:block;margin-bottom:8px}input,button{font:inherit;border-radius:9px;padding:12px}input{width:100%;background:#11141c;border:1px solid #56617a;color:white;margin-bottom:10px}button{border:0;background:#9bb6ff;color:#101a39;font-weight:650;cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.eyebrow{font-size:12px;letter-spacing:2px;color:#9bb6ff}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#11141c;padding:12px;border-radius:8px;font-size:13px}footer{font-size:13px;margin:24px 0}"""
_JS = """'use strict';
let token='', timer=null, latest=null, busy=false;
const el=id=>document.getElementById(id);
const buttons=[...document.querySelectorAll('[data-action]')];
const confirms={backup:'PAUSEI OS ESCRITORES',verify:'VOLUME PRIVADO',restore:'DESTINO VAZIO E INDEPENDENTE',restart:'DESTINO ISOLADO'};
const statusLabels={idle:'Pronto para iniciar',running:'Operação em andamento',ok:'Etapa concluída',error:'Falha: revisão necessária',interrupted:'Interrompido: revisão necessária',blocked:'Bloqueado'};
function disable(){buttons.forEach(b=>b.disabled=true);el('copy').disabled=true}
async function request(path,options={}){const response=await fetch(path,{...options,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);return response.json()}
function render(s){latest=s;el('state').textContent=(statusLabels[s.status]||s.status)+(s.running?' — '+s.running:'')+(s.reason?' ('+s.reason+')':'');buttons.forEach(b=>{b.disabled=busy||s.next!==b.dataset.action;b.textContent=b.dataset.label||b.textContent;if(!b.dataset.label)b.dataset.label=b.textContent;if(s.completed.includes(b.dataset.action))b.textContent='Concluído — '+b.dataset.label});el('report').textContent=JSON.stringify(s.reports,null,2);el('copy').disabled=!Object.keys(s.reports).length}
async function refresh(){if(!token)return;try{render(await request('/api/status'));el('connection').textContent='Conectado'}catch(e){disable();el('connection').textContent='Falha de acesso: '+e.message}}
el('connect').addEventListener('click',async()=>{token=el('token').value;el('token').value='';if(timer)clearInterval(timer);await refresh();timer=setInterval(refresh,2000)});
buttons.forEach(b=>b.addEventListener('click',async()=>{const action=b.dataset.action;const text=confirms[action];const answer=window.prompt('Para confirmar esta etapa, digite exatamente: '+text);if(answer!==text)return;busy=true;disable();try{await request('/api/actions/'+action,{method:'POST',body:JSON.stringify({confirmation:answer})})}catch(e){el('connection').textContent='Não iniciado: '+e.message}finally{busy=false;await refresh()}}));
el('copy').addEventListener('click',async()=>{if(!latest)return;try{await navigator.clipboard.writeText(JSON.stringify(latest.reports,null,2));el('connection').textContent='Relatórios copiados'}catch(e){el('connection').textContent='Selecione o relatório para copiar'}});
"""
