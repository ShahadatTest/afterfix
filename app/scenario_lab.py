"""Owner-approved Scenario Lab: fixed synthetic fixtures, backend only.

Disabled unless AFTERFIX_ENABLE_SCENARIO_LAB is exactly "1".  Loading and
advancing never call a model.  Fixtures are built in an isolated in-memory
SQLite database with the existing seed workflow, then copied to the target
in one BEGIN IMMEDIATE transaction so any failure restores the prior demo.
"""
import os
import re
import sqlite3
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from app import store
from app.auth import reset_allowed

router = APIRouter()

DATA_LABEL = "Synthetic Scenario Lab"
_CONFLICT = "scenario state conflict"
_LOAD_UNAVAILABLE = "scenario load unavailable"
_ADVANCE_UNAVAILABLE = "scenario advance unavailable"
_SCENARIOS = (
    {"id": "duplicate_order", "title": "Duplicate order",
     "description": "Synthetic inventory exercise with repeated order-delivery records.",
     "can_advance": False},
    {"id": "missing_opening", "title": "Missing opening record",
     "description": "Synthetic inventory exercise with incomplete opening history.",
     "can_advance": False},
    {"id": "stale_snapshot", "title": "Stale snapshot",
     "description": "Synthetic inventory exercise where a later legitimate restock can invalidate an earlier snapshot.",
     "can_advance": True},
)
_SCENARIO_IDS = {s["id"] for s in _SCENARIOS}
_STATE_SQL = (
    "CREATE TABLE IF NOT EXISTS scenario_lab_state("
    "id INTEGER PRIMARY KEY CHECK(id=1), load_id TEXT NOT NULL, "
    "scenario_id TEXT NOT NULL, advanced INTEGER NOT NULL DEFAULT 0, "
    "actor_id TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL DEFAULT 0)"
)
_CLEAR_ORDER = ("case_records", "idempotency_keys", "audit_log", "approvals",
                "proposals", "events", "incidents", "products")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class LoadRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    confirmation: Literal["RESET_SYNTHETIC_SCENARIO"]


class AdvanceRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    confirmation: Literal["ADD_SYNTHETIC_RESTOCK"]
    load_id: Annotated[StrictStr, Field(min_length=36, max_length=36)]
    proposal_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    expected_snapshot_hash: Annotated[
        StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


def _enabled():
    return os.environ.get("AFTERFIX_ENABLE_SCENARIO_LAB") == "1"


def _require_enabled():
    if not _enabled():
        raise HTTPException(404, "not found")


def _actor(request):
    actor = getattr(request.state, "actor", None) or request.scope.get("afterfix_actor")
    if actor is None:
        raise HTTPException(503, "authentication unavailable")
    return actor


def _can_load(actor):
    return getattr(actor, "role", None) == "operator" and reset_allowed()


def _require_mutation(request):
    """Feature/actor/reset gate; runs strictly before any DB connection."""
    _require_enabled()
    actor = _actor(request)
    if not _can_load(actor):
        raise HTTPException(403, "forbidden")
    return actor


def _safe_rollback(conn):
    if conn is not None:
        try:
            conn.rollback()
        except Exception:
            pass


def _safe_close(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def invalidate_session(conn):
    """Drop the lab session row if (and only if) its table already exists.

    Used by ordinary /admin/reset.  Never creates the table, never reads
    env/files, and never touches proposal/repair data.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scenario_lab_state'"
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM scenario_lab_state")
    conn.commit()
    return True


@router.get("/lab/scenarios")
def catalogue(request: Request):
    _require_enabled()
    actor = _actor(request)
    can_load = _can_load(actor)
    return {"enabled": True, "data_label": DATA_LABEL, "can_load": can_load,
            "scenarios": [dict(s) for s in _SCENARIOS]}


def _build_scratch(scenario_id, actor_id):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        store.reset_and_seed(scratch, actor_id=actor_id)
        if scenario_id == "missing_opening":
            scratch.execute(
                "DELETE FROM events WHERE product_id=? AND kind='init'",
                (store.CANONICAL_PRODUCT,))
            scratch.commit()
        products = [tuple(r) for r in scratch.execute(
            "SELECT product_id, name, stock FROM products ORDER BY product_id")]
        incidents = [tuple(r) for r in scratch.execute(
            "SELECT id, product_id, description, status FROM incidents ORDER BY id")]
        events = [tuple(r) for r in scratch.execute(
            "SELECT product_id, kind, qty, order_id, ts, note, reversed_ids, "
            "repair_proposal_id FROM events ORDER BY id")]
        return products, incidents, events
    finally:
        scratch.close()


@router.post("/lab/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str, body: LoadRequest, request: Request):
    actor = _require_mutation(request)
    if scenario_id not in _SCENARIO_IDS:
        raise HTTPException(404, "scenario not found")
    conn = None
    try:
        # Scratch construction, connection creation, BEGIN and metadata
        # setup are all inside this sanitized boundary.  The lab table is
        # created only inside the same transaction as reset/copy/audit/
        # session update: no pre-transaction metadata commit exists.
        products, incidents, events = _build_scratch(scenario_id, actor.actor_id)
        load_id = str(uuid.uuid4())
        conn = store.connect()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_STATE_SQL)
        for table in _CLEAR_ORDER:  # fixed table names only
            conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            "INSERT INTO products(product_id, name, stock) VALUES (?,?,?)", products)
        conn.executemany(
            "INSERT INTO incidents(id, product_id, description, status) VALUES (?,?,?,?)",
            incidents)
        # No scratch event IDs copied: AUTOINCREMENT allocates fresh IDs
        # and sqlite_sequence is never reset, so prior snapshots go stale.
        conn.executemany(
            "INSERT INTO events(product_id, kind, qty, order_id, ts, note, "
            "reversed_ids, repair_proposal_id) VALUES (?,?,?,?,?,?,?,?)", events)
        store.audit(conn, "scenario_load", "-", scenario_id, actor_id=actor.actor_id)
        conn.execute(
            "INSERT INTO scenario_lab_state(id, load_id, scenario_id, advanced, actor_id, updated_at) "
            "VALUES (1,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET load_id=excluded.load_id, "
            "scenario_id=excluded.scenario_id, advanced=0, actor_id=excluded.actor_id, "
            "updated_at=excluded.updated_at",
            (load_id, scenario_id, actor.actor_id, int(time.time())))
        snapshot_hash = store.fingerprint(conn, store.CANONICAL_PRODUCT)
        widget = store.get_product(conn, store.CANONICAL_PRODUCT)["stock"]
        gadget = store.get_product(conn, "GADGET-9")["stock"]
        conn.commit()
    except HTTPException:
        _safe_rollback(conn)
        raise
    except Exception:
        _safe_rollback(conn)
        raise HTTPException(500, _LOAD_UNAVAILABLE) from None
    finally:
        _safe_close(conn)
    return {"scenario_id": scenario_id, "load_id": load_id,
            "incident_id": store.CANONICAL_INCIDENT,
            "product_id": store.CANONICAL_PRODUCT,
            "snapshot_hash": snapshot_hash, "data_label": DATA_LABEL,
            "widget_stock": widget, "gadget_stock": gadget}


def _canonical_uuid(value):
    try:
        return str(uuid.UUID(value)) == value
    except Exception:
        return False


@router.post("/lab/scenarios/stale_snapshot/advance")
def advance_stale(request: Request, body: AdvanceRequest):
    actor = _require_mutation(request)
    if (not _canonical_uuid(body.load_id)
            or not _HEX64.fullmatch(body.expected_snapshot_hash)):
        raise HTTPException(422, "Invalid request")
    conn = None
    try:
        conn = store.connect()
        conn.execute("BEGIN IMMEDIATE")
        # Never create the metadata table here: an invalid/unloaded
        # session is a conflict and must leave schema/app state untouched.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='scenario_lab_state'").fetchone()
        if exists is None:
            raise HTTPException(409, _CONFLICT)
        state = conn.execute(
            "SELECT * FROM scenario_lab_state WHERE id=1").fetchone()
        if (state is None or state["scenario_id"] != "stale_snapshot"
                or state["load_id"] != body.load_id or state["advanced"]):
            raise HTTPException(409, _CONFLICT)
        current = store.fingerprint(conn, store.CANONICAL_PRODUCT)
        if current != body.expected_snapshot_hash:
            raise HTTPException(409, _CONFLICT)
        proposal = conn.execute(
            "SELECT * FROM proposals WHERE id=?", (body.proposal_id,)).fetchone()
        if (proposal is None or proposal["incident_id"] != store.CANONICAL_INCIDENT
                or proposal["product_id"] != store.CANONICAL_PRODUCT
                or proposal["snapshot_hash"] != body.expected_snapshot_hash
                or proposal["status"] not in ("proposed", "approved")):
            raise HTTPException(409, _CONFLICT)
        ts = int(time.time())
        cur = conn.execute(
            "INSERT INTO events(product_id, kind, qty, order_id, ts, note) "
            "VALUES (?,?,?,?,?,?)",
            (store.CANONICAL_PRODUCT, "restock", 1, None, ts,
             "synthetic scenario restock"))
        event_id = cur.lastrowid
        conn.execute(
            "UPDATE products SET stock = stock + 1 WHERE product_id=?",
            (store.CANONICAL_PRODUCT,))
        store.audit(conn, "scenario_advance", body.proposal_id,
                    "stale_snapshot", actor_id=actor.actor_id)
        conn.execute(
            "UPDATE scenario_lab_state SET advanced=1, actor_id=?, updated_at=? WHERE id=1",
            (actor.actor_id, ts))
        new_hash = store.fingerprint(conn, store.CANONICAL_PRODUCT)
        stock = store.get_product(conn, store.CANONICAL_PRODUCT)["stock"]
        conn.commit()
    except HTTPException:
        _safe_rollback(conn)
        raise
    except Exception:
        _safe_rollback(conn)
        raise HTTPException(500, _ADVANCE_UNAVAILABLE) from None
    finally:
        _safe_close(conn)
    return {"scenario_id": "stale_snapshot", "load_id": body.load_id,
            "snapshot_hash": new_hash, "incident_id": store.CANONICAL_INCIDENT,
            "product_id": store.CANONICAL_PRODUCT, "data_label": DATA_LABEL,
            "stock": stock, "event_id": event_id}
