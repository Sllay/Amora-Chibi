from __future__ import annotations

import argparse
import json
import socket

from fastapi.responses import JSONResponse, RedirectResponse

from amora.api import create_api
from amora.app import AmoraCore


_LAN_ALLOWED_EXACT = {"/", "/health", "/v1/pairing/redeem"}
_LAN_ALLOWED_PREFIXES = ("/avatar/", "/v1/avatar/")


def _dump(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _lan_path_allowed(path: str) -> bool:
    return path in _LAN_ALLOWED_EXACT or any(path.startswith(prefix) for prefix in _LAN_ALLOWED_PREFIXES)


def _detect_lan_ip() -> str | None:
    """Best-effort discovery of the A15 address used for the current LAN route."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect chooses a route/address without sending application data.
        sock.connect(("8.8.8.8", 80))
        address = str(sock.getsockname()[0])
        if address and not address.startswith("127."):
            return address
    except OSError:
        return None
    finally:
        sock.close()
    return None


def _restrict_lan_routes(app) -> None:
    @app.middleware("http")
    async def avatar_lan_guard(request, call_next):
        path = request.url.path
        if path == "/":
            return RedirectResponse(url="/avatar/", status_code=307)
        if not _lan_path_allowed(path):
            return JSONResponse(
                {"detail": "route unavailable in Avatar LAN mode"},
                status_code=404,
            )
        return await call_next(request)


def state() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.avatar.semantic_state().model_dump(mode="json"))
        return 0
    finally:
        core.close()


def project() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump(core.avatar.project().model_dump(mode="json"))
        return 0
    finally:
        core.close()


def assets() -> int:
    core = AmoraCore.bootstrap()
    try:
        _dump([item.model_dump(mode="json") for item in core.avatar.store.list_assets()])
        return 0
    finally:
        core.close()


def serve(*, port: int, lan: bool) -> int:
    import uvicorn

    core = AmoraCore.bootstrap()
    app = create_api(core)
    host = "0.0.0.0" if lan else "127.0.0.1"
    if lan:
        _restrict_lan_routes(app)
        address = _detect_lan_ip()
        print("Avatar Studio LAN: acesso restrito a Avatar + pareamento + health.")
        print("Use apenas em Wi-Fi/hotspot confiável.")
        if address:
            print(f"Outro celular: http://{address}:{port}/avatar/")
        else:
            print(f"Outro celular: http://<IP_DO_A15>:{port}/avatar/")
    else:
        print(f"Avatar Studio local: http://127.0.0.1:{port}/avatar/")
    try:
        uvicorn.run(app, host=host, port=port, log_config=None)
    finally:
        core.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="amora-avatar", description="Avatar Studio / Runtime V0.9")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    sub.add_parser("project")
    sub.add_parser("assets")

    url_parser = sub.add_parser("url")
    url_parser.add_argument("--port", type=int, default=8765)

    serve_parser = sub.add_parser("serve", help="Serve o Avatar Studio; --lan permite outro aparelho na rede local")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument(
        "--lan",
        action="store_true",
        help="escuta em 0.0.0.0 e restringe a API às rotas necessárias ao Avatar Studio",
    )
    args = parser.parse_args()

    if args.command == "state":
        return state()
    if args.command == "project":
        return project()
    if args.command == "assets":
        return assets()
    if args.command == "url":
        address = _detect_lan_ip()
        _dump({
            "local_studio": f"http://127.0.0.1:{args.port}/avatar/",
            "local_runtime": f"http://127.0.0.1:{args.port}/avatar/?mode=runtime",
            "lan_studio": f"http://{address}:{args.port}/avatar/" if address else None,
            "lan_runtime": f"http://{address}:{args.port}/avatar/?mode=runtime" if address else None,
        })
        return 0
    if args.command == "serve":
        return serve(port=args.port, lan=args.lan)
    return 2
