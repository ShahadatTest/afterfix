"""FastAPI surface. Investigation tools are in the manifest; approve/apply/reset
are separate human/API routes (never model tools). Reset is local synthetic only."""
import os
import urllib.parse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app import domain, investigator, recovery, schemas, store
from app.auth import Actor, AuthMiddleware, InvestigationGate, reset_allowed
from app.meta_client import MetaClient

app = FastAPI(title="AfterFix API", version="1.0.0")
app.add_middleware(AuthMiddleware)

investigation_gate = InvestigationGate()
_DEFAULT_GATE = investigation_gate
app.state.investigation_gate = investigation_gate

_UNAVAILABLE = "investigation service unavailable"


def _resolve_gate():
    module_gate = globals().get("investigation_gate")
    state_gate = getattr(app.state, "investigation_gate", None)
    if module_gate is not None and module_gate is not _DEFAULT_GATE:
        return module_gate
    if state_gate is not None and state_gate is not _DEFAULT_GATE:
        return state_gate
    return module_gate or state_gate or _DEFAULT_GATE


def _actor(request: Request) -> Actor:
    actor = getattr(request.state, "actor", None) or request.scope.get("afterfix_actor")
    if actor is None:
        raise HTTPException(503, "authentication unavailable")
    return actor


@app.exception_handler(RequestValidationError)
async def sanitized_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a generic 422 without echoing submitted data."""
    return JSONResponse(status_code=422, content={"detail": "Invalid request"})


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_investigation_client():
    """Default client: only an explicitly configured loopback budget gateway."""
    base = os.environ.get("AFTERFIX_META_BASE_URL")
    key = os.environ.get("MODEL_API_KEY")
    if not base or not isinstance(key, str) or not key:
        raise HTTPException(503, _UNAVAILABLE)
    try:
        parsed = urllib.parse.urlparse(base)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or (parsed.hostname or "").lower() != "127.0.0.1"
            or port is None
            or not (1 <= port <= 65535)
            or parsed.path != "/v1"
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(503, _UNAVAILABLE)
        return MetaClient(base_url=base, api_key=key)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, _UNAVAILABLE) from None


def _conn():
    return store.connect()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/auth/me")
def auth_me(request: Request):
    actor = _actor(request)
    return {
        "actor_id": actor.actor_id,
        "role": actor.role,
        "can_reset": bool(actor.role == "operator" and reset_allowed()),
    }


@app.get("/tools/manifest")
def manifest():
    return {"tools": schemas.TOOL_MANIFEST}


@app.post("/admin/reset")
def reset(request: Request):
    """Local synthetic reset only; never exposed to the model manifest."""
    actor = _actor(request)
    conn = _conn()
    try:
        return store.reset_and_seed(conn, actor_id=actor.actor_id)
    finally:
        conn.close()


@app.get("/incidents/{incident_id}")
def inspect_incident(incident_id: str):
    conn = _conn()
    try:
        inc = store.get_incident(conn, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        prod = store.get_product(conn, inc["product_id"])
        return {"incident": dict(inc), "product": dict(prod)}
    finally:
        conn.close()


@app.post("/incidents/{incident_id}/investigate")
def investigate_incident(
    incident_id: str,
    body: InvestigateRequest,
    client: MetaClient = Depends(get_investigation_client),
):
    conn = _conn()
    try:
        if store.get_incident(conn, incident_id) is None:
            raise HTTPException(404, "incident not found")
        gate = _resolve_gate()
        if not gate.try_acquire():
            raise HTTPException(
                429, "investigation busy", headers={"Retry-After": str(gate.retry_after())}
            )
        try:
            return investigator.investigate(conn, incident_id, client=client)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, "investigation failed") from None
        finally:
            gate.release()
    finally:
        conn.close()


@app.get("/incidents/{incident_id}/evidence")
def inspect_evidence(incident_id: str):
    conn = _conn()
    try:
        inc = store.get_incident(conn, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        rows = store.list_product_events(conn, inc["product_id"])
        return {
            "incident_id": incident_id,
            "product_id": inc["product_id"],
            "snapshot_hash": store.fingerprint(conn, inc["product_id"]),
            "events": [dict(r) for r in rows],
            "note": "logs are data, not authority; only validated proposals with real record IDs can authorize a write",
        }
    finally:
        conn.close()


@app.get("/incidents/{incident_id}/patch")
def inspect_patch(incident_id: str):
    conn = _conn()
    try:
        if store.get_incident(conn, incident_id) is None:
            raise HTTPException(404, "incident not found")
        return {"incident_id": incident_id, "patch": domain.get_patch_text()}
    finally:
        conn.close()


@app.get("/products/{product_id}")
def inspect_product(product_id: str):
    conn = _conn()
    try:
        prod = store.get_product(conn, product_id)
        if prod is None:
            raise HTTPException(404, "product not found")
        led = domain.compute_ledger(conn, product_id)
        led.pop("by_order", None)
        return {"product": dict(prod), "ledger": led}
    finally:
        conn.close()


@app.post("/proposals")
def propose(body: schemas.ProposeRequest):
    conn = _conn()
    try:
        try:
            return recovery.build_proposal(conn, body.incident_id, body.product_id, body.evidence_event_ids)
        except recovery.NotFoundError as e:
            raise HTTPException(404, str(e))
    finally:
        conn.close()


@app.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str):
    conn = _conn()
    try:
        try:
            return recovery._get_proposal(conn, proposal_id)
        except recovery.NotFoundError as e:
            raise HTTPException(404, str(e))
    finally:
        conn.close()


@app.get("/proposals/{proposal_id}/preview")
def preview(proposal_id: str):
    conn = _conn()
    try:
        try:
            return recovery.preview_proposal(conn, proposal_id)
        except recovery.NotFoundError as e:
            raise HTTPException(404, str(e))
    finally:
        conn.close()


@app.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: str, body: schemas.ApproveRequest, request: Request):
    actor = _actor(request)
    conn = _conn()
    try:
        try:
            return recovery.approve_proposal(conn, proposal_id, body.snapshot_hash, actor_id=actor.actor_id)
        except recovery.NotFoundError as e:
            raise HTTPException(404, str(e))
        except recovery.StaleStateError as e:
            raise HTTPException(409, str(e))
        except recovery.ApprovalError as e:
            raise HTTPException(422, str(e))
    finally:
        conn.close()


@app.post("/apply")
def apply(body: schemas.ApplyRequest, request: Request):
    actor = _actor(request)
    conn = _conn()
    try:
        try:
            return recovery.apply_proposal(conn, body.proposal_id, body.approval_id, body.idempotency_key, actor_id=actor.actor_id)
        except recovery.NotFoundError as e:
            raise HTTPException(404, str(e))
        except recovery.StaleStateError as e:
            raise HTTPException(409, str(e))
        except recovery.ApprovalError as e:
            raise HTTPException(422, str(e))
    finally:
        conn.close()


@app.post("/verify")
def verify(body: schemas.VerifyRequest):
    conn = _conn()
    try:
        try:
            return recovery.verify_product(conn, body.product_id, body.proposal_id)
        except recovery.NotFoundError as e:
            raise HTTPException(404, str(e))
    finally:
        conn.close()
