"""Connection metadata/scan only. No user-selected file paths or SQL over HTTP."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from app import agent

router = APIRouter(prefix="/agent")


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _operator(request):
    actor = request.scope.get("afterfix_actor")
    if actor is None or actor.role != "operator":
        raise HTTPException(403, "operator access required")


@router.get("/status")
def status(request: Request):
    _operator(request)
    if not agent.enabled():
        return {"enabled": False, "connector": None}
    try:
        return agent.scan()
    except Exception:
        raise HTTPException(503, "inventory connection unavailable; review local agent configuration") from None


@router.post("/scan")
def scan(body: ScanRequest, request: Request):
    _operator(request)
    if not agent.enabled():
        raise HTTPException(404, "no local inventory connector configured")
    try:
        return agent.scan(register=True)
    except Exception:
        raise HTTPException(503, "inventory scan unavailable; review local agent configuration") from None
