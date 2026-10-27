"""Phase A1 security tests: auth, authorization, body limits, investigation gate.

Offline only: temp DBs and fake model transports; no real API/network/keys.
No sleeps: gate timing uses an injected clock and threading Events.
"""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app import store
from app.auth import InvestigationGate
from app.main import app, get_investigation_client
from app.meta_client import MetaClient
from tests.test_investigator import FakeTransport, ids, tool_then_final

OPERATOR = "operator-token-for-tests-1234567890abc"
VIEWER = "viewer-token-for-tests-1234567890abcd"
MARKER = "SYNTHETIC_MARKER_DO_NOT_ECHO"
WIDGET = "WIDGET-1"


def _fresh_gate(clock=None):
    gate = InvestigationGate(clock=clock) if clock else InvestigationGate()
    main_module.investigation_gate = gate
    app.state.investigation_gate = gate
    return gate


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
        event_ids = ids(conn)
    finally:
        conn.close()
    saved_overrides = dict(app.dependency_overrides)
    saved_gate = main_module.investigation_gate
    gate = _fresh_gate()
    client = TestClient(app, headers={"Authorization": f"Bearer {OPERATOR}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER}"})
    anon = TestClient(app)
    try:
        yield {"client": client, "viewer": viewer, "anon": anon, "path": str(path),
               "ids": event_ids, "gate": gate}
    finally:
        client.close(); viewer.close(); anon.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)
        main_module.investigation_gate = saved_gate
        app.state.investigation_gate = saved_gate


def install_fake(api, handler):
    fake = FakeTransport(handler)
    app.dependency_overrides[get_investigation_client] = lambda: MetaClient(transport=fake)
    return fake


def proposals(api):
    conn = store.connect(api["path"])
    try:
        return conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    finally:
        conn.close()


def test_health_public_and_protected_denied_before_model_or_validation(api):
    assert api["anon"].get("/health").status_code == 200
    calls = {"n": 0}
    def counting_client():
        calls["n"] += 1
        return MetaClient(transport=FakeTransport(tool_then_final(["get_patch"], api["ids"]["all"])))
    app.dependency_overrides[get_investigation_client] = counting_client
    # Invalid body + marker: auth (401) precedes validation/model/DB effects.
    resp = api["anon"].post("/incidents/INC-001/investigate", json={"api_key": MARKER})
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"
    assert MARKER not in resp.text
    bad = api["anon"].post("/incidents/INC-001/investigate", json={},
                            headers={"Authorization": "Bearer wrong-token-value-123456"})
    assert bad.status_code == 401 and MARKER not in bad.text
    assert api["anon"].get("/incidents/INC-001").status_code == 401
    assert api["anon"].get("/openapi.json").status_code == 401
    assert api["anon"].get("/docs").status_code == 401
    assert calls["n"] == 0 and proposals(api) == 0


@pytest.mark.parametrize("op,view", [
    (None, VIEWER), ("short", VIEWER), (OPERATOR, OPERATOR),
    (OPERATOR, "bad token with spaces!!"), ("x" * 129, VIEWER),
])
def test_invalid_missing_colliding_config_fails_closed(api, monkeypatch, op, view):
    if op is None:
        monkeypatch.delenv("AFTERFIX_OPERATOR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AFTERFIX_OPERATOR_TOKEN", op)
    monkeypatch.setenv("AFTERFIX_VIEWER_TOKEN", view)
    resp = api["client"].get("/incidents/INC-001")
    assert resp.status_code == 503
    assert OPERATOR not in resp.text and VIEWER not in resp.text
    assert api["anon"].get("/health").status_code == 200


def test_viewer_reads_and_verify_but_writes_denied_before_effects(api):
    fake = install_fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    v = api["viewer"]
    assert v.get("/incidents/INC-001").status_code == 200
    assert v.get("/auth/me").json() == {"actor_id": "viewer", "role": "viewer", "can_reset": False}
    assert v.post("/verify", json={"product_id": WIDGET}).status_code == 200
    assert v.post("/incidents/INC-001/investigate", json={}).status_code == 403
    assert v.post("/proposals", json={"incident_id": "INC-001", "product_id": WIDGET,
                                       "evidence_event_ids": []}).status_code == 403
    assert v.post("/admin/reset").status_code == 403
    assert v.post("/proposals/x/approve", json={"snapshot_hash": "0" * 64}).status_code == 403
    assert v.post("/apply", json={"proposal_id": "x", "approval_id": "y",
                                  "idempotency_key": "key-1234"}).status_code == 403
    assert fake.payloads == [] and proposals(api) == 0


def test_reset_default_disabled_and_me_never_leaks_token(api, monkeypatch):
    monkeypatch.delenv("AFTERFIX_ALLOW_RESET", raising=False)
    assert api["client"].post("/admin/reset").status_code == 403
    me = api["client"].get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {"actor_id": "operator", "role": "operator", "can_reset": False}
    assert OPERATOR not in me.text and VIEWER not in me.text
    assert me.headers.get("cache-control") == "no-store"
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "1")
    assert api["client"].get("/auth/me").json()["can_reset"] is True
    assert api["client"].post("/admin/reset").status_code == 200


def test_actor_spoof_via_headers_or_query_denied_or_ignored(api):
    resp = api["viewer"].get("/auth/me?actor=operator&role=operator",
                             headers={"X-Actor-Id": "operator", "X-Role": "operator"})
    assert resp.json()["role"] == "viewer"
    spoof = api["anon"].get("/incidents/INC-001",
                            headers={"X-Actor-Id": "operator", "X-Role": "operator"})
    assert spoof.status_code == 401


def test_oversized_and_malformed_body_do_not_echo(api):
    big = api["client"].post("/verify", content='{"product_id": "' + MARKER + "x" * 70000 + '"}',
                             headers={"Content-Type": "application/json"})
    assert big.status_code == 413 and MARKER not in big.text
    malformed = api["client"].post("/verify", content='{"product_id": "' + MARKER + '",',
                                   headers={"Content-Type": "application/json"})
    assert malformed.status_code == 422
    assert malformed.json() == {"detail": "Invalid request"} and MARKER not in malformed.text


async def _asgi_request(headers, chunks, path="/verify"):
    messages, queue = [], list(chunks)
    async def receive():
        if queue:
            return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 1), "server": ("test", 80), "state": {}}
    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    hdrs = {k.decode().lower(): v.decode() for m in messages
            if m["type"] == "http.response.start" for k, v in m.get("headers", [])}
    return status, body, hdrs


def test_streamed_body_limit_without_content_length_direct_asgi(api):
    # No Content-Length header at all; limit enforced on actual streamed bytes.
    headers = [(b"authorization", f"Bearer {OPERATOR}".encode()),
               (b"content-type", b"application/json")]
    chunks = [(MARKER + "a" * 40000).encode(), b"b" * 40000]
    status, body, _ = asyncio.run(_asgi_request(headers, chunks))
    assert status == 413 and MARKER.encode() not in body


def test_multiple_authorization_headers_direct_asgi(api):
    headers = [(b"authorization", f"Bearer {OPERATOR}".encode()),
               (b"authorization", f"Bearer {VIEWER}".encode())]
    status, body, hdrs = asyncio.run(_asgi_request(headers, [b"{}"], path="/incidents/INC-001"))
    # GET path via POST harness still exercises header-count rejection first.
    assert status == 401 and hdrs.get("www-authenticate") == "Bearer"
    assert OPERATOR.encode() not in body and VIEWER.encode() not in body


def test_gate_rate_limit_with_injected_clock():
    now = [1000.0]
    gate = InvestigationGate(clock=lambda: now[0])
    for _ in range(3):
        assert gate.try_acquire() is True
        gate.release()
    assert gate.try_acquire() is False and gate.retry_after() >= 1
    now[0] += 61.0
    assert gate.try_acquire() is True
    gate.release()


def test_gate_concurrent_investigation_one_permit_no_extra_model_call(api):
    started, release = threading.Event(), threading.Event()
    base = tool_then_final(["get_patch", "read_events"], api["ids"]["all"])
    def blocking(payload, n):
        if not started.is_set():
            started.set()
            release.wait(timeout=10)
        return base(payload, n)
    fake = install_fake(api, blocking)
    result = {}
    def run_first():
        result["resp"] = api["client"].post("/incidents/INC-001/investigate", json={})
    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert started.wait(timeout=10)
        second = api["client"].post("/incidents/INC-001/investigate", json={})
        assert second.status_code == 429 and second.headers.get("retry-after")
        assert len(fake.payloads) == 1  # no extra model request spawned/queued
    finally:
        release.set()
        thread.join(timeout=15)
    assert result["resp"].status_code == 200


def test_gate_released_on_investigation_failure(api):
    def boom(payload, n):
        raise RuntimeError("synthetic transport failure")
    install_fake(api, boom)
    first = api["client"].post("/incidents/INC-001/investigate", json={})
    assert first.status_code in (200, 500) and first.status_code != 429
    fake = install_fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    second = api["client"].post("/incidents/INC-001/investigate", json={})
    assert second.status_code == 200 and len(fake.payloads) == 3
