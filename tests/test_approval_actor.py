"""Phase A2: approvals/apply/audit are bound to the authenticated actor.

Offline only: synthetic temp DBs, no network/model calls. Two trusted actor
IDs are exercised directly against the recovery engine; the HTTP surface is
exercised only with the configured operator/viewer identities.
"""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app import recovery, store
from app.auth import MAX_BODY_BYTES, AuthMiddleware, InvestigationGate
from app.main import app
from tests.test_auth import OPERATOR, VIEWER, _asgi_request

WIDGET = "WIDGET-1"
ACTOR_A = "actor-a"
ACTOR_B = "actor-b"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    path = tmp_path / "afterfix.db"
    monkeypatch.setenv("AFTERFIX_DB", str(path))
    monkeypatch.setenv("AFTERFIX_OPERATOR_TOKEN", OPERATOR)
    monkeypatch.setenv("AFTERFIX_VIEWER_TOKEN", VIEWER)
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "1")
    conn = store.connect(str(path))
    try:
        store.reset_and_seed(conn)
    finally:
        conn.close()
    client = TestClient(app, headers={"Authorization": f"Bearer {OPERATOR}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER}"})
    try:
        yield {"client": client, "viewer": viewer, "path": str(path)}
    finally:
        client.close(); viewer.close()


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "direct.db")
    conn = store.connect(path)
    try:
        store.reset_and_seed(conn)
        yield conn, path
    finally:
        conn.close()


def _evidence(conn):
    return [r["id"] for r in store.list_product_events(conn, WIDGET)]


def _proposal(conn, path=None):
    p = recovery.build_proposal(conn, "INC-001", WIDGET, _evidence(conn))
    assert p["status"] == "proposed", p
    return p


def _state(conn):
    return {
        "stock": store.get_product(conn, WIDGET)["stock"],
        "repairs": conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0],
        "approvals": conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0],
        "keys": conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0],
        "audit": [tuple(r) for r in conn.execute("SELECT action, proposal_id, actor_id FROM audit_log ORDER BY id")],
        "status": conn.execute("SELECT status FROM proposals ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()[0]
        if conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] else None,
    }


def _approved(conn, actor=ACTOR_A):
    p = _proposal(conn)
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id=actor)
    return p, appr


# --- direct engine: happy path and actor recording -------------------------

def test_approve_apply_retry_same_actor_records_actor_everywhere(db):
    conn, _ = db
    p, appr = _approved(conn)
    assert appr["actor_id"] == ACTOR_A
    row = conn.execute("SELECT actor_id FROM approvals WHERE id=?", (appr["approval_id"],)).fetchone()
    assert row["actor_id"] == ACTOR_A
    result = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-actor-1", actor_id=ACTOR_A)
    assert result["status"] == "applied" and result["after_stock"] == 110
    retry = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-actor-1", actor_id=ACTOR_A)
    assert retry == result
    audit = {r["action"]: r["actor_id"] for r in conn.execute("SELECT action, actor_id FROM audit_log")}
    assert audit["approve"] == ACTOR_A and audit["apply"] == ACTOR_A
    assert _state(conn)["repairs"] == 1


def test_default_trusted_actor_still_works(db):
    conn, _ = db
    p = _proposal(conn)
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"])
    assert appr["actor_id"] == "local-core"
    out = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-default-1")
    assert out["status"] == "applied"


# --- direct engine: different actor is denied before every shortcut --------

def test_different_actor_denied_before_first_mutation(db):
    conn, _ = db
    p, appr = _approved(conn)
    before = _state(conn)
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-other-1", actor_id=ACTOR_B)
    assert _state(conn) == before


def test_different_actor_denied_before_cached_and_already_applied_replays(db):
    conn, _ = db
    p, appr = _approved(conn)
    recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-actor-1", actor_id=ACTOR_A)
    before = _state(conn)
    # Same key: must not receive the cached success.
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-actor-1", actor_id=ACTOR_B)
    # Different key: must not receive the already-applied shortcut (or write a key).
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-other-2", actor_id=ACTOR_B)
    assert _state(conn) == before


def test_unknown_approval_and_blank_or_reassigned_actor_fail_without_writes(db):
    conn, _ = db
    p, appr = _approved(conn)
    before = _state(conn)
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], "APPR-missing", "key-x-1234", actor_id=ACTOR_A)
    assert _state(conn) == before
    # Blank actor as stored by a pre-A2 (migrated) approval, and an approval
    # reassigned to somebody else, both fail closed.
    for stored in ("", "someone-else"):
        conn.execute("UPDATE approvals SET actor_id=? WHERE id=?", (stored, appr["approval_id"]))
        conn.commit()
        with pytest.raises(recovery.ApprovalError):
            recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-x-1234", actor_id=ACTOR_A)
        assert _state(conn) == before


@pytest.mark.parametrize("bad", ["", "   ", None, 123, "x" * 129, " actor-a", "actor-a\n", "actor\ta"])
def test_malformed_actor_rejected_with_no_partial_writes(db, bad):
    conn, _ = db
    p = _proposal(conn)
    before = _state(conn)
    with pytest.raises(recovery.ApprovalError):
        recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id=bad)
    assert _state(conn) == before
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id=ACTOR_A)
    before = _state(conn)
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-bad-actor", actor_id=bad)
    assert _state(conn) == before


# --- migration -------------------------------------------------------------

def test_migration_preserves_old_records_and_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE approvals(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, snapshot_hash TEXT NOT NULL, baseline_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL)")
    raw.execute("CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, action TEXT NOT NULL, proposal_id TEXT NOT NULL, detail TEXT NOT NULL)")
    raw.execute("INSERT INTO approvals(id, proposal_id, snapshot_hash, created_at) VALUES ('APPR-old','PROP-old','abc',1)")
    raw.execute("INSERT INTO audit_log(ts, action, proposal_id, detail) VALUES (1,'approve','PROP-old','legacy')")
    raw.commit(); raw.close()
    conn = store.connect(path)  # runs init_db once via connect
    try:
        store.init_db(conn); store.init_db(conn)  # and twice more explicitly
        appr = conn.execute("SELECT proposal_id, snapshot_hash, actor_id FROM approvals WHERE id='APPR-old'").fetchone()
        assert tuple(appr) == ("PROP-old", "abc", "")
        audit = conn.execute("SELECT action, detail, actor_id FROM audit_log").fetchone()
        assert tuple(audit) == ("approve", "legacy", "")
    finally:
        conn.close()


def test_migrated_blank_actor_approval_cannot_apply(db):
    conn, _ = db
    p, appr = _approved(conn)
    conn.execute("UPDATE approvals SET actor_id='' WHERE id=?", (appr["approval_id"],))
    conn.commit()
    before = _state(conn)
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-migrated", actor_id=ACTOR_A)
    assert _state(conn) == before


def test_reset_and_seed_stamps_configured_actor(db):
    conn, _ = db
    store.reset_and_seed(conn, actor_id="operator")
    row = conn.execute("SELECT actor_id FROM audit_log WHERE action='seed'").fetchone()
    assert row["actor_id"] == "operator"


# --- lead auth-review regressions ------------------------------------------

def test_configured_token_with_trailing_newline_is_invalid(api, monkeypatch):
    monkeypatch.setenv("AFTERFIX_OPERATOR_TOKEN", OPERATOR + "\n")
    assert api["client"].get("/incidents/INC-001").status_code == 503


def test_oversized_first_chunk_rejected_direct_asgi(api):
    headers = [(b"authorization", f"Bearer {OPERATOR}".encode()), (b"content-type", b"application/json")]
    status, body, _ = asyncio.run(_asgi_request(headers, [b"x" * (MAX_BODY_BYTES + 1)]))
    assert status == 413


async def _get_with_body():
    messages = []
    sent_request = {"done": False}
    async def receive():
        if not sent_request["done"]:
            sent_request["done"] = True
            return {"type": "http.request", "body": b"x" * (MAX_BODY_BYTES + 1), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/incidents/INC-001", "raw_path": b"/incidents/INC-001",
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1), "server": ("test", 80),
             "headers": [(b"authorization", f"Bearer {OPERATOR}".encode())], "state": {}}
    await app(scope, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def test_get_body_is_bounded_too(api):
    assert asyncio.run(_get_with_body()) == 413


def test_replay_delegates_further_receives_to_original_transport(api):
    async def downstream(scope, receive, send):
        first = await receive()
        second = await receive()
        payload = first.get("body", b"") + b"|" + second.get("body", b"")
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": payload, "more_body": False})
    calls = {"n": 0}
    async def original_receive():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"type": "http.request", "body": b"first-body", "more_body": False}
        return {"type": "http.request", "body": b"from-original", "more_body": False}
    messages = []
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/verify", "raw_path": b"/verify", "query_string": b"",
             "root_path": "", "client": ("127.0.0.1", 1), "server": ("test", 80),
             "headers": [(b"authorization", f"Bearer {OPERATOR}".encode())], "state": {}}
    asyncio.run(AuthMiddleware(downstream)(scope, original_receive, send))
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    assert body == b"first-body|from-original"


def test_gate_has_no_context_manager_that_could_release_others_permit():
    gate = InvestigationGate()
    assert gate.try_acquire() is True
    with pytest.raises((AttributeError, TypeError)):
        with gate:
            pass
    # The failed context attempt must not have released the held permit.
    assert gate.try_acquire() is False
    gate.release()
    assert gate.try_acquire() is True
    gate.release()
    assert not hasattr(gate, "acquire")


# --- API: authenticated identity only --------------------------------------

def _api_proposal(api):
    conn = store.connect(api["path"])
    try:
        evidence = _evidence(conn)
    finally:
        conn.close()
    resp = api["client"].post("/proposals", json={"incident_id": "INC-001", "product_id": WIDGET, "evidence_event_ids": evidence})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_api_operator_workflow_binds_authenticated_actor_and_spoofing_rejected(api):
    p = _api_proposal(api)
    pid = p["proposal_id"]
    # Spoofed actor fields are forbidden by the existing strict body models.
    spoof = api["client"].post(f"/proposals/{pid}/approve", json={"snapshot_hash": p["snapshot_hash"], "actor_id": "viewer"})
    assert spoof.status_code == 422 and "viewer" not in spoof.text
    conn = store.connect(api["path"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        conn.close()
    approve = api["client"].post(f"/proposals/{pid}/approve", json={"snapshot_hash": p["snapshot_hash"]})
    assert approve.status_code == 200, approve.text
    assert approve.json()["actor_id"] == "operator"
    spoof_apply = api["client"].post("/apply", json={"proposal_id": pid, "approval_id": approve.json()["approval_id"], "idempotency_key": "api-key-1", "actor_id": "viewer"})
    assert spoof_apply.status_code == 422
    applied = api["client"].post("/apply", json={"proposal_id": pid, "approval_id": approve.json()["approval_id"], "idempotency_key": "api-key-1"})
    assert applied.status_code == 200 and applied.json()["status"] == "applied"
    retry = api["client"].post("/apply", json={"proposal_id": pid, "approval_id": approve.json()["approval_id"], "idempotency_key": "api-key-1"})
    assert retry.status_code == 200 and retry.json()["status"] == "applied"
    conn = store.connect(api["path"])
    try:
        assert conn.execute("SELECT actor_id FROM approvals").fetchone()[0] == "operator"
        actors = {r["action"]: r["actor_id"] for r in conn.execute("SELECT action, actor_id FROM audit_log WHERE action IN ('approve','apply')")}
        assert actors == {"approve": "operator", "apply": "operator"}
        assert store.get_product(conn, WIDGET)["stock"] == 110
    finally:
        conn.close()


def test_api_viewer_cannot_approve_apply_reset_or_spend(api):
    p = _api_proposal(api)
    pid = p["proposal_id"]
    v = api["viewer"]
    assert v.post(f"/proposals/{pid}/approve", json={"snapshot_hash": p["snapshot_hash"]}).status_code == 403
    assert v.post("/apply", json={"proposal_id": pid, "approval_id": "x", "idempotency_key": "viewer-key"}).status_code == 403
    assert v.post("/admin/reset").status_code == 403
    assert v.post("/incidents/INC-001/investigate", json={}).status_code == 403
    assert v.get("/auth/me").json()["actor_id"] == "viewer"
    conn = store.connect(api["path"])
    try:
        state = _state(conn)
        assert state["approvals"] == 0 and state["repairs"] == 0 and state["stock"] == 100
    finally:
        conn.close()


def test_api_reset_stamps_authenticated_operator(api):
    assert api["client"].post("/admin/reset").status_code == 200
    conn = store.connect(api["path"])
    try:
        row = conn.execute("SELECT actor_id FROM audit_log WHERE action='seed'").fetchone()
        assert row["actor_id"] == "operator"
    finally:
        conn.close()
