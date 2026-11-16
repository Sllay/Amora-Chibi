"""Operator-only agenda controls; do not expose private text to paired viewers."""
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from .models import AgendaCreate
from .service import AgendaService


def register_agenda_routes(app: FastAPI, core, require_auth):
    service = AgendaService(core.database, core.clock, core.messaging_store)

    def require_operator(request: Request):
        if getattr(request.state, "security_role", None) not in {"admin", "operator"}:
            raise HTTPException(status_code=403, detail="AGENDA_OPERATOR_REQUIRED")

    auth = [Depends(require_auth), Depends(require_operator)]

    @app.get("/v1/operator/agenda", dependencies=auth)
    def agenda_list(year: int | None = Query(default=None, ge=2000, le=9999)):
        try:
            return {"items": service.projection(year=year), "timezone": "America/Recife"}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/v1/operator/agenda", dependencies=auth, status_code=201)
    def agenda_create(payload: AgendaCreate):
        try:
            item = service.create(payload)
            return {"item": item.model_dump(mode="json", exclude={"external_actor_id","conversation_id","account_scope"})}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.delete("/v1/operator/agenda/{item_id}", dependencies=auth)
    def agenda_cancel(item_id: UUID):
        try:
            item = service.cancel(item_id)
            return {"id": str(item.id), "status": "cancelled"}
        except KeyError:
            raise HTTPException(status_code=404, detail="AGENDA_NOT_FOUND") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
